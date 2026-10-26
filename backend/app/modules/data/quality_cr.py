"""Quality definitions are staged in the existing CR transaction and published atomically."""

from uuid import uuid4

from pydantic import ValidationError
from sqlalchemy import select

from app.common.auth import CurrentUser
from app.core.errors import AppError, Forbidden, NotFound, VersionConflict
from app.modules.data import quality
from app.modules.data.quality_models import QualityCheck
from app.modules.data.quality_schemas import ChangeOptions, CheckDefinition
from app.modules.static.cr_write import CrWriteService
from app.modules.static.models import ChangeRequest, CrItem, CRStatus, CrTarget, ObjectType
from app.modules.static.schemas import CRCreateIn, ItemIn, ItemsPutIn, TargetIn
from app.modules.static.writeback import Ctx, register_writeback, writeback_error

FIELDS = ("name", "typeKey", "kind", "threshold", "schedule")
HOLDING = (CRStatus.draft, CRStatus.rejected, CRStatus.in_review, CRStatus.approved)


def definition(check, obj):
    return {
        "name": check.name,
        "typeKey": obj.code,
        "kind": check.kind,
        "threshold": check.threshold,
        "schedule": check.schedule,
    }


async def validate_definition(session, value):
    body = CheckDefinition.model_validate(value)
    obj = await session.scalar(select(ObjectType).where(ObjectType.code == body.type_key))
    if obj is None:
        raise NotFound("对象类型不存在")
    if body.kind not in quality.kinds(obj.code):
        raise AppError("该对象的检查类型尚未配置物理字段映射", code="QUALITY_NOT_CONFIGURED", status_code=422)
    return body, obj, quality.limit_value(body.kind, body.threshold)


async def pending(session, owner):
    rows = (
        await session.execute(
            select(ChangeRequest, CrTarget)
            .join(CrTarget)
            .where(
                CrTarget.target_kind == "quality",
                ChangeRequest.created_by == owner,
                ChangeRequest.status.in_(HOLDING),
            )
            .order_by(ChangeRequest.id)
        )
    ).all()
    items = await session.scalars(
        select(CrItem).where(
            CrItem.cr_id.in_([cr.id for cr, _ in rows]),
            CrItem.target_kind == "quality",
            CrItem.section == "def",
        )
    )
    grouped = {}
    for item in items:
        grouped.setdefault((item.cr_id, item.target_id), {})[item.item_key] = item.after
    out = {}
    for cr, target in rows:
        values = grouped.get((cr.id, target.target_id), {})
        if not all(k in values for k in FIELDS):
            continue
        try:
            valid = CheckDefinition.model_validate({k: values[k] for k in FIELDS}).model_dump(by_alias=True)
        except ValidationError:
            continue
        out[target.target_id] = {
            "crId": cr.code,
            "lockVersion": cr.lock_version,
            "status": cr.status.value,
            "isNew": target.is_new,
            "definition": valid,
            "retire": values.get("_retire", False),
            "baseLockVersion": values.get("_baseLockVersion"),
        }
    return out


async def stage(session, user: CurrentUser, options: ChangeOptions, value=None, code=None, retire=False):
    saved_pending = await pending(session, user.id)
    previous = saved_pending.get(code)
    check = await session.scalar(select(QualityCheck).where(QualityCheck.code == code)) if code else None
    if code and check is None and previous is None:
        raise NotFound("质量检查不存在")
    if check and check.created_by != user.id:
        raise NotFound("质量检查不存在")
    if retire and (check is None or check.retired_at):
        raise AppError("仅已发布且未退休的检查可删除", code="STATE_INVALID", status_code=409)
    if check and check.retired_at and not retire:
        raise AppError("已退休检查仅保留历史，不可修改", code="STATE_INVALID", status_code=409)
    if previous and options.cr_id != previous["crId"]:
        raise AppError(
            "检查已在变更单中，请重新加载后编辑",
            code="TARGET_IN_OTHER_CR",
            status_code=409,
            details={"crId": previous["crId"]},
        )
    obj = await session.get(ObjectType, check.object_type_id) if check else None
    base = definition(check, obj) if check else {}
    value = value if value is not None else base
    await validate_definition(session, value)
    code = code or f"QC-{uuid4().hex[:16]}"
    target = TargetIn(kind="quality", id=code, name=value["name"][:128], is_new=check is None)
    writer = CrWriteService(session)
    if options.cr_id:
        if options.lock_version is None:
            raise VersionConflict("缺少变更单版本，请重新加载草稿")
        cr = await writer.lock_draft(options.cr_id, user, options.lock_version)
        if cr.created_by != user.id:
            raise Forbidden("质量检查只能加入自己发起的变更单")
        cr_id = cr.code
    else:
        cr_id = await writer.create(
            user,
            CRCreateIn(
                title=options.title or f"质量检查：{value['name']}"[:40], reason=options.reason, target=target
            ),
        )
    baseline = previous["baseLockVersion"] if previous else check.lock_version if check else None
    rows = [
        ItemIn(
            key=k,
            op="add" if check is None else "update",
            before=base.get(k),
            after=value[k],
            type="质量检查",
            field=k,
        )
        for k in FIELDS
    ]
    rows += [
        ItemIn(key="_baseLockVersion", op="add", after=baseline),
        ItemIn(key="_retire", op="add", after=retire),
    ]
    saved = await writer.put_items(
        cr_id,
        user,
        ItemsPutIn(
            target=target,
            section="def",
            rows=rows,
            lock_version=options.lock_version if options.cr_id else None,
        ),
    )
    return {"id": code, "crId": cr_id, "lockVersion": saved["lockVersion"], "status": "草稿"}


async def publish_definition(ctx: Ctx, code: str, rows: list[CrItem]) -> set[str]:
    allowed = {*FIELDS, "_baseLockVersion", "_retire"}
    if any(i.item_key not in allowed or i.op == "delete" for i in rows):
        raise writeback_error("质量检查含未知字段或删除行，请从质量页重新保存", id=code)
    values = {i.item_key: i.after for i in rows}
    try:
        body, obj, limit = await validate_definition(ctx.session, values)
        if set(values) != allowed or not isinstance(values["_retire"], bool):
            raise ValueError("定义不完整")
    except (AppError, ValidationError, ValueError, TypeError) as error:
        raise writeback_error("质量检查定义无效", id=code, reason=str(error)) from None
    check = await ctx.session.scalar(select(QualityCheck).where(QualityCheck.code == code).with_for_update())
    if check is None:
        if not ctx.is_new or values["_retire"]:
            raise writeback_error("检查不存在，不能修改或退休", id=code)
        check = QualityCheck(code=code, created_by=ctx.cr.created_by)
        ctx.session.add(check)
    elif (
        ctx.is_new
        or check.created_by != ctx.cr.created_by
        or check.retired_at
        or values["_baseLockVersion"] != check.lock_version
    ):
        raise writeback_error("质量检查已变化或不属于变更发起人，请重新加载", id=code)
    check.name, check.object_type_id, check.kind = body.name, obj.id, body.kind
    check.threshold, check.limit_value, check.schedule = body.threshold, limit, body.schedule
    check.retired_at = ctx.now if values["_retire"] else None
    await ctx.session.flush()
    return {"qualityChecks"}


def register():
    register_writeback("quality", "def", publish_definition)
