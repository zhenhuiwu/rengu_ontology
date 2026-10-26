"""真实创建路径的双事务编号回归：以 pg_locks 确认等待，不靠固定 sleep 猜测时序。"""

import asyncio
from uuid import uuid4

import pytest
from sqlalchemy import delete, insert, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.auth import CurrentUser
from app.common.context import ViewContext
from app.core import clock
from app.modules.admin.models import SysUser
from app.modules.dynamic.rules_models import Rule, Scenario
from app.modules.dynamic.rules_schemas import RuleCreateIn
from app.modules.dynamic.rules_service import RuleService, ScenarioService
from app.modules.qa import catalog
from app.modules.qa.models import (
    CertStatus,
    QaCert,
    QaFeedback,
    QaPage,
    QaPermissionRequest,
    QaSession,
    QaTurn,
    TurnKind,
)
from app.modules.qa.pages_api import PageCreateIn, PinIn, create_page, pin_card
from app.modules.qa.router import (
    _new_cert,
    ask,
    create_permission_request,
    create_session,
    patch_session,
    vote_turn,
)
from app.modules.qa.schemas import AskIn, PermRequestIn, SessionCreateIn, SessionPatchIn, VoteIn
from app.modules.static.cr_write import CrWriteService
from app.modules.static.models import (
    ChangeRequest,
    ConflictStrategy,
    Lifecycle,
    Metric,
    ObjectStatus,
    ObjectType,
)
from app.modules.static.schemas import CRCreateIn


async def _create(kind, session, actor, n):
    if kind == "session":
        return (await create_session(SessionCreateIn(), session, actor, ViewContext())).id
    if kind == "page":
        return (await create_page(PageCreateIn(name=f"编号回归{n}"), session, actor, ViewContext())).id
    if kind == "rule":
        return (
            await RuleService(session, actor).create(RuleCreateIn(name=f"编号回归{n}", obj_kind=actor.id))
        )["id"]
    if kind == "scenario":
        return (await ScenarioService(session, actor).create(None))["id"]
    if kind == "permission":
        body = PermRequestIn(scope="华东", object="门店", reason=f"编号回归{n}", duration=30)
        return (await create_permission_request(body, session, actor, ViewContext())).id
    if kind == "cert":
        return (
            await _new_cert(
                session,
                f"{actor.id}-{n}",
                {"metric": actor.id, "time": "上周"},
                "编号回归",
                (await catalog.directory(session))[0],
                status=CertStatus.pending,
            )
        ).code
    if kind == "feedback":
        tid = await session.scalar(select(QaTurn.code).where(QaTurn.question == f"{actor.id}-{n}"))
        await vote_turn(tid, VoteIn(vote="down"), session, actor, ViewContext())
        return await session.scalar(select(QaFeedback.code).where(QaFeedback.question == f"{actor.id}-{n}"))
    return await CrWriteService(session).create(actor, CRCreateIn(title=f"编号回归{n}", reviewer=""))


async def _waiting(session, pid, task):
    async with asyncio.timeout(5):
        while not await session.scalar(
            text(
                "SELECT EXISTS (SELECT 1 FROM pg_locks "
                "WHERE pid=:pid AND locktype='advisory' AND NOT granted)"
            ),
            {"pid": pid},
        ):
            if task.done():
                await task  # 保留真实创建错误；无锁直接成功也必须让测试失败。
                pytest.fail("第二个事务未等待编号锁就完成了创建")


async def _two_creates(engine, create, *, commit_second=True):
    async with (
        engine.connect() as c1,
        engine.connect() as c2,
        AsyncSession(bind=c1, expire_on_commit=False) as s1,
        AsyncSession(bind=c2, expire_on_commit=False) as s2,
    ):
        pid2 = await s2.scalar(text("SELECT pg_backend_pid()"))
        await s2.rollback()
        task = None
        try:
            first = await create(s1, 1)
            task = asyncio.create_task(create(s2, 2))
            await _waiting(s1, pid2, task)
            assert not task.done()
            await s1.commit()
            second = await asyncio.wait_for(task, 5)
            if commit_second:
                await s2.commit()
            return first, second
        finally:
            if task is not None and not task.done():
                task.cancel()
            if task is not None:
                await asyncio.gather(task, return_exceptions=True)
            await s1.rollback()
            await s2.rollback()


@pytest.mark.parametrize(
    "kind,model,prefix",
    [
        ("session", QaSession, "S"),
        ("page", QaPage, "P"),
        ("rule", Rule, "R-"),
        ("scenario", Scenario, "SCN-"),
        ("cr", ChangeRequest, "CR-"),
        ("permission", QaPermissionRequest, "AP"),
        ("cert", QaCert, "CQ-"),
        ("feedback", QaFeedback, "FB-"),
    ],
    ids=["qa-session", "qa-page", "rule", "scenario", "cr-existing-lock", "permission", "cert", "feedback"],
)
async def test_creation_code_lock_covers_insert_until_commit(engine, kind, model, prefix):
    token = uuid4().hex[:20]
    actor = CurrentUser(
        id=f"lock-{token}", name=f"编号锁-{token}", is_ontology_admin=True, func={"releases": "manage"}
    )
    async with engine.begin() as setup:
        owner_id = await setup.scalar(
            insert(SysUser).values(code=actor.id, name=actor.name).returning(SysUser.id)
        )
        if kind == "rule":
            await setup.execute(
                insert(ObjectType).values(
                    code=actor.id,
                    api_name=f"LockObject_{token}",
                    display_name="编号锁对象",
                    obj_group="测试",
                    status=ObjectStatus.published,
                    conflict_strategy=ConflictStrategy.user_edit_wins,
                    lifecycle=Lifecycle.active,
                )
            )
        if kind == "cert":
            await setup.execute(
                insert(Metric).values(
                    code=actor.id,
                    api_name=actor.id,
                    display_name="编号指标",
                    metric_group="测试",
                    unit="个",
                    version="v1",
                    status="published",
                    owner=actor.name,
                )
            )
        if kind == "feedback":
            session_id = await setup.scalar(
                insert(QaSession)
                .values(code=actor.id, owner_id=owner_id, title="编号回归", last_at=clock.now())
                .returning(QaSession.id)
            )
            await setup.execute(
                insert(QaTurn),
                [
                    {
                        "code": f"{actor.id}-T{n}",
                        "session_id": session_id,
                        "seq": n,
                        "kind": TurnKind.answer,
                        "question": f"{actor.id}-{n}",
                        "snapshot": {"answer": {"text": "编号回归"}},
                    }
                    for n in (1, 2)
                ],
            )
    try:
        first, second = await _two_creates(engine, lambda s, n: _create(kind, s, actor, n))
        assert int(second[len(prefix) :]) == int(first[len(prefix) :]) + 1
        async with engine.connect() as check:
            assert set(
                (await check.scalars(select(model.code).where(model.code.in_([first, second])))).all()
            ) == {
                first,
                second,
            }
    finally:
        async with engine.begin() as cleanup:
            if kind in {"session", "page"}:
                owner = model.owner_id == owner_id
            elif kind == "feedback":
                owner = model.user_id == owner_id
            elif kind == "permission":
                owner = model.requester_id == owner_id
            elif kind == "cert":
                owner = model.question.startswith(actor.id)
            else:
                owner = model.owner == actor.name
            await cleanup.execute(delete(model).where(owner))
            if kind == "feedback":
                await cleanup.execute(delete(QaSession).where(QaSession.owner_id == owner_id))
            if kind == "cert":
                await cleanup.execute(delete(Metric).where(Metric.code == actor.id))
            if kind == "rule":
                await cleanup.execute(delete(ObjectType).where(ObjectType.code == actor.id))
            await cleanup.execute(delete(SysUser).where(SysUser.code == actor.id))


@pytest.mark.parametrize("kind", ["turn", "patch", "card"])
async def test_resource_numbers_serialize_locally_and_leave_other_resources_free(engine, monkeypatch, kind):
    from datetime import timedelta
    from itertools import count

    from app.modules.qa import chain

    # 真实时钟前进时 ask 会更新 last_at；固定演示时钟会掩盖等待锁后的 ORM 版本冲突。
    base, ticks = clock.now(), count()
    monkeypatch.setattr(clock, "now", lambda: base + timedelta(seconds=next(ticks)))
    actor = CurrentUser(id=f"lock-{uuid4().hex[:20]}", name="局部编号回归", is_ontology_admin=True)
    async with AsyncSession(engine) as setup:
        setup.add(SysUser(code=actor.id, name=actor.name))
        await setup.flush()
        sid = (await create_session(SessionCreateIn(), setup, actor, ViewContext())).id
        other_sid = (await create_session(SessionCreateIn(), setup, actor, ViewContext())).id
        page_ids = []
        if kind == "card":
            owner_session = await setup.scalar(select(QaSession).where(QaSession.code == sid))
            setup.add(
                QaTurn(
                    code=f"{sid}-T001",
                    session_id=owner_session.id,
                    seq=1,
                    kind=TurnKind.answer,
                    question="可固定的回答",
                    snapshot={
                        "answer": {
                            "card": "table",
                            "table": {"columns": [{"key": "k", "title": "值"}], "items": [{"k": 1}]},
                        }
                    },
                )
            )
            for name in ("卡片编号甲", "卡片编号乙"):
                page_ids.append((await create_page(PageCreateIn(name=name), setup, actor, ViewContext())).id)
        await setup.commit()

    async def answer(*args, **kwargs):
        return "answer", {"answer": {"text": "测试回答"}}, 0, 1, "SELECT 1"

    monkeypatch.setattr(chain, "answer", answer)

    async def create(session, n, *, other=False):
        if kind == "card":
            pid = page_ids[int(other)]
            out = await pin_card(
                pid, PinIn(turn_id=f"{sid}-T001", title=f"卡片{n}"), session, actor, ViewContext()
            )
            return out.cards[-1]["key"]
        resource = other_sid if other else sid
        if n == 1 or other or kind == "patch":
            out = await patch_session(
                resource, SessionPatchIn(scope=f"范围{n}"), session, actor, ViewContext()
            )
            return out.turns[-1]["id"]
        return (
            await ask(resource, AskIn(question="下一问"), session, actor, ViewContext(), None, None)
        ).turn_id

    try:
        # ask 产生只追加查询日志；第二事务验证 flush 成功后回滚，避免在测试库遗留日志和用户。
        first, second = await _two_creates(engine, create, commit_second=kind == "card")
        assert (
            int(second.rsplit("C" if kind == "card" else "T", 1)[1])
            == int(first.rsplit("C" if kind == "card" else "T", 1)[1]) + 1
        )
        async with AsyncSession(engine) as s1, AsyncSession(engine) as s2:
            await create(s1, 1)
            # 另一个会话 / 页面不应被同一资源的编号锁阻塞。
            await asyncio.wait_for(create(s2, 2, other=True), 5)
            await s1.rollback()
            await s2.rollback()
        if kind == "turn":
            from app.core.errors import NotFound

            async def delete_while_answering(*args, **kwargs):
                async with engine.begin() as deletion:
                    await deletion.execute(delete(QaSession).where(QaSession.code == other_sid))
                return await answer()

            monkeypatch.setattr(chain, "answer", delete_while_answering)
            async with AsyncSession(engine) as session:
                with pytest.raises(NotFound):
                    await ask(
                        other_sid,
                        AskIn(question="处理中会话被删除"),
                        session,
                        actor,
                        ViewContext(),
                        None,
                        None,
                    )
    finally:
        async with engine.begin() as cleanup:
            owner_id = select(SysUser.id).where(SysUser.code == actor.id).scalar_subquery()
            await cleanup.execute(delete(QaPage).where(QaPage.owner_id == owner_id))
            await cleanup.execute(delete(QaSession).where(QaSession.owner_id == owner_id))
            await cleanup.execute(delete(SysUser).where(SysUser.code == actor.id))
