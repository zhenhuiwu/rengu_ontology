"""决策评估：库存告警 → 补货建议（只靠种子元数据），只建议不暂存，记录可查；API 密钥范围。"""

from tests.dynamic_support import API, dyn, err  # noqa: F401
from tests.test_use_ops_runtime import alert_runtime, runtime  # noqa: F401


async def test_decision_evaluate_replenish(dyn, client):  # noqa: F811
    alerts = (
        await client.get(f"{API}/alerts", params={"status": "all", "ruleId": "R-012", "size": 1})
    ).json()
    assert alerts["items"], "种子应有 R-012 告警"
    alert = alerts["items"][0]
    runs_before = (await client.get(f"{API}/action-runs", params={"size": 1})).json()["total"]

    r = await client.post(
        f"{API}/decisions:evaluate",
        json={"alertId": alert["id"], "caller": {"aiSessionId": "S-1", "aiRoundId": "R-1"}},
    )
    assert r.status_code == 200, r.text
    d = r.json()
    top = d["recommendations"][0]
    assert top["actionType"]["id"] == "request_replenish", d["recommendations"]
    assert any(x["kind"] == "alert" and x["ruleId"] == "R-012" for x in top["reasons"])
    assert top["params"]["alert"] == alert["id"] and top["params"]["product"] == alert["objId"]
    assert top["params"]["mode"] == "仓库调拨"
    assert top["dryRun"]["ran"] is True and "policy" in top and top["handoff"]["requiresHumanConfirmation"]
    assert d["caller"]["kind"] == "ai" and d["caller"]["aiRoundId"] == "R-1"
    assert (await client.get(f"{API}/action-runs", params={"size": 1})).json()["total"] == runs_before

    got = await client.get(f"{API}/decisions/{d['decisionId']}")
    assert got.status_code == 200 and got.json()["recommendations"] == d["recommendations"]
    listed = (await client.get(f"{API}/decisions", params={"aiSessionId": "S-1"})).json()
    assert [x["decisionId"] for x in listed["items"]] == [d["decisionId"]]
    assert listed["items"][0]["top"]["id"] == "request_replenish"
    err(await client.get(f"{API}/decisions/DEC-NOPE"), 404, "NOT_FOUND")
    err(await client.post(f"{API}/decisions:evaluate", json={"objectId": "x"}), 422, "VALIDATION_FAILED")

    by_obj = await client.post(
        f"{API}/decisions:evaluate",
        json={"objectTypeId": alert["objKind"], "objectId": alert["objId"], "dryRun": False},
    )
    assert by_obj.status_code == 200, by_obj.text
    assert by_obj.json()["recommendations"][0]["dryRun"]["ran"] is False


async def test_decision_api_key_scopes(dyn, client):  # noqa: F811
    alert = (await client.get(f"{API}/alerts", params={"status": "all", "ruleId": "R-012", "size": 1})).json()
    alert_id = alert["items"][0]["id"]
    admin = {"X-Demo-As": "U010245"}
    tokens = {}
    for name, scopes in {
        "dyn": ["objects:read", "dynamic:read"],
        "dry": ["objects:read", "dynamic:read", "actions:dry-run"],
        "obj": ["objects:read"],
    }.items():
        body = {
            "name": "k-decision-" + name,
            "env": "测试",
            "scopes": scopes,
            "binding": {"kind": "用户", "userId": "U010245"},
        }
        r = await client.post("/api/admin/v1/api-keys", json=body, headers=admin)
        assert r.status_code == 201, r.text
        tokens[name] = {"Authorization": f"Bearer {r.json()['secret']}"}
    url = f"{API}/decisions:evaluate"
    assert (await client.post(url, json={"alertId": alert_id}, headers=tokens["obj"])).status_code == 403
    err(await client.post(url, json={"alertId": alert_id}, headers=tokens["dyn"]), 403, "NO_PERMISSION")
    no_dry = await client.post(url, json={"alertId": alert_id, "dryRun": False}, headers=tokens["dyn"])
    assert no_dry.status_code == 200, no_dry.text
    d = no_dry.json()
    assert d["caller"]["apiKeyPrefix"] and d["caller"]["kind"] in ("user", "service_account")
    dry = await client.post(url, json={"alertId": alert_id}, headers=tokens["dry"])
    assert dry.status_code == 200, dry.text
    assert dry.json()["recommendations"][0]["dryRun"]["ran"] is True
