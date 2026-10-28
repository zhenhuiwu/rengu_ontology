"""Current definitions and CR drafts; only publishing mutates function definitions."""

import re
from typing import Any

from pydantic import ValidationError
from sqlalchemy import ARRAY, Numeric, String, case, cast, func, literal, select, union_all
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.auth import CurrentUser
from app.common.pagination import PageParams
from app.core.errors import AppError, NotFound, VersionConflict
from app.core.schema import _fmt_dt
from app.db.dialects import get_adapter
from app.modules.admin.models import SysUser
from app.modules.functions.models import FnParam, FnUse, FnVersion, Function
from app.modules.functions.runtime import signature
from app.modules.functions.schemas import CreateIn, RollbackIn, SaveIn
from app.modules.static.cr_write import CrWriteService
from app.modules.static.repo import pending_definition_drafts, reserved_target_codes
from app.modules.static.schemas import CRCreateIn, ItemIn, ItemsPutIn, TargetIn

FIELDS = ("name", "title", "desc", "timeoutS", "code", "inputs", "output", "sampleIn", "sampleOut")


def ver_key(ver: str) -> tuple[int, int, int]:
    match = re.fullmatch(r"v(\d+)\.(\d+)\.(\d+)", ver)
    return tuple(map(int, match.groups())) if match else (0, 0, 0)


def next_version(versions: list[FnVersion]) -> str:
    if not versions:
        return "v1.0.0"
    a, b, c = max(ver_key(row.ver) for row in versions)
    return f"v{a}.{b}.{c + 1}"


async def drafts(session: AsyncSession, codes: list[str] | None = None) -> dict[str, dict[str, Any]]:
    return await pending_definition_drafts(session, "function", "def", codes=codes)


def version_order() -> list:
    """Keep numeric semantic-version ordering in SQL, with an ID tie-breaker."""
    return [
        case(
            (
                FnVersion.ver.op("~")(r"^v[0-9]+\.[0-9]+\.[0-9]+$"),
                cast(func.split_part(func.substr(FnVersion.ver, 2), ".", part), Numeric),
            ),
            else_=0,
        ).desc()
        for part in (1, 2, 3)
    ] + [FnVersion.id.desc()]


def current_select():
    version = (
        select(FnVersion.ver)
        .where(FnVersion.function_id == Function.id)
        .order_by(*version_order())
        .limit(1)
        .scalar_subquery()
    )
    return select(Function, SysUser.name, version).join(SysUser, SysUser.id == Function.owner_id)


def draft_row(code: str, draft: dict) -> dict | None:
    if not draft["isNew"] or not all(k in draft["definition"] for k in FIELDS):
        return None
    try:
        parsed = CreateIn.model_validate(draft["definition"])
        inputs, output = signature(parsed.code, parsed.name)
    except (AppError, ValidationError):
        return None  # Invalid generic CR input stays visible on its CR, never as executable metadata.
    return {
        **{k: v for k, v in draft["definition"].items() if k in FIELDS},
        "name": parsed.name,
        "title": parsed.title,
        "desc": parsed.desc,
        "code": parsed.code,
        "timeoutS": parsed.timeout_s,
        "inputs": inputs,
        "output": output,
        "id": code,
        "runtime": "受限纯计算表达式",
        "version": "草稿",
        "status": "草稿",
        "owner": draft["author"],
        "updatedAt": draft["at"],
        "avgMs": 0,
        "calls7d": 0,
        "usedBy": [],
        "lockVersion": 1,
        "pending": draft,
    }


async def current_rows(session: AsyncSession, records, pending: dict) -> list[dict]:
    ids = [fn.id for fn, _, _ in records]
    if not ids:
        return []
    params: dict[int, list[FnParam]] = {}
    for param in await session.scalars(
        select(FnParam).where(FnParam.function_id.in_(ids)).order_by(FnParam.direction, FnParam.seq)
    ):
        params.setdefault(param.function_id, []).append(param)
    uses: dict[int, list[dict]] = {}
    for use in await session.scalars(select(FnUse).where(FnUse.function_id.in_(ids)).order_by(FnUse.id)):
        uses.setdefault(use.function_id, []).append({"kind": use.kind, "name": use.name})
    result = []
    for fn, owner, version in records:

        def param(p: FnParam) -> dict:
            return {
                "name": p.name,
                "type": p.type,
                "desc": p.desc,
                **({"objType": p.obj_type} if p.obj_type else {}),
            }

        ps = params.get(fn.id, [])
        row = {
            "id": fn.code,
            "name": fn.name,
            "title": fn.title,
            "runtime": fn.runtime,
            "version": version or "",
            "status": {"published": "已发布", "draft": "草稿", "disabled": "已停用"}.get(
                fn.status, fn.status
            ),
            "owner": owner,
            "updatedAt": _fmt_dt(fn.updated_at),
            "timeoutS": fn.timeout_s,
            "desc": fn.description,
            "code": fn.code_text,
            "sampleIn": fn.sample_in,
            "sampleOut": fn.sample_out,
            "avgMs": fn.avg_ms,
            "calls7d": fn.calls_7d,
            "lockVersion": fn.lock_version,
            "usedBy": uses.get(fn.id, []),
            "inputs": [param(p) for p in ps if p.direction == "in"],
            "output": next(
                (param(p) for p in ps if p.direction == "out"), {"name": "result", "type": "Any", "desc": ""}
            ),
        }
        if fn.code in pending:
            row["pending"] = pending[fn.code]
        result.append(row)
    return result


async def list_page(session: AsyncSession, kw: str, status: str, page: PageParams) -> dict:
    where = []
    kw = kw.strip().lower()
    if kw:
        where.append(func.lower(Function.name + literal(" ") + Function.title).contains(kw, autoescape=True))
    if status not in ("", "全部"):
        where.append(
            Function.status
            == {"已发布": "published", "草稿": "draft", "已停用": "disabled"}.get(status, status)
        )
    pending_new = await pending_definition_drafts(session, "function", "def", new_only=True)
    new_rows = {}
    if pending_new:
        existing = set(await session.scalars(select(Function.code).where(Function.code.in_(pending_new))))
        # New definitions still need AST validation before counting; current definitions never get rescanned.
        for code, draft in pending_new.items():
            if (
                code not in existing
                and (row := draft_row(code, draft)) is not None
                and status in ("", "全部", "草稿")
                and kw in f"{row['name']} {row['title']}".lower()
            ):
                new_rows[code] = row
    total = int(await session.scalar(select(func.count()).select_from(Function).where(*where)) or 0) + len(
        new_rows
    )
    p = page.clamp(total)
    codes = select(Function.code.label("code")).where(*where)
    if new_rows:
        codes = union_all(codes, select(func.unnest(cast(list(new_rows), ARRAY(String(64)))).label("code")))
    candidates = codes.subquery()
    selected = list(
        await session.scalars(
            select(candidates.c.code).order_by(candidates.c.code).offset(p.offset).limit(p.size)
        )
    )
    pending = await drafts(session, selected)
    records = (
        (
            await session.execute(current_select().where(Function.code.in_(selected)).order_by(Function.code))
        ).all()
        if selected
        else []
    )
    current = {row["id"]: row for row in await current_rows(session, records, pending)}
    return {
        "items": [current[code] if code in current else new_rows[code] for code in selected],
        "total": total,
        "page": p.page,
        "size": p.size,
    }


async def detail(session: AsyncSession, code: str) -> dict:
    pending = await drafts(session, [code])
    records = (await session.execute(current_select().where(Function.code == code))).all()
    found = (await current_rows(session, records, pending))[0] if records else None
    if found is None and code in pending:
        found = draft_row(code, pending[code])
    if found is None:
        raise NotFound("函数不存在", details={"id": code})
    return found


async def history(session: AsyncSession, code: str, page: PageParams) -> dict:
    fn_id = await session.scalar(select(Function.id).where(Function.code == code))
    pending = (await drafts(session, [code])).get(code)
    if fn_id is None and (pending is None or draft_row(code, pending) is None):
        raise NotFound("函数不存在", details={"id": code})
    published_total = (
        int(
            await session.scalar(
                select(func.count()).select_from(FnVersion).where(FnVersion.function_id == fn_id)
            )
            or 0
        )
        if fn_id
        else 0
    )
    total = published_total + int(pending is not None)
    p = page.clamp(total)
    result = []
    if pending is not None and p.offset == 0:
        result.append(
            {
                "ver": "草稿",
                "time": pending["at"],
                "author": pending["author"],
                "note": pending["note"],
                "status": "变更中",
                "cr": pending["cr"],
            }
        )
    offset = max(0, p.offset - int(pending is not None))
    if fn_id and (limit := p.size - len(result)):
        records = (
            await session.execute(
                select(FnVersion.ver, FnVersion.at, FnVersion.note, SysUser.name)
                .join(SysUser, SysUser.id == FnVersion.author_id)
                .where(FnVersion.function_id == fn_id)
                .order_by(*version_order())
                .offset(offset)
                .limit(limit)
            )
        ).all()
        for i, (ver, at, note, author) in enumerate(records):
            result.append(
                {
                    "ver": ver,
                    "time": _fmt_dt(at),
                    "author": author,
                    "note": note,
                    "status": "当前" if offset + i == 0 else "历史",
                }
            )
    return {"items": result, "total": total, "page": p.page, "size": p.size}


async def save(
    session: AsyncSession,
    user: CurrentUser,
    body: SaveIn,
    code: str | None = None,
    *,
    rollback: str | None = None,
    snapshot: dict | None = None,
) -> dict:
    # One short definition transaction covers code allocation AND duplicate draft names.
    if body.cr_id:
        raise AppError(
            "已有草稿不能通过直接保存覆盖", code="TARGET_IN_OTHER_CR", status_code=409,
            details={"crId": body.cr_id},
        )
    await get_adapter(session).xact_lock(session, "functions.definition")
    current = await detail(session, code) if code is not None else None
    if current is not None and body.lock_version != current["lockVersion"]:
        raise VersionConflict(details={"id": code, "lockVersion": current["lockVersion"]})
    name = body.name if isinstance(body, CreateIn) else current["name"]
    if code is None:
        pending = await drafts(session)
        if await session.scalar(select(Function.id).where(Function.name == name)) is not None or any(
            draft.get("definition", {}).get("name") == name
            and (not draft["isNew"] or draft_row(target, draft) is not None)
            for target, draft in pending.items()
        ):
            raise AppError("函数名已存在或已登记到变更单", code="DUPLICATE_KEY", status_code=409)
        codes = list(await session.scalars(select(Function.code)))
        codes.extend(await reserved_target_codes(session, "function"))
        n = max((int(c[3:]) for c in codes if c.startswith("FN-") and c[3:].isdigit()), default=0)
        code = f"FN-{n + 1:03d}"
    inputs, output = signature(body.code, name)
    base = {k: current[k] for k in FIELDS} if current else {}
    # Signature is inferred from the actual code; keep descriptions for unchanged parameters.
    old_inputs = {p["name"]: p for p in base.get("inputs", [])}
    inputs = [
        {**old_inputs.get(p["name"], {}), **p, "desc": old_inputs.get(p["name"], {}).get("desc", "")}
        for p in inputs
    ]
    value = snapshot or {
        **base,
        "name": name,
        "title": body.title,
        "desc": body.desc,
        "timeoutS": body.timeout_s,
        "code": body.code,
        "inputs": inputs,
        "output": {**output, **base.get("output", {}), "type": output["type"]},
        "sampleIn": base.get("sampleIn", {}),
        "sampleOut": base.get("sampleOut"),
    }
    is_new = current is None or bool(current.get("pending", {}).get("isNew"))
    target = TargetIn(kind="function", id=code, is_new=is_new, name=value["title"])
    writer = CrWriteService(session)
    pending = current.get("pending") if current else None
    if pending:
        raise AppError(
            "函数已在变更单中，请重新加载草稿",
            code="TARGET_IN_OTHER_CR",
            status_code=409,
            details={"crId": pending["cr"]},
        )
    cr_id = await writer.create(
        user,
        CRCreateIn(title=body.change_title or f"函数：{value['title']}"[:40], reason=body.reason, target=target),
    )
    # Explicit baseline protects against current-definition changes before publication.
    baseline = current["lockVersion"] if current and not is_new else None
    items = [
        ItemIn(
            key=k, op="add" if is_new else "update", before=base.get(k), after=value[k], type="函数", field=k
        )
        for k in FIELDS
    ]
    items.append(ItemIn(key="_baseLockVersion", op="add", after=baseline, type="函数", field="基线版本"))
    if rollback:
        items.append(ItemIn(key="_rollbackOf", op="add", after=rollback, type="函数", field="回滚来源"))
    saved = await writer.put_items(
        cr_id,
        user,
        ItemsPutIn(
            target=target,
            section="def",
            rows=items,
        ),
    )
    from app.modules.static.publish import PublishService

    ver = await PublishService(session).publish(user, None, draft_code=cr_id, save_mode=True)
    return {"id": code, "crId": cr_id, "crLockVersion": saved["lockVersion"], "status": "已发布", "ver": ver}


async def rollback(session: AsyncSession, user: CurrentUser, code: str, body: RollbackIn) -> dict:
    current = await detail(session, code)
    if body.to_version == current["version"]:
        raise AppError("当前版本无需回滚", code="STATE_INVALID", status_code=409)
    fn = await session.scalar(select(Function).where(Function.code == code))
    version = (
        await session.scalar(
            select(FnVersion).where(FnVersion.function_id == fn.id, FnVersion.ver == body.to_version)
        )
        if fn
        else None
    )
    if version is None:
        raise NotFound("函数版本不存在")
    snapshot = version.snapshot
    parsed = SaveIn(
        **{k: snapshot[k] for k in ("title", "desc", "timeoutS", "code")},
        lock_version=body.lock_version,
        cr_id=body.cr_id,
        cr_lock_version=body.cr_lock_version,
        change_title=body.change_title or f"回滚函数：{current['title']}"[:40],
        reason=body.reason,
    )
    return await save(session, user, parsed, code, rollback=body.to_version, snapshot=snapshot)
