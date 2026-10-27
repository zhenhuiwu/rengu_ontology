"""按本体自动出评测题（每天一次，挂在问数评测 worker 上）。

不写任何业务词：题目全部由当前本体工作空间的元数据拼出来——
- 指标题：已发布指标 → 「<指标名>」「按<维度>看<指标名>」（维度取指标允许拆分的前两个）；
  期望 `{"metric": 对外键, "dims": [...]}`，沿用现有判定（eval/job.judge）。
- 动作题：能力目录（按最近一次评测发起人的权限）里的动作 → 「给一个<目标对象>发起<动作名>」；
  期望 `{"action": 动作编码}`，判定看回答是不是这个动作的建议卡（只建议，不执行）。

写入 `qa_eval_question`（kind = semplan，分组空，`meta = {"auto": true, "ontologyId", "source"}`），
按题目原文去重（表按本体 RLS 隔离，唯一键 = 本体 + 题目）；
不改、不删人工题，也不删以前生成的题（指标下线后判「期望指标不可用」，
人工处理）。评测运行另按 meta.ontologyId 兜底过滤（eval/job.own_question）。
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.ontology import require_ontology_id
from app.modules.qa.models import EvalKind, QaEvalQuestion

log = logging.getLogger(__name__)

TASK = "qa.eval.generate"
LOCK = "qa.eval.generate"
CRON = "30 2 * * *"  # 每天 02:30（worker 时区）
MAX_METRICS = 30
MAX_ACTIONS = 10
MAX_DIMS = 2


def _code(ontology_id: int, question: str) -> str:
    return f"EQA-{ontology_id}-{hashlib.sha1(question.encode()).hexdigest()[:10]}"


def metric_questions(metrics: list[dict[str, Any]]) -> list[tuple[str, dict[str, Any], str]]:
    """[{key, name, dims}] → [(题目, 期望, 来源)]。"""
    out = []
    for m in metrics[:MAX_METRICS]:
        name = (m.get("name") or "").strip()
        if not name:
            continue
        src = f"metric:{m['key']}"
        out.append((name, {"metric": m["key"], "dims": []}, src))
        for d in [d for d in m.get("dims") or [] if isinstance(d, str) and d.strip()][:MAX_DIMS]:
            out.append((f"按{d}看{name}", {"metric": m["key"], "dims": [d]}, src))
    return out


def action_questions(actions: list[dict[str, Any]]) -> list[tuple[str, dict[str, Any], str]]:
    """[{id, name, target}] → [(题目, 期望, 来源)]。"""
    out = []
    for a in actions[:MAX_ACTIONS]:
        name, target = (a.get("name") or "").strip(), (a.get("target") or "").strip()
        if not name:
            continue
        q = f"给一个{target}发起{name}" if target else f"发起{name}"
        out.append((q, {"action": a["id"], "actionName": name}, f"action:{a['id']}"))
    return out


async def _metrics(session: AsyncSession) -> list[dict[str, Any]]:
    from app.modules.qa.catalog import metric_key
    from app.modules.static.models import Metric, MetricStatus

    oid = require_ontology_id(session)
    rows = await session.execute(
        select(Metric.code, Metric.display_name, Metric.dims)
        .where(Metric.status == MetricStatus.published)
        .order_by(Metric.seq, Metric.code)
    )
    return [{"key": metric_key(c, oid), "name": n, "dims": d or []} for c, n, d in rows]


async def _actions(session: AsyncSession, user: Any) -> list[dict[str, Any]]:
    """动作走能力目录（同 GET /meta/capabilities，按 user 的权限），不直读动作表；没有 user 不出动作题。"""
    if user is None:
        return []
    from app.modules.qa.intent import action_catalog

    return [
        {"id": a["id"], "name": a["name"], "target": a.get("targetKind") or ""}
        for a in await action_catalog(session, user)
    ]


async def eval_user(session: AsyncSession) -> Any:
    """后台生成用的身份 = 本空间最近一次评测运行的发起人（有 qa-tuning 管理权）；没有则 None。"""
    from app.common.auth import check_func, resolve_user
    from app.modules.qa.models import QaEvalRun

    ctx = await session.scalar(
        select(QaEvalRun.execution_context)
        .where(QaEvalRun.execution_context.is_not(None))
        .order_by(QaEvalRun.id.desc())
        .limit(1)
    )
    if not ctx or not ctx.get("userId"):
        return None
    try:
        user = await resolve_user(session, ctx["userId"], ctx.get("tenantId"))
        check_func(user, "qa-tuning", "manage")
    except Exception:
        return None
    return user


async def generate(session: AsyncSession, user: Any = None) -> list[str]:
    """给当前工作空间补齐自动题，返回新加的题目编号。user 给了才出动作题（按其能力目录）。"""
    oid = require_ontology_id(session)
    items = metric_questions(await _metrics(session)) + action_questions(await _actions(session, user))
    if not items:
        return []
    texts = [q for q, _, _ in items]
    have = set(
        await session.scalars(select(QaEvalQuestion.question).where(QaEvalQuestion.question.in_(texts)))
    )
    added = []
    for q, expect, src in items:
        if q in have:
            continue
        have.add(q)
        code = _code(oid, q)
        session.add(
            QaEvalQuestion(
                code=code,
                question=q,
                group_name=None,
                kind=EvalKind.semplan,
                expect=expect,
                meta={"auto": True, "ontologyId": oid, "source": src},
            )
        )
        added.append(code)
    await session.flush()
    return added


async def generate_all() -> dict[int, int]:
    """逐工作空间生成；一个空间出错不影响别的空间。"""
    from app.jobs.scope import scoped_sessions, workspace_ids

    done: dict[int, int] = {}
    for oid in await workspace_ids():
        try:
            async with scoped_sessions(oid)() as session, session.begin():
                done[oid] = len(await generate(session, await eval_user(session)))
        except Exception:
            log.exception("qa eval question generation failed for workspace %s", oid)
    return done


async def _task(timestamp: int = 0) -> None:
    await generate_all()


def register(app, queue: str) -> None:
    """问数评测 worker 登记每日周期任务（同一队列，独立锁）。"""
    task = app.task(name=TASK, queue=queue, lock=LOCK)(_task)
    app.periodic(cron=CRON, periodic_id=TASK)(task)
