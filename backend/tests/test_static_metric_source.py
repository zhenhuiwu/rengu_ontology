"""Actual PG source metrics: approved mapping, readonly/RLS, privacy and explicit semantic boundary."""

import json

import asyncpg
import pytest
from sqlalchemy import select

from app.modules.admin.models import SysRole
from app.modules.static.models import Metric, MetricStatus, Property
from tests.test_object_source_regression import real_source, source_case  # noqa: F401

P = "/api/ontology/v1"


@pytest.fixture
async def metric_source(source_case):  # noqa: F811 - imported pytest fixture
    case = source_case
    assert case.real.admin_url.database == "rengu_fix_static_20261027"
    connection = await asyncpg.connect(
        case.real.admin_url.set(drivername="postgresql").render_as_string(hide_password=False)
    )
    try:
        exists = await connection.fetchval("SELECT to_regclass($1)", f'"{case.real.schema}".metric_events')
        if not exists:
            await connection.execute(f'''CREATE TABLE "{case.real.schema}".metric_events AS
            SELECT *, TIMESTAMPTZ '2026-10-24 12:00:00+08' AS happened_at FROM "{case.real.schema}".assets''')
            await connection.execute(
                f'ALTER TABLE "{case.real.schema}".metric_events ENABLE ROW LEVEL SECURITY'
            )
            await connection.execute(
                f'ALTER TABLE "{case.real.schema}".metric_events FORCE ROW LEVEL SECURITY'
            )
            await connection.execute(f'''CREATE POLICY scope ON "{case.real.schema}".metric_events
            USING (ontology_id=NULLIF(current_setting('rengu.ontology_id',true),'')::bigint)''')
        await connection.execute(
            f'GRANT SELECT ON "{case.real.schema}".metric_events TO "{case.real.reader}"'
        )
    finally:
        await connection.close()
    case.obj.source_table = f"{case.real.schema}.metric_events"
    case.obj.properties.append(
        Property(
            api_name="happened_at",
            display_name="发生时间",
            data_type="timestamp",
            prop_group="时间",
            source_column="happened_at",
            description="",
            visibility="normal",
        )
    )
    case.manifest[0]["table"] = "metric_events"
    case.manifest[0]["columns"].append("happened_at")
    case.file.write_text(json.dumps(case.manifest), encoding="utf-8")
    metric = Metric(
        code="source_amount",
        api_name="SourceAmount",
        display_name="源金额",
        metric_group="测试",
        unit="元",
        version="v1",
        owner="王敏",
        status=MetricStatus.published,
        formula="SUM(TestAsset.amount)",
        def_rows=[
            {"k": "时间字段", "v": "TestAsset.happened_at"},
            {"k": "默认过滤", "v": "TestAsset.segment = 'even'"},
        ],
    )
    case.session.add(metric)
    await case.session.flush()
    return case, metric


async def test_source_metric_actual_sql_and_routing_boundary(metric_source, monkeypatch):
    case, metric = metric_source
    response = await case.client.get(f"{P}/metrics/{metric.code}/trial-readiness")
    assert response.status_code == 200 and response.json()["ready"] is True, response.text
    result = await case.client.post(f"{P}/metrics/{metric.code}:trial", json={})
    assert result.status_code == 200, result.text
    assert result.json()["rows"] == [{"date": "2026-10-24", "value": "172.25"}]
    # An unrelated configured object's restricted identifier must not invalidate this metric.
    target_key = next(p for p in case.target.properties if p.is_pk)
    target_key.is_sensitive = True
    await case.session.flush()
    assert (await case.client.get(f"{P}/metrics/{metric.code}/trial-readiness")).json()["ready"] is True
    target_key.is_sensitive = False
    await case.session.flush()
    exported = await case.client.get(f"{P}/exports/metricflow")
    assert exported.status_code == 200
    skipped = next(r for r in exported.json()["skipped"] if r["id"] == metric.code)
    assert skipped["issues"][0]["reason"] == "SEMANTIC_SOURCE_UNSUPPORTED"
    role = await case.session.scalar(select(SysRole).where(SysRole.code == "R-01"))
    role.objs = [{**p, "view": False} if p.get("api") == "TestAsset" else p for p in role.objs]
    await case.session.flush()
    denied = await case.client.post(f"{P}/metrics/{metric.code}:trial", json={})
    assert denied.status_code == 200 and denied.json()["rows"] == []
    monkeypatch.setenv(
        "RENGU_OBJECT_READ_URL_TEST_ONLY", case.real.admin_url.render_as_string(hide_password=False)
    )
    assert (await case.client.post(f"{P}/metrics/{metric.code}:trial", json={})).status_code == 503


async def test_source_metric_missing_mapping_privacy_and_cross_object_fail_closed(metric_source):
    case, metric = metric_source
    for formula in (
        "SUM(TestAsset.secret)",
        "SUM(TestAsset.hidden_text)",
        "SUM(TestAsset.amount) + SUM(TestTarget.amount)",
    ):
        metric.formula = formula
        await case.session.flush()
        response = await case.client.post(f"{P}/metrics/{metric.code}:trial", json={})
        assert response.status_code == 422, response.text
    metric.formula = "SUM(TestAsset.amount)"
    case.manifest[0]["columns"].remove("amount")
    case.file.write_text(json.dumps(case.manifest), encoding="utf-8")
    assert (await case.client.get(f"{P}/metrics/{metric.code}/trial-readiness")).json()["ready"] is False
    assert (await case.client.post(f"{P}/metrics/{metric.code}:trial", json={})).status_code == 422


async def test_explicit_incomplete_binding_never_borrows_retail(metric_source, monkeypatch):
    from app.modules.static.models import ObjectType

    case, metric = metric_source
    order = await case.session.scalar(select(ObjectType).where(ObjectType.code == "order"))
    order.datasource_name, order.source_table = case.obj.datasource_name, case.obj.source_table
    case.manifest.append({**case.manifest[0], "objectType": "order"})
    case.file.write_text(json.dumps(case.manifest), encoding="utf-8")
    metric.formula = "SUM(order.paid_amount)"
    metric.def_rows = [{"k": "时间字段", "v": "order.created_at"}]
    await case.session.flush()
    monkeypatch.delenv("RENGU_OBJECT_READ_URL_TEST_ONLY")
    assert (await case.client.get(f"{P}/metrics/{metric.code}/trial-readiness")).json()["ready"] is False
    response = await case.client.post(f"{P}/metrics/{metric.code}:trial", json={})
    assert response.status_code == 422 and "OBJECT_UNBOUND" in response.text
