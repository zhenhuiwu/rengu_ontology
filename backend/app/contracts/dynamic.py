"""持久动态定义对静态目标的直接引用；不从命中展示数推算影响。"""

from sqlalchemy import or_, select

from app.db.dialects import get_adapter
from app.modules.action.models import ActionType
from app.modules.dynamic.rules_models import Lifecycle, LifecycleTransition, LifecycleVersion, Rule
from app.modules.event.models import EventType


async def definition_usages(session, targets: list[dict[str, str]]) -> list[dict]:
    by_kind = {}
    for target in targets:
        by_kind.setdefault(target["kind"], set()).add(target["id"])
    conditions = []
    if objects := by_kind.get("object"):
        conditions.append(Rule.obj_kind.in_(objects))
    if metrics := by_kind.get("metric"):
        conds = get_adapter(session).json_array_rows(Rule.conds)
        conditions.append(
            select(1).select_from(conds).where(conds.c.value["metric"].as_string().in_(metrics)).exists()
        )
    if actions := by_kind.get("action"):
        conditions.append(Rule.action_type_id.in_(select(ActionType.id).where(ActionType.code.in_(actions))))
    if events := by_kind.get("event"):
        conditions.append(Rule.trigger["eventId"].as_string().in_(events))
    out = []
    if conditions:
        rules = await session.execute(
            select(Rule.code, Rule.name)
            .where(Rule.version.is_not(None), or_(*conditions))
            .order_by(Rule.code)
        )
        out.extend(
            {
                "kind": "规则",
                "name": name,
                "note": "已保存的规则定义直接引用该变更目标",
                "hover": {"kind": "rule", "id": code},
            }
            for code, name in rules
        )
    transitions = []
    if actions:
        transitions.append(
            LifecycleTransition.action_type_id.in_(select(ActionType.id).where(ActionType.code.in_(actions)))
        )
    if events:
        transitions.append(
            LifecycleTransition.event_type_id.in_(select(EventType.id).where(EventType.code.in_(events)))
        )
    if transitions:
        rows = await session.execute(
            select(Lifecycle.code, Lifecycle.object_name)
            .join(LifecycleVersion, LifecycleVersion.lifecycle_id == Lifecycle.id)
            .join(LifecycleTransition, LifecycleTransition.version_id == LifecycleVersion.id)
            .where(LifecycleVersion.version == Lifecycle.version, or_(*transitions))
            .distinct()
            .order_by(Lifecycle.code)
        )
        out.extend(
            {
                "kind": "生命周期",
                "name": name,
                "note": "当前迁移定义引用该动作或事件",
                "hover": {"kind": "object", "id": code},
            }
            for code, name in rows
        )
    return out
