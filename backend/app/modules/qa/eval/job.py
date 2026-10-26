"""T11 运行评测的后台任务：逐题走问数链路（chain.answer），按期望语义计划判定，
结果写 qa_eval_run / qa_eval_fail。

- 复用 eval/run.py 的做法：每题在只读连接的事务里跑、整体回滚，不改任何数据和口径。
- [推断] 判定规则（路线 B 的链路不产出 SemPlan，只能看回答）：
  反问 / 拒答 / 执行失败 → 未召回指标；0 行 → 过滤条件缺失；
  回答的列名里找不到期望指标名（去掉括号注释）→ 指标选错；
  期望维度在列名里找不到 → 维度不匹配；其余算通过。时间范围不判。
- [推断] 调度：app/jobs（procrastinate）是共享文件，这一轮用进程内 asyncio 任务（schedule_run），
  进程重启会丢正在跑的任务（run 停在「运行中」）；接 procrastinate 见 handoff/qa.md。
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from app.core.errors import AppError
from app.modules.qa import catalog, chain
from app.modules.qa.llm import LLMClient
from app.modules.qa.models import EvalLast, FailKind, QaEvalFail, QaEvalQuestion, QaEvalRun, RunStatus

log = logging.getLogger(__name__)

ALL = ("all", "全部")


def questions_query(group: str | None):
    stmt = select(QaEvalQuestion).where(QaEvalQuestion.kind == "semplan").order_by(QaEvalQuestion.code)
    if group and group not in ALL:
        stmt = stmt.where(QaEvalQuestion.group_name == group)
    return stmt


def _stem(name: str) -> str:
    return re.sub(r"[（(].*?[）)]", "", name).strip()


def judge(q: QaEvalQuestion, kind: str, snap: dict[str, Any]) -> tuple[FailKind, str] | None:
    """返回 None = 通过；否则 (失败类型, 实际结果文字)。"""
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
    m = catalog.t_metrics().get(exp.get("metric") or "")
    if m and _stem(m["name"]) not in hay:
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
    run.total, run.passed = len(qs), 0
    for q in qs:
        try:
            kind, snap, *_ = await chain.answer(session, conn, llm, user, q.question)
            res = judge(q, kind, snap)
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


# ---------------------------------------------------------------- 进程内调度
_TASKS: set[asyncio.Task] = set()


async def _background(run_code: str, group: str | None, llm: LLMClient, user: Any) -> None:
    from app.db.session import get_qa_readonly_engine, get_sessionmaker

    async with get_sessionmaker()() as s:
        # T11 经 BackgroundTasks 调度：请求事务（get_session，scope="function"）已提交，不用轮询等
        run = await s.scalar(select(QaEvalRun).where(QaEvalRun.code == run_code))
        if run is None:
            log.warning("eval run %s not found", run_code)
            return
        try:
            async with get_qa_readonly_engine().connect() as conn:
                tr = await conn.begin()
                await conn.execute(text("set local statement_timeout = 15000"))
                try:
                    await execute(s, conn, llm, user, run, group, checkpoint=s.commit)
                finally:
                    await tr.rollback()
        except Exception as e:  # 只读库未配置等：标已完成并写说明，不留在「运行中」
            log.exception("eval run %s failed", run_code)
            await s.rollback()
            run = await s.scalar(select(QaEvalRun).where(QaEvalRun.code == run_code))
            if run is not None:
                run.status = RunStatus.done
                run.note = f"运行中断：{getattr(e, 'code', None) or type(e).__name__}"
        await s.commit()


def schedule_run(run_code: str, group: str | None, llm: LLMClient, user: Any) -> None:
    t = asyncio.get_running_loop().create_task(_background(run_code, group, llm, user))
    _TASKS.add(t)
    t.add_done_callback(_TASKS.discard)


def get_runner() -> Callable[[str, str | None, LLMClient, Any], None]:
    """依赖：测试覆盖成记录参数、再直接调 execute。"""
    return schedule_run
