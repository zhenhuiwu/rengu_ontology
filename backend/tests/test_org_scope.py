"""组织（@org）行级规则：组织列按本体 + 对象类型配置；没配的对象类型 fail-closed。

夹具复用 test_object_row_access 的示例表；组织列选哪一列只是测试数据，规则本身不认识任何业务字段。
"""

from sqlalchemy import select

from app.common.auth import resolve_user
from app.modules.static.models import ObjectStatus, ObjectType
from tests import test_object_row_access as row_access

ADMIN = {"X-Demo-As": "U010245"}
facts = row_access.facts
grant, query = row_access.grant, row_access.query
URL = "/api/admin/v1/row-scope/org-columns"


async def count(session, user, table):
    return await query(session, await resolve_user(session, user.code), f"select count(*) from {table}")


async def test_org_columns_config_validation_and_gaps(client, facts):
    put = await client.put(URL, json={"columns": {"store": "big_region"}}, headers=ADMIN)
    assert put.status_code == 200, put.text
    items = {i["objectTypeCode"]: i for i in put.json()["items"]}
    assert items["store"]["field"] == "big_region" and "big_region" in items["store"]["candidates"]
    assert "store" not in put.json()["gaps"] and "order" in put.json()["gaps"]
    bad = await client.put(URL, json={"columns": {"store": "no_such_column"}}, headers=ADMIN)
    assert bad.status_code == 422 and bad.json()["code"] == "ORG_COLUMN_INVALID", bad.text
    assert (await client.get(URL, headers=ADMIN)).json()["items"] == put.json()["items"]
    viewer = {"X-Demo-As": "U010006"}
    assert (await client.put(URL, json={"columns": {}}, headers=viewer)).status_code == 403


async def test_org_rule_filters_configured_table_and_denies_unconfigured(client, facts):
    session, user = facts
    assert (
        await client.put(URL, json={"columns": {"store": "big_region"}}, headers=ADMIN)
    ).status_code == 200
    await grant(
        facts, "T-ORG", [("门店", "@org", ["华东", "华北"]), ("订单", "@org", ["华东"])], default_deny=True
    )
    assert await count(session, user, "store") == 2
    # 订单没配组织列：@org 范围不能变成「不限制」，只能拒绝。
    assert await count(session, user, "biz_order") == 0


async def test_org_rule_offered_in_scope_props_only_when_configured(client, facts):
    session, _user = facts
    store = await session.scalar(select(ObjectType).where(ObjectType.code == "store"))
    store.status = ObjectStatus.published
    await session.flush()
    role = await grant(facts, "T-ORG-API", default_deny=True)
    body = {"objType": "门店", "objectTypeCode": "store", "prop": "@org", "op": "∈", "values": ["华东"]}
    before = await client.post(f"/api/admin/v1/roles/{role.code}/scopes", json=body, headers=ADMIN)
    assert before.status_code == 422, before.text
    assert (
        await client.put(URL, json={"columns": {"store": "big_region"}}, headers=ADMIN)
    ).status_code == 200
    meta = await client.get("/api/admin/v1/roles/meta", headers=ADMIN)
    org = [p for p in meta.json()["scopeProps"] if p["prop"] == "@org"]
    assert [p["objectTypeCode"] for p in org] == ["store"] and "华东" in org[0]["values"], org
    after = await client.post(f"/api/admin/v1/roles/{role.code}/scopes", json=body, headers=ADMIN)
    assert after.status_code in (200, 201), after.text
