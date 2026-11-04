"""Offline business examples for pinned metric composition and calendar windows."""

import asyncio
from datetime import date, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

from app.modules.static import metric_program as program
from app.modules.static import metric_trial
from app.modules.static.metric_trial import parse_formula


async def mcp_filter_contract():
    from app.modules.qa.mcp_tools import _metric_query, _ParamError

    context = SimpleNamespace(session=None, user=object())
    with patch.object(metric_trial, "evaluate_metric", AsyncMock(return_value={"rows": []})) as evaluate:
        for extra in ({"filters": [{"dimension": "object__kind", "value": "restricted"}]},
                      {"dimensions": ["object__kind"]}, {"time": {"start": "2026-10-01"}}):
            try:
                await _metric_query(context, {"metric": "P-1", "snapshot": True, **extra})
            except _ParamError:
                pass
            else:
                raise AssertionError("MCP silently discarded an unsupported query restriction")
        evaluate.assert_not_awaited()
        await _metric_query(context, {"metric": "P-1", "snapshot": True})
        evaluate.assert_awaited_once_with(context.session, context.user, "P-1", days=30,
                                          period="day", snapshot=True)


async def calendar_history_contract():
    requested_start, end = date(2026, 10, 15), date(2026, 10, 20)
    records = [
        (date(2026, 9, 1), Decimal(100)),
        (date(2026, 9, 20), Decimal(10)),
        (date(2026, 10, 20), Decimal(20)),
    ]

    async def child(_session, _user, code, **kwargs):
        assert code == "M-1"
        lower = metric_trial.metric_window(kwargs["days"], at=kwargs["at"], max_days=731)[0]
        totals = {}
        for day, value in records:
            if lower <= day <= end:
                bucket = str(day.replace(day=1))
                totals[bucket] = totals.get(bucket, Decimal(0)) + value
        return {
            "version": "v1",
            "rows": [{"date": day, "value": str(value)} for day, value in totals.items()],
        }

    parent = {"id": "P-1", "version": "v1", "def": [], "formula": "MOVING_AVG(METRIC('sales','v1'),2)"}
    session = SimpleNamespace(
        info={"ontology_id": 17},
        scalars=AsyncMock(
            return_value=[SimpleNamespace(code="M-1", version="v1", status=SimpleNamespace(value="已发布"))]
        ),
    )
    evaluate_parent = metric_trial.evaluate_metric.__wrapped__
    with (
        patch.object(metric_trial, "check_func"),
        patch("app.modules.governance.policy.enforce", AsyncMock()),
        patch.object(metric_trial, "program_details", AsyncMock()),
        patch.object(metric_trial, "metric_definition", AsyncMock(return_value=parent)),
        patch.object(metric_trial, "evaluate_metric", side_effect=child),
    ):
        result = await evaluate_parent(
            session,
            None,
            "P-1",
            days=(end - requested_start).days + 1,
            period="month",
            at=datetime(2026, 10, 20, 12, tzinfo=ZoneInfo("Asia/Shanghai")),
        )
    current = next(row for row in result["rows"] if row["date"] == "2026-10-01")
    assert Decimal(current["value"]) == 65, (
        f"previous month must include its first day; got {current['value']}"
    )


async def query_contract():
    """Query orchestration over fake evaluated aggregates, never database rows."""
    principal = object()
    now = datetime(2026, 10, 20, 12, tzinfo=ZoneInfo("Asia/Shanghai"))
    manifest = {
        "metrics": [
            {"name": name, "type": kind, "config": {"meta": {"rengu": {"id": code, "version": "v1"}}}}
            for name, code, kind in (("net", "P-1", "program"), ("other", "M-2", "simple"))
        ]
    }
    calls = []

    async def evaluate(_session, user, code, **kwargs):
        assert user is principal
        calls.append((code, kwargs))
        if kwargs["snapshot"]:
            rows = [{"date": "当前", "value": None if code == "P-1" else "2"}]
        elif kwargs["period"] == "month":
            if code == "P-1":

                async def load(_reference, _version, _padding):
                    return {"rows": [{"date": "2026-10-01", "value": "80"}]}

                rows = (
                    await program.evaluate(
                        parse_formula("METRIC('sales','v1')"),
                        load,
                        period="month",
                        snapshot=False,
                        start=date(2026, 10, 15),
                        end=date(2026, 10, 20),
                    )
                )["rows"]
            else:
                rows = [{"date": "2026-10-01", "value": "7"}]
        else:
            rows = (
                [{"date": "2026-10-01", "value": "80"}, {"date": "2026-10-03", "value": "300"}]
                if code == "P-1"
                else [{"date": "2026-10-02", "value": "2"}]
            )
        return {"version": "v1", "rows": rows}

    async def query(**kwargs):
        return await program.query_programs(None, principal, manifest, ["net", "other"], now=now, **kwargs)

    with patch.object(metric_trial, "evaluate_metric", side_effect=evaluate):
        result = await query(time_range={"start": "2026-10-01", "end": "2026-10-04", "grain": "day"})
        assert result["columns"] == ["metric_time", "net", "other"]
        assert result["rows"] == [
            ["2026-10-01", Decimal(80), None],
            ["2026-10-02", None, Decimal(2)],
            ["2026-10-03", Decimal(300), None],
        ]
        assert len(calls) == 2
        for _, params in calls:
            assert params["days"] == 3 and params["period"] == "day" and not params["snapshot"]
            assert params["at"].isoformat() == "2026-10-03T23:59:59.999999+08:00"
        calls.clear()
        snapshot = await query()
        assert snapshot["columns"] == ["net", "other"] and snapshot["rows"] == [[None, Decimal(2)]]
        assert all(params["snapshot"] and params["at"] is None for _, params in calls)
        for kwargs in ({"where": ["unsafe"]}, {"group_by": ["object__kind"]}):
            before = len(calls)
            try:
                await query(**kwargs)
            except program.AppError:
                pass
            else:
                raise AssertionError("Unmapped filter/dimension accepted")
            assert len(calls) == before
        monthly = await query(time_range={"start": "2026-10-15", "end": "2026-10-21", "grain": "month"})
        assert monthly["rows"] == [["2026-10-01", Decimal(80), Decimal(7)]], (
            "partial first calendar bucket must not vanish from the program column"
        )
        for malformed in ({"grain": ["day"]}, ["start"]):
            try:
                await query(time_range=malformed)
            except program.AppError:
                pass
            else:
                raise AssertionError("Malformed time shape accepted")
    with patch.object(metric_trial, "evaluate_metric", AsyncMock(return_value={"version": "v2", "rows": []})):
        try:
            await query()
        except program.AppError:
            pass
        else:
            raise AssertionError("Changed metric version accepted")


async def check():
    samples = {
        "sales": [("2026-10-01", 100), ("2026-10-03", 300), ("2026-10-08", 150)],
        "refunds": [("2026-10-01", 20), ("2026-10-03", 0), ("2026-10-08", 30)],
        "lat": [("2026-10-01", 1)],
        "zero": [("2026-10-01", 0)],
    }
    calls = []

    async def load(code, version, padding):
        assert version == "v1"
        calls.append((code, padding))
        return {"rows": [{"date": d, "value": str(v)} for d, v in samples[code]]}

    async def run(formula):
        return await program.evaluate(
            parse_formula(formula),
            load,
            period="day",
            snapshot=False,
            start=date(2026, 10, 1),
            end=date(2026, 10, 8),
        )

    def values(result):
        return {
            row["date"]: Decimal(row["value"]) if row["value"] is not None else None for row in result["rows"]
        }

    result = values(await run("METRIC('sales','v1')-METRIC('refunds','v1')"))
    assert result == {"2026-10-01": Decimal(80), "2026-10-03": Decimal(300), "2026-10-08": Decimal(120)}
    ratio = values(await run("METRIC('sales','v1')/METRIC('refunds','v1')"))
    assert ratio["2026-10-03"] is None
    moving = values(await run("MOVING_AVG(METRIC('sales','v1'),2)"))
    assert moving["2026-10-03"] == 300, "calendar windows must not use the previous available row"
    previous = values(await run("PREV(METRIC('sales','v1'),1)"))
    assert previous["2026-10-02"] == 100 and previous["2026-10-04"] == 300
    wow = values(await run("WOW(METRIC('sales','v1'))"))
    assert wow["2026-10-08"] == 50
    running = values(await run("RUNNING_SUM(METRIC('sales','v1'))"))
    assert running["2026-10-08"] == 550
    geometry = values(await run("GEO_DISTANCE(METRIC('lat','v1'),0,METRIC('zero','v1'),0)"))
    assert abs(geometry["2026-10-01"] - Decimal("111.195")) < Decimal("0.001")
    before = len(calls)
    for formula in ("METRIC('sales')", "EVIL(METRIC('sales','v1'))", "METRIC('sales','v1')+secret"):
        try:
            await run(formula)
        except program.AppError:
            pass
        else:
            raise AssertionError(formula)
    assert len(calls) == before, "invalid programs must fail before reading referenced metrics"

    @program.bounded
    async def recursive(session, user, code):
        return await recursive(session, user, code)

    try:
        await recursive(None, None, "cycle")
    except program.AppError:
        pass
    else:
        raise AssertionError("cycle allowed")
    assert program._budget.get() is None
    await query_contract()
    await calendar_history_contract()
    await mcp_filter_contract()
    print("PASS: independent aggregates, null propagation, calendar windows, geometry and bounded recursion")


if __name__ == "__main__":
    asyncio.run(check())
