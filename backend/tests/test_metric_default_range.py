"""Published defaults use real metric mappings and PG guards; never invoke a provider."""

import os
from dataclasses import replace
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select
from sqlglot import parse_one

from app.common.auth import resolve_user
from app.common.sql_guard import run_guarded
from app.core.errors import AppError
from app.modules.biz.models import BizOrder, OrderStatus
from app.modules.qa import chain
from app.modules.qa.llm import FakeLLM
from app.modules.qa.metric_defaults import resolve_default
from app.modules.static.metric_defaults import default_range
from app.modules.static.metric_versions import exec_def, with_exec
from app.modules.static.models import Metric, MetricStatus
from tests import test_static_metric_source as source_support
from tests import test_static_trials as support

metric_facts = support.facts
real_sections = support.real_sections
real_source = source_support.real_source
source_case = source_support.source_case
metric_source = source_support.metric_source


@pytest.fixture
async def configured(metric_facts):
    from app.common.sql_guard import load_governed_tables
    from app.modules.biz import governance  # noqa: F401

    facts = metric_facts
    assert facts.get_bind().engine.url.database == os.environ.get("RENGU_TEST_DB")
    assert facts.get_bind().engine.url.database.startswith(("rengu_test", "rengu_fix_"))
    metric = await facts.scalar(select(Metric).where(Metric.code == "order_cnt"))
    metric.display_name = "窗口订单量核验"
    metric.api_name = "window_order_count"
    metric.def_rows = with_exec(metric.def_rows, "defaultRange", {"kind": "rolling_days", "days": 7})
    store = await facts.scalar(select(BizOrder.store_id).where(BizOrder.code == "trial_order_0"))
    facts.add(
        BizOrder(
            code="outside_default_range",
            store_id=store,
            channel="线上",
            status=OrderStatus.shipped,
            amount=Decimal("5000"),
            created_at_biz=datetime(2026, 10, 10, 12, tzinfo=ZoneInfo("Asia/Shanghai")),
        )
    )
    await facts.flush()
    await load_governed_tables(facts)
    return facts, metric, await resolve_user(facts, "U010001")


async def test_real_range_and_saved_filters_are_enforced(configured):
    session, metric, actor = configured
    default, problem = await resolve_default(session, actor, metric.display_name)
    assert problem is None and default.days == 7
    assert (default.start, default.end) == ("2026-10-19", "2026-10-25")
    tree = parse_one(
        "SELECT COUNT(*) FROM biz_order o WHERE o.created_at_biz >= '2020-01-01'", read="postgres"
    )
    default.apply(tree)
    result = await run_guarded(
        await session.connection(), tree.sql(dialect="postgres"), actor, session=session
    )
    assert result.scalar_one() == 3  # old row and cancelled 999 are both excluded by real predicates.
    with pytest.raises(AppError, match="没有使用"):
        default.apply(parse_one("SELECT 1"))
    for expression in (
        "o.created_at_biz >= CURRENT_DATE",
        "o.created_at_biz >= NOW() - INTERVAL '1 day'",
        "CAST(o.created_at_biz AS DATE) = CURRENT_DATE",
        "DATE_TRUNC('day', o.created_at_biz) = CURRENT_DATE",
    ):
        with pytest.raises(AppError, match="可靠应用"):
            default.apply(parse_one("SELECT COUNT(*) FROM biz_order o WHERE " + expression, read="postgres"))


@pytest.mark.parametrize("semantic_query", [False, True])
async def test_actual_chain_uses_default_without_time_clarification(configured, semantic_query):
    session, metric, actor = configured
    llm = FakeLLM(
        '{"metric":{"metrics":["window_order_count"]}}'
        if semantic_query
        else "SELECT COUNT(*) AS 数量 FROM biz_order"
    )
    kind, snap, rows, _, sql = await chain.answer(
        session, await session.connection(), llm, actor, metric.display_name
    )
    assert kind == "answer" and "clarify" not in snap["answer"]
    assert rows == 1 and snap["answer"]["table"]["items"][0]["c0"] == 3
    assert "2026-10-19" in sql and "默认最近7天" in snap["answer"]["text"]
    assert "2026-10-25" in snap["answer"]["basis"]["defaultRange"]


async def test_missing_default_asks_before_model_and_explicit_time_wins(configured):
    session, metric, actor = configured

    class NoProvider:
        async def generate_sql(self, messages):
            pytest.fail("Missing published default must clarify before a model call")

    metric.def_rows = with_exec(metric.def_rows, "defaultRange", None)
    kind, snap, rows, _, sql = await chain.answer(
        session, await session.connection(), NoProvider(), actor, metric.display_name
    )
    assert kind == "answer" and snap["answer"]["card"] == "clarify" and rows == 0 and sql is None
    metric.def_rows = with_exec(metric.def_rows, "defaultRange", {"kind": "rolling_days", "days": 7})
    kind, snap, _, _, sql = await chain.answer(
        session,
        await session.connection(),
        FakeLLM(
            "SELECT COUNT(*) FROM biz_order WHERE created_at_biz >= '2026-10-24' "
            "AND created_at_biz < '2026-10-25'"
        ),
        actor,
        "昨天" + metric.display_name,
    )
    assert kind == "answer" and "2026-10-19" not in sql
    assert "defaultRange" not in snap["answer"]["basis"]


async def test_explicit_time_missing_default_conflict_and_permissions(configured):
    session, metric, actor = configured
    assert await resolve_default(session, actor, "昨天" + metric.display_name) == (None, None)
    for grouping in ("按日统计", "按月统计"):
        assert (await resolve_default(session, actor, grouping + metric.display_name))[0].days == 7
    assert await resolve_default(session, actor, "本月按日统计" + metric.display_name) == (None, None)
    assert (await resolve_default(session, actor, metric.display_name, ver="old"))[1]["term"] == "时间范围"
    for restricted in (replace(actor, func={}), replace(actor, row_filters={})):
        assert (await resolve_default(session, restricted, metric.display_name))[1]["code"] == "NO_PERMISSION"
    with pytest.raises(AppError):
        await resolve_default(session, replace(actor, ontology_id=2), metric.display_name)
    metric.def_rows = with_exec(metric.def_rows, "defaultRange", None)
    assert (await resolve_default(session, actor, metric.display_name))[1]["term"] == "时间范围"
    metric.def_rows = with_exec(metric.def_rows, "defaultRange", {"kind": "rolling_days", "days": 7})
    other = await session.scalar(select(Metric).where(Metric.code == "gmv"))
    other.display_name = metric.display_name
    await session.flush()
    assert (await resolve_default(session, actor, metric.display_name))[1]["term"] == "指标口径"


async def test_unexecutable_default_and_draft_never_applied(configured):
    session, metric, actor = configured
    metric.formula = None
    assert (await resolve_default(session, actor, metric.display_name))[1]["code"] == "NO_DATA"
    metric.status = MetricStatus.draft
    await session.flush()
    assert (await resolve_default(session, actor, metric.display_name))[1]["code"] == "NO_DATA"


async def test_default_cr_publish_clear_snapshot_and_rollback(client, metric_facts):
    from app.modules.static.models import ChangeRequest, CRStatus
    from tests.test_static_trials import ADMIN, AUTHOR, P

    for existing in await metric_facts.scalars(
        select(ChangeRequest).where(ChangeRequest.status == CRStatus.approved)
    ):
        existing.status = CRStatus.draft
    await metric_facts.flush()
    target = {"kind": "metric", "id": "order_cnt", "name": "订单量"}

    async def publish(row):
        created = await client.post(
            P + "/change-requests",
            headers=AUTHOR,
            json={"title": "默认范围", "reason": "明确默认最近7天", "reviewer": "U010001", "target": target},
        )
        assert created.status_code == 201, created.text
        cr = created.json()
        updated = await client.put(
            P + f"/change-requests/{cr['id']}/items",
            headers=AUTHOR,
            json={"target": target, "section": "def", "lockVersion": cr["lockVersion"], "rows": [row]},
        )
        assert updated.status_code == 200, updated.text
        # A saved draft is not a published default for QA.
        before = await client.get(P + "/metrics/order_cnt", headers=ADMIN)
        for action, who in (("submit", AUTHOR), ("approve", ADMIN)):
            response = await client.post(P + f"/change-requests/{cr['id']}:{action}", headers=who, json={})
            assert response.status_code == 200, response.text
        response = await client.post(P + "/releases", headers=ADMIN, json={})
        assert response.status_code == 201, response.text
        return before.json(), response.json()["ver"]

    original, first = await publish(
        {"key": "defaultRange", "op": "add", "after": {"kind": "rolling_days", "days": 7}}
    )
    assert original["execDef"]["defaultRange"] is None
    current = await client.get(P + "/metrics/order_cnt", headers=ADMIN)
    assert current.json()["execDef"]["defaultRange"]["days"] == 7
    saved, second = await publish(
        {"key": "defaultRange", "op": "delete", "before": {"kind": "rolling_days", "days": 7}}
    )
    assert saved["execDef"]["defaultRange"]["days"] == 7
    current = await client.get(P + "/metrics/order_cnt", headers=ADMIN)
    assert current.json()["execDef"]["defaultRange"] is None
    historical = await client.get(P + "/metrics/order_cnt", headers={**ADMIN}, params={"ver": first})
    assert historical.json()["execDef"]["defaultRange"]["days"] == 7
    response = await client.post(P + f"/releases/{second}:rollback", headers=ADMIN, json={})
    assert response.status_code == 201, response.text
    restored = await client.get(P + "/metrics/order_cnt", headers=ADMIN)
    assert restored.json()["execDef"]["defaultRange"]["days"] == 7


@pytest.mark.parametrize(
    "value",
    [
        {},
        {"kind": "rolling_days", "days": True},
        {"kind": "rolling_days", "days": 0},
        {"kind": "rolling_days", "days": 94},
        {"kind": "rolling_days", "days": "7"},
        {"kind": "rolling_days", "days": 7, "sql": "anything"},
    ],
)
def test_default_range_strict(value):
    with pytest.raises(ValueError):
        default_range(value)


def test_default_metadata_copy_clear_and_old_unknown():
    rows = [{"k": "时间字段", "v": "source.at"}]
    changed = with_exec(rows, "defaultRange", {"kind": "rolling_days", "days": 7})
    assert exec_def(changed)["defaultRange"]["days"] == 7
    assert exec_def(rows)["defaultRange"] is None
    assert exec_def(with_exec(changed, "defaultRange", None))["defaultRange"] is None


async def test_dedicated_source_exact_metric_uses_original_whole_aggregate(metric_source):
    case, metric = metric_source
    metric.def_rows = with_exec(metric.def_rows, "defaultRange", {"kind": "rolling_days", "days": 7})
    await case.session.flush()
    actor = await resolve_user(case.session, "U010001")

    class NoProvider:
        async def generate_sql(self, messages):
            pytest.fail("Exact dedicated-source metric must use controlled evaluation, never model SQL")

    kind, snap, rows, _, sql = await chain.answer(
        case.session, await case.session.connection(), NoProvider(), actor, metric.display_name
    )
    assert kind == "answer" and rows == 1 and sql is None
    assert snap["answer"]["table"]["items"] == [{"c0": 172.25}]
    assert snap["answer"]["canPin"] is False and "不支持固定" in snap["answer"]["text"]
    metric.formula = "SUM(TestAsset.amount) / COUNT(TestAsset)"
    await case.session.flush()
    result = await chain.answer(
        case.session, await case.session.connection(), NoProvider(), actor, metric.display_name
    )
    assert result[1]["answer"]["table"]["items"] == [{"c0": 13.25}]
    for extra in ("按日统计", "只看even的"):
        result = await chain.answer(
            case.session, await case.session.connection(), NoProvider(), actor, extra + metric.display_name
        )
        assert result[0] == "denied", "Unsupported conditions must not be silently discarded"
    result = await chain.answer(
        case.session,
        await case.session.connection(),
        NoProvider(),
        actor,
        metric.display_name,
        session_scope="any",
    )
    assert result[0] == "denied"
