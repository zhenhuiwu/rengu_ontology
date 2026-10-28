"""T11 运行评测的后台任务：逐题走问数链路（chain.answer），按期望语义计划判定，
结果写 qa_eval_run / qa_eval_fail。

- 复用 eval/run.py 的做法：每题在只读连接的事务里跑、整体回滚，不改任何数据和口径。
- [推断] 判定规则（路线 B 的链路不产出 SemPlan，只能看回答）：
  默认反问 / 拒答 / 执行失败 → 未召回指标；显式声明的拒答或追问另按合同验证；0 行 → 过滤条件缺失；
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


def own_question(q: QaEvalQuestion, ontology_id: int) -> bool:
    """自动题（eval_gen）只在生成它的工作空间里跑（表已按本体 RLS 隔离，这里兜底）；人工题照旧。"""
    meta = q.meta or {}
    return not meta.get("auto") or meta.get("ontologyId") == ontology_id


def _stem(name: str) -> str:
    return re.sub(r"[（(].*?[）)]", "", name).strip()


_SQL_UNKNOWN = object()


def _expected_response_matches(expect, kind, snap, sql):
    """Only an explicit, reviewed rubric can accept a non-answer; omitted SQL is unknown."""
    ans = snap.get("answer") or {}
    if sql is not None or any(k in snap or k in ans for k in ("table", "action")):
        return False
    if "refuse" in expect:
        want = expect["refuse"]
        return (
            "allowClarify" not in expect
            and isinstance(want, dict)
            and set(want) == {"code", "reason"}
            and want["code"] == "NO_DATA"
            and isinstance(want["reason"], str)
            and bool(want["reason"].strip())
            and kind == "denied"
            and "answer" not in snap
            and snap.get("code") == "NO_DATA"
            and snap.get("bold") == want["reason"]
        )
    want = expect.get("allowClarify")
    actual = ans.get("clarify")
    if (
        not isinstance(want, dict)
        or set(want) != {"term", "options"}
        or not isinstance(want["term"], str)
        or not want["term"].strip()
        or not isinstance(want["options"], list)
        or not 2 <= len(want["options"]) <= 6
        or any(not isinstance(x, str) or not x.strip() for x in want["options"])
        or len(set(want["options"])) != len(want["options"])
        or kind != "answer"
        or ans.get("card") != "clarify"
        or not isinstance(ans.get("text"), str)
        or not ans["text"].strip()
        or not isinstance(actual, dict)
        or actual.get("term") != want["term"]
        or actual.get("chosen") is not None
    ):
        return False
    options = actual.get("options")
    if not isinstance(options, list) or len(options) != len(want["options"]):
        return False
    if any(
        not isinstance(x, dict)
        or not isinstance(x.get("label"), str)
        or not isinstance(x.get("value"), str)
        or not x["value"].strip()
        for x in options
    ):
        return False
    return (
        len({x["value"] for x in options}) == len(options)
        and len({x["label"] for x in options}) == len(options)
        and {x["label"] for x in options} == set(want["options"])
    )


def judge(
    q: QaEvalQuestion,
    kind: str,
    snap: dict[str, Any],
    metrics: catalog.Metrics,
    *,
    sql: Any = _SQL_UNKNOWN,
) -> tuple[FailKind, str] | None:
    """返回 None = 通过；否则 (失败类型, 实际结果文字)。"""
    if (q.expect or {}).get("action"):
        return judge_action(q, kind, snap, sql=sql)
    m = catalog.metric_of(metrics, (q.expect or {}).get("metric"))
    if m is None or not _stem(m["name"]):
        return FailKind.metric_not_recalled, "期望指标不可用：数据库目录中不存在该指标"
    ans = snap.get("answer") or {}
    if "refuse" in (q.expect or {}) or (ans.get("card") == "clarify" and "allowClarify" in (q.expect or {})):
        if _expected_response_matches(q.expect, kind, snap, sql):
            return None
        return FailKind.metric_not_recalled, "回答不满足已声明的拒答/追问合同"
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


def judge_action(
    q: QaEvalQuestion, kind: str, snap: dict[str, Any], *, sql: Any = _SQL_UNKNOWN
) -> tuple[FailKind, str] | None:
    """动作题（eval_gen）：回答是期望动作的建议卡就算通过（只建议，从不执行）。"""
    want = (q.expect or {})["action"]
    ans = snap.get("answer") or {}
    if "refuse" in (q.expect or {}) or (ans.get("card") == "clarify" and "allowClarify" in (q.expect or {})):
        if _expected_response_matches(q.expect, kind, snap, sql):
            return None
        return FailKind.metric_not_recalled, "回答不满足已声明的拒答/追问合同"
    if kind == "denied":
        return FailKind.metric_not_recalled, f"拒答（{snap.get('tag', '')}）：{snap.get('bold', '')}"
    action = ans.get("action") if ans.get("card") == "action" else None
    if not isinstance(action, dict):
        return FailKind.metric_not_recalled, f"未给出动作建议（{ans.get('card') or kind}）"
    if action.get("type") != want:
        return FailKind.wrong_metric, f"建议了动作 {action.get('type') or '--'}，期望 {want}"
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
    oid = require_ontology_id(session)
    qs = [q for q in (await session.scalars(questions_query(group))).all() if own_question(q, oid)][:limit]
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
            exp = q.expect or {}
            if not exp.get("action") and catalog.metric_of(metrics, exp.get("metric")) is None:
                kind, snap, sql = "unavailable", {}, None
            else:
                kind, snap, _n, _ms, sql = await chain.answer(
                    session, conn, llm, user, q.question, **answer_context
                )
            res = judge(q, kind, snap, metrics, sql=sql)
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
    run_code: str,
    group: str | None = None,
    llm: LLMClient | None = None,
    user: Any = None,
    *,
    ontology_id: int | None = None,
) -> None:
    from app.db.session import get_engine

    # Pre-migration queue arguments belong only to the retained retail workspace.
    if ontology_id is None:
        ontology_id = DEFAULT_ONTOLOGY_ID

    # 同一连接跨逐题commit持有全局和单run锁；Procrastinate本身也使用全局lock。
    async with get_engine().connect() as connection:
        session = AsyncSession(bind=connection, expire_on_commit=False)
        session.info["workspace_worker"] = True
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
        from app.common.ontology import WorkspaceInactive
        try:
            recovered += await _recover_workspace(ontology_id)
        except WorkspaceInactive:
            continue
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
