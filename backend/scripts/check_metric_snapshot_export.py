"""Offline checks for snapshot semantics and governed mirror column mappings."""

from types import SimpleNamespace
from unittest.mock import patch

import sqlalchemy as sa

from app.common import metricflow_engine as engine
from app.common.sql_guard import inject_row_filters
from app.modules.qa.semantic import plan
from app.modules.static import metric_trial as trial
from app.modules.static.metricflow_export import _export_metric


def fixture(mirror=False):
    columns = ("SOURCE_ID", "SOURCE_TIME", "SOURCE_AMOUNT") if mirror else ("id", "created_at", "amount")
    table = sa.Table(
        "governed_asset",
        sa.MetaData(),
        sa.Column(columns[0], sa.String, primary_key=True),
        sa.Column(columns[1], sa.DateTime),
        sa.Column(columns[2], sa.Numeric),
    )
    props = [
        SimpleNamespace(
            api_name=api,
            display_name=api,
            source_column=column,
            backing_column=api if mirror else column,
            is_pk=index == 0,
            is_sensitive=False,
            visibility="visible",
            transform=None,
        )
        for index, (api, column) in enumerate(zip(("id", "created_at", "amount"), columns, strict=True))
    ]
    obj = SimpleNamespace(
        id=1,
        code="Asset",
        api_name="Asset",
        display_name="设备",
        description="",
        pk_property="id",
        properties=props,
        status=SimpleNamespace(value="已发布"),
    )
    gov = SimpleNamespace(
        table=table,
        object_type="Asset",
        projection=frozenset(columns),
        source_sql='SELECT "SOURCE_ID", "SOURCE_TIME", "SOURCE_AMOUNT" FROM public.fixture_mirror'
        if mirror
        else None,
        columns={c.name: c for c in table.c},
    )
    return obj, gov


def manifest(mirror=False):
    obj, gov = fixture(mirror)
    row = SimpleNamespace(
        code="MET-SNAPSHOT",
        api_name="asset_count",
        display_name="设备数",
        version="v1",
        unit="台",
        status=SimpleNamespace(value="已发布"),
    )
    models = {}
    definition = {"formula": "COUNT(Asset)", "def": []}
    with patch.object(trial, "governed_tables", return_value={gov.table.name: gov}):
        metrics = _export_metric(definition, row, [obj], 1, models)
        timed = {"formula": "SUM(Asset.amount)", "def": [{"k": "时间字段", "v": "Asset.created_at"}]}
        row.api_name, row.code = "asset_amount", "MET-TIMED"
        metrics += _export_metric(timed, row, [obj], 1, models)
    return {"semantic_models": [m.out for m in models.values()], "metrics": metrics}, gov


def check():
    from datetime import UTC, datetime

    for mirrored in (False, True):
        exported, gov = manifest(mirrored)
        snapshot, timed = exported["metrics"]
        assert snapshot["config"]["meta"]["rengu"]["hasTimeField"] is False
        assert timed["config"]["meta"]["rengu"]["hasTimeField"] is True
        model = exported["semantic_models"][0]
        measure = model["measures"][0]
        assert "agg_time_dimension" not in measure
        assert model["defaults"]["agg_time_dimension"] == "created_at"
        with (
            patch.object(engine, "metricflow_available", return_value=True),
            patch.object(
                engine, "_compile_metricflow", side_effect=AssertionError("snapshot must use builtin")
            ),
        ):
            compiled = engine.compile_metrics(exported, ["asset_count"], backend="auto")
        assert compiled.backend == "builtin" and "COUNT(" in compiled.sql
        plan({"metric": {"metrics": ["asset_count"]}}, exported, datetime(2026, 11, 3, tzinfo=UTC))
        try:
            plan(
                {"metric": {"metrics": ["asset_count"], "time": {"grain": "day"}}},
                exported,
                datetime(2026, 11, 3, tzinfo=UTC),
            )
        except engine.MetricQueryInvalid:
            pass
        else:
            raise AssertionError("snapshot cannot inherit another metric's time")
        if mirrored:
            assert measure["expr"] == "SOURCE_ID"
            assert model["dimensions"][0]["expr"] == "SOURCE_TIME"
            assert model["measures"][1]["expr"] == "SOURCE_AMOUNT"
            guarded, _ = inject_row_filters(
                compiled.sql, {gov.table.name: ("TRUE", [])}, relations={gov.table.name: gov.source_sql}
            )
            assert "fixture_mirror" in guarded and '"SOURCE_ID"' in guarded
        else:
            assert measure["expr"] == "id"
    print("PASS: timeless snapshots, mixed temporal metrics and governed mirror mappings")


if __name__ == "__main__":
    check()
