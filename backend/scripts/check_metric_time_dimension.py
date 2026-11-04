"""Offline selected-metric time boundaries; no database, HTTP, or model calls."""

from copy import deepcopy
from datetime import UTC, datetime
from unittest.mock import patch

from app.common import metricflow_engine as engine
from app.modules.qa.semantic import plan, semantic_lines


def manifest():
    model = {
        "name": "orders",
        "node_relation": {"schema_name": "public", "alias": "orders"},
        "defaults": {"agg_time_dimension": "created"},
        "dimensions": [
            {"name": name, "expr": name + "_at", "type": "time"}
            for name in ("created", "shipped")
        ],
        "measures": [
            {"name": "created_count", "agg": "count", "expr": "id", "agg_time_dimension": "created"},
            {"name": "shipped_count", "agg": "count", "expr": "id", "agg_time_dimension": "shipped"},
            {"name": "snapshot", "agg": "count", "expr": "id"},
            {"name": "legacy", "agg": "count", "expr": "id"},
        ],
    }
    metrics = [
        {
            "name": name, "type": "simple", "type_params": {"measure": name},
            "config": {"meta": {"rengu": {"hasTimeField": name != "snapshot"}}},
        }
        for name in ("created_count", "shipped_count", "snapshot")
    ]
    metrics.append({"name": "legacy", "type": "simple", "type_params": {"measure": "legacy"}})
    for name, parts in (
        ("mixed", ["created_count", "shipped_count"]),
        ("with_snapshot", ["created_count", "snapshot"]),
        ("same_time", ["created_count", "legacy"]),
    ):
        metrics.append({
            "name": name, "type": "derived",
            "type_params": {"metrics": [{"name": part} for part in parts], "expr": " + ".join(parts)},
        })
    return {"semantic_models": [model], "metrics": metrics}


def rejects(call):
    try:
        call()
    except engine.MetricQueryInvalid:
        return
    raise AssertionError("Ambiguous or unavailable metric time was accepted")


def main():
    doc = manifest()
    now = datetime(2026, 2, 1, tzinfo=UTC)
    assert engine.metric_time_dimension(doc, ["shipped_count"]) == ("orders", "shipped")
    assert engine.metric_time_dimension(doc, ["legacy"]) == ("orders", "created")
    assert engine.metric_time_dimension(doc, ["same_time"]) == ("orders", "created")
    for names in (["snapshot"], ["mixed"], ["with_snapshot"], ["created_count", "shipped_count"]):
        rejects(lambda selected=names: engine.metric_time_dimension(doc, selected))
        rejects(lambda selected=names: engine.compile_metrics(doc, selected, ["metric_time"]))
        rejects(lambda selected=names: plan(
            {"metric": {"metrics": selected, "time": {"start": "2026-01-01"}}},
            doc, now,
        ))
    sql = engine.compile_metrics(doc, ["shipped_count"], ["metric_time"], backend="builtin").sql
    assert 'CAST("shipped_at" AS DATE)' in sql and "created_at" not in sql
    planned = plan({"metric": {
        "metrics": ["shipped_count"], "time": {"start": "2026-01-01", "grain": "day"},
    }}, doc, now)
    assert all("orders__shipped" in text for text in planned.where)
    assert planned.group_by == ["metric_time"]
    rejects(lambda: plan({"metric": {"metrics": ["snapshot"], "dimensions": ["metric_time"]}},
                         doc, now))
    lines = "\n".join(semantic_lines(doc))
    assert "指标 shipped_count：shipped_count；time 按 orders__shipped 过滤" in lines
    assert "指标 snapshot：snapshot；无唯一时间字段，不支持 time" in lines
    # A stale model default must not supply time for a new explicit snapshot declaration.
    doc["semantic_models"][0]["measures"][2]["agg_time_dimension"] = "created"
    rejects(lambda: engine.metric_time_dimension(doc, ["snapshot"]))
    missing = deepcopy(doc)
    missing["semantic_models"][0]["measures"][1].pop("agg_time_dimension")
    rejects(lambda: engine.metric_time_dimension(missing, ["shipped_count"]))
    with (
        patch.object(engine, "metricflow_available", return_value=True),
        patch.object(engine, "_compile_metricflow", side_effect=AssertionError("Remote compiler called")),
    ):
        for backend in ("auto", "metricflow"):
            for names in (["snapshot"], ["with_snapshot"]):
                assert engine.compile_metrics(doc, names, backend=backend).backend == "builtin"
            assert engine.compile_metrics(missing, ["shipped_count"], backend=backend).backend == "builtin"
    print("Metric time passed: selected components, explicit snapshots, "
          "legacy fallback, plans, backend choice.")


if __name__ == "__main__":
    main()
