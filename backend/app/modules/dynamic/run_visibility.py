"""Protect runtime parameter text when its referenced objects cannot be verified."""

from sqlalchemy import select

from app.common.auth import row_filter
from app.common.row_filter import is_deny_all
from app.core.errors import AppError
from app.modules.action.checker import param_valid, structured_params
from app.modules.action.condition_expr import Unavailable
from app.modules.action.execution_options import action_operation_objects
from app.modules.action.models import ActionType
from app.modules.action.sections import action_def
from app.modules.integration.action_edits import existing_row, object_binding, relation_binding


async def params_unavailable(session, user, run):
    try:
        params = structured_params(run.params if run.params is not None else run.params_text)
        action = await session.scalar(select(ActionType).where(ActionType.code == run.action_type_code))
        if action is None or action.version != run.type_version or action.retired_release_id is not None:
            return "参数不可用：绑定版本的对象引用无法核验"
        definition = action_def(action)
        declared = {p["api"] for p in definition["params"]}
        if set(params) - declared:
            return "参数不可用：存在未声明的字段"
        for edit in definition["writeback"]["edits"]:
            if edit.get("op") == "关联":
                await relation_binding(session, edit.get("obj"))
            else:
                await object_binding(session, edit.get("obj"))
        for obj in await action_operation_objects(session, definition):
            if is_deny_all(await row_filter(user, obj.code)):
                return "参数不可用：涉及无权查看的对象"
        for param in definition["params"]:
            if not param["type"].startswith(("对象引用 · ", "对象引用列表 · ")) or param["api"] not in params:
                continue
            value = params[param["api"]]
            if value in (None, "", []):
                continue
            if not param_valid(param["type"], value):
                return "参数不可用：对象引用格式无效"
            identities = value if isinstance(value, list) else [value]
            if any(len(code) > 64 or "\x00" in code for code in identities):
                return "参数不可用：对象引用格式无效"
            kind = param["type"].split(" · ", 1)[1]
            if kind == "告警":
                from app.modules.dynamic.models import Alert
                from app.modules.dynamic.row_scope import scoped_instances

                for code in identities:
                    stmt = await scoped_instances(
                        select(Alert.id).where(Alert.code == code), user, Alert.obj_kind, Alert.obj_id
                    )
                    if not await session.scalar(stmt):
                        return "参数不可用：引用告警不可见"
            else:
                obj, governed = await object_binding(session, kind)
                for code in identities:
                    await existing_row(session, user, obj, governed, code, lock=False)
        return None
    except (AppError, Unavailable):
        return "参数不可用：无法确认全部引用对象的可见性"
