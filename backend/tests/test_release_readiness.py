"""Focused structural and actual CR mapping checks, without source introspection."""

from copy import deepcopy

from sqlalchemy import select

from app.common.sql_guard import load_governed_tables
from app.modules.biz import governance  # noqa: F401
from app.modules.static.models import Metric
from app.modules.static.release_readiness import object_structure
from tests import test_static_trials as support

facts = support.facts
real_sections = support.real_sections


def test_object_structure_checks_only_saved_metadata_without_mutation():
    fields = {"pk": "id", "source": "DS-TEST", "table": "public.source"}
    props = {"id": {"type": "string", "source": "source_id"}}
    before = deepcopy((fields, props))
    result, note = object_structure(fields, props)
    assert result == "通过" and "未验证源数据唯一性" in note and "连通性" in note
    assert object_structure({**fields, "pk": "missing"}, props)[0] == "阻断"
    assert object_structure(fields, {"id": {"type": "unsupported", "source": "id"}})[0] == "阻断"
    assert object_structure(fields, {"id": {"type": "string", "source": ""}})[0] == "阻断"
    assert object_structure({"pk": "id"}, props)[0] == "警告"
    assert (fields, props) == before


async def test_v5_uses_real_saved_cr_overlay_and_preserves_definition(client, facts):  # noqa: F811
    from tests.test_static_trials import ADMIN, AUTHOR, P

    assert facts.get_bind().engine.url.database == "rengu_fix_static_20261027"
    await load_governed_tables(facts)
    metric = await facts.scalar(select(Metric).where(Metric.code == "order_cnt"))
    original = metric.formula
    target = {"kind": "metric", "id": "order_cnt", "name": "订单量"}
    response = await client.post(
        P + "/change-requests",
        headers=AUTHOR,
        json={"title": "检查指标映射", "reviewer": "U010001", "target": target},
    )
    assert response.status_code == 201, response.text
    code = response.json()["id"]
    for formula, expected in (("SUM(order.not_a_property)", "阻断"), ("COUNT(order)", "通过")):
        head = await client.get(P + f"/change-requests/{code}", headers=AUTHOR)
        response = await client.put(
            P + f"/change-requests/{code}/items",
            headers=AUTHOR,
            json={
                "target": target,
                "section": "def",
                "lockVersion": head.json()["lockVersion"],
                "rows": [{"key": "formula", "op": "update", "before": original, "after": formula}],
            },
        )
        assert response.status_code == 200, response.text
        response = await client.post(P + f"/change-requests/{code}:validate", headers=ADMIN, json={})
        assert response.status_code == 200, response.text
        checks = {row["code"]: row for row in response.json()["checks"]}
        assert checks["V5"]["result"] == expected
        assert "未执行源查询或EXPLAIN" in checks["V5"]["note"]
        assert checks["V2"]["result"] == "不适用"
        assert checks["V14"]["result"] == "不适用"
        await facts.refresh(metric)
        assert metric.formula == original  # Only CR/check records can change.
