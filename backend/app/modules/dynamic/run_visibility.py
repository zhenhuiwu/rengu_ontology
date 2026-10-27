"""Page-batched structured action reference visibility; never expose unverifiable text."""

from sqlalchemy import func, select

from app.common.auth import row_filter
from app.common.row_filter import is_deny_all
from app.core.errors import AppError
from app.modules.action.checker import param_valid, structured_params
from app.modules.action.condition_expr import Unavailable
from app.modules.action.execution_options import action_operation_objects
from app.modules.action.models import ActionType
from app.modules.action.sections import action_def
from app.modules.dynamic.row_scope import scoped_instances, visible_references
from app.modules.integration.action_edits import (
    declared_text,
    external_webhook,
    object_binding,
    relation_binding,
)

HIDDEN = "参数不可用：无法确认全部引用对象的可见性"


async def parameters_visibility(session, user, runs):
    reasons, values = {}, {}
    for run in runs:
        try:
            values[run.id] = structured_params(run.params if run.params is not None else run.params_text)
        except Unavailable:
            reasons[run.id] = HIDDEN
    codes = {run.action_type_code for run in runs if run.id not in reasons}
    actions = (
        {row.code: row for row in await session.scalars(select(ActionType).where(ActionType.code.in_(codes)))}
        if codes
        else {}
    )
    bindings, operations, references = {}, {}, {}

    async def binding(identity):
        if identity not in bindings:
            try:
                bindings[identity] = await object_binding(session, identity)
            except AppError as error:
                bindings[identity] = error
        value = bindings[identity]
        if isinstance(value, AppError):
            raise value
        return value

    for run in runs:
        if run.id in reasons:
            continue
        try:
            action = actions.get(run.action_type_code)
            if action is None or action.version != run.type_version or action.retired_release_id is not None:
                reasons[run.id] = "参数不可用：绑定版本的对象引用无法核验"
                continue
            definition, params = action_def(action), values[run.id]
            if set(params) - {param["api"] for param in definition["params"]}:
                reasons[run.id] = "参数不可用：存在未声明的字段"
                continue
            operation_key = (
                definition.get("targetKind"),
                definition.get("sysId"),
                tuple((edit.get("op"), edit.get("obj")) for edit in definition["writeback"]["edits"]),
                tuple(
                    (effect.get("kind"), effect.get("target"))
                    for effect in definition["writeback"]["effects"]
                ),
            )
            if operation_key not in operations:
                declared = await external_webhook(session, definition)
                for edit in definition["writeback"]["edits"]:
                    if declared and declared_text(edit.get("field")):
                        continue  # 外部 Webhook 声明效果：不绑定本地对象
                    if edit.get("op") == "关联":
                        await relation_binding(session, edit.get("obj"))
                    else:
                        await binding(edit.get("obj"))
                objects = await action_operation_objects(session, definition)
                denied = False
                for obj in objects:
                    denied = denied or is_deny_all(await row_filter(user, obj.code))
                operations[operation_key] = denied
            if operations[operation_key]:
                reasons[run.id] = "参数不可用：涉及无权查看的对象"
                continue
            referenced = set()
            for param in definition["params"]:
                if (
                    not param["type"].startswith(("对象引用 · ", "对象引用列表 · "))
                    or param["api"] not in params
                ):
                    continue
                value = params[param["api"]]
                if value in (None, "", []):
                    continue
                if not param_valid(param["type"], value):
                    raise Unavailable("对象引用格式无效")
                identities = value if isinstance(value, list) else [value]
                if any(len(code) > 64 or chr(0) in code for code in identities):
                    raise Unavailable("对象引用格式无效")
                kind = param["type"].split(" · ", 1)[1]
                if kind != "告警":
                    obj, _ = await binding(kind)
                    kind = obj.code
                referenced.update((kind, identity) for identity in identities)
            references[run.id] = referenced
        except (AppError, Unavailable):
            reasons[run.id] = HIDDEN
    all_refs = set().union(*references.values()) if references else set()
    visible = await visible_references(session, user, {ref for ref in all_refs if ref[0] != "告警"})
    alert_codes = {code for kind, code in all_refs if kind == "告警"}
    if alert_codes:
        from app.modules.dynamic.models import Alert

        unique = select(Alert.code).group_by(Alert.code).having(func.count() == 1)
        statement = await scoped_instances(
            select(Alert.code).where(Alert.code.in_(alert_codes), Alert.code.in_(unique)),
            user,
            Alert.obj_kind,
            Alert.obj_id,
        )
        visible.update(("告警", code) for code in await session.scalars(statement))
    for identity, refs in references.items():
        if not refs <= visible:
            reasons[identity] = HIDDEN
    return reasons


async def params_unavailable(session, user, run):
    return (await parameters_visibility(session, user, [run])).get(run.id)
