"""整版发布 / 回滚（Releases.vue 的「发布」「回滚」按钮）。

合并校验与 mock mergeCheck 一致、发布 / 回滚经 create_version、合并校验阻断、权限、只读。
"""

import pytest
from sqlalchemy import func, select

from app.core import clock
from app.modules.static.models import ChangeRequest, CrEvent, CRStatus
from app.modules.versioning.models import OntologyRelease
from app.modules.versioning.service import next_ver
from app.seeds.loader import run_seeds
from tests.function_access_support import grant_fixture_roles
from tests.helpers.workspace import bind_workspace_members

P = "/api/ontology/v1"
ADMIN = {"X-Demo-As": "U010001"}  # 王敏：本体管理员
OTHER = {"X-Demo-As": "U010002"}  # 陈立军：非本体管理员


@pytest.fixture
async def seeded(session):
    from app.modules.static.sections import BUILDERS
    from app.modules.versioning.sections import isolated_sections

    with isolated_sections(*BUILDERS):
        await run_seeds(session, ["admin", "versioning", "static"])
        await grant_fixture_roles(session)
        await bind_workspace_members(session)
        await session.commit()
        yield session


def expected_rows(next_version: str) -> list[dict]:
    """mock/changes.ts mergeCheck(已通过的 CR-0015、CR-0008) 的实跑结果。

    NEXT_VERSION 换成实际下一版。
    """
    return [
        {
            "code": "M1",
            "name": "同一定义只被一张单子改动",
            "result": "通过",
            "note": "2 张单子改动的定义互不重叠",
        },
        {
            "code": "V9",
            "name": "同义词冲突",
            "result": "通过",
            "note": "新增同义词 “成交单量”、“发货及时率” 互不冲突",
        },
        {"code": "V17", "name": "同名词唯一默认", "result": "通过", "note": "每个词只有一个默认指向"},
        {"code": "V1–V17", "name": "各单自身校验", "result": "通过", "note": "各单均无阻断"},
        {
            "code": "提示",
            "name": "同义词索引",
            "result": "信息",
            "note": "CR-0015、CR-0008 都改了同义词，发布后同义词索引一起重建，约 2 分钟生效",
        },
        {
            "code": "提示",
            "name": "评测回放",
            "result": "信息",
            "note": f"{next_version} 草稿涉及 5 道评测题，尚未执行回放",
        },
    ]


async def cr(session, code: str) -> ChangeRequest:
    return await session.scalar(select(ChangeRequest).where(ChangeRequest.code == code))


async def events(session, code: str) -> list[CrEvent]:
    c = await cr(session, code)
    return list(
        (await session.scalars(select(CrEvent).where(CrEvent.cr_id == c.id).order_by(CrEvent.id))).all()
    )


async def release_count(session) -> int:
    return await session.scalar(select(func.count()).select_from(OntologyRelease))


async def test_seed_change_requests(seeded):
    assert await seeded.scalar(select(func.count()).select_from(ChangeRequest)) == 16
    c7 = await cr(seeded, "CR-0007")
    assert c7.status == CRStatus.published and c7.release_id is not None
    assert (await cr(seeded, "CR-0015")).detail["synonyms"] == [{"word": "成交单量", "to": "有效订单数"}]


async def test_merge_check_matches_mock(client, seeded):
    r = await client.post(P + "/releases:merge-check", headers=ADMIN)
    assert r.status_code == 200, r.text
    assert r.json() == {"rows": expected_rows(next_ver("v2026.10.3", clock.now())), "blocked": False}
    assert await release_count(seeded) == 5  # 不落库


async def test_publish_happy_path(client, seeded):
    ver = next_ver("v2026.10.3", clock.now())
    r = await client.post(P + "/releases", json={}, headers=ADMIN)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["ver"] == ver and body["parentVer"] == "v2026.10.3" and body["kind"] == "发布"
    assert body["by"] == "王敏" and body["byId"] == "U010001"
    assert body["changeOrderIds"] == ["CR-0015", "CR-0008"]

    rel = await seeded.scalar(select(OntologyRelease).where(OntologyRelease.ver == ver))
    desc = "合并校验：" + "；".join(x["note"] for x in expected_rows(ver)[4:])
    for code in ("CR-0015", "CR-0008"):
        c = await cr(seeded, code)
        assert c.status == CRStatus.published and c.release_id == rel.id
        [e] = await events(seeded, code)
        assert (e.action, e.actor, e.actor_code, e.text, e.desc) == (
            "publish",
            "王敏",
            "U010001",
            f"王敏 整版发布为 {ver}",
            desc,
        )
    cur = (await client.get(P + "/releases", headers=ADMIN)).json()
    assert cur["current"] == ver
    # 发布后没有已通过的单子 → create_version 的 EMPTY_RELEASE [推断：前端按钮此时置灰]
    r = await client.post(P + "/releases", json={}, headers=ADMIN)
    assert r.status_code == 409 and r.json()["code"] == "EMPTY_RELEASE"


@pytest.mark.parametrize("how", ["overlap", "self_blocked", "synonym_clash"])
async def test_publish_gate_blocked(client, seeded, how):
    c8, c15 = await cr(seeded, "CR-0008"), await cr(seeded, "CR-0015")
    if how == "overlap":
        c8.detail = {**c8.detail, "touches": list(c15.detail["touches"])}
        code = "M1"
    elif how == "self_blocked":
        c8.detail = {
            **c8.detail,
            "checks": [*c8.detail["checks"], {"code": "V3", "name": "x", "result": "阻断", "note": "y"}],
        }
        code = "V1–V17"
    else:
        c8.detail = {**c8.detail, "synonyms": [{"word": "成交单量", "to": "订单数 orders"}]}
        code = "V9"
    await seeded.commit()

    mc = (await client.post(P + "/releases:merge-check", headers=ADMIN)).json()
    assert mc["blocked"] is True and any(x["code"] == code and x["result"] == "阻断" for x in mc["rows"])
    r = await client.post(P + "/releases", json={}, headers=ADMIN)
    assert r.status_code == 409, r.text
    err = r.json()
    assert err["code"] == "MERGE_BLOCKED" and err["details"]["rows"] == mc["rows"]
    assert await release_count(seeded) == 5
    assert (await cr(seeded, "CR-0008")).status == CRStatus.approved
    assert await events(seeded, "CR-0008") == []


async def test_publish_requires_ontology_admin(client, seeded):
    # 种子里 R-02（陈立军）已含 releases·manage；用无发布权限的工作空间成员（任明：R-03/R-04）验 403。
    r = await client.post(P + "/releases", json={}, headers={"X-Demo-As": "U010259"})
    assert r.status_code == 403 and r.json()["code"] == "NO_PERMISSION"
    assert await release_count(seeded) == 5


async def test_rollback(client, seeded):
    r = await client.post(
        P + "/releases/v2026.10.3:rollback", json={}, headers=OTHER
    )  # 不限管理员（static-api §2.2-6）
    assert r.status_code == 201, r.text
    body = r.json()
    ver = next_ver("v2026.10.3", clock.now())
    assert body["ver"] == ver and body["kind"] == "回滚" and body["rollbackOf"] == "v2026.10.3"
    assert body["changeOrderIds"] == ["CR-0017"] and body["by"] == "陈立军"

    new = await seeded.scalar(select(OntologyRelease).where(OntologyRelease.ver == ver))
    old = await seeded.scalar(select(OntologyRelease).where(OntologyRelease.ver == "v2026.10.2"))
    assert new.snapshot == old.snapshot  # 内容同上一版
    rb = await cr(seeded, "CR-0017")
    assert (rb.title, rb.status, rb.changes, rb.owner, rb.release_id) == (
        "回滚 v2026.10.3",
        CRStatus.published,
        0,
        "陈立军",
        new.id,
    )
    [e] = await events(seeded, "CR-0007")  # v2026.10.3 的单子
    assert (e.action, e.text, e.desc, e.level) == (
        "rollback",
        "陈立军 回滚 v2026.10.3",
        "生成新版本，内容同 v2026.10.2",
        "warn",
    )

    # 只能回滚最近一次发布：整次请求回滚，不留回滚单
    r = await client.post(P + "/releases/v2026.10.3:rollback", json={}, headers=ADMIN)
    assert r.status_code == 409 and r.json()["code"] == "ROLLBACK_NOT_LATEST"
    assert await cr(seeded, "CR-0018") is None
    assert await release_count(seeded) == 6
    r = await client.post(P + "/releases/v1999.01.1:rollback", json={}, headers=ADMIN)
    assert r.status_code == 404


async def test_writes_readonly(client, seeded):
    for url in ("/releases", "/releases/v2026.10.3:rollback", "/releases:merge-check"):
        for q in ("?ver=v2026.10.2", "?previewAs=U010002"):
            r = await client.post(P + url + q, json={}, headers=ADMIN)
            assert r.status_code == 403 and r.json()["code"] == "READONLY", (url, q, r.text)
    assert await release_count(seeded) == 5


async def test_writes_require_login_and_releases_manage(client, seeded):
    from app.modules.static.router import router

    posts = {r.path for r in router.routes if "POST" in r.methods}
    assert {
        "/releases:merge-check",
        "/releases",
        "/releases/{ver}:rollback",
    } <= posts  # 变更单写接口见 test_static_cr
    for r in router.routes:
        if r.path in posts and r.path.startswith("/releases"):
            assert len(r.dependencies) == 1, r.path
    for url in ("/releases:merge-check", "/releases", "/releases/v2026.10.3:rollback"):
        r = await client.post(P + url, json={}, headers={"X-Demo-As": "U999999"})
        assert r.status_code == 401 and r.json()["code"] == "UNAUTHORIZED", url


async def test_publish_lock_version_rejects_stale_head(client, seeded):
    head = (await client.get(P + "/releases/current", headers=ADMIN)).json()
    token = head["lockVersion"]
    before = await release_count(seeded)
    stale = await client.post(P + "/releases", json={"lockVersion": token + 1}, headers=ADMIN)
    assert stale.status_code == 409 and stale.json()["code"] == "VERSION_CONFLICT"
    assert await release_count(seeded) == before
    assert (await cr(seeded, "CR-0015")).status == CRStatus.approved
    good = await client.post(P + "/releases", json={"lockVersion": token}, headers=ADMIN)
    assert good.status_code == 201, good.text
    assert good.json()["skipped"] == []
    retry = await client.post(P + "/releases", json={"lockVersion": token}, headers=ADMIN)
    assert retry.status_code == 409 and retry.json()["code"] == "VERSION_CONFLICT"
    assert await release_count(seeded) == before + 1


async def test_publish_response_lists_unregistered_sections(client, seeded):
    from app.modules.static.models import CrItem

    approved = await cr(seeded, "CR-0015")
    seeded.add(
        CrItem(
            cr_id=approved.id,
            target_kind="metric",
            target_id="gmv",
            section="future",
            item_key="definition",
            op="update",
            before={"example": "old"},
            after={"example": "unchanged"},
            seq=1,
        )
    )
    await seeded.commit()
    response = await client.post(P + "/releases", json={}, headers=ADMIN)
    assert response.status_code == 201, response.text
    assert response.json()["skipped"] == ["metric/future"]
    assert any("未写回定义表：metric/future" in (e.desc or "") for e in await events(seeded, approved.code))
