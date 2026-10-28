"""Platform action events provisioned with each new ontology workspace."""

from sqlalchemy import insert, text

from app.modules.action.models import TypeStatus
from app.modules.event.models import EventPayloadField, EventSource, EventType

STANDARD_EVENTS = (
    (
        "ActionExecuted",
        "动作执行完成",
        (
            ("runId", "执行记录", "字符串"),
            ("actionType", "动作类型", "字符串"),
            ("targetId", "目标对象", "字符串"),
            ("initiator", "发起人", "字符串"),
            ("confirmer", "确认人", "字符串"),
            ("writeback", "写回结果", "字符串"),
        ),
    ),
    (
        "ActionOutcomeUnknown",
        "动作结果未知",
        (
            ("runId", "执行记录", "字符串"),
            ("actionType", "动作类型", "字符串"),
            ("system", "外部系统", "字符串"),
            ("idemKey", "幂等键", "字符串"),
            ("waitedMs", "等待时长", "整数"),
        ),
    ),
)


async def initialize_workspace_events(session, ontology_id: int, creator: str) -> None:
    original = session.info.get("ontology_id")
    if type(ontology_id) is not int or ontology_id <= 0 or ontology_id == original:
        raise ValueError("Expected a newly created workspace")
    # Only these inserts use the new RLS scope; session scope and identity map never change.
    scope = text("SELECT set_config('rengu.ontology_id', :id, true)")
    await session.execute(scope, {"id": str(ontology_id)})
    for code, name, fields in STANDARD_EVENTS:
        event_id = await session.scalar(
            insert(EventType)
            .values(
                ontology_id=ontology_id,
                code=code,
                api=code,
                name=name,
                source=EventSource.action,
                object="动作执行",
                object_api="ActionRun",
                status=TypeStatus.published,
                version="v1",
                owner=creator,
                dedup_key_rule="执行记录 ID",
                dedup_window="24 小时",
                retention_days=365,
                created_by=creator,
            )
            .returning(EventType.id)
        )
        await session.execute(
            insert(EventPayloadField),
            [
                {
                    "ontology_id": ontology_id,
                    "event_type_id": event_id,
                    "seq": seq,
                    "api": api,
                    "name": label,
                    "type": kind,
                    "required": True,
                    "created_by": creator,
                }
                for seq, (api, label, kind) in enumerate(fields, 1)
            ],
        )
    await session.execute(scope, {"id": str(original) if original is not None else ""})
