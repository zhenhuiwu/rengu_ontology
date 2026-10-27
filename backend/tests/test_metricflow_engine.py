"""MetricFlow 适配骨架：导出形状夹具 → 内置编译 → run_guarded（行级过滤）。

夹具是手写的最小导出（与 export_metricflow 的 JSON 形状一致），对象 / 字段只是测试数据。
"""

import pytest
import sqlglot

from app.common import metricflow_engine as engine
from app.common.auth import resolve_user
from app.modules.static import metricflow_export
from tests import test_object_row_access as row_access

facts = row_access.facts
grant = row_access.grant

MANIFEST = {
    "semantic_models": [
        {
            "name": "store",
            "label": "门店",
            "description": "",
            "node_relation": {"schema_name": "public", "alias": "store"},
            "defaults": {"agg_time_dimension": "opened_on"},
            "entities": [{"name": "store", "type": "primary", "expr": "id"}],
            "dimensions": [
                {"name": "area", "label": "大区", "expr": "big_region", "type": "categorical"},
                {"name": "kind", "label": "业态", "expr": "format", "type": "categorical"},
                {
                    "name": "opened_on",
                    "label": "开业日",
                    "expr": "opened_at",
                    "type": "time",
                    "type_params": {"time_granularity": "day"},
                },
            ],
            "measures": [
                {"name": "store_count", "agg": "count", "expr": "id", "agg_time_dimension": "opened_on"},
                {"name": "kind_count", "agg": "count", "expr": "id", "agg_time_dimension": "opened_on"},
            ],
        }
    ],
    "metrics": [
        {
            "name": "store_count",
            "label": "门店数",
            "description": "",
            "config": {"meta": {"rengu": {"id": "M1", "version": 1, "unit": "家", "status": "published"}}},
            "type": "simple",
            "type_params": {"measure": "store_count"},
        },
        {
            "name": "kind_count",
            "label": "社区店数",
            "type": "simple",
            "type_params": {"measure": "kind_count"},
            "filter": "{{ Dimension('store__kind') }} = '社区店'",
        },
        {
            "name": "kind_share",
            "label": "社区店占比",
            "type": "derived",
            "type_params": {
                "expr": "kind_count / store_count",
                "metrics": [{"name": "kind_count"}, {"name": "store_count"}],
            },
        },
    ],
    "skipped": [],
    "notes": [],
}


def test_builtin_compiles_simple_filtered_and_derived():
    sql = engine.compile_query(
        MANIFEST,
        ["store_count", "kind_count", "kind_share"],
        ["store__area", "metric_time"],
        ["{{ Dimension('store__area') }} <> 'x'"],
        limit=10,
    )
    tree = sqlglot.parse_one(sql, read="postgres")
    assert [t.name for t in tree.find_all(sqlglot.exp.Table)] == ["store"]
    assert '"big_region" AS "store__area"' in sql and 'CAST("opened_at" AS DATE) AS "metric_time"' in sql
    assert 'CASE WHEN "format" = \'社区店\' THEN "id" END' in sql
    assert "GROUP BY 1, 2" in sql and "LIMIT 10" in sql


@pytest.mark.parametrize(
    ("metrics", "group_by", "where"),
    [
        (["nope"], [], []),
        (["store_count"], ["store__nope"], []),
        (["store_count"], ["other__area"], []),
        (["store_count"], [], ["1 = 1; drop table store"]),
        (["store_count"], [], ["{{ Dimension('store__area') }} = 'a' OR TRUE"]),
        ([], [], []),
    ],
)
def test_unknown_or_unsafe_inputs_rejected(metrics, group_by, where):
    with pytest.raises(engine.MetricQueryInvalid):
        engine.compile_query(MANIFEST, metrics, group_by, where)


def test_metricflow_backend_is_optional_and_not_wired():
    with pytest.raises(engine.MetricQueryInvalid) as info:
        engine.compile_query(MANIFEST, ["store_count"], backend="metricflow")
    assert info.value.details == {"installed": engine.metricflow_available()}


async def test_query_runs_through_export_and_row_filters(facts, monkeypatch):
    session, user = facts
    await grant(facts, "T-MF", [("门店", "大区", ["华东", "华南"])], default_deny=True)
    actor = await resolve_user(session, user.code)
    calls = []

    async def fake_export(s, u, *, ver=None):
        calls.append(u.id)
        return MANIFEST

    monkeypatch.setattr(metricflow_export, "export_metricflow", fake_export)
    out = await engine.query_metrics(session, actor, ["store_count"], ["store__area"])
    assert calls == [actor.id]
    rows = {area: n for area, n in out["rows"] if area in ("华东", "华南", "华北")}
    assert rows == {"华东": 1, "华南": 1}  # 华北 被行级范围过滤
    assert out["columns"] == ["store__area", "store_count"]
