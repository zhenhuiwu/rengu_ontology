"""规则与生命周期页不能在取回全部业务行后才切片。"""

import re

from sqlalchemy import event, select

from app.modules.dynamic.rules_models import Rule
from app.modules.static.models import ChangeRequest, CRStatus
from tests.dynamic_support import API, dyn  # noqa: F401


async def test_rules_hits_and_changes_are_sql_pages(dyn, client, engine):  # noqa: F811
    statements = []

    def captured(_conn, _cursor, sql, _parameters, _context, _many):
        if sql.lstrip().upper().startswith("SELECT"):
            statements.append(sql)

    event.listen(engine.sync_engine, "before_cursor_execute", captured)
    try:
        for path, table in (
            ("/rules", "rule"),
            ("/rules/R-007/hits?range=30d", "rule_hit"),
            ("/object-types/order/lifecycle/changes", "lifecycle_change"),
        ):
            sep = "&" if "?" in path else "?"
            first = await client.get(API + path + sep + "page=1&size=2")
            statements.clear()
            second = await client.get(API + path + sep + "page=2&size=2")
            assert first.status_code == second.status_code == 200, second.text
            one, two = first.json(), second.json()
            assert one["total"] == two["total"] > 2 and two["page"] == 2
            assert not ({row["id"] for row in one["items"]} & {row["id"] for row in two["items"]})
            loads = [
                sql
                for sql in statements
                if re.search(rf"\bFROM {table}\s", sql) and "count(" not in sql.lower()
            ]
            assert loads and all("LIMIT" in sql and "OFFSET" in sql for sql in loads), loads
            last = (await client.get(API + path + sep + "page=999&size=2")).json()
            assert last["page"] == (last["total"] + 1) // 2 and last["items"]
        rules = (await client.get(API + "/rules", params={"size": 200})).json()["items"]
        for status in ("已发布", "草稿中", "已停用"):
            expected = [row["id"] for row in rules if row["displayStatus"] == status]
            result = (await client.get(API + "/rules", params={"status": status, "size": 200})).json()
            assert result["total"] == len(expected)
            assert [row["id"] for row in result["items"]] == expected
        rule = await dyn.scalar(select(Rule).order_by(Rule.id).limit(1))
        rule.name = "真实%_名称"
        await dyn.flush()
        found = (await client.get(API + "/rules", params={"kw": "%_"})).json()
        assert found["total"] == 1 and found["items"][0]["id"] == rule.code
        missing = (await client.get(API + "/rules", params={"kw": "空查询结果", "page": 99})).json()
        assert missing["items"] == [] and missing["total"] == 0 and missing["page"] == 1
        invalid = await client.get(API + "/rules", params={"kw": "nul\x00"})
        assert invalid.status_code == 422
        change = await dyn.scalar(select(ChangeRequest).where(ChangeRequest.code == "CR-0011"))
        change.status = CRStatus.in_review
        await dyn.flush()
        detail = (await client.get(API + "/rules/R-007")).json()
        assert detail["displayStatus"] == "审核中"
        review = (await client.get(API + "/rules", params={"status": "审核中", "size": 200})).json()
        assert "R-007" in {row["id"] for row in review["items"]}
        draft = (await client.get(API + "/rules", params={"status": "草稿中", "size": 200})).json()
        assert "R-007" not in {row["id"] for row in draft["items"]}
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", captured)
