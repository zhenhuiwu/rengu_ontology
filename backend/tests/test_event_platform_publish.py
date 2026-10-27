"""One real-PG CR save/publish-hook case in the isolated fix DB; no runtime result or event is seeded."""

from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from app.common.auth import CurrentUser
from app.core import clock
from app.core.errors import AppError
from app.modules.event.ingress import receive
from app.modules.event.ingress_schema import IngressIn
from app.modules.event.models import Event, EventType
from app.modules.event.platform_context import ACTION_RUN, validate_draft
from app.modules.event.writeback import definition, payload
from app.modules.static.cr_write import CrWriteService
from app.modules.static.models import ChangeRequest, CrItem, CRLevel, CRStatus, ObjectType
from app.modules.static.schemas import ItemsPutIn
from app.modules.static.writeback import Ctx


async def test_real_cr_preserves_bounded_platform_context_without_business_object(session):
    assert session.bind.engine.url.database == "rengu_fix_qa_20261027"
    assert await session.scalar(select(EventType.id).where(EventType.api == "ActionExecuted")) is None
    now = clock.now()
    cr = ChangeRequest(code="WF-PLATFORM-CONTEXT", title="标准动作事件合同", owner="技术验收", reviewer="",
                       status=CRStatus.draft, level=CRLevel.minor, status_at=now, detail={})
    session.add(cr)
    await session.flush()
    before_events = await session.scalar(select(func.count()).select_from(Event))
    before_objects = await session.scalar(select(func.count()).select_from(ObjectType))
    actor = CurrentUser(id="fixture-owner", name="技术验收", func={"event-types": "manage"}, ontology_id=1, ontology_role="owner")
    service = CrWriteService(session)
    target = {"kind": "event", "id": "WF_STANDARD_COMPLETE", "isNew": True, "name": "动作执行完成"}
    values = {"name": "动作执行完成", "api": "ActionExecuted", "source": "动作", "objKind": ACTION_RUN}
    for section, rows in (("def", [{"key": key, "op": "add", "after": value} for key, value in values.items()]),
                          ("payload", [{"key": "runId", "op": "add", "after": {"api": "runId", "name": "执行记录", "type": "字符串", "required": True}}])):
        await service.put_items(cr.code, actor, ItemsPutIn.model_validate({"target": target, "section": section, "rows": rows}))
    assert await validate_draft(session, target["id"], cr.id, complete=True)
    assert await session.scalar(select(EventType.id).where(EventType.code == target["id"])) is None
    ctx = Ctx(session, cr, "v2099.01.1", now, actor.name, True, target["name"])
    for section, hook in (("def", definition), ("payload", payload)):
        rows = list(await session.scalars(select(CrItem).where(CrItem.cr_id == cr.id, CrItem.section == section)))
        await hook(ctx, target["id"], rows)
    stored = await session.scalar(select(EventType).where(EventType.code == target["id"]))
    assert stored.object_api == "ActionRun" and stored.obj_kind is None
    with pytest.raises(AppError) as denied:
        await receive(session, IngressIn(event_id="WF-SPOOFED-COMPLETION", event_type=stored.code,
            occurred_at=now, source="http", object_type="ActionRun", object_id="not-a-run",
            payload={"runId": "not-a-run"}), request=SimpleNamespace(state=SimpleNamespace()))
    assert denied.value.code == "EVENT_SOURCE_NOT_EXTERNAL" and denied.value.status_code == 403
    assert await session.scalar(select(func.count()).select_from(ObjectType)) == before_objects
    assert await session.scalar(select(func.count()).select_from(Event)) == before_events
