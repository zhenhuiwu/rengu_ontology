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


async def test_object_dynamic_summary_api_key(dyn, client):  # noqa: F811
    """API 密钥：objects:read 能调摘要但动态段 missing-scope；objects:read + dynamic:read 看到各段；
    没有 objects:read 的密钥 403；dynamic:read 也放行告警列表等只读接口。"""
    changes = await client.get(f"{API}/object-types/order/lifecycle/changes", params={"size": 1})
    obj = changes.json()["items"][0]
    url = f"{API}/object-types/order/objects/{obj['objId']}/dynamic-summary"
    admin = {"X-Demo-As": "U010245"}
    tokens = {}
    for name, scopes in {
        "obj": ["objects:read"],
        "dyn": ["objects:read", "dynamic:read"],
        "meta": ["meta:read"],
    }.items():
        body = {
            "name": "k-summary-" + name,
            "env": "测试",
            "scopes": scopes,
            "binding": {"kind": "用户", "userId": "U010245"},
        }
        r = await client.post("/api/admin/v1/api-keys", json=body, headers=admin)
        assert r.status_code == 201, r.text
        tokens[name] = {"Authorization": f"Bearer {r.json()['secret']}"}
    sections = ("alerts", "ruleHits", "lifecycle", "actionRuns")
    only_obj = await client.get(url, headers=tokens["obj"])
    assert only_obj.status_code == 200, only_obj.text
    assert only_obj.json()["id"] == obj["objId"]
    assert all(only_obj.json()[k]["reason"] == "missing-scope" for k in sections)
    full = await client.get(url, headers=tokens["dyn"])
    assert full.status_code == 200, full.text
    assert all(full.json()[k]["available"] is True for k in sections), full.json()
    assert (await client.get(url, headers=tokens["meta"])).status_code == 403
    assert (await client.get(f"{API}/alerts", headers=tokens["dyn"])).status_code == 200
    assert (await client.get(f"{API}/alerts", headers=tokens["obj"])).status_code == 403
