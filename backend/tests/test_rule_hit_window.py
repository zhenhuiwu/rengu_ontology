"""规则命中7/30天真实滚动窗口，排除未来记录。"""

from datetime import timedelta

from sqlalchemy import func, select

from app.core.clock import now
from app.modules.dynamic.rules_models import HitResult, Rule, RuleHit
from tests.dynamic_support import API, dyn  # noqa: F401


async def test_rule_hits_rolling_window_and_future(dyn, client, monkeypatch):  # noqa: F811
    rule = await dyn.scalar(select(Rule).where(Rule.code == "R-007"))
    rule.weekly = 999999  # 定义历史统计不能冒充当前近7天。
    records = [
        ("CURRENT", now()),
        ("SEVEN", now() - timedelta(days=7)),
        ("OLD7", now() - timedelta(days=7, seconds=1)),
        ("OLD30", now() - timedelta(days=30, seconds=1)),
        ("FUTURE", now() + timedelta(seconds=1)),
    ]
    for i, (name, at) in enumerate(records):
        dyn.add(
            RuleHit(
                code=f"HIT-WINDOW-{name}",
                rule_id=rule.id,
                seq=-10 + i,
                at=at,
                obj_kind="store",
                obj_id="store",
                obj_label="Store",
                dims=[],
                value="1",
                threshold="0",
                result=HitResult.notified,
            )
        )
    await dyn.flush()
    seven = (await client.get(f"{API}/rules/R-007/hits", params={"range": "7d", "size": 200})).json()
    thirty = (await client.get(f"{API}/rules/R-007/hits", params={"range": "30d", "size": 200})).json()
    seven_ids = {r["id"] for r in seven["items"] if r["id"].startswith("HIT-WINDOW-")}
    thirty_ids = {r["id"] for r in thirty["items"] if r["id"].startswith("HIT-WINDOW-")}
    assert seven_ids == {"HIT-WINDOW-CURRENT", "HIT-WINDOW-SEVEN"}
    assert thirty_ids == seven_ids | {"HIT-WINDOW-OLD7"}
    until = now()
    expected = await dyn.scalar(
        select(func.count()).where(
            RuleHit.rule_id == rule.id, RuleHit.at >= until - timedelta(days=7), RuleHit.at <= until
        )
    )
    detail = (await client.get(f"{API}/rules/{rule.code}")).json()
    assert detail["hits7d"] == detail["weekly"] == expected
    listed = (await client.get(f"{API}/rules", params={"size": 200})).json()["items"]
    assert next(r for r in listed if r["id"] == rule.code)["weekly"] == expected
    assert rule.weekly == 999999
    from app.core import clock
    from app.modules.dynamic import rules_service
    from app.modules.dynamic.catalog import get_catalog

    advanced = until + timedelta(days=90)
    monkeypatch.setattr(rules_service, "now", lambda: advanced)
    monkeypatch.setattr(clock, "now", lambda: advanced)
    assert (await client.get(f"{API}/rules/{rule.code}")).json()["hits7d"] == 0
    assert (await get_catalog(dyn)).rule(rule.code)["weekly"] == 0


async def test_lifecycle_illegal_and_transition_weekly_roll_with_clock(dyn, client, monkeypatch):  # noqa: F811
    from app.core import clock
    from app.modules.dynamic import rules_service
    from app.modules.dynamic.rules_models import (
        Lifecycle,
        LifecycleChange,
        LifecycleTransition,
        LifecycleVersion,
    )

    lc = await dyn.scalar(select(Lifecycle).where(Lifecycle.code == "order"))
    transition = await dyn.scalar(
        select(LifecycleTransition)
        .join(LifecycleVersion, LifecycleVersion.id == LifecycleTransition.version_id)
        .where(LifecycleVersion.lifecycle_id == lc.id, LifecycleVersion.version == lc.version)
        .order_by(LifecycleTransition.id)
        .limit(1)
    )
    transition.weekly = 999999
    until = now()
    times = [
        until,
        until - timedelta(days=7),
        until - timedelta(days=7, seconds=1),
        until + timedelta(seconds=1),
    ]
    for i, at in enumerate(times):
        for kind, ref in (("bad", None), ("ok", transition.id)):
            dyn.add(
                LifecycleChange(
                    code=f"LC-WINDOW-{kind}-{i}",
                    lifecycle_id=lc.id,
                    at=at,
                    obj_id=f"window-{kind}-{i}",
                    obj_label="Window",
                    from_state="window-from",
                    to_state="window-to",
                    transition_id=ref,
                    by="真实记录",
                )
            )
    await dyn.flush()
    illegal = (await client.get(f"{API}/object-types/order/lifecycle/illegal")).json()
    assert next(g for g in illegal if g["from"] == "window-from")["count"] == 2
    expected = await dyn.scalar(
        select(func.count())
        .select_from(LifecycleChange)
        .join(LifecycleTransition, LifecycleTransition.id == LifecycleChange.transition_id)
        .where(
            LifecycleChange.lifecycle_id == lc.id,
            LifecycleTransition.tr_code == transition.tr_code,
            LifecycleChange.at >= until - timedelta(days=7),
            LifecycleChange.at <= until,
        )
    )
    detail = (await client.get(f"{API}/object-types/order/lifecycle")).json()
    assert next(t for t in detail["transitions"] if t["id"] == transition.tr_code)["weekly"] == expected
    assert transition.weekly == 999999
    bad = await client.get(f"{API}/object-types/order/lifecycle/illegal", params={"since": "not-a-date"})
    assert bad.status_code == 422
    advanced = until + timedelta(days=90)
    monkeypatch.setattr(rules_service, "now", lambda: advanced)
    monkeypatch.setattr(clock, "now", lambda: advanced)
    assert (await client.get(f"{API}/object-types/order/lifecycle/illegal")).json() == []
    fresh = (await client.get(f"{API}/object-types/order/lifecycle")).json()
    assert all(t["weekly"] == 0 for t in fresh["transitions"])
