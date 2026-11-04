"""Register function CR publication, without adding an ontology snapshot section."""

from pydantic import ValidationError
from sqlalchemy import delete, select

from app.common.ontology import require_ontology_id
from app.core.errors import AppError
from app.modules.admin.models import SysUser
from app.modules.functions.executor import definition_signature
from app.modules.functions.models import FnParam, FnVersion, Function
from app.modules.functions.references import function_uses
from app.modules.functions.runtime import json_value
from app.modules.functions.schemas import CreateIn, ParamIn
from app.modules.functions.service import EXEC_FIELDS, FIELDS, RUNTIMES, execution_fields, next_version
from app.modules.static.models import CrItem
from app.modules.static.writeback import Ctx, register_writeback, writeback_error


async def publish_definition(ctx: Ctx, code: str, rows: list[CrItem]) -> set[str]:
    allowed = {*FIELDS, *EXEC_FIELDS, "_baseLockVersion", "_rollbackOf"}
    if any(row.item_key not in allowed or row.op == "delete" for row in rows):
        raise writeback_error("函数定义含未知字段或删除操作", id=code)
    values = {row.item_key: row.after for row in rows}
    if not all(key in values for key in FIELDS):
        raise writeback_error("函数定义不完整，请在函数页重新保存", id=code)
    try:
        body = CreateIn(
            name=values["name"],
            title=values["title"],
            desc=values["desc"],
            timeout_s=values["timeoutS"],
            code=values["code"],
            runtime_mode=values.get("runtimeMode", "local"),
            remote=values.get("remote"),
        )
        execution = execution_fields(body, require_ontology_id(ctx.session))
        # Re-infer the stored signature; CR callers cannot inject different parameter metadata.
        inferred_inputs, inferred_output = definition_signature({**values, **execution})
        if not isinstance(values["inputs"], list) or len(values["inputs"]) > 32:
            raise ValueError("入参最多 32 个")
        inputs = [ParamIn.model_validate(p).model_dump(exclude_none=True) for p in values["inputs"]]
        output = ParamIn.model_validate(values["output"]).model_dump(exclude_none=True)
        if [(p["name"], p["type"]) for p in inputs] != [(p["name"], p["type"]) for p in inferred_inputs]:
            raise ValueError("参数定义与代码签名不一致")
        if output["type"] != inferred_output["type"]:
            raise ValueError("出参类型与代码签名不一致")
        json_value(values["sampleIn"])
        json_value(values["sampleOut"])
    except (AppError, ValidationError, TypeError, ValueError, KeyError) as exc:
        raise writeback_error("函数定义校验失败", id=code, reason=str(exc)) from None
    if not isinstance(values["sampleIn"], dict):
        raise writeback_error("函数示例输入须为 JSON 对象", id=code)
    fn = await ctx.session.scalar(select(Function).where(Function.code == code).with_for_update())
    if fn is None and not ctx.is_new:
        raise writeback_error("函数不存在，不能发布编辑", id=code)
    if fn is not None and ctx.is_new:
        raise writeback_error("新建函数编号已存在", id=code)
    if fn and (values.get("_baseLockVersion") != fn.lock_version or body.name != fn.name):
        raise writeback_error("函数已变化或试图修改函数名，请重新确认草稿", id=code)
    same_name = await ctx.session.scalar(
        select(Function.id).where(Function.name == body.name, Function.code != code)
    )
    if same_name:
        raise writeback_error("函数名已存在", id=code)
    owner = await ctx.session.scalar(select(SysUser.id).where(SysUser.code == ctx.by))
    if owner is None:
        raise writeback_error("发布人不存在", id=code)
    if fn is None:
        definition_owner = await ctx.session.scalar(
            select(SysUser.id).where(SysUser.code == ctx.cr.created_by)
        )
        if definition_owner is None:
            raise writeback_error("新建函数的变更发起人不存在，无法确认负责人", id=code)
        fn = Function(
            code=code,
            name=body.name,
            runtime=RUNTIMES[body.runtime_mode],
            status="published",
            owner_id=definition_owner,
            title=body.title,
            timeout_s=body.timeout_s,
            description=body.desc,
            code_text=body.code,
            sample_in=values["sampleIn"],
            sample_out=values["sampleOut"],
            created_by=ctx.by,
        )
        ctx.session.add(fn)
        await ctx.session.flush()
    else:
        used = (await function_uses(ctx.session, [fn.code]))[fn.code]
        old = list(
            await ctx.session.scalars(
                select(FnParam).where(FnParam.function_id == fn.id).order_by(FnParam.direction, FnParam.seq)
            )
        )
        old_signature = {(p.direction, p.seq): (p.name, p.type) for p in old}
        new_signature = {("in", i): (p["name"], p["type"]) for i, p in enumerate(inputs)}
        new_signature[("out", 0)] = (output["name"], output["type"])
        if used and old_signature != new_signature:
            raise writeback_error("函数被动作或规则引用，不能发布不兼容的签名变更", id=code)
        if used and body.runtime_mode == "remote_http":
            latest = await ctx.session.scalar(
                select(FnVersion).where(FnVersion.function_id == fn.id)
                .order_by(FnVersion.id.desc()).limit(1)
            )
            old_remote = latest.snapshot.get("remote") if latest else None
            if old_remote and any(old_remote.get(key) != execution["remote"][key]
                                  for key in ("inputSchema", "outputSchema")):
                raise writeback_error("函数被动作或规则引用，不能发布不兼容的远程合同", id=code)
        fn.title, fn.timeout_s, fn.description, fn.code_text = (
            body.title,
            body.timeout_s,
            body.desc,
            body.code,
        )
        fn.sample_in, fn.sample_out, fn.status = values["sampleIn"], values["sampleOut"], "published"
        fn.runtime = RUNTIMES[body.runtime_mode]
    versions = list(await ctx.session.scalars(select(FnVersion).where(FnVersion.function_id == fn.id)))
    await ctx.session.execute(delete(FnParam).where(FnParam.function_id == fn.id))
    for direction, params in (("in", inputs), ("out", [output])):
        for seq, param in enumerate(params):
            ctx.session.add(
                FnParam(function_id=fn.id, direction=direction, seq=seq, **param, created_by=ctx.by)
            )
    snapshot = {key: values[key] for key in FIELDS}
    snapshot.update(execution)
    snapshot["inputs"], snapshot["output"] = inputs, output
    ctx.session.add(
        FnVersion(
            function_id=fn.id,
            ver=next_version(versions),
            at=ctx.now,
            author_id=owner,
            note=f"回滚到 {values['_rollbackOf']}" if values.get("_rollbackOf") else ctx.cr.title,
            snapshot=snapshot,
            created_by=ctx.by,
        )
    )
    await ctx.session.flush()
    return set()


def register() -> None:
    register_writeback("function", "def", publish_definition)
