"""T5 新建 / T6 认证 / T7 停用启用 / T8 同问法 / T19 高频问题设为认证问答（调优页联调）。"""

import pytest

from app.seeds.loader import run_seeds

B = "/api/qa/v1/tuning"


@pytest.fixture
async def qa(users, client):
    await run_seeds(users, ["qa"])
    await users.flush()
    return client


async def _summary(qa) -> dict:
    return (await qa.get(f"{B}/summary")).json()


async def test_create_then_certify(qa):
    plan = {"metric": "orders", "dims": ["渠道"], "time": "上周"}
    r = await qa.post(f"{B}/certs", json={"question": "上周各渠道有效订单数", "plan": plan})
    assert r.status_code == 201, r.text
    c = r.json()
    assert c["status"] == "待认证" and c["source"] == "手动新建" and "certifier" not in c
    assert c["planText"].startswith("有效订单数")
    # 重复（不区分大小写）→ DUPLICATE_KEY；指标不存在 → 422
    r = await qa.post(f"{B}/certs", json={"question": "上周各渠道有效订单数", "plan": plan})
    assert r.status_code == 409 and r.json()["code"] == "DUPLICATE_KEY"
    bad = {"question": "x", "plan": {"metric": "nope", "time": "上周"}}
    assert (await qa.post(f"{B}/certs", json=bad)).status_code == 422
    assert (await qa.post(f"{B}/certs", json={"question": "q" * 41, "plan": plan})).status_code == 422
    r = await qa.post(f"{B}/certs/{c['id']}:certify")
    assert r.status_code == 200 and r.json()["status"] == "已认证" and r.json()["certifier"]
    # 已认证不能再认证
    assert (await qa.post(f"{B}/certs/{c['id']}:certify")).status_code == 409
    # 新建在 待认证 排序位置之后，列表里能查到
    items = (await qa.get(f"{B}/certs", params={"kw": "各渠道有效订单"})).json()["items"]
    assert any(x["id"] == c["id"] for x in items)


async def test_certify_with_pending_cr_goes_recert(qa):
    plan = {"metric": "margin", "dims": [], "time": "上个月"}
    c = (await qa.post(f"{B}/certs", json={"question": "上个月整体毛利率", "plan": plan})).json()
    assert c["cr"] == "CR-0011"
    before = (await _summary(qa))["recertCount"]
    r = (await qa.post(f"{B}/certs/{c['id']}:certify")).json()
    assert r["status"] == "待重新认证" and "CR-0011 发布后需再次认证" in r["note"]
    assert (await _summary(qa))["recertCount"] == before + 1


async def test_toggle_and_variants(qa):
    cid = (await qa.get(f"{B}/certs", params={"status": "已认证", "size": 1})).json()["items"][0]["id"]
    r = await qa.patch(f"{B}/certs/{cid}", json={"enabled": False})
    assert r.status_code == 200 and r.json()["status"] == "已停用"
    assert (await qa.patch(f"{B}/certs/{cid}", json={"enabled": True})).json()["status"] == "已认证"
    old = (await qa.get(f"{B}/certs/{cid}")).json()["variants"]
    r = await qa.put(f"{B}/certs/{cid}/variants", json={"variants": [*old, "测试同问法甲"]})
    assert r.status_code == 200 and r.json()["variants"][-1] == "测试同问法甲"
    r = await qa.put(f"{B}/certs/{cid}/variants", json={"variants": old})
    assert r.json()["variants"] == old
    dup = await qa.put(f"{B}/certs/{cid}/variants", json={"variants": ["甲", "甲"]})
    assert dup.status_code == 409 and dup.json()["code"] == "DUPLICATE_KEY"
    # 别的认证问答已有的同问法也不能挂
    other = next(
        x
        for x in (await qa.get(f"{B}/certs", params={"size": 50})).json()["items"]
        if x["variants"] and x["id"] != cid
    )
    r = await qa.put(f"{B}/certs/{cid}/variants", json={"variants": [other["variants"][0]]})
    assert r.status_code == 409
    # 只读
    r = await qa.patch(f"{B}/certs/{cid}?ver=v2026.10.2", json={"enabled": False})
    assert r.status_code == 403 and r.json()["code"] == "READONLY"


async def test_freq_certify(qa):
    before = (await _summary(qa))["uncertFreq"]
    f = (await qa.get(f"{B}/freq", params={"cert": "未认证", "size": 1})).json()["items"][0]
    r = await qa.post(f"{B}/freq/{f['id']}:certify")
    assert r.status_code == 200, r.text
    c = r.json()
    assert c["source"] == "高频问题推荐" and c["hits"] == f["hits30"] and c["question"] == f["question"]
    assert c["status"] in ("已认证", "待重新认证")
    assert (await _summary(qa))["uncertFreq"] == before - 1
    items = (await qa.get(f"{B}/freq", params={"kw": f["question"]})).json()["items"]
    assert items[0]["certId"] == c["id"] and items[0]["certStatus"] == c["status"]
    assert (await qa.post(f"{B}/freq/{f['id']}:certify")).status_code == 409
