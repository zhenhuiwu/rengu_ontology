"""The existing action-completion envelope is a platform context, never a business SQL binding."""

from sqlalchemy import select

from app.contracts.actions import EVENT_ACTION_EXECUTED
from app.modules.event.models import EventPayloadField, EventType
from app.modules.static.models import CrItem
from app.modules.static.writeback import writeback_error

ACTION_RUN = "platform:ActionRun"
ACTION_FIELDS = frozenset({"runId", "actionType", "targetId", "initiator", "confirmer", "writeback"})


async def authorize_completion_view(session, user, event):
    """Read internal completion delivery metadata under the original action-record row scope."""
    from app.common.auth import check_func
    from app.common.ontology import require_ontology_id
    from app.core.errors import Forbidden, NotFound
    from app.modules.dynamic.models import ActionRun
    from app.modules.dynamic.row_scope import scoped_instances

    definition = event.definition_snapshot or {}
    if (definition.get("api") != EVENT_ACTION_EXECUTED or definition.get("source") != "动作"
            or definition.get("objectApi") != "ActionRun" or definition.get("objKind")
            or event.obj_kind or event.source != "dynamic" or event.producer_object_type != "ActionRun"
            or event.producer_type not in (definition.get("id"), EVENT_ACTION_EXECUTED)
            or (event.payload or {}).get("runId") != event.object_id):
        return False
    check_func(user, "action-center", "view")
    if user.api_scopes is not None and "dynamic:read" not in user.api_scopes:
        raise Forbidden("平台动作事件需要动作记录读取权限")
    ontology_id = require_ontology_id(session)
    if event.ontology_id != ontology_id or user.ontology_id != ontology_id:
        raise NotFound("事件不存在或无权查看")
    query = await scoped_instances(select(ActionRun.id).where(
        ActionRun.ontology_id == ontology_id, ActionRun.code == event.object_id),
        user, ActionRun.obj_kind, ActionRun.obj_id)
    if await session.scalar(query.limit(1)) is None:
        raise NotFound("事件不存在或无权查看")
    return True


def platform_binding(values):
    identity = values.get("objKind")
    if isinstance(identity, str) and identity.startswith("platform:") and identity != ACTION_RUN:
        raise writeback_error("未知平台事件上下文，不允许引用内部表", field="objKind")
    if identity != ACTION_RUN:
        if values.get("api") == EVENT_ACTION_EXECUTED:
            raise writeback_error("标准动作完成事件须关联动作执行记录（平台）", field="objKind")
        return False
    if values.get("api") != EVENT_ACTION_EXECUTED or values.get("source") != "动作":
        raise writeback_error("动作执行记录仅供来源为动作的 ActionExecuted 标准事件使用", field="objKind")
    return True


def action_payload(fields):
    for key, field in fields.items():
        if (key not in ACTION_FIELDS or not isinstance(field, dict) or field.get("api", key) != key
                or field.get("type") != "字符串" or type(field.get("required")) is not bool):
            raise writeback_error("标准动作完成载荷仅允许执行器提供的六个字符串字段", field="payload")
    if "runId" not in fields or fields["runId"].get("required") is not True:
        raise writeback_error("标准动作完成事件须有必填字符串 runId", field="payload")


async def validate_draft(session, code, cr_id, *, section=None, rows=(), complete=False):
    """Project persisted CR metadata only; do not create definitions or read ActionRun rows."""
    current = await session.scalar(select(EventType).where(EventType.code == code))
    values = {"api": current.api if current else code,
              "source": current.source.value if current else None,
              "objKind": (ACTION_RUN if current and current.api == EVENT_ACTION_EXECUTED
                          and current.object_api == "ActionRun" and not current.obj_kind
                          else current.obj_kind if current else None)}
    saved = list(await session.scalars(select(CrItem).where(
        CrItem.cr_id == cr_id, CrItem.target_kind == "event", CrItem.target_id == code,
        CrItem.section.in_(("def", "payload"))).order_by(CrItem.seq, CrItem.id)))
    changes = [(item.section, item.item_key, item.op, item.after) for item in saved
               if item.section != section]
    changes.extend((section, row.key, row.op, row.after) for row in rows)
    for sec, key, operation, after in changes:
        if sec == "def":
            if operation == "delete":
                values.pop(key, None)
            else:
                values[key] = after
    if not platform_binding(values):
        return False
    if complete or section == "payload":
        fields = {}
        if current:
            for field in await session.scalars(select(EventPayloadField).where(EventPayloadField.event_type_id == current.id)):
                fields[field.api] = {"api": field.api, "type": field.type, "required": field.required}
        for sec, key, operation, after in changes:
            if sec == "payload":
                if operation == "delete":
                    fields.pop(key, None)
                else:
                    fields[key] = after
        action_payload(fields)
    return True
