"""action / event / integration（Palantir专家）第 1 批：seed 与 mock 一致、只读接口、?ver= 快照、下线过滤。"""

import asyncio
import json
import os
import subprocess
import sys
import uuid

import pytest
from sqlalchemy import func, select, update

from app.core.config import get_settings
from app.modules.action import sections as action_sections
from app.modules.action.models import (
    ActionParam,
    ActionType,
    ActionTypeRule,
    ActionTypeVersion,
    OntologyEditRule,
    SideEffect,
    SubmissionCriterion,
)
from app.modules.event import sections as event_sections
from app.modules.event.models import (
    EventEnvelopeField,
    EventPayloadField,
    EventSubscription,
    EventType,
    EventTypeVersion,
)
from app.modules.integration.models import ExtSystem
from app.modules.versioning.models import OntologyRelease
from app.seeds.loader import run_seeds

_ROOT = get_settings().seeds_json_dir
ONT = "/api/ontology/v1"
DATA = "/api/data/v1"


def mock(path: str):
    return json.loads((_ROOT / path).read_text(encoding="utf-8"))


def derived(name: str, type_id: str):
    for c in mock(f"action/derived/{name}.json"):
        if c["args"] and isinstance(c["args"][0], dict) and c["args"][0].get("id") == type_id:
            return c["result"]
    raise KeyError(type_id)


def drop_none(d: dict) -> dict:
    return {k: v for k, v in d.items() if v is not None}


ACTIONS = mock("action/ACTION_TYPES.json")
EVENTS = mock("action/EVENT_TYPES.json")
SYSTEMS = mock("system/EXT_SYSTEMS.json")
MODULES = ["admin", "integration", "action", "event"]


@pytest.fixture
async def ae(users):
    await run_seeds(users, MODULES[1:])
    await (
        users.commit()
    )  # 只释放 savepoint（外层事务测试结束回滚）；接口 404 时 rollback 不会把 seed 一起撤掉
    return users


@pytest.fixture
def ae_sections():
    from app.modules.versioning.sections import unregister_section

    action_sections.register()
    event_sections.register()
    yield
    unregister_section(action_sections.SECTION)
    unregister_section(event_sections.SECTION)


@pytest.fixture
async def released(ae, ae_sections):
    """带 actionTypes / eventTypes 段的发布快照（seed_section）。"""
    await run_seeds(ae, ["versioning"])
    await ae.commit()
    return ae


async def count(session, model) -> int:
    return int(await session.scalar(select(func.count()).select_from(model)))


# ---------- seed ----------


async def test_seed_counts_match_mock(ae):
    n = lambda name: sum(len(c["result"]) for c in mock(f"action/derived/{name}.json"))  # noqa: E731
    wb = mock("action/derived/writebackOf.json")
    expected = {
        ExtSystem: len(SYSTEMS),
        ActionType: len(ACTIONS),
        ActionTypeRule: sum(len(a["usedBy"]) for a in ACTIONS),
        ActionParam: n("paramsOf"),
        SubmissionCriterion: n("condsOf"),
        OntologyEditRule: sum(len(c["result"]["edits"]) for c in wb),
        SideEffect: sum(len(c["result"]["effects"]) for c in wb),
        ActionTypeVersion: n("versionsOf.action"),
        EventType: len(EVENTS),
        EventPayloadField: sum(len(e["specific"]) for e in EVENTS),
        EventEnvelopeField: 10,
        EventSubscription: n("subscribersOf"),
        EventTypeVersion: n("versionsOf.event"),
    }
    for model, want in expected.items():
        assert await count(ae, model) == want, model.__tablename__
    # 幂等：再跑一遍行数不变
    await run_seeds(ae, MODULES[1:])
    await ae.flush()
    for model, want in expected.items():
        assert await count(ae, model) == want, model.__tablename__


async def test_rule_generates_matches_rule_action(ae):
    rows = (
        await ae.execute(
            select(ActionTypeRule.rule_code, ActionType.code)
            .join(ActionType, ActionType.id == ActionTypeRule.action_type_id)
            .where(ActionTypeRule.generates.is_(True))
        )
    ).all()
    assert dict(rows) == mock("action/RULE_ACTION.json")


# ---------- 动作类型 ----------

LIST_KEYS = ("id", "name", "api", "group", "status", "version", "target", "targetApi", "targetKind")


async def test_action_types_list(client, ae):
    r = await client.get(f"{ONT}/action-types")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["groups"] == mock("action/ACTION_GROUPS.json")
    assert body["total"] == len(ACTIONS) and body["size"] == 22
    assert body["items"] == [{k: a.get(k) for k in LIST_KEYS} for a in ACTIONS[:22]]

    r = await client.get(f"{ONT}/action-types", params={"group": "告警"})
    assert [i["id"] for i in r.json()["items"]] == [a["id"] for a in ACTIONS if a["group"] == "告警"]
    r = await client.get(f"{ONT}/action-types", params={"group": "全部", "kw": "告警"})
    assert [i["id"] for i in r.json()["items"]] == [a["id"] for a in ACTIONS if "告警" in a["name"]]
    r = await client.get(f"{ONT}/action-types", params={"page": 2, "size": 10})
    assert [i["id"] for i in r.json()["items"]] == [a["id"] for a in ACTIONS[10:20]]


@pytest.mark.parametrize("a", ACTIONS, ids=[a["id"] for a in ACTIONS])
async def test_action_type_detail_matches_mock(client, ae, a):
    r = await client.get(f"{ONT}/action-types/{a['id']}")
    assert r.status_code == 200, r.text
    assert drop_none(r.json()) == drop_none(a)  # mock 已含 withSystem 合并的 system/timeout/retry/idempotent


async def test_action_subresources_match_mock(client, ae):
    for a in ACTIONS:
        params = derived("paramsOf", a["id"])
        r = await client.get(f"{ONT}/action-types/{a['id']}/params")
        body = r.json()
        assert body["total"] == len(params) and body["size"] == 12
        assert body["items"] == params[:12]
        r = await client.get(f"{ONT}/action-types/{a['id']}/submission-criteria")
        assert r.json()["items"] == derived("condsOf", a["id"])
        wb = derived("writebackOf", a["id"])
        r = await client.get(f"{ONT}/action-types/{a['id']}/writeback")
        body = r.json()
        assert body["edits"] == wb["edits"] and body["effects"] == wb["effects"]
        assert drop_none(body["config"]) == drop_none(
            {"sysId": a.get("sysId"), "endpoint": a["endpoint"], "idemKey": a["idemKey"]}
        )
        assert [s["time"] for s in body["flow"]] == [str(i) for i in range(1, len(body["flow"]) + 1)]


async def test_writeback_flow(client, ae):
    body = (await client.get(f"{ONT}/action-types/request_replenish/writeback")).json()
    a = next(x for x in ACTIONS if x["id"] == "request_replenish")
    erp = next(s for s in SYSTEMS if s["id"] == "EXT-ERP")
    assert [(f["text"], f["status"]) for f in body["flow"]] == [
        ("规则生成 ⇒ 待确认", "warn"),
        ("校验提交条件", None),
        ("审批", None),
        (f"调用 {erp['name']}（同步）", None),
        ("外部明确返回失败 ⇒ 自动重试", "error"),
        ("超时或无响应 ⇒ 结果未知 · 待对账", "unknown"),
        ("提交本体编辑", None),
        ("执行副作用（异步）", None),
    ]
    assert body["flow"][1]["desc"] == f"{len(derived('condsOf', a['id']))} 项，任一不满足即拒绝提交"
    assert body["flow"][2]["desc"] == a["approval"]
    assert body["flow"][3]["desc"] == f"超时 {a['timeout']}；幂等键 {erp['idemKey']}"
    assert body["flow"][4]["desc"] == erp["retry"]

    # 无外部系统 + 免审 + 没有规则生成
    body = (await client.get(f"{ONT}/action-types/assign_region_manager/writeback")).json()
    assert [f["text"] for f in body["flow"]] == ["校验提交条件", "提交本体编辑", "执行副作用（异步）"]
    # close_alert：被规则使用但规则不生成它 → 没有「规则生成」
    body = (await client.get(f"{ONT}/action-types/close_alert/writeback")).json()
    assert body["flow"][0]["text"] == "校验提交条件"


async def test_action_versions(client, ae):
    calls = mock("action/derived/versionsOf.action.json")
    for a, call in zip(ACTIONS, calls, strict=True):
        r = await client.get(f"{ONT}/action-types/{a['id']}/versions")
        assert r.json()["items"] == call["result"], a["id"]


async def test_action_not_found(client, ae):
    for path in ("", "/params", "/submission-criteria", "/writeback", "/versions"):
        r = await client.get(f"{ONT}/action-types/nope{path}")
        assert r.status_code == 404, path
        assert r.json()["code"] == "NOT_FOUND"


async def test_desc_sys_template_replaced(client, ae):
    await ae.execute(
        update(ActionType).where(ActionType.code == "adjust_region_budget").values(desc_tpl="经{sys}写回")
    )
    await ae.execute(update(ActionType).where(ActionType.code == "close_alert").values(desc_tpl="{sys}内部"))
    assert (await client.get(f"{ONT}/action-types/adjust_region_budget")).json()[
        "desc"
    ] == "经 ERP 写回".replace("ERP", next(s["name"] for s in SYSTEMS if s["id"] == "EXT-ERP"))
    assert (await client.get(f"{ONT}/action-types/close_alert")).json()["desc"] == "本体内部"


# ---------- 事件类型 ----------

EVENT_LIST_KEYS = ("id", "name", "api", "source", "status", "version", "object")
EVENT_DETAIL_KEYS = (
    *EVENT_LIST_KEYS,
    "sourceRef", "ruleId", "objectApi", "objKind", "owner", "desc", "dedupKey", "dedupWindow",
    "delivery", "ordering", "retention", "weekly",
)  # fmt: skip


async def test_event_types_list(client, ae):
    body = (await client.get(f"{ONT}/event-types")).json()
    assert body["sources"] == mock("action/EVENT_SOURCES.json")
    assert body["total"] == len(EVENTS)
    assert body["items"] == [{k: e[k] for k in EVENT_LIST_KEYS} for e in EVENTS[:22]]
    body = (await client.get(f"{ONT}/event-types", params={"source": "规则", "kw": "告警"})).json()
    assert [i["id"] for i in body["items"]] == [
        e["id"] for e in EVENTS if e["source"] == "规则" and "告警" in e["name"]
    ]


async def test_event_detail_and_subresources(client, ae):
    for e in EVENTS:
        r = await client.get(f"{ONT}/event-types/{e['id']}")
        assert drop_none(r.json()) == drop_none({k: e.get(k) for k in EVENT_DETAIL_KEYS}), e["id"]

        payload = derived("payloadOf", e["id"])
        spec = {f["api"] for f in e["specific"]}
        body = (await client.get(f"{ONT}/event-types/{e['id']}/payload-fields", params={"size": 100})).json()
        assert body["envelopeCount"] == 10 and body["total"] == len(payload)
        assert body["items"] == [{**f, "envelope": f["api"] not in spec} for f in payload]
        body = (await client.get(f"{ONT}/event-types/{e['id']}/payload-fields")).json()
        assert body["size"] == 12 and len(body["items"]) == min(12, len(payload))

        r = await client.get(f"{ONT}/event-types/{e['id']}/subscribers")
        assert [drop_none(s) for s in r.json()["items"]] == derived("subscribersOf", e["id"])

    calls = mock("action/derived/versionsOf.event.json")
    for e, call in zip(EVENTS, calls, strict=True):
        r = await client.get(f"{ONT}/event-types/{e['id']}/versions")
        assert r.json()["items"] == call["result"], e["id"]
    assert (await client.get(f"{ONT}/event-types/nope/subscribers")).status_code == 404


# ---------- 外部系统 ----------


async def test_ext_systems(client, ae):
    body = (await client.get(f"{DATA}/ext-systems")).json()
    assert [drop_none(s) for s in body["items"]] == SYSTEMS
    body = (await client.get(f"{DATA}/ext-systems", params={"writeback": 1})).json()
    assert [drop_none(s) for s in body["items"]] == mock("action/derived/writebackSystems.json")[0]["result"]
    body = (await client.get(f"{DATA}/ext-systems", params={"kw": "CRM"})).json()
    assert [s["id"] for s in body["items"]] == [
        s["id"] for s in SYSTEMS if "CRM" in s["name"] or "CRM" in s["id"] or "CRM" in s["vendor"]
    ]

    r = await client.get(f"{DATA}/ext-systems/EXT-ERP")
    body = r.json()
    assert drop_none({k: v for k, v in body.items() if k != "actionTypes"}) == next(
        s for s in SYSTEMS if s["id"] == "EXT-ERP"
    )
    assert [a["id"] for a in body["actionTypes"]] == [a["id"] for a in ACTIONS if a.get("sysId") == "EXT-ERP"]
    assert set(body["actionTypes"][0]) == {"id", "name", "group", "status", "endpoint"}
    assert (await client.get(f"{DATA}/ext-systems/EXT-NOPE")).status_code == 404
    # integration 只挂在 /api/data/v1
    assert (await client.get(f"{ONT}/ext-systems")).status_code == 404


# ---------- ?ver= / ?previewAs= / 下线 ----------


async def test_ver_unknown_404(client, ae):
    assert (await client.get(f"{ONT}/action-types", params={"ver": "v2099.01.1"})).status_code == 404
    assert (
        await client.get(f"{ONT}/event-types/order_status_changed", params={"ver": "v2099.01.1"})
    ).status_code == 404


class _OldActionTypes(action_sections.ActionTypesSection):
    """模拟历史定义不同：v2026.10.1 的快照里 close_alert 改过名、没有参数。"""

    def seed_section(self, ver, ctx):
        out = super().seed_section(ver, ctx)
        if ver == "v2026.10.1":
            out["close_alert"] = {**out["close_alert"], "name": "关闭告警（旧）", "params": []}
        return out


class _OldEventTypes(event_sections.EventTypesSection):
    def seed_section(self, ver, ctx):
        out = super().seed_section(ver, ctx)
        if ver == "v2026.10.1":
            out["order_status_changed"] = {**out["order_status_changed"], "payload": []}
        return out


async def test_ver_reads_snapshot(client, released):
    rel = (await released.scalars(select(OntologyRelease).where(OntologyRelease.ver == "v2026.10.1"))).one()
    assert set(rel.snapshot[action_sections.SECTION]) == {a["id"] for a in ACTIONS}
    assert set(rel.snapshot[event_sections.SECTION]) == {e["id"] for e in EVENTS}
    # 快照和当前一致时，历史视图返回同样内容
    for path in ("action-types/request_replenish/writeback", "event-types/order_status_changed/subscribers"):
        cur = (await client.get(f"{ONT}/{path}")).json()
        assert (await client.get(f"{ONT}/{path}", params={"ver": rel.ver})).json() == cur


async def test_ver_reads_changed_snapshot(client, ae):
    """?ver= 读快照里的定义，当前版不受影响。"""
    from app.modules.versioning.sections import register_section, unregister_section

    register_section(_OldActionTypes())
    register_section(_OldEventTypes())
    try:
        await run_seeds(ae, ["versioning"])
        await ae.commit()
    finally:
        unregister_section(action_sections.SECTION)
        unregister_section(event_sections.SECTION)
    v = {"ver": "v2026.10.1"}
    assert (await client.get(f"{ONT}/action-types/close_alert", params=v)).json()["name"] == "关闭告警（旧）"
    assert (await client.get(f"{ONT}/action-types/close_alert/params", params=v)).json()["total"] == 0
    assert (await client.get(f"{ONT}/action-types/close_alert")).json()["name"] != "关闭告警（旧）"
    body = (await client.get(f"{ONT}/event-types/order_status_changed/payload-fields", params=v)).json()
    assert body["total"] == 10 and all(f["envelope"] for f in body["items"])
    # 列表顺序仍按定义表 id（jsonb 不保序）
    items = (await client.get(f"{ONT}/action-types", params=v)).json()["items"]
    assert [i["id"] for i in items] == [a["id"] for a in ACTIONS]


async def test_ver_without_section_falls_back_to_tables(client, ae, sections):
    """快照里没有 actionTypes 段 → 退回当前定义表【推断】。"""
    await run_seeds(ae, ["versioning"])
    await ae.flush()
    r = await client.get(f"{ONT}/action-types", params={"ver": "v2026.10.1"})
    assert r.status_code == 200 and r.json()["total"] == len(ACTIONS)
    r = await client.get(f"{ONT}/event-types", params={"ver": "v2026.10.1", "previewAs": "U001"})
    assert r.status_code == 200 and r.json()["total"] == len(EVENTS)


async def test_retired_types_hidden(client, released):
    rels = {r.ver: r for r in (await released.scalars(select(OntologyRelease))).all()}
    retire_at = rels["v2026.10.2"]
    before = min((r for r in rels.values() if r.id < retire_at.id), key=lambda r: -r.id)
    after = max(rels.values(), key=lambda r: r.id)
    await released.execute(
        update(ActionType).where(ActionType.code == "close_alert").values(retired_release_id=retire_at.id)
    )
    await released.execute(
        update(EventType)
        .where(EventType.code == "order_status_changed")
        .values(retired_release_id=retire_at.id)
    )
    await released.commit()  # 接口 404 会 rollback，先提交（只释放 savepoint）
    cases = [(before.ver, True), (retire_at.ver, False), (after.ver, after.id < retire_at.id)]

    ids = lambda body: [i["id"] for i in body["items"]]  # noqa: E731
    assert "close_alert" not in ids((await client.get(f"{ONT}/action-types")).json())
    assert (await client.get(f"{ONT}/action-types/close_alert")).status_code == 404
    for ver, visible in cases:
        body = (await client.get(f"{ONT}/action-types", params={"ver": ver})).json()
        assert ("close_alert" in ids(body)) is visible, ver
        r = await client.get(f"{ONT}/event-types/order_status_changed", params={"ver": ver})
        assert (r.status_code == 200) is visible, ver
        for path in ("action-types/close_alert/versions", "event-types/order_status_changed/versions"):
            r = await client.get(f"{ONT}/{path}", params={"ver": ver})
            assert (r.status_code == 200) is visible, (path, ver)
    # 版本记录和详情一样：已下线 404
    for path in ("action-types/close_alert/versions", "event-types/order_status_changed/versions"):
        assert (await client.get(f"{ONT}/{path}")).status_code == 404, path
    assert "order_status_changed" not in ids((await client.get(f"{ONT}/event-types")).json())
    # 外部系统详情的关联动作也不含已下线的
    body = (await client.get(f"{DATA}/ext-systems/EXT-OMS")).json()
    assert "close_alert" not in [a["id"] for a in body["actionTypes"]]


async def test_build_section_equals_seed_section(ae):
    from app.seeds import SeedContext

    ctx = SeedContext(json_dir=_ROOT / "static", root=_ROOT)  # versioning seed 用的是 static 组
    for b in (action_sections.ActionTypesSection(), event_sections.EventTypesSection()):
        built = await b.build_section(ae, None, [])
        assert json.loads(json.dumps(built)) == b.seed_section("v2026.10.3", ctx), b.section


def test_sections_register_on_import_idempotently():
    """import sections.py 即登记（新进程里验证；测试进程的注册表被 conftest 按测试清空）；重复登记不报错。"""
    code = (
        "import json\n"
        "import app.modules.action.sections as a, app.modules.event.sections as e\n"
        "from app.modules.versioning.sections import registered_sections as r\n"
        "assert r()['actionTypes'] is a.BUILDER and r()['eventTypes'] is e.BUILDER\n"
        "assert a.register() is a.BUILDER and e.register() is e.BUILDER\n"
        "print(json.dumps(sorted(r())))"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr[-3000:]
    assert {"actionTypes", "eventTypes"} <= set(json.loads(out.stdout.strip().splitlines()[-1]))
    for mod in (action_sections, event_sections):
        assert mod.register() is mod.BUILDER and mod.register() is mod.BUILDER


@pytest.mark.parametrize("model", [ActionTypeVersion, EventTypeVersion])
async def test_version_tables_append_only(ae, model):
    from sqlalchemy.exc import DBAPIError

    with pytest.raises(DBAPIError, match="只追加"):
        async with ae.begin_nested():
            await ae.execute(update(model).values(change_note="改"))


async def test_ext_systems_writeback_invalid_reports_once(client, ae):
    r = await client.get(f"{DATA}/ext-systems", params={"writeback": 2})
    assert r.status_code == 422
    assert json.dumps(r.json(), ensure_ascii=False).count('"writeback"') == 1, r.json()


# ---------- restore_section（整版回滚，签名 restore_section(session, content, *, release_id)） ----------


async def _snap(session) -> tuple[dict, dict]:
    a = await action_sections.BUILDER.build_section(session, None, [])
    e = await event_sections.BUILDER.build_section(session, None, [])
    return json.loads(json.dumps(a)), json.loads(json.dumps(e))


async def _generates(session) -> dict:
    rows = await session.execute(
        select(ActionType.code, ActionTypeRule.rule_code, ActionTypeRule.generates).join(
            ActionTypeRule, ActionTypeRule.action_type_id == ActionType.id
        )
    )
    return {(c, r): g for c, r, g in rows.all()}


async def test_restore_section_overwrites_and_retires(ae):
    acts, evs = await _snap(ae)
    gen0 = await _generates(ae)
    rel_id = 987654
    # 恢复到「close_alert 名字和参数不同、request_replenish 不存在」的那一版
    old_acts = {k: v for k, v in acts.items() if k != "request_replenish"}
    ca = old_acts["close_alert"]
    old_acts["close_alert"] = {**ca, "name": "关闭告警（旧）", "params": ca["params"][:1]}
    old_evs = {k: v for k, v in evs.items() if k != "order_status_changed"}
    first = next(iter(old_evs))
    old_evs[first] = {**old_evs[first], "retention": "7 天", "payload": [], "desc": "旧说明"}
    await action_sections.BUILDER.restore_section(ae, old_acts, release_id=rel_id)
    await event_sections.BUILDER.restore_section(ae, old_evs, release_id=rel_id)

    acts2, evs2 = await _snap(ae)
    assert acts2 == old_acts and evs2 == old_evs  # 定义表 = 被恢复版本的内容
    retired = dict((await ae.execute(select(ActionType.code, ActionType.retired_release_id))).all())
    assert retired["request_replenish"] == rel_id and retired["close_alert"] is None
    ev_retired = dict((await ae.execute(select(EventType.code, EventType.retired_release_id))).all())
    assert ev_retired["order_status_changed"] == rel_id
    assert (await ae.scalar(select(EventType.retention_days).where(EventType.code == first))) == 7

    # 再恢复到包含它们的版本（重新发布）→ 清掉下线标记、内容回到原样
    await action_sections.BUILDER.restore_section(ae, acts, release_id=rel_id + 1)
    await event_sections.BUILDER.restore_section(ae, evs, release_id=rel_id + 1)
    acts3, evs3 = await _snap(ae)
    assert acts3 == acts and evs3 == evs
    n_ret = select(func.count()).select_from(ActionType).where(ActionType.retired_release_id.is_not(None))
    assert await ae.scalar(n_ret) == 0
    n_ret = select(func.count()).select_from(EventType).where(EventType.retired_release_id.is_not(None))
    assert await ae.scalar(n_ret) == 0
    # 不进快照的字段沿用：规则「生成」标记（RULE_ACTION）、订阅方 weekly（统计）
    assert await _generates(ae) == gen0
    weekly = {
        (s.event_type_id, s.name): s.weekly for s in (await ae.scalars(select(EventSubscription))).all()
    }
    want = sum(s.get("weekly", 0) for c in mock("action/derived/subscribersOf.json") for s in c["result"])
    assert sum(weekly.values()) == want


async def test_restore_section_without_release_id_uses_next_release(released):
    """骨架改签名前不传 release_id：用 max(ontology_release.id)+1（新回滚记录的 id 不会小于它）。"""
    top = await released.scalar(select(func.max(OntologyRelease.id)))
    acts, _ = await _snap(released)
    acts.pop("close_alert")
    await action_sections.BUILDER.restore_section(released, acts)
    got = await released.scalar(select(ActionType.retired_release_id).where(ActionType.code == "close_alert"))
    assert got == top + 1


# ---------- 全新库：默认 seed 写出带 actionTypes / eventTypes 的版本记录；迁移往返只看自己的表 ----------

MY_TABLES = {
    "ext_system", "action_type", "action_type_rule", "action_param", "submission_criterion",
    "ontology_edit_rule", "side_effect", "action_type_version", "event_type", "event_payload_field",
    "event_subscription", "event_type_version", "event_envelope_field",
}  # fmt: skip


async def _temp_db():
    import asyncpg
    from sqlalchemy.engine import make_url

    from tests.conftest import BASE_URL

    db = f"rengu_pal_{uuid.uuid4().hex[:8]}"
    base = make_url(BASE_URL)
    admin = await asyncpg.connect(base.set(drivername="postgresql").render_as_string(hide_password=False))
    await admin.execute(f'CREATE DATABASE "{db}"')
    url = base.set(database=db).render_as_string(hide_password=False)
    plain = base.set(drivername="postgresql", database=db).render_as_string(hide_password=False)
    return admin, db, url, plain


async def _drop(admin, db) -> None:
    await admin.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = $1", db)
    await admin.execute(f'DROP DATABASE IF EXISTS "{db}"')
    await admin.close()


def _alembic(url: str | None = None):
    from alembic.config import Config

    cfg = Config("alembic.ini")
    if url:
        cfg.attributes["database_url"] = url
    return cfg


async def test_fresh_db_default_seed_writes_action_event_sections():
    import asyncpg
    from alembic import command

    admin, db, url, plain = await _temp_db()
    try:
        await asyncio.to_thread(command.upgrade, _alembic(url), "head")
        env = {**os.environ, "RENGU_DATABASE_URL": url, "RENGU_ENV": "test"}
        argv = [sys.executable, "-m", "app.cli", "seed"]
        r = await asyncio.to_thread(
            subprocess.run, argv, env=env, capture_output=True, text=True, timeout=300
        )
        assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-4000:]
        conn = await asyncpg.connect(plain)
        try:
            rows = await conn.fetch("SELECT ver, snapshot::text AS snap FROM ontology_release ORDER BY id")
        finally:
            await conn.close()
        assert rows
        for row in rows:
            snap = json.loads(row["snap"])
            assert set(snap["actionTypes"]) == {a["id"] for a in ACTIONS}, row["ver"]
            assert set(snap["eventTypes"]) == {e["id"] for e in EVENTS}, row["ver"]
    finally:
        await _drop(admin, db)


async def test_my_migrations_roundtrip_only_my_tables():
    """降到 integration_0001 的 down_revision（从脚本算，不写死 0003），只断言自己的表消失 / 回来；
    之后串在我后面的模块迁移不影响这个测试。"""
    import asyncpg
    from alembic import command
    from alembic.script import ScriptDirectory

    down = ScriptDirectory.from_config(_alembic()).get_revision("integration_0001").down_revision
    admin, db, url, plain = await _temp_db()

    async def tables() -> set[str]:
        conn = await asyncpg.connect(plain)
        try:
            rows = await conn.fetch("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
            return {r["tablename"] for r in rows}
        finally:
            await conn.close()

    try:
        cfg = _alembic(url)
        await asyncio.to_thread(command.upgrade, cfg, "head")
        assert await tables() >= MY_TABLES
        await asyncio.to_thread(command.downgrade, cfg, down)
        left = await tables()
        assert not (MY_TABLES & left), MY_TABLES & left
        assert {"ontology_release", "sys_user"} <= left  # 我依赖的表还在
        await asyncio.to_thread(command.upgrade, cfg, "head")
        assert await tables() >= MY_TABLES
    finally:
        await _drop(admin, db)
