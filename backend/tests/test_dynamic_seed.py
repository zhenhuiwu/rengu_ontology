"""动态种子：行数、PEOPLE 回填、列表顺序与前端 mock 一致。"""

import json

from sqlalchemy import func, select

from app.core.config import get_settings
from app.modules.admin.models import SysUser
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
    r = await client.get(f"{API}/people")
    assert r.status_code == 200
    assert r.json() == _json("dynamic/PEOPLE.json")
    m = (await client.get(f"{API}/actions/meta")).json()
    assert m["actionNames"] == _json("dynamic/ACTION_NAMES.json")
    assert set(m) == {"actionNames", "actionSystems", "actionMeta", "objKindLabel"}


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
    # 默认：待处理，一屏 12
    d = (await client.get(f"{API}/alerts")).json()
    assert d["size"] == 12 and d["total"] == 4 and all(x["status"] == "待处理" for x in d["items"])


async def test_action_center_matches_mock(dyn, client):  # noqa: F811
    mock = _json("dynamic/ACTIONS.json")
    r = (await client.get(f"{API}/action-runs", params={"scope": "center", "size": 200})).json()
    assert r["total"] == 141
    assert [x["id"] for x in r["items"]] == [x["id"] for x in mock]
    pend = (await client.get(f"{API}/action-runs", params={"tab": "pending"})).json()
    assert pend["size"] == 16
    assert pend["counts"] == {"pending": 25, "records": 116, "unknown": 31}
    unk = (await client.get(f"{API}/action-runs", params={"tab": "unknown", "size": 40})).json()
    sts = [x["status"] for x in unk["items"]]
    assert sts == sorted(sts, key=lambda s: s != "结果未知")  # 结果未知排前
    allr = (await client.get(f"{API}/action-runs")).json()
    assert allr["total"] == 1990
    runs = (await client.get(f"{API}/action-runs", params={"scope": "runs"})).json()
    assert runs["total"] == 1849 and isinstance(runs["items"][0]["source"], str)
