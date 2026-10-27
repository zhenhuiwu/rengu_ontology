"""Real metric execution and validation analysis: no invented formulas, values or dependencies."""

from copy import deepcopy
from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import func, select
from sqlglot import exp

from app.core.errors import AppError
from app.modules.admin.models import SysRole
from app.modules.biz.models import BizOrder, OrderStatus, Store, StoreStatus
from app.modules.static.metric_trial import parse_formula
from app.modules.static.models import ChangeRequest, CrImpact, CRStatus, CrTrial, Metric, ObjectType
from app.seeds.loader import run_seeds
from tests.function_access_support import grant_fixture_roles
from tests.helpers.workspace import bind_workspace_members

P = "/api/ontology/v1"
AUTHOR = {"X-Demo-As": "U010004"}
ADMIN = {"X-Demo-As": "U010001"}


@pytest.fixture
def real_sections(_isolated_sections):
    from app.modules.static.sections import BUILDERS
    from app.modules.versioning.sections import isolated_sections

    with isolated_sections(*BUILDERS):
        yield


@pytest.fixture
async def facts(session, real_sections):
    await run_seeds(session, ["admin", "versioning", "static"])
    await grant_fixture_roles(session)
    await bind_workspace_members(session)
    metric = await session.scalar(select(Metric).where(Metric.code == "order_cnt"))
    # Test facts are truthful and local to the test transaction; exported demo data stays untouched.
    metric.formula = "COUNT(order) WHERE status <> 已取消"
    metric.def_rows = [{"k": "时间字段", "v": "order.created_at"}]
    store = Store(
        code="trial_store",
        store_no="TRIAL",
        name="试算门店",
        city="上海",
        region="华东一区",
        big_region="华东",
        format="社区店",
        opened_at=date(2025, 1, 1),
        status=StoreStatus.open,
    )
    session.add(store)
    await session.flush()
    tz = ZoneInfo("Asia/Shanghai")
    for n, (day, channel, status, amount) in enumerate(
        [
            (24, "线上", OrderStatus.shipped, "10.20"),
            (24, "线下", OrderStatus.shipped, "20.30"),
            (24, "线上", OrderStatus.cancelled, "999"),
            (25, "线上", OrderStatus.shipped, "30.40"),
        ]
    ):
        session.add(
            BizOrder(
                code=f"trial_order_{n}",
                store_id=store.id,
                channel=channel,
                status=status,
                created_at_biz=datetime(2026, 10, day, 12, tzinfo=tz),
                amount=Decimal(amount),
            )
        )
    await session.commit()
    return session


async def test_metric_trial_actual_rows_saved_cr_and_readonly_context(client, facts, monkeypatch):
    async def scoped(user, object_type):
        return {"field": "channel", "op": "=", "value": "线上"} if object_type == "order" else None

    monkeypatch.setattr("app.common.auth.row_filter", scoped)
    response = await client.post(P + "/metrics/order_cnt:trial", headers=AUTHOR, json={"range": "30d"})
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["rows"] == [{"date": "2026-10-24", "value": "1"}, {"date": "2026-10-25", "value": "1"}]
    assert data["dataCutoff"].startswith("2026-10-25")
    assert (
        await client.post(P + "/metrics/order_cnt:trial?previewAs=U010001", headers=AUTHOR, json={})
    ).json()["rows"] == data["rows"]
    cr = await client.post(
        P + "/change-requests",
        headers=AUTHOR,
        json={"title": "真实试算", "target": {"kind": "metric", "id": "order_cnt"}},
    )
    assert cr.status_code == 201, cr.text
    code = cr.json()["id"]
    saved = await client.put(
        P + f"/change-requests/{code}/items",
        headers=AUTHOR,
        json={
            "target": {"kind": "metric", "id": "order_cnt"},
            "section": "def",
            "lockVersion": cr.json()["lockVersion"],
            "rows": [{"key": "formula", "op": "update", "after": "COUNT(order) * 2 WHERE status <> 已取消"}],
        },
    )
    assert saved.status_code == 200, saved.text
    draft = await client.post(P + "/metrics/order_cnt:trial", headers=AUTHOR, json={"crId": code})
    assert draft.status_code == 200, draft.text
    assert [row["value"] for row in draft.json()["rows"]] == ["2", "2"]
    assert (
        await facts.scalar(select(Metric).where(Metric.code == "order_cnt"))
    ).formula == "COUNT(order) WHERE status <> 已取消"
    validated = await client.post(P + f"/change-requests/{code}:validate", headers=AUTHOR, json={})
    assert validated.status_code == 200, validated.text
    detail = (await client.get(P + f"/change-requests/{code}", headers=AUTHOR)).json()["detail"]
    assert detail["trial"]["rows"] == [{"period": "2026-10", "before": "2", "after": "4", "delta": "2"}]
    assert await facts.scalar(select(func.count()).select_from(CrTrial)) == 1
    assert await facts.scalar(select(func.count()).select_from(CrImpact)) > 0
    other = (await client.get(P + f"/change-requests/{code}", headers=ADMIN)).json()["detail"]
    assert "trial" not in other and "其他用户" in other["trialUnavailable"]
    preview = (await client.get(P + f"/change-requests/{code}?previewAs=U010001", headers=AUTHOR)).json()[
        "detail"
    ]
    assert "trial" not in preview
    listing = (await client.get(P + "/change-requests?full=true", headers=ADMIN)).json()
    assert "trial" not in next(row for row in listing if row["id"] == code)["detail"]
    role = await facts.scalar(select(SysRole).where(SysRole.code == "R-02"))
    original_permissions = deepcopy(role.objs)
    role.objs = [{**row, "view": False} if row.get("api") == "SalesOrder" else row for row in role.objs]
    await facts.flush()
    revoked = (await client.get(P + f"/change-requests/{code}", headers=AUTHOR)).json()["detail"]
    assert "trial" not in revoked and "已变更的数据权限" in revoked["trialUnavailable"]
    role.objs = original_permissions
    await facts.flush()
    readonly = await client.post(
        P + f"/change-requests/{code}:validate?previewAs=U010001", headers=AUTHOR, json={}
    )
    assert readonly.status_code == 403 and readonly.json()["code"] == "READONLY"
    # Saving another edit invalidates recorded analysis, rather than showing stale business values.
    saved = await client.put(
        P + f"/change-requests/{code}/items",
        headers=AUTHOR,
        json={
            "target": {"kind": "metric", "id": "order_cnt"},
            "section": "def",
            "rows": [{"key": "formula", "op": "update", "after": "COUNT(order) * 3"}],
        },
    )
    assert saved.status_code == 200, saved.text
    assert await facts.scalar(select(func.count()).select_from(CrTrial)) == 0
    assert "trial" not in (await client.get(P + f"/change-requests/{code}", headers=AUTHOR)).json()["detail"]


async def test_metric_trial_missing_semantics_and_invalid_input_never_succeed(client, facts):
    response = await client.post(P + "/metrics/gmv:trial", headers=ADMIN, json={})
    assert response.status_code == 422 and response.json()["code"] == "METRIC_TRIAL_UNAVAILABLE"
    assert (
        await client.post(P + "/metrics/order_cnt:trial", headers=ADMIN, json={"range": "arbitrary"})
    ).status_code == 422
    assert (await client.post(P + "/metrics/bad%00id:trial", headers=ADMIN, json={})).status_code == 422
    bad_cr = await client.post(P + "/metrics/order_cnt:trial", headers=ADMIN, json={"crId": "bad\x00id"})
    assert bad_cr.status_code == 422
    history = await client.post(P + "/metrics/order_cnt:trial?ver=v2026.09.3", headers=ADMIN, json={})
    assert history.status_code in (404, 422) and history.json()["code"] != "READONLY"
    metric = await facts.scalar(select(Metric).where(Metric.code == "order_cnt"))
    metric.def_rows = []
    await facts.flush()
    missing_time = await client.post(P + "/metrics/order_cnt:trial", headers=ADMIN, json={})
    assert missing_time.status_code == 422 and "时间字段" in missing_time.json()["message"]


async def test_metric_sum_default_filter_empty_results_and_unavailable_cr(client, facts):
    metric = await facts.scalar(select(Metric).where(Metric.code == "order_cnt"))
    metric.formula = "SUM(order.paid_amount)"
    metric.def_rows = [
        {"k": "时间字段", "v": "order.created_at"},
        {"k": "默认过滤", "v": "order.status <> 已取消；order.channel = '线上'"},
    ]
    await facts.flush()
    response = await client.post(P + "/metrics/order_cnt:trial", headers=ADMIN, json={})
    assert response.status_code == 200, response.text
    assert [Decimal(row["value"]) for row in response.json()["rows"]] == [Decimal("10.20"), Decimal("30.40")]
    metric.formula = "COUNT(order) WHERE channel = '不存在的渠道'"
    metric.def_rows = [{"k": "时间字段", "v": "order.created_at"}]
    await facts.flush()
    empty = await client.post(P + "/metrics/order_cnt:trial", headers=ADMIN, json={})
    assert empty.status_code == 200 and empty.json()["rows"] == [] and empty.json()["dataCutoff"] is None
    cr = (
        await client.post(
            P + "/change-requests",
            headers=AUTHOR,
            json={"title": "缺少执行语义", "target": {"kind": "metric", "id": "order_cnt"}},
        )
    ).json()
    await client.put(
        P + f"/change-requests/{cr['id']}/items",
        headers=AUTHOR,
        json={
            "target": {"kind": "metric", "id": "order_cnt"},
            "section": "def",
            "rows": [{"key": "formula", "op": "update", "after": "销售额 / 成本"}],
        },
    )
    validated = await client.post(P + f"/change-requests/{cr['id']}:validate", headers=AUTHOR, json={})
    assert validated.status_code == 200, validated.text
    detail = (await client.get(P + f"/change-requests/{cr['id']}", headers=AUTHOR)).json()["detail"]
    assert "trial" not in detail and "METRIC_TRIAL_UNAVAILABLE" in detail["trialUnavailable"]
    assert await facts.scalar(select(func.count()).select_from(CrTrial)) == 0
    assert any(row["code"] == "V14" and row["result"] == "警告" for row in validated.json()["checks"])


async def test_gmv_readiness_reports_exact_missing_references_and_no_trial_writes(client, facts):
    metric = await facts.scalar(select(Metric).where(Metric.code == "gmv"))
    before = metric.formula
    count = await facts.scalar(select(func.count()).select_from(CrTrial))
    response = await client.get(P + "/metrics/gmv/trial-readiness", headers=ADMIN)
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["ready"] is False
    issues = data["issues"]
    missing = next(i for i in issues if i.get("reference") == "order_line.不含税金额")
    assert missing["reason"] == "PROPERTY_NOT_FOUND"
    candidate = next(p for p in missing["candidates"] if p["reference"] == "order_line.amount_ex_tax")
    assert candidate["name"] == "行金额（不含税）" and candidate["mapped"] is False
    assert any(i.get("reference") == "refund_line.不含税退款金额" for i in issues)
    assert any(i["reason"] == "OBJECT_UNBOUND" and i["reference"] == "refund_line" for i in issues)
    assert any(i["reason"] == "CROSS_OBJECT_UNSUPPORTED" for i in issues)
    attempted = await client.post(P + "/metrics/gmv:trial", headers=ADMIN, json={})
    assert attempted.status_code == 422 and attempted.json()["details"]["issues"] == issues
    assert metric.formula == before
    assert await facts.scalar(select(func.count()).select_from(CrTrial)) == count
    for code, state in (("atv", "草稿"), ("turnover", "已下线")):
        blocked = (await client.get(P + f"/metrics/{code}/trial-readiness", headers=ADMIN)).json()
        assert blocked["ready"] is False and blocked["issues"][0]["reason"] == "METRIC_NOT_PUBLISHED"
        assert state in blocked["issues"][0]["message"]


async def test_readiness_and_actual_sql_share_exact_property_mapping_and_ambiguity_rules(client, facts):
    metric = await facts.scalar(select(Metric).where(Metric.code == "order_cnt"))
    metric.formula = "SUM(SalesOrder.paid_amount)"
    metric.def_rows = [
        {"k": "时间字段", "v": "SalesOrder.created_at"},
        {"k": "默认过滤", "v": "SalesOrder.status <> 已取消；SalesOrder.channel = '线上'"},
    ]
    await facts.flush()
    assert (await client.get(P + "/metrics/order_cnt/trial-readiness", headers=ADMIN)).json()["ready"] is True
    response = await client.post(P + "/metrics/order_cnt:trial", headers=ADMIN, json={})
    assert response.status_code == 200, response.text
    assert [Decimal(row["value"]) for row in response.json()["rows"]] == [Decimal("10.20"), Decimal("30.40")]
    obj = await facts.scalar(select(ObjectType).where(ObjectType.code == "order"))
    paid = next(p for p in obj.properties if p.api_name == "paid_amount")
    metric.formula = f'SUM("订单"."{paid.display_name}")'
    await facts.flush()
    assert (await client.post(P + "/metrics/order_cnt:trial", headers=ADMIN, json={})).status_code == 200
    other = next(p for p in obj.properties if p.api_name == "channel")
    other.display_name = paid.display_name
    await facts.flush()
    duplicate = (await client.get(P + "/metrics/order_cnt/trial-readiness", headers=ADMIN)).json()
    assert duplicate["ready"] is False and any(
        i["reason"] == "PROPERTY_AMBIGUOUS" for i in duplicate["issues"]
    )
    metric.formula = "SUM(order.paid_amount)"
    paid.backing_column = None
    await facts.flush()
    unmapped = (await client.get(P + "/metrics/order_cnt/trial-readiness", headers=ADMIN)).json()
    assert any(
        i["reason"] == "PROPERTY_UNBOUND" and i["reference"] == "order.paid_amount"
        for i in unmapped["issues"]
    )
    assert (await client.post(P + "/metrics/order_cnt:trial", headers=ADMIN, json={})).status_code == 422


async def test_existing_metric_form_fields_create_publish_edit_and_trial(client, facts):
    """The existing UI formula field alone can define an executable metric; no def_rows test patch."""
    for existing in await facts.scalars(
        select(ChangeRequest).where(ChangeRequest.status == CRStatus.approved)
    ):
        existing.status = CRStatus.draft
    await facts.flush()
    code = "created_order_count"
    values = {
        "name": "按创建时间计数订单",
        "api": code,
        "group": "销售",
        "owner": "U010004",
        "unit": "单",
        "version": "v1",
        "formula": "COUNT(order.created_at) WHERE order.status <> 已取消",
        "dims": [],
        "variants": [],
    }

    async def publish_form(is_new, fields):
        target = {"kind": "metric", "id": code, "name": values["name"], "isNew": is_new}
        response = await client.post(
            P + "/change-requests",
            headers=AUTHOR,
            json={
                "title": "表单真实指标",
                "reason": "按公式明确指定创建时间，不推断业务日期",
                "reviewer": "U010001",
                "target": target,
            },
        )
        assert response.status_code == 201, response.text
        cr = response.json()
        response = await client.put(
            P + f"/change-requests/{cr['id']}/items",
            headers=AUTHOR,
            json={
                "target": target,
                "section": "def",
                "lockVersion": cr["lockVersion"],
                "rows": [
                    {"key": key, "op": "add" if is_new else "update", "after": value}
                    for key, value in fields.items()
                ],
            },
        )
        assert response.status_code == 200, response.text
        draft = await client.post(P + f"/metrics/{code}:trial", headers=AUTHOR, json={"crId": cr["id"]})
        assert draft.status_code == 200, draft.text
        for action, headers in (("submit", AUTHOR), ("approve", ADMIN)):
            response = await client.post(
                P + f"/change-requests/{cr['id']}:{action}", headers=headers, json={}
            )
            assert response.status_code == 200, response.text
        response = await client.post(P + "/releases", headers=ADMIN, json={})
        assert response.status_code == 201 and response.json()["skipped"] == [], response.text
        return draft.json()

    first = await publish_form(True, values)
    assert [row["value"] for row in first["rows"]] == ["2", "1"]
    published = await client.post(P + f"/metrics/{code}:trial", headers=AUTHOR, json={})
    assert published.status_code == 200, published.text
    assert published.json()["rows"] == first["rows"] and "order.created_at" in published.json()["scope"]
    stored = await facts.scalar(select(Metric).where(Metric.code == code))
    assert stored.def_rows == [] and stored.version == "v1"
    edited = await publish_form(
        False, {"formula": "COUNT(order.created_at) * 2 WHERE order.status <> 已取消", "version": "v2"}
    )
    assert [row["value"] for row in edited["rows"]] == ["4", "2"]
    current = await client.post(P + f"/metrics/{code}:trial", headers=AUTHOR, json={})
    assert current.status_code == 200 and current.json()["version"] == "v2", current.text
    assert current.json()["rows"] == edited["rows"]


@pytest.mark.parametrize(
    "formula",
    [
        "COUNT(order); DELETE FROM metric",
        "COUNT(order) FROM biz_order",
        "COUNT(order) LIMIT 1",
        "COUNT(order) UNION SELECT 1",
        "COUNT(order) -- comment\nFROM metric",
    ],
)
def test_trial_rejects_query_syntax(formula):
    with pytest.raises(AppError):
        parse_formula(formula)


def test_formula_normalization_preserves_sql_literals_and_quoted_identifiers():
    value = "渠'道−≠;；"
    quoted = "渠''道−≠;；"
    formula = parse_formula(f"""COUNT(order.created_at) − 1 WHERE "order"."channel−≠;；" ≠ '{quoted}' """)
    assert isinstance(formula.expressions[0], exp.Sub)
    assert formula.find(exp.NEQ).expression.this == value
    assert formula.find(exp.NEQ).this.name == "channel−≠;；"
    filters = parse_formula(
        f"""1 WHERE order.channel = '{quoted}'； "order"."channel−≠;；" ≠ 'x'; order.status = '已取消' """,
        normalize_separators=True,
    )
    assert {node.this for node in filters.find_all(exp.Literal) if node.is_string} == {value, "x", "已取消"}
    assert len(list(filters.find_all(exp.And))) == 2
    assert filters.find(exp.NEQ).this.name == "channel−≠;；"


async def test_metric_time_field_and_filter_via_cr_publish_and_rollback(client, facts):
    """时间字段/默认过滤走变更单 metric/def（timeField/defaultFilter）。

    草稿试算即生效，发布写 def_rows，整版回滚恢复。"""
    for existing in await facts.scalars(
        select(ChangeRequest).where(ChangeRequest.status == CRStatus.approved)
    ):
        existing.status = CRStatus.draft
    await facts.flush()
    code = "order_cnt"
    target = {"kind": "metric", "id": code, "name": "订单量"}

    async def publish(rows):
        r = await client.post(
            P + "/change-requests",
            headers=AUTHOR,
            json={
                "title": "执行口径",
                "reason": "补时间字段与默认过滤",
                "reviewer": "U010001",
                "target": target,
            },
        )
        assert r.status_code == 201, r.text
        cr = r.json()
        r = await client.put(
            P + f"/change-requests/{cr['id']}/items",
            headers=AUTHOR,
            json={"target": target, "section": "def", "lockVersion": cr["lockVersion"], "rows": rows},
        )
        assert r.status_code == 200, r.text
        draft = await client.post(P + f"/metrics/{code}:trial", headers=AUTHOR, json={"crId": cr["id"]})
        assert draft.status_code == 200, draft.text
        for action, headers in (("submit", AUTHOR), ("approve", ADMIN)):
            r = await client.post(P + f"/change-requests/{cr['id']}:{action}", headers=headers, json={})
            assert r.status_code == 200, r.text
        r = await client.post(P + "/releases", headers=ADMIN, json={})
        assert r.status_code == 201 and r.json()["skipped"] == [], r.text
        return draft.json(), r.json()["ver"]

    def exec_rows():
        return {r["k"]: r["v"] for r in metric.def_rows if r["k"] in ("时间字段", "默认过滤")}

    first, _ = await publish(
        [
            {"key": "timeField", "op": "update", "before": "order.created_at", "after": "order.shipped_at"},
            {"key": "defaultFilter", "op": "add", "after": "order.channel = 线上"},
        ]
    )
    assert "order.shipped_at" in first["scope"]
    metric = await facts.scalar(
        select(Metric).where(Metric.code == code).execution_options(populate_existing=True)
    )
    assert exec_rows() == {"时间字段": "order.shipped_at", "默认过滤": "order.channel = 线上"}
    published = await client.post(P + f"/metrics/{code}:trial", headers=AUTHOR, json={})
    assert published.status_code == 200 and published.json()["rows"] == first["rows"], published.text

    _, ver = await publish(
        [
            {"key": "timeField", "op": "update", "before": "order.shipped_at", "after": "order.created_at"},
            {"key": "defaultFilter", "op": "delete", "before": "order.channel = 线上"},
        ]
    )
    await facts.refresh(metric)
    assert exec_rows() == {"时间字段": "order.created_at"}

    r = await client.post(P + f"/releases/{ver}:rollback", headers=ADMIN, json={})
    assert r.status_code == 201, r.text
    await facts.refresh(metric)
    assert exec_rows() == {"时间字段": "order.shipped_at", "默认过滤": "order.channel = 线上"}
