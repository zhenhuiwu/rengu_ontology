"""对象行为页的表格页与摘要不再读取全量命中/动作参数。"""

from datetime import timedelta

from sqlalchemy import event, func, select

from app.core.clock import now
from app.modules.dynamic.models import ActionRun, ActionStatus
from app.modules.dynamic.rules_models import Rule, RuleHit
from tests.dynamic_support import API, dyn  # noqa: F401


async def test_object_behavior_pages_and_aggregates(dyn, client, engine):  # noqa: F811
    statements = []

    def captured(_conn, _cursor, sql, _parameters, _context, _many):
        if sql.lstrip().upper().startswith("SELECT"):
            statements.append(sql)

    event.listen(engine.sync_engine, "before_cursor_execute", captured)
    try:
        response = await client.get(API + "/object-types/product/behavior/stats")
        assert response.status_code == 200, response.text
        stats = response.json()
        for child in ("action_param", "submission_criterion", "ontology_edit_rule", "side_effect"):
            assert not any(f"FROM {child} " in sql or f"FROM {child}\n" in sql for sql in statements)
        assert all("params" not in action and "conds" not in action for action in stats["actions"])
        counts = dict(
            (
                await dyn.execute(
                    select(ActionRun.status, func.count())
                    .where(ActionRun.obj_kind == "product", ActionRun.in_center.is_(True))
                    .group_by(ActionRun.status)
                )
            ).all()
        )
        assert stats["centerStatusCounts"] == {status.value: count for status, count in counts.items()}
        rules = list(await dyn.scalars(select(Rule).where(Rule.obj_kind == "product")))
        for rule in rules:
            hit_count = await dyn.scalar(
                select(func.count())
                .select_from(RuleHit)
                .where(
                    RuleHit.rule_id == rule.id, RuleHit.at >= now() - timedelta(days=7), RuleHit.at <= now()
                )
            )
            pending = await dyn.scalar(
                select(func.count())
                .select_from(ActionRun)
                .where(ActionRun.rule_code == rule.code, ActionRun.status == ActionStatus.staged)
            )
            assert stats["rules"][rule.code]["hits7d"] == hit_count
            assert stats["rules"][rule.code]["pending"] == pending
        statements.clear()
        first = await client.get(API + "/object-types/product/automation/hits", params={"size": 2})
        second = await client.get(
            API + "/object-types/product/automation/hits", params={"size": 2, "page": 2}
        )
        assert first.status_code == second.status_code == 200, second.text
        a, b = first.json(), second.json()
        assert a["total"] == b["total"] > 2 and b["page"] == 2
        assert not ({row["id"] for row in a["items"]} & {row["id"] for row in b["items"]})
        assert any("FROM rule_hit " in sql and "LIMIT" in sql and "OFFSET" in sql for sql in statements)
        filtered = (
            await client.get(
                API + "/object-types/product/automation/hits",
                params={"ruleId": "R-007", "page": 999, "size": 2},
            )
        ).json()
        assert filtered["page"] == max(1, (filtered["total"] + 1) // 2)
        assert all(row["ruleId"] == "R-007" for row in filtered["items"])
        directory = (await client.get(API + "/rules", params={"objKind": "product", "size": 200})).json()
        assert directory["items"] and all(row["objKind"] == "product" for row in directory["items"])
        assert (await client.get(API + "/object-types/missing/behavior/stats")).status_code == 404
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", captured)
