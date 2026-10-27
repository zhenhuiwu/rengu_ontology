"""对象动态摘要：与各列表接口同一行级范围 / 输出；limit；不可见 = 404。"""

from tests.dynamic_support import API, dyn, err  # noqa: F401
from tests.test_use_ops_runtime import alert_runtime, runtime  # noqa: F401


async def test_object_dynamic_summary(dyn, client):  # noqa: F811
    lc_changes = (await client.get(f"{API}/object-types/order/lifecycle/changes", params={"size": 1})).json()
    assert lc_changes["items"], "种子应有订单生命周期变化"
    obj = lc_changes["items"][0]["objId"]
    url = f"{API}/object-types/order/objects/{obj}/dynamic-summary"
    r = await client.get(url, params={"limit": 2})
    assert r.status_code == 200, r.text
    s = r.json()
    assert s["objKind"] == "order" and s["id"] == obj and s["limit"] == 2 and s["objLabel"]

    alerts = (
        await client.get(f"{API}/alerts", params={"status": "all", "objKind": "order", "size": 100})
    ).json()
    mine = [a for a in alerts["items"] if a["objId"] == obj and a["status"] in ("待处理", "处理中")]
    assert s["alerts"]["openCount"] == len(mine) and len(s["alerts"]["latest"]) == min(2, len(mine))
    assert all(a["objId"] == obj for a in s["alerts"]["latest"])

    runs = (await client.get(f"{API}/action-runs", params={"objKind": "order", "objId": obj})).json()
    assert s["actionRuns"]["total"] == runs["total"]
    assert [x["id"] for x in s["actionRuns"]["latest"]] == [x["id"] for x in runs["items"][:2]]

    assert all(h["objKind"] == "order" and h["objId"] == obj for h in s["ruleHits"]["latest"])
    assert len(s["ruleHits"]["latest"]) == min(2, s["ruleHits"]["total"])

    lc = s["lifecycle"]
    assert lc["defined"] is True and lc["lifecycleId"] == "order" and lc["current"]["state"]
    assert lc["current"]["source"] in ("object", "lastChange")
    assert lc["current"]["lastChangeTo"] == lc["changes"][0]["to"]
    assert lc["changeTotal"] >= 1 and all(c["objId"] == obj for c in lc["changes"])
    assert all(c["trId"] is None for c in lc["illegal"]) and lc["illegalTotal"] >= len(lc["illegal"])

    err(await client.get(f"{API}/object-types/order/objects/NO-SUCH-OBJ/dynamic-summary"), 404, "NOT_FOUND")
    err(await client.get(f"{API}/object-types/no_such_type/objects/{obj}/dynamic-summary"), 404, "NOT_FOUND")
    err(await client.get(url, params={"limit": 0}), 422, "VALIDATION_FAILED")
    err(await client.get(url, params={"limit": 51}), 422, "VALIDATION_FAILED")
