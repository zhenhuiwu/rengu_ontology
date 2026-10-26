"""action / event 运行数据（palantir-r2）：近期事件、运行健康、近 14 天趋势、动作每日执行量。

事件列表与趋势按数据库真实记录与滚动时间；历史种子样本仅作对拍输入。
"""

from collections import Counter
from datetime import timedelta

import pytest
from sqlalchemy import func, select, update

from app.core import clock
from app.modules.action.models import ActionRunDailyStat
from app.modules.event.models import Event, EventDailyStat
from tests.function_access_support import grant_fixture_roles
from tests.test_action_event import ACTIONS, EVENTS, ONT, ae, mock  # noqa: F401  (ae 是 fixture)

OUTCOMES = ["已投递", "已生成告警", "已去重", "部分投递", "投递失败"]


@pytest.fixture(autouse=True)
async def runtime_permissions(ae):  # noqa: F811
    await grant_fixture_roles(ae, {"U010001": "R-01"})


def calls(name: str) -> dict[str, list]:
    return {c["args"][0]: c["result"] for c in mock(f"action/derived/{name}.json")}


EV = calls("eventsOf")
EV_DAILY = calls("eventDaily")
RUN_DAILY = calls("runDaily")


def window_rows(rows, days=7):
    end = clock.local_now().strftime("%Y-%m-%d %H:%M:%S")
    start = (clock.local_now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    return [r for r in rows if start <= r["at"] <= end]


def expected_daily(rows):
    start, _ = clock.last_days(14)
    until = clock.local_now().strftime("%Y-%m-%d %H:%M:%S")
    counts = Counter(r["at"][:10] for r in rows if r["at"] <= until)
    return [
        {
            "date": (start + timedelta(days=i)).strftime("%m-%d"),
            "value": counts[(start + timedelta(days=i)).isoformat()],
        }
        for i in range(14)
    ]


def health(rows: list[dict]) -> dict:
    """同 EventTypes.vue 的 health computed。"""
    c = lambda o: sum(1 for r in rows if r["outcome"] == o)  # noqa: E731
    lat = sorted(r["latencyMs"] for r in rows)
    return {
        "total": len(rows),
        "dedup": c("已去重"),
        "failed": c("投递失败"),
        "partial": c("部分投递"),
        "p95": lat[int(len(rows) * 0.95)] if rows else None,
    }


async def test_seed_counts(ae):  # noqa: F811
    assert await ae.scalar(select(func.count()).select_from(Event)) == sum(len(v) for v in EV.values())
    assert await ae.scalar(select(func.count()).select_from(EventDailyStat)) == 14 * len(EV_DAILY)
    assert await ae.scalar(select(func.count()).select_from(ActionRunDailyStat)) == 14 * len(RUN_DAILY)


async def _all(client, type_id: str, **q) -> tuple[list[dict], int]:
    out: list[dict] = []
    page = 1
    while True:
        r = await client.get(f"{ONT}/event-types/{type_id}/events", params={**q, "page": page, "size": 200})
        assert r.status_code == 200, r.text
        b = r.json()
        out += b["items"]
        if len(out) >= b["total"] or not b["items"]:
            return out, b["total"]
        page += 1


async def test_events_match_mock(client, ae):  # noqa: F811
    for e in EVENTS:
        rows = window_rows(EV.get(e["id"], []))
        got, total = await _all(client, e["id"])
        assert total == len(rows)
        assert [{k: v for k, v in g.items() if v is not None} for g in got] == rows, e["id"]
        got24, _ = await _all(client, e["id"], range="24h")
        assert [g["id"] for g in got24] == [r["id"] for r in window_rows(rows, days=1)], e["id"]
        for oc in OUTCOMES[:2]:
            got_oc, _ = await _all(client, e["id"], outcome=oc)
            assert [g["id"] for g in got_oc] == [r["id"] for r in rows if r["outcome"] == oc]


async def test_events_paging_defaults(client, ae):  # noqa: F811
    tid = "order_status_changed"
    rows = window_rows(EV[tid])
    b = (await client.get(f"{ONT}/event-types/{tid}/events")).json()
    assert (b["page"], b["size"], b["total"]) == (1, 10, len(rows))
    assert [i["id"] for i in b["items"]] == [r["id"] for r in rows[:10]]
    b = (await client.get(f"{ONT}/event-types/{tid}/events", params={"page": 99})).json()
    last = -(-len(rows) // 10)
    assert b["page"] == last and [i["id"] for i in b["items"]] == [r["id"] for r in rows[(last - 1) * 10 :]]
    b = (
        await client.get(f"{ONT}/event-types/{tid}/events", params={"outcome": "全部", "range": "7d"})
    ).json()
    assert b["total"] == len(rows)


async def test_events_bad_params_and_404(client, ae):  # noqa: F811
    tid = "order_status_changed"
    assert (await client.get(f"{ONT}/event-types/{tid}/events", params={"range": "30d"})).status_code == 422
    assert (await client.get(f"{ONT}/event-types/{tid}/events", params={"outcome": "x"})).status_code == 422
    assert (await client.get(f"{ONT}/event-types/nope/events")).status_code == 404
    assert (await client.get(f"{ONT}/event-types/nope/stats")).status_code == 404


async def test_event_detail_matches_mock(client, ae):  # noqa: F811
    """GET /events/{id} = mock eventById(id)（事件抽屉读的那一行），全部种子事件逐条比对。"""
    rows = [r for v in EV.values() for r in v]
    assert rows
    for r in rows:
        resp = await client.get(f"{ONT}/events/{r['id']}")
        assert resp.status_code == 200, resp.text
        assert {k: v for k, v in resp.json().items() if v is not None} == r


async def test_event_detail_404(client, ae):  # noqa: F811
    resp = await client.get(f"{ONT}/events/EV-NOPE")
    assert resp.status_code == 404
    assert resp.json()["code"] == "NOT_FOUND"


@pytest.mark.parametrize(
    "path",
    [
        "event-types/x%00",
        "event-types/x%00/payload-fields",
        "event-types/x%00/subscribers",
        "event-types/x%00/versions",
        "event-types/x%00/events",
        "event-types/x%00/stats",
        "events/EV-%00x",
    ],
)
async def test_event_path_rejects_nul(client, ae, path):  # noqa: F811
    resp = await client.get(f"{ONT}/{path}")
    assert resp.status_code == 422
    assert resp.json()["code"] == "VALIDATION_FAILED"


async def test_test_event_detail_excluded_from_runtime(client, ae):  # noqa: F811
    tid = "order_status_changed"
    event = EV[tid][0]
    await ae.execute(update(Event).where(Event.code == event["id"]).values(is_test=True))
    await ae.commit()

    resp = await client.get(f"{ONT}/events/{event['id']}")
    assert resp.status_code == 200, resp.text
    assert {k: v for k, v in resp.json().items() if v is not None} == event

    remaining = window_rows(EV[tid][1:])
    rows, total = await _all(client, tid)
    assert total == len(remaining)
    assert [r["id"] for r in rows] == [r["id"] for r in remaining]
    stats = (await client.get(f"{ONT}/event-types/{tid}/stats")).json()
    assert {k: stats[k] for k in ("total", "dedup", "failed", "partial", "p95")} == health(remaining)
    assert stats["daily"] == expected_daily(EV[tid][1:])


async def test_event_stats_match_mock(client, ae):  # noqa: F811
    for e in EVENTS:
        r = await client.get(f"{ONT}/event-types/{e['id']}/stats")
        assert r.status_code == 200, r.text
        b = r.json()
        assert {k: b[k] for k in ("total", "dedup", "failed", "partial", "p95")} == health(
            window_rows(EV.get(e["id"], []))
        )
        assert b["weekly"] == len(window_rows(EV.get(e["id"], [])))
        assert b["asOf"] == "2026-10-25 15:42"
        assert b["daily"] == expected_daily(EV.get(e["id"], [])), e["id"]


async def test_run_daily_match_mock(client, ae):  # noqa: F811
    for a in ACTIONS:
        r = await client.get(f"{ONT}/action-types/{a['id']}/stats/daily")
        assert r.status_code == 200, r.text
        assert r.json()["items"] == RUN_DAILY[a["id"]], a["id"]
    b = (await client.get(f"{ONT}/action-types/{ACTIONS[0]['id']}/stats/daily", params={"days": 20})).json()
    assert len(b["items"]) == 20 and b["items"][6:] == RUN_DAILY[ACTIONS[0]["id"]]
    assert b["items"][0] == {"date": "10-06", "ok": 0, "failed": 0, "unknown": 0}
    assert (await client.get(f"{ONT}/action-types/nope/stats/daily")).status_code == 404
    assert (
        await client.get(f"{ONT}/action-types/{ACTIONS[0]['id']}/stats/daily", params={"days": 0})
    ).status_code == 422


async def test_event_rolling_boundaries_and_live_daily(client, ae):  # noqa: F811
    tid = "order_status_changed"
    selected = EV[tid][:5]
    times = [
        clock.now() - timedelta(days=8),
        clock.now() - timedelta(days=7),
        clock.now(),
        clock.now() + timedelta(seconds=1),
        clock.now() - timedelta(days=1),
    ]
    for row, when in zip(selected, times, strict=True):
        await ae.execute(update(Event).where(Event.code == row["id"]).values(occurred_at=when))
    await ae.execute(update(Event).where(Event.code == selected[-1]["id"]).values(is_test=True))
    await ae.commit()
    rows, total = await _all(client, tid)
    ids = {r["id"] for r in rows}
    assert selected[0]["id"] not in ids
    assert selected[1]["id"] in ids and selected[2]["id"] in ids
    assert selected[3]["id"] not in ids and selected[4]["id"] not in ids
    stats = (await client.get(f"{ONT}/event-types/{tid}/stats")).json()
    assert stats["weekly"] == total
    assert {k: stats[k] for k in ("total", "dedup", "failed", "partial", "p95")} == health(rows)
    all_rows = []
    from app.core.schema import _fmt_dt_seconds

    for event in (
        await ae.scalars(
            select(Event).where(
                Event.is_test.is_(False),
                Event.event_type_id
                == (select(Event.event_type_id).where(Event.code == selected[0]["id"]).scalar_subquery()),
            )
        )
    ).all():
        all_rows.append({"at": _fmt_dt_seconds(event.occurred_at)})
    assert stats["daily"] == expected_daily(all_rows), (
        "daily values must follow event mutations, not seeded aggregates"
    )
