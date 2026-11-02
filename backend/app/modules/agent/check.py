"""One offline public-adapter boundary check. No database, model, network or queue."""

import asyncio
from contextlib import asynccontextmanager, suppress
from types import SimpleNamespace as Namespace
from unittest.mock import AsyncMock, patch

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.dialects import postgresql

import app.db.models  # noqa: F401
from app.core.errors import NotFound
from app.modules.admin.models import SysUser, UserStatus
from app.modules.agent import service
from app.modules.agent.router import CreateIn, create, router
from app.modules.qa.models import TurnKind, TurnStatus
from app.modules.static.models import Metric


async def main():
    with (
        patch.object(service, "get_settings", return_value=Namespace(agent_public_ontology_ids=[])),
        patch.object(service, "get_sessionmaker") as sessions,
    ):
        assert await service.directory() == []
        try:
            async with service.public_db(16):
                raise AssertionError("closed public namespace opened")
        except NotFound:
            pass
        sessions.assert_not_called()
    with patch.object(service, "get_settings", return_value=Namespace(agent_public_ontology_ids=[16])):
        service.require_public(16)
        for bad in (1, True, "16"):
            try:
                service.require_public(bad)
            except NotFound:
                pass
            else:
                raise AssertionError("allowlist widened")
    try:
        CreateIn.model_validate({"ontologyId": 16, "ownerId": 1, "createdBy": "admin"})
    except ValidationError:
        pass
    else:
        raise AssertionError("owner fields accepted")
    sid = "P_" + "a" * 43
    row = Namespace(code=sid, ontology_id=16, owner_id=7, created_by=service.MARKER)
    public_owner = Namespace(id=7)
    detail = AsyncMock(return_value=row)
    with patch.object(service.repo, "SessionRepo", return_value=Namespace(detail=detail)):
        assert await service.own_session(None, 16, sid, public_owner) is row
        for field, wrong in (("ontology_id", 1), ("owner_id", 1), ("created_by", "admin")):
            previous = getattr(row, field)
            setattr(row, field, wrong)
            try:
                await service.own_session(None, 16, sid, public_owner)
            except NotFound:
                pass
            else:
                raise AssertionError("public session marker bypassed")
            setattr(row, field, previous)
        before = detail.await_count
        with suppress(NotFound):
            await service.own_session(None, 16, "S0001", public_owner)
        assert detail.await_count == before

    async def scalar(statement):
        return None

    added = []

    async def flush():
        for item in added:
            item.id = 7

    session = Namespace(scalar=AsyncMock(side_effect=scalar), add=added.append, flush=flush)
    with patch.object(service, "get_adapter", return_value=Namespace(xact_lock=AsyncMock())):
        owner = await service.owner(session, create=True)
    assert len(added) == 1 and isinstance(owner, SysUser)
    assert (
        owner.status == UserStatus.disabled
        and owner.account is None
        and owner.role is None
        and owner.created_by == service.MARKER
    )
    created_rows = []
    create_session = Namespace(add=created_rows.append, flush=AsyncMock())

    @asynccontextmanager
    async def public_database(ontology_id):
        assert ontology_id == 16
        yield create_session, None, None

    with (
        patch.object(service, "public_db", public_database),
        patch.object(service, "owner", AsyncMock(return_value=owner)),
        patch.object(service.qa_service, "variant_index", AsyncMock(return_value={})),
    ):
        created = await create(CreateIn(ontology_id=16))
    assert service.SESSION_CODE.fullmatch(created["id"]) and created["turns"] == []
    assert len(created_rows) == 1 and created_rows[0].created_by == service.MARKER
    actor = await service.actor(Namespace(scalars=AsyncMock(return_value=["Device"])), 16)
    assert actor.row_filters == {"Device": None} and not actor.is_ontology_admin
    assert (
        actor.func.get("action-types") is None
        and actor.func.get("rules") is None
        and not actor.action_allowlist
    )
    selected = Namespace(statement=select(Metric), is_select=True)
    service.published_only(selected)
    sql = str(
        selected.statement.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})
    )
    assert "metric.status = 'published'" in sql
    raw = Namespace(statement=service._BOUND_SQL, is_select=False)
    service.published_only(raw)
    assert "status = 'published'" in raw.statement.text
    turn = Namespace(
        code=sid + "-T001",
        kind=TurnKind.answer,
        asked_at=None,
        question="设备数",
        caliber_variant=None,
        vote="",
        is_seed=False,
        status=TurnStatus.done,
        sql_text="private-sql",
        release_id=3,
        answered_ver="v1",
        snapshot={
            "ownerId": "admin",
            "semPlan": {"private": True},
            "_publicContext": {"rootQuestion": "private-context"},
            "answer": {
                "card": "table",
                "text": "真实结果",
                "table": {"items": [{"count": 25}]},
                "sql": "private-sql",
                "intentTrace": {"admin": True},
            },
        },
    )
    output = service.public_turn(turn)
    assert output["answer"]["table"]["items"] == [{"count": 25}]
    assert "private" not in str(output) and "ownerId" not in output and "sql" not in output["answer"]
    turn.snapshot = {"answer": {"card": "action", "action": {"name": "测试建议", "stage": {"admin": True}}}}
    output = service.public_turn(turn)
    assert output["answer"]["action"]["canStage"] is False and "不支持执行" in output["answer"]["text"]
    turn.snapshot = {
        "_publicContext": {"rootQuestion": "设备数"},
        "answer": {"clarify": {"term": "状态", "options": [{"value": "o1", "label": "正常"}]}},
    }
    assert "设备数" in service.contextual_question(
        Namespace(turns=[turn]), "o1"
    ) and "正常" in service.contextual_question(Namespace(turns=[turn]), "o1")
    assert service.contextual_question(Namespace(turns=[]), "新问题") == "新问题"
    assert {(route.path, next(iter(route.methods))) for route in router.routes} == {
        ("/config", "GET"),
        ("/sessions", "POST"),
        ("/sessions/{sid}", "GET"),
        ("/sessions/{sid}/ask", "POST"),
    }
    assert all(not route.dependant.dependencies for route in router.routes)
    print(
        "public QA: allowlist/session/disabled owner/read-only scope/output/clarify/routes passed (offline)"
    )


if __name__ == "__main__":
    asyncio.run(main())
