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
- 只给模型看业务列（Vanna「只训练相关 DDL」的做法）：系统列（is_seed / lock_version / created_at …）不进 DDL；
  过滤列（is_test）按表从 DDL 推出，没有这列的表明确告诉模型不要加。
- 并列第一：「= MAX / RANK() = 1」返回全部并列项，是 Spider / BIRD 评测里常见的金标写法
  [推断：思路，非代码复制]。
"""

from __future__ import annotations

import re
import time
from collections.abc import AsyncIterator
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from sqlalchemy import select
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession
from sqlalchemy.schema import CreateTable, SetColumnComment, SetTableComment
from sqlglot import exp

from app.common.sql_guard import DIALECT, assert_single_select, run_guarded
from app.core import clock
from app.core.config import get_settings
from app.core.enums import LabelEnum
from app.core.errors import AppError
from app.db.base import Base
from app.db.session import QaReadonlyNotConfigured, get_qa_readonly_engine
from app.modules.qa.llm import LLMClient
from app.modules.qa.models import QaSynonymLink
from app.modules.static.models import (
    Dimension,
    DimMapping,
    DimValue,
    LinkType,
    Metric,
    ObjectType,
    QaPath,
)

# [推断] 本期可问的物理表 = biz 受治理表（行级权限已注册）
QA_TABLES = ("store", "product", "biz_order", "order_line")
TABLE_PAGE = 10  # 前端表格首屏行数，超过标 paged
ROW_LIMIT = 200  # 单次结果最多行数（外包 LIMIT）
FIX_RETRIES = 1  # 执行报错后回喂 LLM 修正的次数
# 公共簿记列（MutableTableMixin）：对问数无意义，模型看见会乱加 is_seed = false 之类条件，DDL 里去掉
SYSTEM_COLS = frozenset({"is_seed", "lock_version", "created_at", "created_by", "updated_at"})
FILTER_COLS = {"is_test": "剔除测试数据"}  # 业务过滤列 → 说明；只在 DDL 里真有这列的表上提示
# [推断] 物理表对应的本体对象（本体 source_table 是源系统表名，对不上 biz 物理表）：只取这些对象的维度取值
QA_DIM_OBJECTS = ("Store", "SalesOrder", "Category", "Product")
DIM_VALUE_CAP = 40
PG_QUERY_CANCELED = "57014"  # statement_timeout 触发的 SQLSTATE


class QaQueryTimeout(AppError):
    status_code = 504
    code = "QUERY_TIMEOUT"
    message = "查询超时，请缩小时间范围或条件后再问"


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


def _label_enum(col: Any) -> type[LabelEnum] | None:
    cls = getattr(col.type, "enum_class", None)
    return cls if isinstance(cls, type) and issubclass(cls, LabelEnum) else None


def _qa_tables() -> list[Any]:
    return [t for n in QA_TABLES if (t := Base.metadata.tables.get(n)) is not None]


def _schema_lines() -> list[str]:
    """Vanna 式 DDL 上下文：用 SQLAlchemy 把 ORM 表编译成 PG 的 CREATE TABLE + COMMENT ON。

    系统列不进 DDL；枚举列（库里存英文 code）在注释里给出 code=中文标签。
    """
    dialect = postgresql.dialect()
    out = []
    for t in _qa_tables():
        create = CreateTable(t)
        create.columns = [c for c in create.columns if c.element.name not in SYSTEM_COLS]
        ddl = [str(create.compile(dialect=dialect)).strip() + ";"]
        if t.comment:
            ddl.append(str(SetTableComment(t).compile(dialect=dialect)) + ";")
        for c in t.columns:
            if c.name in SYSTEM_COLS:
                continue
            if enum := _label_enum(c):
                codes = "，".join(f"{m.name}={m.value}" for m in enum)
                ddl.append(f"COMMENT ON COLUMN {t.name}.{c.name} IS '{c.comment or '取值'}：{codes}';")
            elif c.comment:
                ddl.append(str(SetColumnComment(c).compile(dialect=dialect)) + ";")
        out.append("\n".join(ddl))
    return out


def _filter_lines() -> list[str]:
    """每张表能用的过滤列，从表结构推出（没有这列的表明说不要加）。"""
    out, none = [], []
    for t in _qa_tables():
        cols = [f"{c} = false（{why}）" for c, why in FILTER_COLS.items() if c in t.columns]
        if cols:
            out.append(f"- {t.name}：{'；'.join(cols)}")
        else:
            none.append(t.name)
    if none:
        out.append(f"- {'、'.join(none)}：没有过滤列，不要加 is_test / is_seed 之类条件")
    return out


def code_labels() -> dict[str, str]:
    """枚举列 code → 中文标签（多个枚举撞同一 code 时不映射）。答案成形时把原始 code 换成标签。"""
    seen: dict[str, set[str]] = {}
    for t in _qa_tables():
        for c in t.columns:
            for m in _label_enum(c) or ():
                seen.setdefault(m.name, set()).add(m.value)
    return {k: next(iter(v)) for k, v in seen.items() if len(v) == 1}


METRIC_DEF_KEYS = ("基础对象", "时间字段", "默认过滤", "同名默认")


def _metric_notes(rows: list | None) -> str:
    """指标定义页里和取数有关的几行（基础对象 / 时间字段 / 默认过滤 / 同名默认），拼在公式后面。"""
    got = {r.get("k"): r.get("v") for r in rows or () if isinstance(r, dict)}
    parts = [f"{k}：{got[k]}" for k in METRIC_DEF_KEYS if got.get(k)]
    return f"（{'；'.join(parts)}）" if parts else ""


SAMPLE_CAP = 30
SAMPLE_SKIP = frozenset({"code", "name", "store_no", "member_phone", "barcode", "spec"})


async def _sample_lines(session: AsyncSession) -> list[str]:
    """低基数文本列的全部取值（≤ SAMPLE_CAP 个），模型写 WHERE 时照抄真实值（如「南京」不是「南京市」）。

    思路取自 auto-ontology 在 schema 里附 sample values（formatters_util / sample_values，Apache-2.0），
    这里只取 distinct，超过上限的列（门店名、地址这类）不列。
    """
    out = []
    for t in _qa_tables():
        for c in t.columns:
            if (
                c.name in SYSTEM_COLS
                or c.name in SAMPLE_SKIP
                or _label_enum(c)
                or not isinstance(c.type, sa.String)
            ):
                continue
            vals = (
                (await session.execute(sa.select(c).distinct().where(c.is_not(None)).limit(SAMPLE_CAP + 1)))
                .scalars()
                .all()
            )
            if vals and len(vals) <= SAMPLE_CAP:
                out.append(f"- {t.name}.{c.name}：" + "、".join(sorted(map(str, vals))))
    return out


async def _dimension_lines(session: AsyncSession) -> list[str]:
    """本体维度的层级 + 取值（含同义词、上级），给模型做取值映射和层级判断。"""
    dims = (
        await session.scalars(
            select(Dimension)
            .join(ObjectType, ObjectType.id == Dimension.object_id)
            .where(ObjectType.api_name.in_(QA_DIM_OBJECTS))
            .order_by(Dimension.seq)
        )
    ).all()
    code_of: dict[str, str] = {}
    for code, label in code_labels().items():  # 标签 → 库里的 code；同一标签多个 code 时不标
        code_of[label] = "" if label in code_of else code
    out = []
    for d in dims:
        vals = (
            await session.scalars(
                select(DimValue)
                .where(DimValue.dimension_id == d.id)
                .order_by(DimValue.id)
                .limit(DIM_VALUE_CAP + 1)
            )
        ).all()
        if not vals or len(vals) > DIM_VALUE_CAP:  # 时间、门店这类大维度不全列，靠表里的列直接查
            continue
        levels = " > ".join(lv.name for lv in d.levels)
        items = []
        for v in vals:
            tag = v.name + (f"={code_of[v.name]}" if code_of.get(v.name) else "")
            tag += f"（同义：{'、'.join(v.synonyms)}）" if v.synonyms else ""
            items.append(f"{v.level_name}:{tag}" + (f"∈{v.parent}" if v.parent else ""))
        out.append(f"- {d.display_name}（层级 {levels}）：" + "；".join(items))
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
    metrics, seen_defs = [], set()
    for m in (
        await session.scalars(select(Metric).where(Metric.formula.is_not(None)).order_by(Metric.id))
    ).all():
        notes = _metric_notes(m.def_rows)
        if notes and notes in seen_defs:  # 多个指标共用同一份定义页时不重复贴，省 token
            notes = "（基础对象、时间字段、默认过滤同上一条）"
        seen_defs.add(notes)
        metrics.append(f"- {m.display_name}（{m.unit}，{m.version}）= {m.formula}{notes}")
    syn = [f"- {s.word} → {s.target}" for s in (await session.scalars(select(QaSynonymLink))).all()]
    for d in (await session.scalars(select(DimMapping).order_by(DimMapping.seq).limit(40))).all():
        syn.append(f"- {d.phrase} → {'/'.join(d.targets)}")
    paths = [
        f"- {p.group_key}：{p.hint or '默认路径'}"
        for p in (await session.scalars(select(QaPath).where(QaPath.is_default).order_by(QaPath.id))).all()
    ]
    return {
        "schema": _schema_lines(),
        "filters": _filter_lines(),
        "dims": await _dimension_lines(session),
        "samples": await _sample_lines(session),
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

# 同摘自 auto-ontology prompts.py（Apache-2.0, NVIDIA）的 **Style** 段（DISTINCT / NULL 两条，原文照录），
# 以及 main_system_prompt_template 的「Today's date」做法（这里写成中文 + 时分）。
_STYLE_RULES = (
    "**Style**\n"
    "- Preserve natural row grain. Use DISTINCT only when the question or authoritative "
    "evidence explicitly asks for distinct, different, unique, or deduplicated results.\n"
    "- NULL filtering: Do not add `IS NOT NULL` solely because a projected field "
    "has `is_nullable: true`. Add it only when the question or evidence requires the "
    "requested value to exist. Otherwise, preserve rows containing NULLs.\n"
)

_GUIDELINES = (
    "===Response Guidelines \n"
    "1. 上下文足够时，只输出一条 SELECT（可用 WITH），放在 ```sql 代码块里，不要解释；\n"
    "2. 只能用 ===Tables 里的表和列；不要写 INSERT/UPDATE/DELETE/DDL；\n"
    "3. 指标按“指标口径”计算；用户说法按“同义词”映射；多表关联优先用“关联”与“默认问数路径”；\n"
    '4. 列用中文别名（AS "门店数"），结果行数尽量少；过滤条件只按“过滤列（按表）”加，'
    "表里没有的列不要写；题目和指标口径都没提到的条件（如门店状态、订单状态）不要自己加；\n"
    "5. 路径型列（值形如“大类/中类”，如 product.category，见“维度层级与取值”）：问“品类 / category”"
    "没指明中类时，SELECT 和 GROUP BY 都用最上层 split_part(列, '/', 1)，不要按整串分组；"
    "按某个上层取值过滤用 LIKE '上层/%'；\n"
    "6. 问“最多 / 最高 / 最少 / most / top”且没说要前几名时，可能有并列，必须返回所有并列项，"
    "禁止 ORDER BY … LIMIT 1。写法：WITH t AS (SELECT 分组, COUNT(*) AS n FROM … GROUP BY 分组) "
    "SELECT 分组, n FROM t WHERE n = (SELECT MAX(n) FROM t)；说了“前 N”才用 ORDER BY … LIMIT N；\n"
    "7. 枚举列在库里存英文 code（见 COMMENT 里的 code=标签），条件里用 code；\n"
    "8. 列出或找出门店、商品这类对象时，同时返回业务编号 code 和名称 name；"
    "排名 / 最值题同时返回对象和对应数值；\n"
    "9. '--'、空串这类占位值表示“无”，按某列计数或分组时排除；文本条件的值照抄“列取值”；"
    "门店、商品用简称（如“张江店”）时用 name LIKE '%张江%'；\n"
    "10. 按日 / 月分组时输出 date：date_trunc('month', 列)::date 或 列::date；指标的时间字段按“指标口径”"
    "（如出库时间 → biz_order.shipped_at）；公式里写了条件时以公式为准；\n"
    "11. 确保 SQL 符合 PostgreSQL 语法、可直接执行。\n"
)

_WEEKDAY = "一二三四五六日"


def _time_anchor() -> str:
    """当前时间 + 常用相对时间的日期区间（左闭右开）。

    思路同 auto-ontology main_system_prompt_template 把「Today's date」写进提示词；
    这里多算好几个常用区间，免得模型自己推日期或用库里的 NOW()（库时间不等于业务时钟）。
    """
    now = clock.local_now()
    d = now.date()
    mon = d - timedelta(days=d.weekday())
    m1 = d.replace(day=1)
    pm1 = (m1 - timedelta(days=1)).replace(day=1)
    cur = f"{now:%Y-%m-%d %H:%M:%S}"
    rng = {
        "今天（截至目前）": (d, cur),
        "昨天": (d - timedelta(days=1), d),
        "前天": (d - timedelta(days=2), d - timedelta(days=1)),
        "本周（周一起，截至目前）": (mon, cur),
        "上周": (mon - timedelta(days=7), mon),
        "本月（截至目前）": (m1, cur),
        "上月": (pm1, m1),
        "今年（截至目前）": (d.replace(month=1, day=1), cur),
        "近 3 天（含今天）": (d - timedelta(days=2), cur),
        "近 7 天（含今天）": (d - timedelta(days=6), cur),
    }
    spans = "；".join(f"{k} [{a}, {b})" for k, (a, b) in rng.items())
    return (
        f"## 时间\n当前时间：{now:%Y-%m-%d %H:%M}（周{_WEEKDAY[d.weekday()]}）；"
        f"数据截至：{get_settings().data_cutoff}\n常用区间（左闭右开）：{spans}\n"
        f"“近 N 小时 / 近 N 天 / 截至目前”以当前时间 {cur} 为终点。"
        "SQL 里只写这些日期字面量，不要用 NOW() / CURRENT_DATE（库时间不是业务时钟）。"
    )


_DOC_TITLES = {
    "filters": "过滤列（按表）",
    "dims": "维度层级与取值",
    "samples": "列取值（全部）",
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
    docs.append(_time_anchor())
    prompt += "\n===Additional Context \n\n" + "\n\n".join(docs) + "\n\n"
    prompt += _PG_RULES + "\n" + _STYLE_RULES + "\n" + _GUIDELINES
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


def _label_rows(rows: list[list[Any]]) -> list[list[Any]]:
    """整列都是同一批枚举 code 时换成中文标签（shipped → 已出库）；返回结构不变。"""
    if not rows:
        return rows
    labels = code_labels()
    for i in range(len(rows[0])):
        vals = [r[i] for r in rows if r[i] is not None]
        if vals and all(isinstance(v, str) and v in labels for v in vals):
            for r in rows:
                if r[i] is not None:
                    r[i] = labels[r[i]]
    return rows


async def answer(
    session: AsyncSession, conn: AsyncConnection, llm: LLMClient, user: Any, question: str
) -> tuple[str, dict[str, Any], int, int, str]:
    """返回 (kind, snapshot, row_count, elapsed_ms, sql)。

    SQL 只进 qa_turn.sql_text，不出接口；SQL 被拒抛 SqlRejected（403 SQL_REJECTED）。
    """
    t0 = time.monotonic()
    ctx = await build_context(session)
    messages = build_prompt(question, ctx)
    limit = ROW_LIMIT
    for attempt in range(1 + FIX_RETRIES):
        reply = await llm.generate_sql(messages)
        sql = extract_sql(reply)
        tree = assert_single_select(sql)  # 先校验原 SQL，拒绝信息更准；被拒不重试
        try:
            async with conn.begin_nested():  # savepoint：执行报错不弄坏连接上的事务
                res = await run_guarded(conn, wrap_limit(tree, limit + 1), user)
                keys = list(res.keys())
                rows = [[_cell(v) for v in r] for r in res.fetchall()]
            break
        except DBAPIError as e:
            state = getattr(e.orig, "sqlstate", None) or getattr(e.orig, "pgcode", None)
            if state == PG_QUERY_CANCELED:  # statement_timeout：不重试，504
                raise QaQueryTimeout() from None
            if attempt >= FIX_RETRIES:
                raise
            # 报错回喂再改一次（常见的自修正思路，非代码复制；只回喂数据库报错首行，不含数据）
            err = str(e.orig).splitlines()[0][:300] if e.orig else type(e).__name__
            messages = [
                *messages,
                {"role": "assistant", "content": reply},
                {"role": "user", "content": f"上面的 SQL 执行报错：{err}\n请修正后只输出一条 SQL。"},
            ]
    truncated = len(rows) > limit
    rows = _label_rows(rows[:limit])
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
