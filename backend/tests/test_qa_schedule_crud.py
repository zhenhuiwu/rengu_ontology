"""Page scheduling CRUD against real scoped PG metadata; no model, worker or delivery calls."""

from copy import deepcopy
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import func, select

from app.common.outbox import OutboxEvent
from app.core import clock
from app.core.config import get_settings
from app.modules.admin.models import SysUser
from app.modules.ontology.models import OntologyMember
from app.modules.qa import page_execution
from app.modules.qa.models import (
    CardViz,
    PageSourceKind,
    QaPage,
    QaPageCard,
    QaPageShareMember,
    ShareMode,
    SharePerm,
)
from app.modules.versioning.repo import ReleaseRepo
from tests.qa_access_support import seed_qa_access

B = "/api/qa/v1/pages/FIX-SCHEDULE"
MANUAL = {
    "refresh": {"freq": "手动", "time": "--", "onFail": "保留上次结果"},
    "sub": {"on": True, "channel": "站内信", "receivers": [], "when": "每天 09:00", "onlyChanged": True},
}


@pytest.fixture
async def scheduled(users, session):
    await seed_qa_access(users)
    owner = await session.scalar(select(SysUser).where(SysUser.code == "U010001"))
    page = QaPage(
        code="FIX-SCHEDULE",
        name="隔离库调度配置",
        owner_id=owner.id,
        source_kind=PageSourceKind.manual,
        refresh=deepcopy(MANUAL["refresh"]),
        cards=[],
        members=[],
    )
    session.add(page)
    await session.flush()
    return page


async def add_plan(session, page):
    release = await ReleaseRepo(session).latest()
    page.cards.append(
        QaPageCard(
            card_key="FIX-SCHED-C1",
            sort_order=1,
            title="明确公开列",
            viz=CardViz.table,
            result_cache={},
            sem_plan={"execution": page_execution.saved_plan("SELECT store_no FROM store", release)},
        )
    )
    await session.flush()


async def test_inbox_save_reload_disable_and_no_delivery(scheduled, session, client):
    before = await session.scalar(select(func.count()).select_from(OutboxEvent))
    saved = await client.put(f"{B}/schedule", json=MANUAL)
    assert saved.status_code == 200, saved.text
    assert saved.json()["sub"]["on"] and saved.json()["nextRun"] == "不自动刷新"
    assert saved.json()["scheduleTimezone"] == "Asia/Shanghai"
    loaded = (await client.get(B)).json()
    assert loaded["subscriptionActive"] and not loaded["scheduleActive"]
    assert loaded["sub"] == MANUAL["sub"] and loaded["canSchedule"]
    assert not loaded["canRefresh"] and loaded["refreshUnavailableReason"]
    assert await session.scalar(select(func.count()).select_from(OutboxEvent)) == before
    off = deepcopy(MANUAL)
    off["sub"]["on"] = False
    assert (await client.put(f"{B}/schedule", json=off)).status_code == 200
    assert not (await client.get(B)).json()["subscriptionActive"]


@pytest.mark.parametrize(
    "plan",
    [
        {"freq": "每小时", "time": "整点"},
        {"freq": "每天", "time": "23:30"},
        {"freq": "每周", "time": "06:00", "weekday": "周一"},
        {"freq": "每月", "time": "08:00", "monthday": 31},
    ],
)
async def test_real_plan_all_frequencies_roundtrip(scheduled, session, client, plan):
    await add_plan(session, scheduled)
    body = deepcopy(MANUAL)
    body["refresh"] = {**plan, "onFail": "保留上次结果"}
    response = await client.put(f"{B}/schedule", json=body)
    assert response.status_code == 200, response.text
    loaded = (await client.get(B)).json()
    assert loaded["scheduleActive"] and loaded["subscriptionActive"] and loaded["canRefresh"]
    assert loaded["refresh"] == body["refresh"] and loaded["nextRun"] == response.json()["nextRun"]
    assert scheduled.next_run > clock.now() and loaded["scheduleTimezone"] == "Asia/Shanghai"


async def test_missing_or_stale_plan_cannot_enable_auto(scheduled, session, client):
    body = deepcopy(MANUAL)
    body["refresh"] = {"freq": "每天", "time": "08:00"}
    assert (await client.put(f"{B}/schedule", json=body)).status_code == 422
    assert scheduled.next_run is None
    await add_plan(session, scheduled)
    scheduled.cards[0].sem_plan = {"execution": {**scheduled.cards[0].sem_plan["execution"], "releaseId": -1}}
    await session.flush()
    assert (await client.put(f"{B}/schedule", json=body)).status_code == 422


async def test_schedule_uses_configured_timezone(scheduled, session, client, monkeypatch):
    monkeypatch.setattr(get_settings(), "display_tz", "America/Los_Angeles")
    await add_plan(session, scheduled)
    body = deepcopy(MANUAL)
    body["refresh"] = {"freq": "每天", "time": "23:30", "onFail": "保留上次结果"}
    result = await client.put(f"{B}/schedule", json=body)
    assert result.status_code == 200, result.text
    assert result.json()["scheduleTimezone"] == "America/Los_Angeles"
    local = scheduled.next_run.astimezone(ZoneInfo("America/Los_Angeles"))
    assert (local.hour, local.minute) == (23, 30) and scheduled.next_run > clock.now()


async def test_owner_current_membership_history_and_preview_guards(scheduled, session, client):
    other = await session.scalar(select(SysUser).where(SysUser.code == "U010763"))
    scheduled.share_mode = ShareMode.members
    scheduled.members.append(QaPageShareMember(user_id=other.id, perm=SharePerm.edit, dept=other.dept))
    member = await session.scalar(select(OntologyMember).where(OntologyMember.user_id == other.id))
    member.role = "editor"  # This case tests page ownership even with an explicit editing membership.
    await session.flush()
    assert (
        await client.put(f"{B}/schedule", json=MANUAL, headers={"X-Demo-As": other.code})
    ).status_code == 403
    assert (await client.put(f"{B}/schedule?ver=v2026.10.2", json=MANUAL)).status_code == 403
    assert (await client.put(f"{B}/schedule?previewAs={other.code}", json=MANUAL)).status_code == 403
    owner_member = await session.scalar(
        select(OntologyMember).where(OntologyMember.user_id == scheduled.owner_id)
    )
    owner_member.role = "viewer"
    await session.flush()
    assert (await client.put(f"{B}/schedule", json=MANUAL)).status_code == 403


@pytest.mark.parametrize(
    "part,value",
    [
        ("refresh", {"freq": "每天", "time": "25:00"}),
        ("refresh", {"freq": "每周", "time": "08:00"}),
        ("refresh", {"freq": "每月", "time": "08:00", "monthday": 0}),
        ("refresh", {"freq": "手动", "time": "--", "onFail": "标记失败并通知"}),
        ("sub", {**MANUAL["sub"], "channel": "邮件"}),
        ("sub", {**MANUAL["sub"], "channel": "企业微信"}),
        ("sub", {**MANUAL["sub"], "receivers": ["不存在的接收人"]}),
    ],
)
async def test_invalid_settings_are_atomic(scheduled, session, client, part, value):
    body = deepcopy(MANUAL)
    body[part] = value
    result = await client.put(f"{B}/schedule", json=body)
    assert result.status_code == 422, result.text
    loaded = (await client.get(B)).json()
    assert not loaded["subscriptionActive"] and not loaded["scheduleActive"]


async def test_only_current_shared_unique_recipient_is_allowed(scheduled, session, client):
    recipient = await session.scalar(select(SysUser).where(SysUser.code == "U010763"))
    body = deepcopy(MANUAL)
    body["sub"]["receivers"] = [recipient.name]
    assert (await client.put(f"{B}/schedule", json=body)).status_code == 422
    scheduled.members.append(
        QaPageShareMember(user_id=recipient.id, perm=SharePerm.view, dept=recipient.dept)
    )
    await session.flush()
    assert (await client.put(f"{B}/schedule", json=body)).status_code == 200
    owner = await session.get(SysUser, scheduled.owner_id)
    recipient.name = owner.name
    await session.flush()
    body["sub"]["receivers"] = [owner.name]
    assert (await client.put(f"{B}/schedule", json=body)).status_code == 422
