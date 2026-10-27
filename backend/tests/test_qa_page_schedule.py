"""我的页面定时刷新 + 订阅钩子（page_schedule.py）：到点才刷、以所有者身份、失败不拖累、订阅记录。"""

from datetime import timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core import clock
from app.core.errors import AppError
from app.modules.qa import page_execution, page_schedule
from app.modules.qa.models import PageStatus, QaPage, QaPageSubscription, SubChannel, SubWhen
from tests.qa_access_support import seed_qa_access

DAILY = {"freq": "每天", "time": "08:00"}


async def _owner_code(session, page):
    from app.modules.admin.models import SysUser

    return await session.scalar(select(SysUser.code).where(SysUser.id == page.owner_id))


@pytest.fixture
async def pages(users, session):
    await seed_qa_access(users)
    await users.flush()
    rows = list(
        await session.scalars(
            select(QaPage)
            .options(selectinload(QaPage.cards), selectinload(QaPage.subscription))
            .order_by(QaPage.id)
        )
    )
    rows = [p for p in rows if p.cards]
    assert len(rows) >= 2, "种子应有带卡片的页面"
    for p in rows:
        p.next_run = None
    await session.flush()
    return rows


@pytest.fixture
def audits(monkeypatch):
    rows = []

    async def capture(_s, user, *, action, target, result="成功", detail=None):
        rows.append({"action": action, "target": target, "result": result, "detail": detail})

    monkeypatch.setattr(page_schedule, "audit", capture)
    return rows


def fake_exec(monkeypatch, value=None, fail_for=()):
    seen = []

    async def run(_s, user, cards, **_kw):
        seen.append((user.id, [c.card_key for c in cards]))
        if any(c.page_id in fail_for for c in cards):
            raise AppError("数据源超时", code="QA_PAGE_UNAVAILABLE", status_code=503)
        return [dict(value or {"value": 1}) for _ in cards], clock.now()

    async def fp(_user):  # 权限指纹走真实行级过滤（需绑定受控数据源）——这里只测调度
        return "fp"

    monkeypatch.setattr(page_execution, "execute_cards", run)
    monkeypatch.setattr(page_execution, "fingerprint", fp)
    return seen


async def test_only_due_pages_refresh_as_owner_and_reschedule(pages, session, monkeypatch, audits):
    seen = fake_exec(monkeypatch, {"value": 42})
    now = clock.now()
    due, later = pages[0], pages[1]
    due.refresh, due.next_run = DAILY, now - timedelta(minutes=1)
    later.refresh, later.next_run = DAILY, now + timedelta(hours=1)
    await session.flush()
    out = await page_schedule.refresh_due_pages(session, at=now)
    assert [o["page"] for o in out] == [due.code] and out[0]["ok"] is True, due.status_note
    assert len(seen) == 1 and seen[0][0] == await _owner_code(session, due)
    assert all(c.result_cache == {"value": 42} for c in due.cards)
    assert due.last_refresh == now and due.next_run > now and later.next_run == now + timedelta(hours=1)
    assert due.status != PageStatus.refresh_failed
    log = [a for a in audits if a["action"] == "qa.page.refresh" and a["target"] == due.code]
    assert len(log) == 1 and log[0]["detail"]["scheduled"] is True
    assert await page_schedule.refresh_due_pages(session, at=now) == []  # 已排到下次


async def test_failure_is_isolated_and_keeps_cache(pages, session, monkeypatch):
    a, b = pages[0], pages[1]
    old = [dict(c.result_cache or {}) for c in a.cards]
    fake_exec(monkeypatch, fail_for={a.id})
    now = clock.now()
    for p in (a, b):
        p.refresh, p.next_run = DAILY, now - timedelta(minutes=5)
    await session.flush()
    out = {o["page"]: o for o in await page_schedule.refresh_due_pages(session, at=now)}
    assert out[a.code]["ok"] is False and out[b.code]["ok"] is True
    assert a.status == PageStatus.refresh_failed and a.status_note == "数据源超时"
    assert [dict(c.result_cache or {}) for c in a.cards] == old  # 失败页的缓存原样保留
    assert a.next_run > now  # 失败也排下次，不每分钟重试


async def test_manual_pages_never_scheduled(pages, session, monkeypatch):
    seen = fake_exec(monkeypatch)
    p = pages[0]
    p.refresh, p.next_run = {"freq": "手动"}, None
    await session.flush()
    assert await page_schedule.refresh_due_pages(session) == [] and seen == []


async def _subscribe(session, page, *, only_changed):
    if page.subscription is None:
        page.subscription = QaPageSubscription(
            enabled=True,
            channel=SubChannel.inbox,
            recipients=["张三"],
            send_when=SubWhen.after_refresh,
            only_changed=only_changed,
        )
    else:
        s = page.subscription
        s.enabled, s.channel, s.recipients, s.send_when, s.only_changed = (
            True,
            SubChannel.inbox,
            ["张三"],
            SubWhen.after_refresh,
            only_changed,
        )
    await session.flush()


async def test_subscription_delivery_record(pages, session, monkeypatch, audits):
    p = pages[0]
    await _subscribe(session, p, only_changed=True)
    fake_exec(monkeypatch, {"value": 7})
    now = clock.now()
    p.refresh, p.next_run = DAILY, now - timedelta(minutes=1)
    await session.flush()
    out = await page_schedule.refresh_due_pages(session, at=now)
    assert out[0]["delivery"] == {"channel": "站内信", "recipients": ["张三"], "changed": True}
    assert [a["target"] for a in audits if a["action"] == "qa.page.subscription.due"] == [p.code]
    # 结果没变 + only_changed → 不投递
    p.next_run = now - timedelta(minutes=1)
    await session.flush()
    out = await page_schedule.refresh_due_pages(session, at=now)
    assert "delivery" not in out[0]


def test_register_adds_periodic_task():
    calls = []

    class _App:
        def task(self, **kw):
            calls.append(("task", kw))
            return lambda fn: fn

        def periodic(self, **kw):
            calls.append(("periodic", kw))
            return lambda fn: fn

    page_schedule.register(_App())
    assert calls == [
        ("task", {"name": "qa.page.sweep", "queue": page_schedule.QUEUE, "lock": "qa.page.sweep"}),
        ("periodic", {"cron": "* * * * *", "periodic_id": "qa.page.sweep"}),
    ]
