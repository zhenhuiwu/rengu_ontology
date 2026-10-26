"""CR影响面只读取动态定义中真实保存的引用。"""

from sqlalchemy import select

from app.contracts.dynamic import definition_usages
from app.modules.action.models import ActionType
from app.modules.dynamic.rules_models import Rule
from tests.dynamic_support import dyn  # noqa: F401


async def test_dynamic_definition_references(dyn):  # noqa: F811
    assert await definition_usages(dyn, []) == []
    assert await definition_usages(dyn, [{"kind": "metric", "id": "missing"}]) == []
    metric_refs = await definition_usages(dyn, [{"kind": "metric", "id": "gross_margin"}])
    assert any(row["hover"] == {"kind": "rule", "id": "R-007"} for row in metric_refs)
    objects = await definition_usages(dyn, [{"kind": "object", "id": "product"}])
    expected = set(
        await dyn.scalars(select(Rule.code).where(Rule.obj_kind == "product", Rule.version.is_not(None)))
    )
    assert {row["hover"]["id"] for row in objects} == expected
    action = await dyn.scalar(select(ActionType).where(ActionType.code == "request_replenish"))
    references = await definition_usages(dyn, [{"kind": "action", "id": action.code}])
    expected = set(
        await dyn.scalars(
            select(Rule.code).where(Rule.action_type_id == action.id, Rule.version.is_not(None))
        )
    )
    assert {row["hover"]["id"] for row in references if row["kind"] == "规则"} == expected
    unpublished = await dyn.scalar(select(Rule).where(Rule.code == "R-007"))
    unpublished.version = None
    await dyn.flush()
    assert not any(
        row["hover"]["id"] == "R-007"
        for row in await definition_usages(dyn, [{"kind": "metric", "id": "gross_margin"}])
    )
