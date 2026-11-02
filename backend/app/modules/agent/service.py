"""Public adapter over existing QA services; no login identity or management grants."""

import re
from contextlib import asynccontextmanager
from copy import deepcopy

from sqlalchemy import event, select, text
from sqlalchemy.orm import with_loader_criteria

from app.common.auth import CurrentUser
from app.common.context import ViewContext
from app.common.ontology import set_ontology_scope
from app.common.sql_guard import _BOUND_SQL
from app.core.config import get_settings
from app.core.errors import AppError, NotFound
from app.db.dialects import get_adapter
from app.db.session import get_sessionmaker
from app.modules.admin.models import AuthCredential, SysRoleMember, SysUser, UserStatus
from app.modules.ontology.models import OntologyMember, OntologyWorkspace
from app.modules.qa import repo
from app.modules.qa import service as qa_service
from app.modules.qa.llm import get_llm
from app.modules.qa.router import _ask_once
from app.modules.qa.schemas import AskIn
from app.modules.static.models import (
    DefStatus,
    Dimension,
    LinkType,
    Metric,
    MetricStatus,
    ObjectStatus,
    ObjectType,
)
from app.modules.versioning.repo import ReleaseRepo

MARKER = "agent-public-v1"
OWNER_CODE = "AGENT-PUBLIC"
SESSION_CODE = re.compile(r"P_[A-Za-z0-9_-]{43}")
PUBLIC_NOTICE = "公共入口仅提供问答，不支持执行动作或规则。"


def allowed_ids():
    return tuple(dict.fromkeys(get_settings().agent_public_ontology_ids))


def require_public(ontology_id):
    if type(ontology_id) is not int or ontology_id not in allowed_ids():
        raise NotFound("公开本体不存在")


def published_only(execute_state):
    if getattr(execute_state.statement, "text", None) == _BOUND_SQL.text:
        execute_state.statement = text(_BOUND_SQL.text + " AND status = 'published'")
    if execute_state.is_select:
        execute_state.statement = execute_state.statement.options(
            with_loader_criteria(
                ObjectType, ObjectType.status == ObjectStatus.published, include_aliases=True
            ),
            with_loader_criteria(Metric, Metric.status == MetricStatus.published, include_aliases=True),
            with_loader_criteria(LinkType, LinkType.status == DefStatus.published, include_aliases=True),
            with_loader_criteria(Dimension, Dimension.status == DefStatus.published, include_aliases=True),
        )


@asynccontextmanager
async def public_db(ontology_id):
    require_public(ontology_id)
    async with get_sessionmaker()() as session, session.begin():
        event.listen(session.sync_session, "do_orm_execute", published_only)
        try:
            await set_ontology_scope(session, ontology_id)
            workspace = await session.scalar(
                select(OntologyWorkspace).where(
                    OntologyWorkspace.id == ontology_id,
                    OntologyWorkspace.deleted_at.is_(None),
                    OntologyWorkspace.permanently_deleted_at.is_(None),
                )
            )
            release = await ReleaseRepo(session).latest()
            if workspace is None or release is None:
                raise NotFound("公开本体不存在")
            yield session, workspace, release
        finally:
            event.remove(session.sync_session, "do_orm_execute", published_only)


async def directory():
    result = []
    for ontology_id in allowed_ids():
        try:
            async with public_db(ontology_id) as (_, workspace, _release):
                result.append({"id": ontology_id, "label": workspace.name})
        except NotFound:
            continue
    return result


async def owner(session, *, create=False):
    if create:
        await get_adapter(session).xact_lock(session, "agent.public.owner")
    user = await session.scalar(select(SysUser).where(SysUser.code == OWNER_CODE))
    if user is None and create:
        user = SysUser(
            code=OWNER_CODE,
            name="公共问数会话",
            account=None,
            status=UserStatus.disabled,
            role=None,
            created_by=MARKER,
            alert_assignable=None,
        )
        session.add(user)
        await session.flush()
    if user is None:
        raise NotFound("公开会话不存在")
    if (
        user.created_by != MARKER
        or user.status != UserStatus.disabled
        or user.account is not None
        or user.role is not None
        or await session.scalar(
            select(AuthCredential.user_id).where(AuthCredential.user_id == user.id).limit(1)
        )
        or await session.scalar(select(SysRoleMember.id).where(SysRoleMember.user_id == user.id).limit(1))
        or await session.scalar(select(OntologyMember.id).where(OntologyMember.user_id == user.id).limit(1))
    ):
        raise AppError("公共问数暂不可用", code="PUBLIC_QA_UNAVAILABLE", status_code=503)
    return user


async def actor(session, ontology_id):
    objects = await session.scalars(
        select(ObjectType.code).where(ObjectType.status == ObjectStatus.published)
    )
    return CurrentUser(
        id=OWNER_CODE,
        name="公共问数",
        zones=frozenset({"use"}),
        func={"qa-workbench": "view", "object-search": "view", "object-types": "view", "metrics": "view"},
        row_filters={code: None for code in objects},
        ontology_id=ontology_id,
        ontology_role="viewer",
        action_allowlist=(),
        event_allowlist=(),
        function_allowlist=(),
    )


async def own_session(session, ontology_id, sid, public_owner):
    if not SESSION_CODE.fullmatch(sid):
        raise NotFound("公开会话不存在")
    row = await repo.SessionRepo(session).detail(sid)
    if (
        row is None
        or row.ontology_id != ontology_id
        or row.owner_id != public_owner.id
        or row.created_by != MARKER
    ):
        raise NotFound("公开会话不存在")
    return row


def public_turn(turn):
    source = qa_service.turn_out(turn)
    result = {
        key: deepcopy(source[key])
        for key in ("id", "kind", "time", "question", "lead", "bold", "tail", "tag", "code", "text")
        if key in source
    }
    if isinstance(source.get("answer"), dict):
        answer = source["answer"]
        if answer.get("card") in ("action", "rule"):
            result["answer"] = {
                "card": "action",
                "text": PUBLIC_NOTICE,
                "action": {
                    "name": (answer.get("action") or {}).get("name", "动作建议"),
                    "mode": "suggest",
                    "canStage": False,
                },
            }
        else:
            result["answer"] = {
                key: deepcopy(answer[key])
                for key in ("card", "text", "dataAsOf", "table", "chart", "charts", "clarify")
                if key in answer
            }
            result["answer"]["canPin"] = False
            if isinstance(answer.get("basis"), dict):
                result["answer"]["basis"] = {
                    key: deepcopy(answer["basis"][key])
                    for key in ("caliber", "defaultRange")
                    if key in answer["basis"]
                }
            if isinstance(answer.get("suggest"), dict):
                result["answer"]["suggest"] = {
                    "followUps": list(answer["suggest"].get("followUps") or [])[:6]
                }
    return result


def session_out(row):
    return {
        "id": row.code,
        "ontologyId": row.ontology_id,
        "title": row.title,
        "caliber": row.caliber,
        "turns": [public_turn(turn) for turn in row.turns],
    }


def contextual_question(row, question):
    """Bounded follow-up only; no fabricated history or cross-session references."""
    turns = row.turns[-4:]
    if not turns:
        return question
    previous = turns[-1]
    root = ((previous.snapshot or {}).get("_publicContext") or {}).get("rootQuestion")
    previous_question = root if isinstance(root, str) else previous.question
    answer = (previous.snapshot or {}).get("answer") or {}
    clarify = answer.get("clarify")
    if isinstance(clarify, dict) and previous_question:
        options = {
            str(option.get("value")): str(option.get("label"))
            for option in clarify.get("options", [])
            if isinstance(option, dict)
        }
        choice = options.get(question, question)
        return (
            f"原问题：{previous_question[:500]}\n本次澄清（{str(clarify.get('term') or '')[:100]}）：{choice}"
        )
    if previous_question and re.match(
        r"^(?:再|那么|那|其中|这些|它们|改为|换成|按|分别|对比|比较|继续|只看)", question
    ):
        return f"上一问题：{previous_question[:500]}\n当前问题：{question}"
    return question


async def ask(session, ontology_id, row, question, refs):
    from app.modules.admin.model_settings import load_model_runtime
    from app.modules.qa.chain import open_qa_conn

    user = await actor(session, ontology_id)
    effective = contextual_question(row, question)
    root = question
    if effective != question and row.turns:
        previous = row.turns[-1]
        root = (
            ((previous.snapshot or {}).get("_publicContext") or {}).get("rootQuestion")
            or previous.question
            or question
        )
    body = AskIn(question=question, refs=refs).model_copy(update={"question": effective})
    llm = get_llm(await load_model_runtime(session, "qa"))
    try:
        async with open_qa_conn(ontology_id) as connection:
            turn = await _ask_once(
                session, connection, llm, user, ViewContext(), row.owner_id, row.code, body
            )
        turn.question = question
        turn.created_by = MARKER
        turn.snapshot = {**(turn.snapshot or {}), "_publicContext": {"rootQuestion": root[:500]}}
        await session.flush()
        return {"turnId": turn.code, "turn": public_turn(turn)}
    except AppError as error:
        raise AppError(
            "本次公开问数未能完成，请调整问题或稍后重试", code=error.code, status_code=error.status_code
        ) from None
    finally:
        if hasattr(llm, "_client"):
            await llm._client.close()
