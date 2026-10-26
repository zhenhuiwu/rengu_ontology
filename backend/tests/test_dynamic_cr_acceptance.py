"""动态正式写链：真实作者/审核身份、静态CR、发布快照与生命周期放弃。"""

from sqlalchemy import select

from app.modules.dynamic.rules_models import Lifecycle, LifecycleDraft, Rule, RuleDraft
from app.modules.versioning.service import VersioningService
from tests.dynamic_support import API, dyn  # noqa: F401

AUTHOR = {"X-Demo-As": "U010004"}
ADMIN = {"X-Demo-As": "U010001"}


async def new_cr(client, title):
    response = await client.post(
        f"{API}/change-requests", json={"title": title, "reviewer": "王敏"}, headers=AUTHOR
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def test_rule_and_lifecycle_real_cr_approve_publish_without_skipped(client, dyn):  # noqa: F811
    cr = await new_cr(client, "动态正式联合验收")
    created = await client.post(
        f"{API}/rules", json={"cr": cr, "name": "真实创建规则", "objKind": "product"}, headers=AUTHOR
    )
    assert created.status_code == 201, created.text
    rule = created.json()
    saved = await client.put(
        f"{API}/rules/{rule['id']}/draft",
        json={"cr": cr, "autoStage": {"enabled": False, "actionTypeId": "", "confirmer": "", "mapping": []}},
        headers=AUTHOR,
    )
    assert saved.status_code == 200, saved.text
    lc = (await client.get(f"{API}/object-types/order/lifecycle", headers=AUTHOR)).json()
    edits = {
        "cr": cr,
        "addStates": [{"key": "验收终态", "name": "验收终态", "kind": "终态"}],
        "addTransitions": [
            {
                "id": "TA1",
                "from": lc["states"][0]["key"],
                "to": "验收终态",
                "trigger": "动作",
                "by": "真实配置动作",
            }
        ],
    }
    lifecycle = await client.put(f"{API}/object-types/order/lifecycle/draft", json=edits, headers=AUTHOR)
    assert lifecycle.status_code == 200, lifecycle.text
    items = (await client.get(f"{API}/change-requests/{cr}/items", headers=AUTHOR)).json()
    assert {i["section"] for i in items} >= {"config", "lc_states", "lc_transitions"}
    submitted = await client.post(f"{API}/change-requests/{cr}:submit", headers=AUTHOR)
    assert submitted.status_code == 200, submitted.text
    approved = await client.post(f"{API}/change-requests/{cr}:approve", headers=ADMIN)
    assert approved.status_code == 200, approved.text
    published = await client.post(f"{API}/releases", json={}, headers=ADMIN)
    assert published.status_code == 201, published.text
    assert published.json()["skipped"] == []
    row = await dyn.scalar(select(Rule).where(Rule.code == rule["id"]))
    assert row.version == 1 and row.status.value == "已发布"
    assert await dyn.scalar(select(RuleDraft.id).where(RuleDraft.rule_id == row.id)) is None
    snapshot = await VersioningService().get_snapshot(dyn, published.json()["ver"])
    assert snapshot["rules"][rule["id"]]["version"] == 1
    assert any(s["key"] == "验收终态" for s in snapshot["lifecycles"]["order"]["states"])
    assert any(t["id"] == "TA1" for t in snapshot["lifecycles"]["order"]["transitions"])
    # 实际整版回滚后再次发布，保留append-only历史并分配更高版本，不能撞回旧vN。
    rollback = await client.post(f"{API}/releases/{published.json()['ver']}:rollback", json={}, headers=ADMIN)
    assert rollback.status_code == 201, rollback.text
    discarded_cr = await new_cr(client, "回滚后放弃草稿")
    assert (
        await client.post(f"{API}/rules/{rule['id']}/draft", json={"cr": discarded_cr}, headers=AUTHOR)
    ).status_code == 200
    discarded = await client.delete(
        f"{API}/rules/{rule['id']}/draft", params={"cr": discarded_cr}, headers=AUTHOR
    )
    assert discarded.status_code == 204, discarded.text
    assert await dyn.scalar(select(Rule.id).where(Rule.code == rule["id"])) is not None
    second = await new_cr(client, "回滚后再次发布")
    reopened = await client.post(f"{API}/rules/{rule['id']}/draft", json={"cr": second}, headers=AUTHOR)
    assert reopened.status_code == 200, reopened.text
    assert reopened.json()["draft"]["version"] > 1
    saved_again = await client.put(
        f"{API}/rules/{rule['id']}/draft",
        json={
            "cr": second,
            "autoStage": {"enabled": False, "actionTypeId": "", "confirmer": "", "mapping": []},
        },
        headers=AUTHOR,
    )
    assert saved_again.status_code == 200, saved_again.text
    lc_again = await client.put(
        f"{API}/object-types/order/lifecycle/draft",
        json={"cr": second, "addStates": [{"key": "回滚后终态", "name": "回滚后终态", "kind": "终态"}]},
        headers=AUTHOR,
    )
    assert lc_again.status_code == 200, lc_again.text
    assert (await client.post(f"{API}/change-requests/{second}:submit", headers=AUTHOR)).status_code == 200
    assert (await client.post(f"{API}/change-requests/{second}:approve", headers=ADMIN)).status_code == 200
    republished = await client.post(f"{API}/releases", json={}, headers=ADMIN)
    assert republished.status_code == 201, republished.text
    next_snapshot = await VersioningService().get_snapshot(dyn, republished.json()["ver"])
    assert next_snapshot["rules"][rule["id"]]["version"] > 1
    assert next_snapshot["lifecycles"]["order"]["version"] > snapshot["lifecycles"]["order"]["version"]


async def test_static_discard_deletes_lifecycle_draft_and_does_not_resurrect(client, dyn):  # noqa: F811
    cr = await new_cr(client, "放弃生命周期")
    edits = {"cr": cr, "addStates": [{"key": "被放弃", "name": "被放弃", "kind": "终态"}]}
    saved = await client.put(f"{API}/object-types/order/lifecycle/draft", json=edits, headers=AUTHOR)
    assert saved.status_code == 200, saved.text
    lc = await dyn.scalar(select(Lifecycle).where(Lifecycle.code == "order"))
    assert await dyn.scalar(
        select(LifecycleDraft.id).where(LifecycleDraft.lifecycle_id == lc.id, LifecycleDraft.cr_code == cr)
    )
    dropped = await client.delete(f"{API}/change-requests/{cr}/targets/object/order", headers=AUTHOR)
    assert dropped.status_code == 204, dropped.text
    assert (
        await dyn.scalar(
            select(LifecycleDraft.id).where(
                LifecycleDraft.lifecycle_id == lc.id, LifecycleDraft.cr_code == cr
            )
        )
        is None
    )
    reopened = (
        await client.get(
            f"{API}/object-types/order/lifecycle", params={"view": "draft", "cr": cr}, headers=AUTHOR
        )
    ).json()
    assert reopened["draft"]["edits"]["addStates"] == []


async def test_r012_existing_fixed_value_mapping_saves_without_seed_whitelist(client, dyn):  # noqa: F811
    cr = await new_cr(client, "修订R012")
    opened = await client.post(f"{API}/rules/R-012/draft", json={"cr": cr}, headers=AUTHOR)
    assert opened.status_code == 200, opened.text
    draft = opened.json()["draft"]
    assert any(m["from"] == "固定值：仓库调拨" for m in draft["autoStage"]["mapping"])
    saved = await client.put(
        f"{API}/rules/R-012/draft", json={"cr": cr, "autoStage": draft["autoStage"]}, headers=AUTHOR
    )
    assert saved.status_code == 200, saved.text


async def test_lifecycle_invalid_columns_rejected_before_draft_write(client, dyn):  # noqa: F811
    from sqlalchemy import func

    cr = await new_cr(client, "生命周期边界")
    current = (await client.get(f"{API}/object-types/order/lifecycle", headers=AUTHOR)).json()
    first = current["states"][0]["key"]
    transition = {"id": "T-new", "from": first, "to": first, "trigger": "人工", "by": "验收"}
    before = await dyn.scalar(select(func.count()).select_from(LifecycleDraft))
    rules_before = await dyn.scalar(select(func.count()).select_from(Rule))
    unknown = await client.post(
        f"{API}/rules", json={"cr": cr, "name": "无效引用", "objKind": "not_registered"}, headers=AUTHOR
    )
    assert unknown.status_code == 422, unknown.text
    assert await dyn.scalar(select(func.count()).select_from(Rule)) == rules_before
    for extra in (
        {"addTransitions": [{**transition, "id": "t" * 17}]},
        {"addTransitions": [{**transition, "trigger": []}]},
        {"addTransitions": [{**transition, "by": "x" * 201}]},
        {"addTransitions": [{**transition, "actionId": "missing_action"}]},
        {"addTransitions": [{**transition, "eventId": "missing_event"}]},
        {"addStates": [{"key": first, "name": "编号重复", "kind": "终态"}]},
        {"addStates": [{"key": "bad", "name": "bad", "kind": []}]},
        {"addStates": [{"key": "bad", "name": "bad", "kind": "终态", "desc": []}]},
        {"addStates": [{"key": "bad", "name": "坏\x00状态", "kind": "终态"}]},
        {"editTransitions": {current["transitions"][0]["id"]: {"from": []}}},
    ):
        failed = await client.put(
            f"{API}/object-types/order/lifecycle/draft", json={"cr": cr, **extra}, headers=AUTHOR
        )
        assert failed.status_code == 422, failed.text
        assert await dyn.scalar(select(func.count()).select_from(LifecycleDraft)) == before
    import json

    invalid_jsonb = json.dumps(
        {"cr": cr, "addStates": [{"key": "nan", "name": "nan", "kind": "终态", "desc": float("nan")}]}
    )
    rejected = await client.put(
        f"{API}/object-types/order/lifecycle/draft",
        content=invalid_jsonb,
        headers={**AUTHOR, "Content-Type": "application/json"},
    )
    assert rejected.status_code == 422, rejected.text
    assert await dyn.scalar(select(func.count()).select_from(LifecycleDraft)) == before
