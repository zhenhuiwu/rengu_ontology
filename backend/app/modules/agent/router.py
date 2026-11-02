"""No-auth public QA endpoints. All ordinary APIs retain their original guards."""

import secrets
from typing import Annotated, Any

from fastapi import APIRouter, Query
from pydantic import ConfigDict, Field, field_validator

from app.core import clock
from app.core.schema import ApiModel
from app.db.dialects import get_adapter
from app.modules.agent import service
from app.modules.qa import service as qa_service
from app.modules.qa.models import QaSession

router = APIRouter(tags=["public-agent"])
OntologyId = Annotated[int, Field(gt=0, strict=True)]


class CreateIn(ApiModel):
    model_config = ConfigDict(extra="forbid")
    ontology_id: OntologyId
    caliber: str | None = Field(default=None, max_length=32)


class AskIn(ApiModel):
    model_config = ConfigDict(extra="forbid")
    ontology_id: OntologyId
    question: str = Field(min_length=1, max_length=500)
    refs: list[dict[str, Any]] = Field(default_factory=list, max_length=10)

    @field_validator("question")
    @classmethod
    def valid_question(cls, value):
        value = value.strip()
        if not value or "\x00" in value:
            raise ValueError("问题不能为空或包含无效字符")
        value.encode("utf-8")
        return value


@router.get("/config")
async def config(ontology_id: Annotated[int | None, Query(alias="ontologyId", gt=0)] = None):
    result = {"workspaces": await service.directory(), "selected": None, "metrics": [], "calibers": []}
    if ontology_id is not None:
        async with service.public_db(ontology_id) as (session, workspace, release):
            result["selected"] = {"id": ontology_id, "label": workspace.name, "version": release.ver}
            for value in ((release.snapshot or {}).get("metrics") or {}).values():
                if (
                    isinstance(value, dict)
                    and value.get("id")
                    and value.get("name")
                    and value.get("status") == "已发布"
                ):
                    result["metrics"].append(
                        {"id": value["id"], "label": value["name"], "version": value.get("version")}
                    )
            result["calibers"] = [
                {"value": label, "label": label}
                for label in sorted(await qa_service.variant_index(session, None))
            ]
    return result


@router.post("/sessions", status_code=201)
async def create(body: CreateIn):
    async with service.public_db(body.ontology_id) as (session, _workspace, _release):
        public_owner = await service.owner(session, create=True)
        caliber = qa_service.check_caliber(body.caliber, await qa_service.variant_index(session, None))
        row = QaSession(
            code="P_" + secrets.token_urlsafe(32),
            owner_id=public_owner.id,
            title="新会话",
            scope=None,
            caliber=caliber,
            last_at=clock.now(),
            created_by=service.MARKER,
        )
        session.add(row)
        await session.flush()
        return {
            "id": row.code,
            "ontologyId": body.ontology_id,
            "title": row.title,
            "caliber": caliber,
            "turns": [],
        }


@router.get("/sessions/{sid}")
async def detail(sid: str, ontology_id: Annotated[int, Query(alias="ontologyId", gt=0)]):
    async with service.public_db(ontology_id) as (session, _workspace, _release):
        row = await service.own_session(session, ontology_id, sid, await service.owner(session))
        return service.session_out(row)


@router.post("/sessions/{sid}/ask")
async def ask(sid: str, body: AskIn):
    async with service.public_db(body.ontology_id) as (session, _workspace, _release):
        await get_adapter(session).xact_lock(session, f"qa.session.turn.{sid}")
        row = await service.own_session(session, body.ontology_id, sid, await service.owner(session))
        return await service.ask(session, body.ontology_id, row, body.question, body.refs)
