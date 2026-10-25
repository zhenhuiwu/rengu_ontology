"""问数最小链路（W6）：问题 → 上下文 → prompt → LLM → 抽 SQL → sql_guard → 只读执行 → 答案轮次快照。

[推断] 本期 LLM 直接写 SQL（Vanna / SQLBot 式：schema + 本体指标/同义词/默认路径进 prompt）；
落地方案里的 SemPlan JSON → 校验器 → 编译器是二期，这里不做。sql_guard 兜底：单条 SELECT、
表白名单（受治理业务表）、行级权限注入；执行走只读引擎（statement_timeout + read_only）并外包 LIMIT。
"""

from __future__ import annotations

import re
import time
from collections.abc import AsyncIterator
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from app.common.sql_guard import assert_single_select, run_guarded
from app.core.config import get_settings
from app.core.errors import AppError
from app.db.base import Base
from app.db.session import QaReadonlyNotConfigured, get_qa_readonly_engine
from app.modules.qa.llm import LLMClient
from app.modules.qa.models import QaSynonymLink
from app.modules.static.models import DimMapping, LinkType, Metric, ObjectType, QaPath

# [推断] 本期可问的物理表 = biz 受治理表（行级权限已注册）
QA_TABLES = ("store", "product", "biz_order", "order_line")
TABLE_PAGE = 10  # 前端表格首屏行数，超过标 paged
ROW_LIMIT = 200  # 单次结果最多行数（外包 LIMIT）


class QaExecUnavailable(AppError):
    status_code = 503
    code = "QA_EXEC_UNAVAILABLE"
    message = "问数只读库未配置（RENGU_QA_READONLY_DATABASE_URL）"


async def get_qa_conn() -> AsyncIterator[AsyncConnection]:
    """只读执行连接（依赖，测试可覆盖）。"""
    try:
        engine = get_qa_readonly_engine()
    except QaReadonlyNotConfigured:
        raise QaExecUnavailable() from None
    async with engine.connect() as conn:
        yield conn


# ---------------------------------------------------------------- 上下文


def _schema_lines() -> list[str]:
    out = []
    for name in QA_TABLES:
        t = Base.metadata.tables.get(name)
        if t is None:
            continue
        cols = ", ".join(f"{c.name}{f'（{c.comment}）' if c.comment else ''}" for c in t.columns)
        out.append(f"- {name}{f'：{t.comment}' if t.comment else ''}\n  列：{cols}")
    return out


async def build_context(session: AsyncSession) -> dict[str, list[str]]:
    """从本体表取：表结构、对象说明、关联 join、指标口径、同义词、默认问数路径。"""
    objs = (
        await session.scalars(
            select(ObjectType).where(ObjectType.source_table.in_(QA_TABLES)).order_by(ObjectType.seq)
        )
    ).all()
    obj_ids = {o.id for o in objs}
    objects = [
        f"- {o.display_name}（{o.api_name}，表 {o.source_table}）：{o.description}".rstrip("：") for o in objs
    ]
    links = []
    if obj_ids:
        for lk in (await session.scalars(select(LinkType).where(LinkType.from_object_id.in_(obj_ids)))).all():
            if lk.to_object_id in obj_ids and lk.join_sql:
                links.append(f"- {lk.display_name}：{lk.join_sql}")
    metrics = [
        f"- {m.display_name}（{m.unit}，{m.version}）= {m.formula}"
        for m in (
            await session.scalars(select(Metric).where(Metric.formula.is_not(None)).order_by(Metric.id))
        ).all()
    ]
    syn = [f"- {s.word} → {s.target}" for s in (await session.scalars(select(QaSynonymLink))).all()]
    for d in (await session.scalars(select(DimMapping).order_by(DimMapping.seq).limit(40))).all():
        syn.append(f"- {d.phrase} → {'/'.join(d.targets)}")
    paths = [
        f"- {p.group_key}：{p.hint or '默认路径'}"
        for p in (await session.scalars(select(QaPath).where(QaPath.is_default).order_by(QaPath.id))).all()
    ]
    return {
        "schema": _schema_lines(),
        "objects": objects,
        "links": links,
        "metrics": metrics,
        "synonyms": syn,
        "paths": paths,
    }


_SYSTEM = """你是零售数据分析助手，把用户问题翻译成一条 PostgreSQL 查询。
规则：
1. 只输出一条 SELECT（可用 WITH），放在 ```sql 代码块里，不要解释；
2. 只能用下面列出的表和列；不要写 INSERT/UPDATE/DELETE/DDL；
3. 指标按“指标口径”计算；用户说法按“同义词”映射；多表关联优先用“关联”与“默认问数路径”；
4. 列用中文别名（AS "门店数"），结果行数尽量少；默认剔除 is_test = true 的测试数据。"""


def build_prompt(question: str, ctx: dict[str, list[str]]) -> list[dict[str, str]]:
    titles = {
        "schema": "表结构",
        "objects": "本体对象",
        "links": "关联",
        "metrics": "指标口径",
        "synonyms": "同义词",
        "paths": "默认问数路径",
    }
    parts = [_SYSTEM]
    for k, title in titles.items():
        if ctx.get(k):
            parts.append(f"## {title}\n" + "\n".join(ctx[k]))
    parts.append(f"数据截至：{get_settings().data_cutoff}")
    return [{"role": "system", "content": "\n\n".join(parts)}, {"role": "user", "content": question}]


_FENCE = re.compile(r"```(?:sql|SQL|postgresql)?\s*(.*?)```", re.S)


def extract_sql(text: str) -> str:
    m = _FENCE.search(text or "")
    sql = (m.group(1) if m else text or "").strip()
    return sql.rstrip(";").strip()


# ---------------------------------------------------------------- 执行 + 成形


def _cell(v: Any) -> Any:
    if isinstance(v, Decimal):
        return int(v) if v == v.to_integral_value() else float(v)
    if isinstance(v, datetime | date):
        return v.isoformat()
    return v


async def answer(
    session: AsyncSession, conn: AsyncConnection, llm: LLMClient, user: Any, question: str
) -> tuple[str, dict[str, Any], int, int, str]:
    """返回 (kind, snapshot, row_count, elapsed_ms, sql)。

    SQL 只进 qa_turn.sql_text，不出接口；SQL 被拒抛 SqlRejected（403 SQL_REJECTED）。
    """
    t0 = time.monotonic()
    ctx = await build_context(session)
    sql = extract_sql(await llm.generate_sql(build_prompt(question, ctx)))
    assert_single_select(sql)  # 先校验原 SQL，拒绝信息更准
    limit = ROW_LIMIT
    res = await run_guarded(conn, f"SELECT * FROM ({sql}) AS _qa LIMIT {limit + 1}", user)
    keys = list(res.keys())
    rows = [[_cell(v) for v in r] for r in res.fetchall()]
    truncated = len(rows) > limit
    rows = rows[:limit]
    elapsed = int((time.monotonic() - t0) * 1000)
    if not rows:
        snap = {
            "lead": "没有查到符合条件的数据",
            "bold": "",
            "tail": "，可以换个时间范围或条件再问。",
            "tag": "无数据",
        }
        return "empty", snap, 0, elapsed, sql
    columns = []
    for i, k in enumerate(keys):
        col: dict[str, Any] = {"key": f"c{i}", "title": k}
        if all(isinstance(r[i], int | float) for r in rows if r[i] is not None):
            col["align"] = "right"
        columns.append(col)
    items = [{f"c{i}": v for i, v in enumerate(r)} for r in rows]
    summary = f"共 {len(rows)}{'+' if truncated else ''} 行"
    single = len(rows) == 1 and len(keys) == 1
    text = f"{keys[0]}：{rows[0][0]}" if single else f"查询返回 {summary}。"
    s = get_settings()
    ans = {
        "card": "table",  # [推断] 本期只出表格卡
        "dataAsOf": s.data_cutoff,
        "basis": {"caliber": "LLM 生成 SQL [推断]，未走 SemPlan 校验", "perm": "已按行级权限过滤"},
        "text": text,
        "table": {
            "id": "result",
            "title": question[:30],
            "columns": columns,
            "items": items,
            "total": len(rows),
            "summary": summary,
            "paged": len(rows) > TABLE_PAGE,
        },
    }
    return "answer", {"answer": ans}, len(rows), elapsed, sql
