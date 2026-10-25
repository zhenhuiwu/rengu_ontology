"""问数最小链路（W6）：问题 → 上下文 → prompt → LLM → 抽 SQL → sql_guard → 只读执行 → 答案轮次快照。

[推断] 本期 LLM 直接写 SQL（Vanna / SQLBot 式：schema + 本体指标/同义词/默认路径进 prompt）；
落地方案里的 SemPlan JSON → 校验器 → 编译器是二期，这里不做。sql_guard 兜底：单条 SELECT、
表白名单（受治理业务表）、行级权限注入；执行走只读引擎（statement_timeout + read_only）并外包 LIMIT。

复用的开源件（只抄 MIT / Apache-2.0 代码；SQLBot、WrenAI 是 GPL/AGPL，只参考思路不抄代码）：
- prompt 结构（===Tables DDL / ===Additional Context / ===Response Guidelines）、extract_sql 的围栏规则：
  Vanna `legacy/base/base.py` get_sql_prompt / extract_sql，MIT，https://github.com/vanna-ai/vanna
- PostgreSQL 方言规则段：NVIDIA auto-ontology `retrieval/text_to_sql/prompts.py`
  `_POSTGRES_DIALECT_RULES`，Apache-2.0（本地 /workspace/tmp/auto-ontology）
- 表结构序列化：SQLAlchemy 自带 DDL 编译（CreateTable / SetTableComment / SetColumnComment），MIT
- 解析、校验、外包 LIMIT：sqlglot AST（app/common/sql_guard 已用），MIT
"""

from __future__ import annotations

import re
import time
from collections.abc import AsyncIterator
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession
from sqlalchemy.schema import CreateTable, SetColumnComment, SetTableComment
from sqlglot import exp

from app.common.sql_guard import DIALECT, assert_single_select, run_guarded
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
    """Vanna 式 DDL 上下文：用 SQLAlchemy 把 ORM 表编译成 PG 的 CREATE TABLE + COMMENT ON。"""
    dialect = postgresql.dialect()
    out = []
    for name in QA_TABLES:
        t = Base.metadata.tables.get(name)
        if t is None:
            continue
        ddl = [str(CreateTable(t).compile(dialect=dialect)).strip() + ";"]
        if t.comment:
            ddl.append(str(SetTableComment(t).compile(dialect=dialect)) + ";")
        ddl += [str(SetColumnComment(c).compile(dialect=dialect)) + ";" for c in t.columns if c.comment]
        out.append("\n".join(ddl))
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


# 照 Vanna get_sql_prompt：初始说明 + ===Tables + ===Additional Context + ===Response Guidelines，
# 问题单独一条 user。
_INITIAL = (
    "你是 PostgreSQL 专家，也是零售数据分析助手。请生成一条 SQL 回答用户问题。"
    "只能依据下面给出的上下文，并遵守回答规范和格式要求。\n"
)

# 摘自 auto-ontology prompts._POSTGRES_DIALECT_RULES（Apache-2.0, NVIDIA），删了与本项目无关的大小写一条。
_PG_RULES = (
    "**PostgreSQL-specific (STRICT — these will error at execution)**\n"
    "- WHERE and HAVING cannot reference SELECT aliases. "
    "Repeat the full expression or wrap in a subquery/CTE.\n"
    "- GROUP BY cannot reference SELECT aliases. Repeat the full expression "
    "(including CASE WHEN blocks) in GROUP BY, or wrap the query in a subquery/CTE.\n"
)

_GUIDELINES = (
    "===Response Guidelines \n"
    "1. 上下文足够时，只输出一条 SELECT（可用 WITH），放在 ```sql 代码块里，不要解释；\n"
    "2. 只能用 ===Tables 里的表和列；不要写 INSERT/UPDATE/DELETE/DDL；\n"
    "3. 指标按“指标口径”计算；用户说法按“同义词”映射；多表关联优先用“关联”与“默认问数路径”；\n"
    '4. 列用中文别名（AS "门店数"），结果行数尽量少；默认剔除 is_test = true 的测试数据；\n'
    "5. 确保 SQL 符合 PostgreSQL 语法、可直接执行。\n"
)

_DOC_TITLES = {
    "objects": "本体对象",
    "links": "关联",
    "metrics": "指标口径",
    "synonyms": "同义词",
    "paths": "默认问数路径",
}


def build_prompt(question: str, ctx: dict[str, list[str]]) -> list[dict[str, str]]:
    prompt = _INITIAL
    if ctx.get("schema"):
        prompt += "\n===Tables \n" + "\n\n".join(ctx["schema"]) + "\n\n"
    docs = [f"## {title}\n" + "\n".join(ctx[k]) for k, title in _DOC_TITLES.items() if ctx.get(k)]
    docs.append(f"数据截至：{get_settings().data_cutoff}")
    prompt += "\n===Additional Context \n\n" + "\n\n".join(docs) + "\n\n"
    prompt += _PG_RULES + "\n" + _GUIDELINES
    return [{"role": "system", "content": prompt}, {"role": "user", "content": question}]


# Vanna extract_sql 的围栏规则（先 ```sql 块，再任意 ``` 块，取最后一个）。
# [推断] 没抄它前面的 `SELECT ... ;` 正则：那条会把 "select 1; drop …" 截成 "select 1" 静默放行，
# 这里要让多语句原样交给 sql_guard 拒绝（403 SQL_REJECTED）。
_FENCE_SQL = re.compile(r"```sql\s*\n(.*?)```", re.S | re.I)
_FENCE_ANY = re.compile(r"```(.*?)```", re.S)


def extract_sql(text: str) -> str:
    text = text or ""
    blocks = _FENCE_SQL.findall(text) or _FENCE_ANY.findall(text)
    sql = (blocks[-1] if blocks else text).strip()
    return sql.rstrip(";").strip()


def wrap_limit(tree: exp.Expression, limit: int) -> str:
    """sqlglot 构造 SELECT * FROM (<原查询>) AS _qa LIMIT n，不拼字符串。"""
    return exp.select("*").from_(tree.subquery("_qa")).limit(limit).sql(dialect=DIALECT)


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
    tree = assert_single_select(sql)  # 先校验原 SQL，拒绝信息更准
    limit = ROW_LIMIT
    res = await run_guarded(conn, wrap_limit(tree, limit + 1), user)
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
