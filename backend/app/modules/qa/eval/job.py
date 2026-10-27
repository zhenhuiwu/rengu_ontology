"""T11 运行评测的后台任务：逐题走问数链路（chain.answer），按期望语义计划判定，
结果写 qa_eval_run / qa_eval_fail。

- 复用 eval/run.py 的做法：每题在只读连接的事务里跑、整体回滚，不改任何数据和口径。
- [推断] 判定规则（路线 B 的链路不产出 SemPlan，只能看回答）：
  反问 / 拒答 / 执行失败 → 未召回指标；0 行 → 过滤条件缺失；
  回答的列名里找不到期望指标名（去掉括号注释）→ 指标选错；
  期望维度在列名里找不到 → 维度不匹配；其余算通过。时间范围不判。
- Procrastinate持久队列，独立QA worker执行；每次运行持有全局与单run会话锁。
  启动只把无人执行的运行中任务标失败，保留已提交结果；不重跑已经完成的题。
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any
from uuid import uuid4

from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from app.common.ontology import DEFAULT_ONTOLOGY_ID, require_ontology_id, set_ontology_scope
from app.core.errors import AppError
from app.db.dialects import get_adapter
from app.jobs.scope import scoped_sessions, workspace_ids
from app.modules.qa import catalog, chain
from app.modules.qa.llm import LLMClient
from app.modules.qa.models import EvalLast, FailKind, QaEvalFail, QaEvalQuestion, QaEvalRun, RunStatus
from app.modules.qa.readonly import scope_readonly_conn

log = logging.getLogger(__name__)

ALL = ("all", "全部")


def questions_query(group: str | None):
    stmt = select(QaEvalQuestion).where(QaEvalQuestion.kind == "semplan").order_by(QaEvalQuestion.code)
    if group and group not in ALL:
        stmt = stmt.where(QaEvalQuestion.group_name == group)
    return stmt


def _stem(name: str) -> str:
    return re.sub(r"[（(].*?[）)]", "", name).strip()


def judge(
    q: QaEvalQuestion, kind: str, snap: dict[str, Any], metrics: catalog.Metrics
) -> tuple[FailKind, str] | None:
    """返回 None = 通过；否则 (失败类型, 实际结果文字)。"""
    m = catalog.metric_of(metrics, (q.expect or {}).get("metric"))
    if m is None or not _stem(m["name"]):
        return FailKind.metric_not_recalled, "期望指标不可用：数据库目录中不存在该指标"
    ans = snap.get("answer") or {}
    if kind == "denied":
        return FailKind.metric_not_recalled, f"拒答（{snap.get('tag', '')}）：{snap.get('bold', '')}"
    if ans.get("card") == "clarify":
        return FailKind.metric_not_recalled, f"反问：{ans.get('text', '')}"
    if kind == "empty":
        return FailKind.missing_filter, "查询结果为 0 行"
    table = ans.get("table") or {}
    cols = [str(c.get("title") or c.get("key") or "") for c in table.get("columns") or []]
    hay = " ".join(cols)  # 只看列名：table.title 是问题原文，text 由列名拼出
    exp = q.expect or {}
    if _stem(m["name"]) not in hay:
        return FailKind.wrong_metric, f"按 {'、'.join(cols) or '--'} 回答，未出现「{m['name']}」"
    miss = [d for d in exp.get("dims") or [] if not any(d in c or c in d for c in cols if c)]
    if miss:
        return FailKind.dim_mismatch, f"缺少维度 {'、'.join(miss)}（实际列 {'、'.join(cols) or '--'}）"
    return None


async def execute(
    session: AsyncSession,
    conn: AsyncConnection,
    llm: LLMClient,
    user: Any,
    run: QaEvalRun,
    group: str | None,
    *,
    checkpoint: Callable[[], Awaitable[None]] | None = None,
    limit: int | None = None,
) -> QaEvalRun:
    """跑完更新 run（passed / status=已完成）与每题 last_result。

    checkpoint 每题后调用（后台任务里 commit 让前端看到进度）。
    """
    qs = list((await session.scalars(questions_query(group))).all())[:limit]
    metrics, _ = await catalog.directory(session)
    from app.modules.qa import service as qa_service

    context = run.execution_context or {}
    lines = qa_service.caliber_lines(context.get("caliber"), await qa_service.variant_index(session))
    answer_context = {}
    if lines or context.get("scope"):
        answer_context = {"caliber_lines": lines, "session_scope": context.get("scope")}
    run.total, run.passed = len(qs), 0
    for q in qs:
        try:
            if catalog.metric_of(metrics, (q.expect or {}).get("metric")) is None:
                kind, snap = "unavailable", {}
            else:
                kind, snap, *_ = await chain.answer(session, conn, llm, user, q.question, **answer_context)
            res = judge(q, kind, snap, metrics)
        except (AppError, DBAPIError) as e:  # SQL 被拒 / 超时 / 大模型失败：记失败，不中断整轮
            code = getattr(e, "code", None) or type(e).__name__
            res = FailKind.metric_not_recalled, f"执行失败：{code}"
        if res is None:
            run.passed += 1
            q.last_result = EvalLast["pass"]
        else:
            q.last_result = EvalLast["fail"]
            session.add(QaEvalFail(run_id=run.id, question_id=q.id, kind=res[0], actual=res[1][:500]))
        await session.flush()
        if checkpoint:
            await checkpoint()
    run.status = RunStatus.done
    await session.flush()
    return run


# ---------------------------------------------------------------- 持久队列执行与孤儿恢复
_WORKER_ID: str | None = None


def current_worker_id() -> str | None:
    return _WORKER_ID


@asynccontextmanager
async def worker_lifespan() -> AsyncIterator[None]:
    """专用QA worker寿命内登记进程归属；不占用API进程连接。"""
    from app.db.session import get_engine

    global _WORKER_ID
    worker_id = uuid4().hex  # lifespan 内生成，防止 pre-fork 部署继承同一个标识。
    async with get_engine().connect() as connection:
        session = AsyncSession(bind=connection)
        async with get_adapter(session).session_lock(connection, f"qa.eval_worker.{worker_id}"), session:
            _WORKER_ID = worker_id
            try:
                yield
            finally:
                _WORKER_ID = None


async def _background(
    run_code: str, group: str | None = None, llm: LLMClient | None = None, user: Any = None,
    *, ontology_id: int | None = None
) -> None:
    from app.db.session import get_engine

    # Pre-migration queue arguments belong only to the retained retail workspace.
    if ontology_id is None:
        ontology_id = DEFAULT_ONTOLOGY_ID

    # 同一连接跨逐题commit持有全局和单run锁；Procrastinate本身也使用全局lock。
    async with get_engine().connect() as connection:
        session = AsyncSession(bind=connection, expire_on_commit=False)
        await set_ontology_scope(session, ontology_id)
        status = await session.scalar(select(QaEvalRun.status).where(QaEvalRun.code == run_code))
        await session.rollback()
        if status not in (RunStatus.queued, RunStatus.running):
            await session.close()
            return
        adapter = get_adapter(session)
        async with (
            adapter.session_lock(connection, f"qa.eval.execution.{ontology_id}"),
            adapter.session_lock(connection, f"qa.eval_run.{ontology_id}.{run_code}"),
            session,
        ):
            await _execute_background(session, run_code, group, llm, user)


async def _mark_failed(session: AsyncSession, run_code: str, owner: str | None, reason: str) -> None:
    await session.rollback()
    run = await session.scalar(
        select(QaEvalRun).where(QaEvalRun.code == run_code).execution_options(populate_existing=True)
    )
    if run is not None and run.status == RunStatus.running and run.worker_id == owner:
        run.status = RunStatus.failed
        run.note = f"运行中断：{reason}；已保留完成题目的结果，请重新运行评测"
        await session.commit()


async def _execute_background(
    s: AsyncSession, run_code: str, group: str | None, llm: LLMClient | None, user: Any
) -> None:
    from app.common.auth import check_func, resolve_user
    from app.db.session import get_qa_readonly_engine
    from app.modules.qa.llm import get_llm

    run = await s.scalar(select(QaEvalRun).where(QaEvalRun.code == run_code))
    if run is None or run.status not in (RunStatus.queued, RunStatus.running):
        return  # 重复交付/迟到任务不能覆盖恢复后的终态。
    owner = current_worker_id()
    run.worker_id, run.status = owner, RunStatus.running
    await s.commit()
    try:
        context = run.execution_context or {}
        if context.get("ontologyId", run.ontology_id) != require_ontology_id(s):
            raise AppError("评测工作空间上下文不匹配", code="ONTOLOGY_SCOPE_CONFLICT", status_code=409)
        if context:
            user = await resolve_user(s, context["userId"], context.get("tenantId"))
            check_func(user, "qa-tuning", "manage")
            group = context.get("group")
        if llm is None:
            from app.modules.admin.model_settings import load_model_runtime

            llm = get_llm(await load_model_runtime(s, "qa"))  # 仅在worker内解密，不持久化客户端或凭据。
        async with get_qa_readonly_engine().connect() as conn:
            tr = await conn.begin()
            await scope_readonly_conn(conn, require_ontology_id(s))
            await conn.execute(text("set local statement_timeout = 15000"))
            try:
                await execute(s, conn, llm, user, run, group, checkpoint=s.commit)
            finally:
                await tr.rollback()
        await s.commit()
    except asyncio.CancelledError:
        await _mark_failed(s, run_code, owner, "worker关闭或任务取消")
        raise
    except Exception as exc:
        log.exception("eval run %s failed", run_code)
        await _mark_failed(s, run_code, owner, str(getattr(exc, "code", None) or type(exc).__name__))
        raise  # Procrastinate job也记失败，不把执行失败当队列成功。


async def recover_interrupted_runs() -> int:
    """启动恢复：保留已提交的逐题进度，不碰仍由其他进程执行的任务。"""
    recovered = 0
    for ontology_id in await workspace_ids():
        recovered += await _recover_workspace(ontology_id)
    return recovered


async def _recover_workspace(ontology_id: int) -> int:
    async with scoped_sessions(ontology_id)() as session:
        runs = (await session.scalars(select(QaEvalRun).where(QaEvalRun.status == RunStatus.running))).all()
        recovered = 0
        for run in runs:
            if run.worker_id and not await get_adapter(session).try_xact_lock(
                session, f"qa.eval_worker.{run.worker_id}"
            ):
                continue
            if not await get_adapter(session).try_xact_lock(session, f"qa.eval_run.{ontology_id}.{run.code}"):
                continue
            # 枚举后任务可能刚好完成；拿锁后再读，不能覆盖它的最终状态。
            await session.refresh(run)
            if run.status == RunStatus.running:
                run.status = RunStatus.failed
                if run.queue_job_id is not None:
                    from app.modules.qa.eval.queue import fail_interrupted

                    await fail_interrupted(session, run.queue_job_id)
                note = "运行中断：服务重启，已保留完成题目的结果，请重新运行评测"
                run.note = f"{run.note}\n{note}" if run.note else note
                recovered += 1
        await session.commit()
        if recovered:
            log.warning("recovered %d interrupted eval runs", recovered)
        return recovered


def get_runner() -> Callable:
    """保留路由依赖名，执行已改为同事务Procrastinate入队。"""
    from app.modules.qa.eval.queue import defer

    return defer
