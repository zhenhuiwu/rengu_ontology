"""动态种子：行数、PEOPLE 回填、列表顺序与前端 mock 一致。"""

import json

from sqlalchemy import func, select

from app.core.config import get_settings
from app.modules.admin.models import SysUser
from app.modules.biz.models import BizOrder, Product, Store
from app.modules.dynamic.models import ActionRun, ActionRunLog, Alert, AlertLog
from tests.dynamic_support import API, dyn  # noqa: F401


def _json(rel: str):
    return json.loads((get_settings().seeds_json_dir / rel).read_text(encoding="utf-8"))


async def test_seed_counts(dyn):  # noqa: F811
    s = dyn
    assert await s.scalar(select(func.count()).select_from(Alert)) == 154
    assert await s.scalar(select(func.count()).select_from(AlertLog)) == 455
    assert await s.scalar(select(func.count()).select_from(ActionRun)) == 1990
    assert await s.scalar(select(func.count()).where(ActionRun.in_center.is_(True))) == 141
    assert await s.scalar(select(func.count()).select_from(ActionRunLog)) == 804 + 8217
    rows = (
        await s.execute(
            select(SysUser.name, SysUser.alert_assignable)
            .where(SysUser.alert_assignable.is_not(None))
            .order_by(SysUser.alert_assignable)
        )
    ).all()
    assert [r[1] for r in rows] == [1, 2, 3, 4, 5, 6, 7]
    assert [r[0] for r in rows] == _json("dynamic/PEOPLE.json")
    assert await s.scalar(select(func.count()).where(Alert.is_seed.is_(False))) == 0


async def test_people_and_meta(dyn, client):  # noqa: F811
    assert (await client.get(f"{API}/people")).status_code == 422  # 候选人员须按具体告警权限筛选。
    r = await client.get(f"{API}/people", params={"alertId": "AL-20261025-0126"})
    assert r.status_code == 200, r.text
    people = r.json()
    assert {"U010001", "U010002", "U010004"} <= {p["id"] for p in people}
    assert [p["id"] for p in people] == sorted(p["id"] for p in people)
    names = dict((await dyn.execute(select(SysUser.code, SysUser.name))).all())
    assert all(p["name"] == names[p["id"]] for p in people)
    m = (await client.get(f"{API}/actions/meta")).json()
    from app.common.auth import resolve_user
    from app.modules.dynamic.catalog import get_catalog

    catalog = await get_catalog(dyn, await resolve_user(dyn, "U010001"))
    assert m["actionNames"] == catalog.meta()["actionNames"] and m["actionNames"]
    assert m["actionMeta"]["request_replenish"]["approvalConfigured"] is False
    assert set(m) == {"actionNames", "actionSystems", "actionMeta", "objKindLabel", "canManage"}
    assert m["canManage"] is True


async def test_alert_list_matches_mock(dyn, client):  # noqa: F811
    mock = _json("dynamic/ALERTS.json")
    r = (await client.get(f"{API}/alerts", params={"status": "all", "size": 200})).json()
    assert r["total"] == 154 and r["counts"] == {"open": 4, "doing": 4, "closed": 146, "all": 154}
    assert [x["id"] for x in r["items"]] == [x["id"] for x in mock[:154]]
    first = r["items"][0]
    src = mock[0]
    for k in (
        "level",
        "ruleId",
        "objKind",
        "objId",
        "objLabel",
        "value",
        "threshold",
        "triggeredAt",
        "status",
    ):
        assert first[k] == src[k], k
    # 默认：待处理，10条/页
    d = (await client.get(f"{API}/alerts")).json()
    assert d["size"] == 10 and d["total"] == 4 and all(x["status"] == "待处理" for x in d["items"])


async def test_action_center_matches_mock(dyn, client):  # noqa: F811
    mock = _json("dynamic/ACTIONS.json")
    # 未登记的客群/大区示例仍保留在种子里，真实列表只显示目标能唯一定位的记录。
    identities = {}
    for kind, model in (("product", Product), ("order", BizOrder), ("store", Store)):
        identities[kind] = set(
            await dyn.scalars(select(model.code).group_by(model.code).having(func.count() == 1))
        )
    visible = [row for row in mock if row["objId"] in identities.get(row["objKind"], set())]
    assert len(mock) == 141 and len(visible) < len(mock)
    r = (await client.get(f"{API}/action-runs", params={"scope": "center", "size": 200})).json()
    assert r["total"] == len(visible)
    assert [x["id"] for x in r["items"]] == [x["id"] for x in visible]
    pend = (await client.get(f"{API}/action-runs", params={"tab": "pending"})).json()
    assert pend["size"] == 10
    assert pend["counts"] == {
        "pending": sum(row["status"] == "待确认" for row in visible),
        "records": sum(row["status"] != "待确认" for row in visible),
        "unknown": sum(row["status"] in ("结果未知", "已对账") for row in visible),
    }
    unk = (await client.get(f"{API}/action-runs", params={"tab": "unknown", "size": 40})).json()
    sts = [x["status"] for x in unk["items"]]
    assert sts == sorted(sts, key=lambda s: s != "结果未知")  # 结果未知排前
    allr = (await client.get(f"{API}/action-runs")).json()
    runs = (await client.get(f"{API}/action-runs", params={"scope": "runs"})).json()
    expected_runs = await dyn.scalar(
        select(func.count())
        .select_from(ActionRun)
        .where(
            ActionRun.in_center.is_(False),
            ((ActionRun.obj_kind == "product") & ActionRun.obj_id.in_(identities["product"]))
            | ((ActionRun.obj_kind == "order") & ActionRun.obj_id.in_(identities["order"]))
            | ((ActionRun.obj_kind == "store") & ActionRun.obj_id.in_(identities["store"])),
        )
    )
    assert runs["total"] == expected_runs and isinstance(runs["items"][0]["source"], str)
    assert allr["total"] == runs["total"] + len(visible)
    ambiguous = select(BizOrder.code).group_by(BizOrder.code).having(func.count() > 1)
    ambiguous_refs = list(
        await dyn.scalars(
            select(ActionRun.obj_id)
            .where(
                ActionRun.in_center.is_(False),
                ActionRun.obj_kind == "order",
                ActionRun.obj_id.in_(ambiguous),
            )
            .distinct()
        )
    )
    assert ambiguous_refs, "旧种子须包含指向歧义订单号的执行记录，才能验证拒绝边界"
    for identity in ambiguous_refs:
        hidden = (
            await client.get(
                f"{API}/action-runs",
                params={
                    "scope": "runs",
                    "objKind": "order",
                    "objId": identity,
                },
            )
        ).json()
        assert hidden["total"] == 0 and hidden["items"] == []
