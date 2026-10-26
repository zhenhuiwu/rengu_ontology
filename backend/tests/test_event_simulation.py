"""Simulation validates real definitions without creating events or faking delivery."""

import pytest
from sqlalchemy import func, select

from app.modules.action.models import TypeStatus
from app.modules.event.models import (
    Event,
    EventPayloadField,
    EventSource,
    EventSubscription,
    EventType,
    SubscriberKind,
    SubscriberStatus,
)
from tests.function_access_support import grant_fixture_roles

B = "/api/ontology/v1"


@pytest.fixture
async def simulation_type(users):
    await grant_fixture_roles(users, {"U010001": "R-01"})
    model = EventType(
        code="simulation_real",
        name="校验事件",
        api="SimulationReal",
        source=EventSource.external,
        status=TypeStatus.published,
        version="v1",
        retention_days=30,
    )
    model.payload = [
        EventPayloadField(seq=0, api="quantity", name="数量", type="整数", required=True, sample="3")
    ]
    model.subscribers = [
        EventSubscription(
            seq=i,
            name=name,
            kind=SubscriberKind.automation,
            status=status,
            filter_text=filter_,
            then_text="无",
        )
        for i, (name, status, filter_) in enumerate(
            [
                ("匹配", SubscriberStatus.enabled, "数量 > 1"),
                ("不匹配", SubscriberStatus.error, "载荷.quantity < 0"),
                ("未知", SubscriberStatus.enabled, "未知配置()"),
                ("停用", SubscriberStatus.disabled, "全部"),
            ]
        )
    ]
    users.add(model)
    await users.flush()
    return model


async def test_validate_payload_filter_and_no_event(simulation_type, users, client):
    before = await users.scalar(select(func.count()).select_from(Event))
    r = await client.post(f"{B}/event-types/simulation_real:simulate", json={})
    assert r.status_code == 200, r.text
    result = r.json()
    assert result["testEventId"] is None and result["deliveredTo"] == [] and result["payloadValid"]
    assert [(row["name"], row["matched"]) for row in result["checks"]] == [
        ("匹配", True),
        ("不匹配", False),
        ("未知", None),
    ]
    assert await users.scalar(select(func.count()).select_from(Event)) == before
    for payload in ({}, {"quantity": "3"}, {"quantity": True}):
        r = await client.post(f"{B}/event-types/simulation_real:simulate", json={"payload": payload})
        assert r.status_code == 422, r.text


async def test_readonly_and_draft_rejected(simulation_type, users, client):
    for suffix in ("?ver=v1", "?previewAs=U010001", "?ver=v1&previewAs=U010001"):
        r = await client.post(f"{B}/event-types/simulation_real:simulate{suffix}", json={})
        assert r.status_code == 403 and r.json()["code"] == "READONLY", r.text
    simulation_type.status = TypeStatus.draft
    await users.flush()
    r = await client.post(f"{B}/event-types/simulation_real:simulate", json={})
    assert r.status_code == 409 and r.json()["code"] == "TYPE_IS_DRAFT", r.text


async def test_saved_draft_overlay_is_used(simulation_type, users, client):
    from app.common.auth import resolve_user
    from app.modules.static.cr_write import CrWriteService
    from app.modules.static.schemas import CRCreateIn, ItemIn, ItemsPutIn, TargetIn

    actor = await resolve_user(users, "U010001")
    writer = CrWriteService(users)
    target = TargetIn(kind="event", id=simulation_type.code)
    cr = await writer.create(actor, CRCreateIn(title="事件模拟草稿", target=target))
    await writer.put_items(
        cr, actor, ItemsPutIn(target=target, section="subs", rows=[ItemIn(key="匹配", op="delete")])
    )
    response = await client.post(
        f"{B}/event-types/{simulation_type.code}:simulate?view=draft&cr={cr}", json={}
    )
    assert response.status_code == 200, response.text
    assert [row["name"] for row in response.json()["checks"]] == ["不匹配", "未知"]
    assert await users.scalar(select(func.count()).select_from(Event)) == 0
