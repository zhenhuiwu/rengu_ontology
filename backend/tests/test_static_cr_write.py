# ruff: noqa: E501
"""变更单写接口（handoff/static.md「变更单写接口契约」）：发起 / 加入 / 放弃 / 保存草稿 / 读回 / 校验 / 改动 / 改单头。

目标通用 kind + 业务编码（静态、动态、Palantir 共用）；种子 cr_target = mock DRAFT_TARGETS。
"""

import pytest
from sqlalchemy import func, select

from app.modules.static.models import ChangeRequest, CrCheck, CrItem, CrTarget
from app.seeds.loader import run_seeds

P = "/api/ontology/v1"
WANG = {"X-Demo-As": "U010001"}  # 王敏（本体管理员）
CHEN = {"X-Demo-As": "U010002"}  # 陈立军
LIU = {"X-Demo-As": "U010004"}  # 刘婷

# mock/releases.ts DRAFT_TARGETS
DRAFT_TARGETS = {
    "CR-0014": {"action": ["adjust_region_budget"]},
    "CR-0013": {"event": ["store_price_changed"]},
    "CR-0012": {"object": ["member"], "link": ["member_holds_cards"]},
    "CR-0011": {"metric": ["gross_margin"], "rule": ["R-007"], "knowledge": ["K-018", "K-024"]},
    "CR-0009": {"object": ["store"], "dimension": ["format"]},
}


@pytest.fixture
async def seeded(session):
    from app.modules.static.sections import BUILDERS
    from app.modules.versioning.sections import isolated_sections

    with isolated_sections(*BUILDERS):
        await run_seeds(session, ["admin", "versioning", "static"])
        await session.commit()
        yield session


async def create(client, who=LIU, **body):
    body.setdefault("title", "新增指标客单价")
    r = await client.post(f"{P}/change-requests", json=body, headers=who)
    assert r.status_code == 201, r.text
    return r.json()


async def put(client, cr, target, section, rows, who=LIU, **kw):
    return await client.put(
        f"{P}/change-requests/{cr}/items",
        json={"target": target, "section": section, "rows": rows, **kw},
        headers=who,
    )


async def one(client, cr):
    r = await client.get(f"{P}/change-requests/{cr}", headers=WANG)
    assert r.status_code == 200, r.text
    return r.json()


async def test_seed_targets_match_mock(client, seeded):
    r = await client.get(f"{P}/change-requests/draft-targets", headers=WANG)
    assert r.status_code == 200 and r.json() == DRAFT_TARGETS
    d = await one(client, "CR-0011")
    assert d["targets"][0] == {"kind": "metric", "id": "gross_margin", "isNew": False}
    assert isinstance(d["lockVersion"], int)
    assert (await client.get(f"{P}/change-requests/draft-targets?status=已发布", headers=WANG)).json() == {}


async def test_create(client, seeded):
    d = await create(
        client,
        title="新增指标“客单价（不含税）”",
        reason="周会要看",
        target={"kind": "metric", "id": "atv_net", "isNew": True, "name": "客单价（不含税）"},
    )
    assert d["id"] == "CR-0017"
    assert (d["status"], d["owner"], d["reviewer"], d["level"], d["changes"]) == (
        "草稿",
        "刘婷",
        "王敏",
        "一般",
        0,
    )
    assert d["targets"] == [{"kind": "metric", "id": "atv_net", "isNew": True, "name": "客单价（不含税）"}]
    assert d["detail"]["touches"] == ["metric:atv_net"] and d["detail"]["edit"] == {
        "kind": "metric",
        "id": "atv_net",
    }
    assert d["detail"]["diffs"] == [] and d["detail"]["times"]["created"] == "2026-10-25 15:42"
    assert d["events"][0]["text"] == "刘婷 创建变更单"
    # 列表里有新单子（单号倒序第一个）；草稿登记里有它
    rows = (await client.get(f"{P}/change-requests", headers=WANG)).json()
    assert rows[0]["id"] == "CR-0017"
    dt = (await client.get(f"{P}/change-requests/draft-targets", headers=WANG)).json()
    assert dt["CR-0017"] == {"metric": ["atv_net"]}
    # 王敏发起：默认审核人留空；不带目标也能建
    d2 = await create(client, who=WANG, title="只建单头")
    assert d2["id"] == "CR-0018" and d2["reviewer"] == "" and d2["targets"] == []
    d3 = await create(client, who=WANG, title="指定审核人", reviewer="陈立军", level="重大")
    assert (d3["reviewer"], d3["level"]) == ("陈立军", "重大")


@pytest.mark.parametrize(
    "body",
    [
        {"title": ""},
        {"title": "x" * 41},
        {"title": "a", "level": "大"},
        {"title": "a", "target": {"kind": "nope", "id": "x"}},
        {},
    ],
)
async def test_create_validation(client, seeded, body):
    r = await client.post(f"{P}/change-requests", json=body, headers=LIU)
    assert r.status_code == 422, r.text


async def test_target_in_other_cr(client, seeded):
    r = await client.post(
        f"{P}/change-requests",
        json={"title": "a", "target": {"kind": "metric", "id": "gross_margin"}},
        headers=LIU,
    )
    assert r.status_code == 409 and r.json()["code"] == "TARGET_IN_OTHER_CR"
    assert r.json()["details"] == {"crId": "CR-0011", "kind": "metric", "id": "gross_margin"}
    r = await client.post(
        f"{P}/change-requests/CR-0014/targets", json={"kind": "rule", "id": "R-007"}, headers=LIU
    )
    assert r.status_code == 409 and r.json()["details"]["crId"] == "CR-0011"
    r = await put(client, "CR-0014", {"kind": "object", "id": "store"}, "properties", [])
    assert r.status_code == 409 and r.json()["details"]["crId"] == "CR-0009"
    # 同一个 id、不同 kind 不冲突；加入是幂等的
    for _ in range(2):
        r = await client.post(
            f"{P}/change-requests/CR-0014/targets", json={"kind": "metric", "id": "R-007"}, headers=LIU
        )
        assert r.status_code == 200, r.text
    assert [t["id"] for t in r.json()["targets"]] == ["adjust_region_budget", "R-007"]
    assert "metric:R-007" in r.json()["detail"]["touches"]
    # 已发布的单子不再占用目标：CR-0007 里的定义可以进新单
    assert (await create(client, target={"kind": "metric", "id": "gmv"}))["targets"][0]["id"] == "gmv"


async def test_put_items_replace_and_read_back(client, seeded):
    cr = (await create(client, target={"kind": "object", "id": "product"}))["id"]
    t = {"kind": "object", "id": "product", "name": "商品"}
    rows = [
        {
            "key": "promo_price",
            "op": "add",
            "after": {"name": "促销价", "type": "decimal"},
            "type": "属性",
            "field": "促销价 promo_price",
            "afterText": "decimal · 元",
        },
        {"key": "brand", "op": "update", "before": {"name": "品牌"}, "after": {"name": "品牌名称"}},
    ]
    r = await put(client, cr, t, "properties", rows, lockVersion=(await one(client, cr))["lockVersion"])
    assert r.status_code == 200, r.text
    out = r.json()
    assert (out["id"], out["changes"]) == (cr, 2)
    assert [i["key"] for i in out["items"]] == ["promo_price", "brand"]
    assert out["items"][0] == {
        "target": {"kind": "object", "id": "product"}, "section": "properties", "key": "promo_price", "op": "add",
        "before": None, "after": {"name": "促销价", "type": "decimal"}, "type": "属性", "field": "促销价 promo_price",
        "updatedBy": "刘婷", "updatedAt": "2026-10-25 15:42",
    }  # fmt: skip
    lv = out["lockVersion"]
    # 旧版本号 → VERSION_CONFLICT
    r = await put(client, cr, t, "properties", rows, lockVersion=lv - 1)
    assert r.status_code == 409 and r.json()["code"] == "VERSION_CONFLICT"
    # 另一段 + 整段替换
    r = await put(
        client,
        cr,
        t,
        "mapping",
        [{"key": "promo_price", "op": "add", "after": "t_goods.promo_price"}],
        lockVersion=lv,
    )
    assert r.json()["changes"] == 3 and r.json()["lockVersion"] == lv + 1
    r = await put(client, cr, t, "properties", rows[:1])
    assert r.json()["changes"] == 2
    items = (await client.get(f"{P}/change-requests/{cr}/items", headers=WANG)).json()
    assert [(i["section"], i["key"]) for i in items] == [
        ("mapping", "promo_price"),
        ("properties", "promo_price"),
    ]
    only = (
        await client.get(
            f"{P}/change-requests/{cr}/items?kind=object&targetId=product&section=mapping", headers=WANG
        )
    ).json()
    assert [i["after"] for i in only] == ["t_goods.promo_price"]
    # 详情 / 列表 changes、改动列表
    d = await one(client, cr)
    assert d["changes"] == 2 and d["targets"][0]["name"] == "商品"
    diffs = d["detail"]["diffs"]
    assert [(x["type"], x["target"], x["field"], x["kind"], x["before"], x["after"]) for x in diffs] == [
        ("对象类型", "商品", "promo_price", "新增", "", "t_goods.promo_price"),
        ("属性", "商品", "促销价 promo_price", "新增", "", "decimal · 元"),
    ]
    assert all(x["key"].startswith("i") for x in diffs)
    page = (await client.get(f"{P}/change-requests/{cr}/diffs", headers=WANG)).json()
    assert (page["total"], page["page"], page["size"], page["items"]) == (2, 1, 10, diffs)
    # rows=[] 清空一段
    assert (await put(client, cr, t, "mapping", [])).json()["changes"] == 1


async def test_put_items_validation(client, seeded):
    cr = (await create(client))["id"]
    t = {"kind": "rule", "id": "R-099", "isNew": True}
    r = await put(client, cr, t, "conds", [{"key": "1", "op": "add"}, {"key": "1", "op": "update"}])
    assert r.status_code == 422 and "key 重复" in r.text
    assert (await put(client, cr, t, "conds", [{"key": "1", "op": "move"}])).status_code == 422
    assert (await put(client, cr, t, "", [])).status_code == 422
    assert (await put(client, "CR-9999", t, "conds", [])).status_code == 404
    # 目标没登记时自动登记（isNew 取请求）
    r = await put(client, cr, t, "conds", [{"key": "1", "op": "add", "after": {"expr": "x > 1"}}])
    assert r.status_code == 200
    assert (await one(client, cr))["targets"] == [{"kind": "rule", "id": "R-099", "isNew": True}]


async def test_seed_cr_keeps_mock_diffs(client, seeded):
    before = await one(client, "CR-0011")
    assert before["changes"] == 6 and len(before["detail"]["diffs"]) == 6
    r = await put(
        client,
        "CR-0011",
        {"kind": "metric", "id": "gross_margin"},
        "def",
        [{"key": "formula", "op": "update", "before": "a", "after": "b"}],
    )
    assert r.json()["changes"] == 7
    d = await one(client, "CR-0011")
    assert d["detail"]["diffs"][:6] == before["detail"]["diffs"] and d["detail"]["diffs"][6]["type"] == "指标"
    full = {x["id"]: x for x in (await client.get(f"{P}/change-requests?full=true", headers=WANG)).json()}
    assert len(full["CR-0011"]["detail"]["diffs"]) == 7 and full["CR-0011"]["changes"] == 7
    # 库里 detail.diffs 不变（草稿行在 cr_item）
    row = await seeded.scalar(select(ChangeRequest).where(ChangeRequest.code == "CR-0011"))
    assert len(row.detail["diffs"]) == 6


async def test_discard_target(client, seeded):
    cr = (await create(client, target={"kind": "link", "id": "l_new", "isNew": True}))["id"]
    await put(
        client,
        cr,
        {"kind": "link", "id": "l_new"},
        "def",
        [{"key": "a", "op": "add", "after": 1}, {"key": "b", "op": "add", "after": 2}],
    )
    await put(
        client, cr, {"kind": "knowledge", "id": "K-099"}, "def", [{"key": "a", "op": "add", "after": 1}]
    )
    assert (await one(client, cr))["changes"] == 3
    r = await client.delete(f"{P}/change-requests/{cr}/targets/link/l_new", headers=LIU)
    assert r.status_code == 204
    d = await one(client, cr)
    assert d["changes"] == 1 and d["targets"] == [{"kind": "knowledge", "id": "K-099", "isNew": False}]
    assert d["detail"]["touches"] == ["knowledge:K-099"] and d["detail"]["edit"] == {
        "kind": "knowledge",
        "id": "K-099",
    }
    assert (
        await client.delete(f"{P}/change-requests/{cr}/targets/link/l_new", headers=LIU)
    ).status_code == 404
    assert (await client.delete(f"{P}/change-requests/{cr}/targets/bogus/x", headers=LIU)).status_code == 422
    # 种子单子放弃一个目标：DRAFT_TARGETS 里少了它
    assert (
        await client.delete(f"{P}/change-requests/CR-0011/targets/knowledge/K-024", headers=LIU)
    ).status_code == 204
    dt = (await client.get(f"{P}/change-requests/draft-targets", headers=WANG)).json()
    assert dt["CR-0011"] == {"metric": ["gross_margin"], "rule": ["R-007"], "knowledge": ["K-018"]}


async def test_locked_after_submit(client, seeded):
    cr = (await create(client))["id"]
    t = {"kind": "metric", "id": "m_x", "isNew": True}
    await put(client, cr, t, "def", [{"key": "name", "op": "add", "after": "x"}])
    r = await client.post(f"{P}/change-requests/{cr}:submit", headers=LIU)
    assert r.status_code == 200 and r.json()["status"] == "审核中"
    for res in (
        await put(client, cr, t, "def", []),
        await client.delete(f"{P}/change-requests/{cr}/targets/metric/m_x", headers=LIU),
        await client.patch(f"{P}/change-requests/{cr}", json={"title": "x"}, headers=LIU),
    ):
        assert res.status_code == 409 and res.json()["code"] == "CR_LOCKED", res.text
        assert res.json()["details"]["status"] == "审核中"
    r = await client.post(
        f"{P}/change-requests/{cr}/targets", json={"kind": "metric", "id": "m_y"}, headers=LIU
    )
    assert r.status_code == 409 and r.json()["code"] == "CR_LOCKED"
    # 审核中不能重新校验（只限草稿 / 已驳回）
    r = await client.post(f"{P}/change-requests/{cr}:validate", headers=LIU)
    assert r.status_code == 409 and r.json()["code"] == "CR_STATE"
    # 审核中的单子仍占用目标
    r = await client.post(f"{P}/change-requests", json={"title": "a", "target": t}, headers=CHEN)
    assert r.json()["code"] == "TARGET_IN_OTHER_CR"
    # 审核人通过
    r = await client.post(f"{P}/change-requests/{cr}:approve", headers=WANG)
    assert r.status_code == 200 and r.json()["status"] == "已通过"


async def test_submit_needs_reviewer_and_patch(client, seeded):
    cr = (await create(client, who=WANG, title="王敏自己的单子"))["id"]
    await put(
        client,
        cr,
        {"kind": "dimension", "id": "d_x", "isNew": True},
        "values",
        [{"key": "a", "op": "add", "after": "A"}],
        who=WANG,
    )
    r = await client.post(f"{P}/change-requests/{cr}:submit", headers=WANG)
    assert r.status_code == 409 and r.json()["code"] == "CR_NO_REVIEWER"
    lv = (await one(client, cr))["lockVersion"]
    r = await client.patch(
        f"{P}/change-requests/{cr}",
        json={"reviewer": "陈立军", "title": "改个标题", "lockVersion": lv - 1},
        headers=WANG,
    )
    assert r.status_code == 409 and r.json()["code"] == "VERSION_CONFLICT"
    r = await client.patch(
        f"{P}/change-requests/{cr}",
        json={"reviewer": "陈立军", "title": "改个标题", "migration": "无", "lockVersion": lv},
        headers=WANG,
    )
    assert r.status_code == 200
    assert (r.json()["reviewer"], r.json()["title"], r.json()["detail"]["migration"]) == (
        "陈立军",
        "改个标题",
        "无",
    )
    assert r.json()["lockVersion"] == lv + 1
    r = await client.post(f"{P}/change-requests/{cr}:submit", headers=WANG)
    assert r.status_code == 200 and r.json()["status"] == "审核中"


async def test_validate(client, seeded):
    cr = (await create(client, level="重大", target={"kind": "object", "id": "product"}))["id"]
    await put(client, cr, {"kind": "object", "id": "product"}, "properties", [
        {"key": "old_code", "op": "delete", "before": {"name": "旧编码"}},
        {"key": "promo", "op": "add", "after": {"name": "促销", "desc": ""}},
    ])  # fmt: skip
    r = await client.post(f"{P}/change-requests/{cr}:validate", headers=LIU)
    assert r.status_code == 200, r.text
    v = r.json()
    by = {c["code"]: c for c in v["checks"]}
    assert [c["code"] for c in v["checks"]] == [f"V{n}" for n in range(1, 18)]
    assert by["V7"]["result"] == "阻断" and "product.old_code" in by["V7"]["note"]
    assert by["V11"]["result"] == "警告" and by["V15"]["result"] == "警告"
    assert by["V3"]["result"] == "不适用" and by["V16"]["result"] == "通过"
    assert by["V12"]["note"] == "改动 2 处，涉及 1 个定义"
    assert (
        v["blocked"] is True
        and v["summary"].startswith("阻断 1 · 警告 2")
        and v["checkedAt"] == "2026-10-25 15:42"
    )
    d = await one(client, cr)
    assert d["detail"]["checks"] == v["checks"] and d["detail"]["times"]["checked"] == "2026-10-25 15:42"
    assert d["events"][0]["text"] == "系统 完成发布校验"
    n = await seeded.scalar(
        select(func.count())
        .select_from(CrCheck)
        .where(CrCheck.cr_id == select(ChangeRequest.id).where(ChangeRequest.code == cr).scalar_subquery())
    )
    assert n == 17
    # 有阻断不能提交；改掉删除 + 写迁移方式后通过
    assert (await client.post(f"{P}/change-requests/{cr}:submit", headers=LIU)).json()["code"] == "CR_BLOCKED"
    await put(
        client,
        cr,
        {"kind": "object", "id": "product"},
        "properties",
        [{"key": "promo", "op": "add", "after": {"name": "促销", "desc": "促销价"}}],
    )
    await client.patch(f"{P}/change-requests/{cr}", json={"migration": "旧页面保留"}, headers=LIU)
    v = (await client.post(f"{P}/change-requests/{cr}:validate", headers=LIU)).json()
    assert not v["blocked"] and {c["result"] for c in v["checks"] if c["code"] in ("V7", "V11", "V15")} == {
        "通过"
    }
    assert (await seeded.scalar(select(func.count()).select_from(CrCheck))) == 17  # 重写，不累加
    assert (await client.post(f"{P}/change-requests/{cr}:submit", headers=LIU)).status_code == 200


async def test_validate_seed_and_published(client, seeded):
    v = (await client.post(f"{P}/change-requests/CR-0011:validate", headers=LIU)).json()
    by = {c["code"]: c for c in v["checks"]}
    assert by["V5"]["result"] == "阻断" and by["V14"]["result"] == "阻断" and v["blocked"]  # 种子阻断不洗掉
    assert by["V3"]["result"] == "不适用"
    v = (await client.post(f"{P}/change-requests/CR-0011:validate", headers=LIU)).json()
    assert {c["code"]: c["result"] for c in v["checks"]}["V5"] == "阻断"  # 再校验仍保留种子基线
    r = await client.post(f"{P}/change-requests/CR-0007:validate", headers=LIU)
    assert r.status_code == 409 and r.json()["code"] == "CR_STATE"
    assert (await client.post(f"{P}/change-requests/CR-9999:validate", headers=LIU)).status_code == 404


async def test_delete_cascades(client, seeded):
    cr = (await create(client))["id"]
    await put(
        client,
        cr,
        {"kind": "event", "id": "e_x", "isNew": True},
        "payload",
        [{"key": "a", "op": "add", "after": 1}],
    )
    await client.post(f"{P}/change-requests/{cr}:validate", headers=LIU)
    assert (await client.delete(f"{P}/change-requests/{cr}", headers=LIU)).status_code == 204
    for m in (CrItem, CrCheck):
        assert await seeded.scalar(select(func.count()).select_from(m)) == 0
    assert (
        await seeded.scalar(select(func.count()).select_from(CrTarget).where(CrTarget.target_id == "e_x"))
        == 0
    )


async def test_readonly_and_unauth(client, seeded):
    for q in ("?ver=v2026.10.2", "?previewAs=U010002"):
        r = await client.post(f"{P}/change-requests{q}", json={"title": "a"}, headers=LIU)
        assert r.status_code == 403 and r.json()["code"] == "READONLY", r.text
    r = await client.put(
        f"{P}/change-requests/CR-0014/items?ver=v2026.10.2",
        json={"target": {"kind": "action", "id": "x"}, "section": "s", "rows": []},
        headers=LIU,
    )
    assert r.status_code == 403 and r.json()["code"] == "READONLY"
    r = await client.post(f"{P}/change-requests", json={"title": "a"}, headers={"X-Demo-As": "U999999"})
    assert r.status_code == 401


async def test_qa_feedback_to_synonym_knowledge_target(client, seeded):
    """qa T15 转同义词：建单直接以业务知识为目标，同义词作为 synonyms 段的一行 add；新词用 isNew。"""
    d = await create(
        client, title="补同义词：复购", target={"kind": "knowledge", "id": "K-001", "name": "复购率"}
    )
    assert d["status"] == "草稿" and d["targets"] == [
        {"kind": "knowledge", "id": "K-001", "isNew": False, "name": "复购率"}
    ]
    r = await put(
        client,
        d["id"],
        {"kind": "knowledge", "id": "K-001"},
        "synonyms",
        [{"key": "回头率", "op": "add", "after": "回头率"}],
    )
    assert r.status_code == 200, r.text
    assert r.json()["changes"] == 1
    # 新词：目标 isNew，加入已有草稿
    r = await client.post(
        f"{P}/change-requests/{d['id']}/targets",
        json={"kind": "knowledge", "id": "K-NEW1", "isNew": True},
        headers=LIU,
    )
    assert r.status_code == 200, r.text
    # 别的单子已占用的词（CR-0011 持有 K-018）→ 409，qa 可据 details.crId 改为加入
    r = await client.post(
        f"{P}/change-requests",
        json={"title": "x", "target": {"kind": "knowledge", "id": "K-018"}},
        headers=LIU,
    )
    assert r.status_code == 409 and r.json()["code"] == "TARGET_IN_OTHER_CR"
    assert r.json()["details"]["crId"] == "CR-0011"
    # 下拉用中文状态
    assert (await client.get(f"{P}/change-requests?status=草稿", headers=LIU)).status_code == 200


DEL_ROW = [{"key": "phone", "op": "delete", "before": {"name": "phone"}}]


async def test_submit_runs_validation_without_prior_validate(client, seeded):
    """复现 1：新单子 checks 为空，直接删已发布属性就提交 → 服务端校验，409 CR_BLOCKED（V7）。"""
    cr = (await create(client, target={"kind": "object", "id": "product"}))["id"]
    assert (
        await put(client, cr, {"kind": "object", "id": "product"}, "properties", DEL_ROW)
    ).status_code == 200
    r = await client.post(f"{P}/change-requests/{cr}:submit", headers=LIU)
    assert r.status_code == 409 and r.json()["code"] == "CR_BLOCKED", r.text
    assert "V7" in r.json()["details"]["blocked"]
    assert (await one(client, cr))["status"] == "草稿"


async def test_put_after_passing_validate_resets_checks(client, seeded):
    """复现 2：校验通过后再保存一条删除行 → checks 作废（未校验），提交时重算 → 409 CR_BLOCKED。"""
    t = {"kind": "object", "id": "product"}
    cr = (await create(client, target=t))["id"]
    await put(
        client,
        cr,
        t,
        "properties",
        [{"key": "price", "op": "update", "after": {"name": "price", "desc": "售价"}}],
    )
    r = await client.post(f"{P}/change-requests/{cr}:validate", headers=LIU)
    assert r.json()["blocked"] is False
    assert len((await one(client, cr))["detail"]["checks"]) == 17
    await put(client, cr, t, "properties", DEL_ROW)
    d = await one(client, cr)
    assert d["detail"]["checks"] == [] and "checked" not in d["detail"]["times"]
    assert await seeded.scalar(select(func.count()).select_from(CrCheck).where(CrCheck.target_db == "")) == 0
    r = await client.post(f"{P}/change-requests/{cr}:submit", headers=LIU)
    assert r.status_code == 409 and r.json()["code"] == "CR_BLOCKED"
    # 加入新目标、放弃目标也作废
    await client.post(f"{P}/change-requests/{cr}:validate", headers=LIU)
    r = await client.post(
        f"{P}/change-requests/{cr}/targets", json={"kind": "metric", "id": "m_q"}, headers=LIU
    )
    assert r.json()["detail"]["checks"] == []
    await client.post(f"{P}/change-requests/{cr}:validate", headers=LIU)
    assert (
        await client.delete(f"{P}/change-requests/{cr}/targets/metric/m_q", headers=LIU)
    ).status_code == 204
    assert (await one(client, cr))["detail"]["checks"] == []


async def test_approve_runs_validation(client, seeded):
    """:approve 同样服务端重算：审核中被塞进删除行（模拟绕过）→ 409 CR_BLOCKED，仍是审核中。"""
    t = {"kind": "object", "id": "product"}
    cr = (await create(client, target=t))["id"]
    await put(
        client,
        cr,
        t,
        "properties",
        [{"key": "price", "op": "update", "after": {"name": "price", "desc": "x"}}],
    )
    assert (await client.post(f"{P}/change-requests/{cr}:submit", headers=LIU)).status_code == 200
    row = await seeded.scalar(select(ChangeRequest).where(ChangeRequest.code == cr))
    seeded.add(
        CrItem(
            cr_id=row.id,
            target_kind="object",
            target_id="product",
            section="properties",
            item_key="phone",
            op="delete",
            before={"name": "phone"},
            after=None,
            seq=99,
        )
    )
    await seeded.commit()
    r = await client.post(f"{P}/change-requests/{cr}:approve", headers=WANG)
    assert r.status_code == 409 and r.json()["code"] == "CR_BLOCKED", r.text
    assert (await one(client, cr))["status"] == "审核中"


async def test_lock_version_bumps_once(client, seeded):
    cr = (await create(client))["id"]
    lv = (await one(client, cr))["lockVersion"]
    r = await put(client, cr, {"kind": "object", "id": "product"}, "properties", DEL_ROW, lockVersion=lv)
    assert r.json()["lockVersion"] == lv + 1  # 新目标 + 写行 + 作废校验：一次
    r = await client.post(
        f"{P}/change-requests/{cr}/targets", json={"kind": "metric", "id": "m_q"}, headers=LIU
    )
    assert r.json()["lockVersion"] == lv + 2
    assert (
        await client.delete(f"{P}/change-requests/{cr}/targets/metric/m_q", headers=LIU)
    ).status_code == 204
    assert (await one(client, cr))["lockVersion"] == lv + 3
