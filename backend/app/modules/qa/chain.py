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
- 意图判定（作答 / 反问 / 拒答三选一）：思路同 SQLBot、DB-GPT 的意图识别与 Vanna 的
  「上下文不够就说明原因」（Vanna 第 1 条 guideline，MIT）；这里让模型在 SQL 之外可回一个 JSON
  `{"clarify": …}` 或 `{"refuse": …}`，
  映射到前端已有的 answer+clarify / denied 两种轮次 [推断：思路，非代码复制]。
- 口径裁定是 qa 自有数据：seeds/extra/qa/caliber/notes.json（指标口径）、refuse.json（拒答清单），
  跟 qa-eval.md §4 一致；静态 metric.def_rows 与之冲突时以这里为准，冲突项另报静态本体负责人。
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import AsyncIterator
from datetime import date, datetime, timedelta
from decimal import Decimal
from functools import cache
from typing import Any

import sqlalchemy as sa
from sqlalchemy import select, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession
from sqlalchemy.schema import CreateTable, SetColumnComment, SetTableComment
from sqlglot import exp

from app.common.ontology import require_ontology_id
from app.common.sql_guard import DIALECT, assert_single_select, run_guarded
from app.core import clock
from app.core.config import get_settings
from app.core.enums import LabelEnum
from app.core.errors import AppError
from app.db.base import Base
from app.db.session import QaReadonlyNotConfigured, SessionDep, get_qa_readonly_engine
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
SYSTEM_COLS = frozenset({"ontology_id", "is_seed", "lock_version", "created_at", "created_by", "updated_at"})
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


async def get_qa_conn(session: SessionDep) -> AsyncIterator[AsyncConnection]:
    """只读执行连接（依赖，测试可覆盖）。"""
    try:
        engine = get_qa_readonly_engine()
    except QaReadonlyNotConfigured:
        raise QaExecUnavailable() from None
    async with engine.connect() as conn, conn.begin():
        await conn.execute(
            text("SELECT set_config('rengu.ontology_id', :ontology_id, true)"),
            {"ontology_id": str(require_ontology_id(session))},
        )
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


def _caliber_dir():
    return get_settings().seeds_extra_dir / "qa" / "caliber"


@cache
def _caliber_lines() -> list[str]:
    """qa 自有口径裁定（seeds/extra/qa/caliber/notes.json），文件没有就不出这一段。"""
    f = _caliber_dir() / "notes.json"
    if not f.exists():
        return []
    out = []
    for n in json.loads(f.read_text(encoding="utf-8")):
        alias = f"（又叫 {'、'.join(n['aliases'])}）" if n.get("aliases") else ""
        sql = f"；写法：{n['sql']}" if n.get("sql") else ""
        out.append(f"- {n['term']}{alias}：{n['caliber']}{sql}")
    names = [x for n in json.loads(f.read_text(encoding="utf-8")) for x in [n["term"], *n.get("aliases", [])]]
    out.append(
        f"- 上面这些中文说法（{'、'.join(x for x in names if not x.isascii())}）口径已裁定，"
        "问到时直接按裁定写 SQL，不要因为指标反问"
    )
    return out


@cache
def _caliber_checks() -> list[tuple[str, list[str], dict[str, Any]]]:
    """口径裁定里带 check 的条目：(term, [term + aliases], check)。"""
    f = _caliber_dir() / "notes.json"
    if not f.exists():
        return []
    return [
        (n["term"], [n["term"], *n.get("aliases", [])], n["check"])
        for n in json.loads(f.read_text(encoding="utf-8"))
        if n.get("check")
    ]


_TIME_COLS = ("shipped_at", "created_at_biz")
_TIME_HINTS = {"created_at_biz": ("下单", "下的单"), "shipped_at": ("出库",)}


def matched_calibers(question: str) -> list[tuple[str, dict[str, Any]]]:
    """问题里命中的口径裁定（最长匹配：被更长说法盖住的短说法不算，如“不含税”里的“含税”）。"""
    hits = []
    for term, names, check in _caliber_checks():
        for nm in names:
            start = question.find(nm)
            while start >= 0:
                hits.append((start, start + len(nm), term, check))
                start = question.find(nm, start + 1)
    out: dict[str, dict[str, Any]] = {}
    for a, b, term, check in hits:
        if not any(a2 <= a and b <= b2 and (b2 - a2) > (b - a) for a2, b2, *_ in hits):
            out.setdefault(term, check)
    return list(out.items())


def _status_ok(tree: exp.Expression, check: dict[str, Any]) -> bool:
    def is_status(e: exp.Expression) -> bool:
        return isinstance(e, exp.Column) and e.name == "status"

    def lits(e: exp.Expression) -> set[str]:
        return {x.this for x in e.find_all(exp.Literal) if x.is_string}

    if want := check.get("status_eq"):
        for e in tree.find_all(exp.EQ, exp.In):
            if is_status(e.this) and lits(e) & set(want) and not isinstance(e.parent, exp.Not):
                return True
        return False
    if want := check.get("status_ne"):
        for e in tree.find_all(exp.NEQ, exp.Not):
            inner = e.this if isinstance(e, exp.Not) else e
            if (
                isinstance(inner, (exp.EQ, exp.In, exp.NEQ))
                and is_status(inner.this)
                and lits(inner) & set(want)
            ):
                return True
        return False
    return True


def caliber_violations(question: str, tree: exp.Expression) -> list[str]:
    """按口径裁定的 check 校验 SQL（sqlglot AST）：状态条件在不在、时间字段对不对。

    时间：SQL 用了时间列才查；题目明说了“下单 / 出库”就按题目换时间字段（规则 3）。
    [推断] 只查命中的口径，不查窗口边界。
    """
    cols = {c.name for c in tree.find_all(exp.Column)}
    out = []
    for term, check in matched_calibers(question):
        if not _status_ok(tree, check):
            cond = (
                f"o.status = '{check['status_eq'][0]}'"
                if check.get("status_eq")
                else f"o.status <> '{check['status_ne'][0]}'"
            )
            out.append(f"{term} 要加条件 {cond}")
        want = check.get("time")
        for col, hints in _TIME_HINTS.items():
            if any(h in question for h in hints):
                want = col
        if want and cols & set(_TIME_COLS) and want not in cols:
            out.append(f"{term} 的时间窗要用 o.{want}")
    return out


@cache
def _refuse_lines() -> list[str]:
    f = _caliber_dir() / "refuse.json"
    if not f.exists():
        return []
    return [f"- {n['term']} → {n['code']}：{n['reason']}" for n in json.loads(f.read_text(encoding="utf-8"))]


async def build_context(session: AsyncSession) -> dict[str, list[str]]:
    """从本体表取：表结构、对象说明、关联 join、指标口径、同义词、默认问数路径。"""
    if require_ontology_id(session) != 1:
        raise AppError("当前本体工作空间尚未绑定问数数据源", code="DATA_NOT_CONFIGURED", status_code=503)
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
        "caliber": _caliber_lines(),
        "refuse": _refuse_lines(),
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
    "1. 三选一输出，不要解释：(a) 能答：只输出一条 SELECT（可用 WITH），放在 ```sql 代码块里；"
    "(b) 问题有歧义、答案会因理解不同而不同（指标没说清，如“表现 / 业绩 / 卖得怎么样”；"
    "时间和指标都没给；一个说法对应多个维度或多个对象，如“地区”“最大”、简称匹配到多家门店；"
    "对比基准不明；同一句里出现两条路径，如“下单”和“履约”；说法不在同义词里也没有对应维度值）时，"
    '只输出 ```json {"clarify": {"term": "有歧义的说法", "question": "反问的话", '
    '"options": ["候选1", "候选2"]}} ```；'
    "有同名默认口径的（如销售额默认不含税）、只缺时间但能按默认理解的不要反问；"
    "门店名或简称在“列取值 / 门店”里只对上一家时直接答，对上多家才反问；"
    "英文泛称（sales、performance）和“业绩 / 表现”这类没落到口径裁定说法上的要反问；"
    "“地区 / 区域”可指大区、区域、城市多个列时反问；渠道组合说法（如“外卖”）没有对应维度值时反问；"
    '(c) 命中“拒答清单”、要写操作、问本体外的东西时，只输出 ```json {"refuse": {"code": '
    '"NO_DATA|NO_PERMISSION|OUT_OF_SCOPE|NOT_SELECT", "reason": "原因"}} ```，'
    "不能用别的列凑答案；拒答优先于反问；code 选法：敏感字段 / 系统表 / 改权限 → NO_PERMISSION，"
    "本体里没有的对象（工资、天气…）→ OUT_OF_SCOPE，"
    "“拒答清单”里标 NO_DATA 的 → NO_DATA，写操作 → NOT_SELECT；\n"
    "2. 只能用 ===Tables 里的表和列；不要写 INSERT/UPDATE/DELETE/DDL；\n"
    "3. 指标先按“口径裁定”，没有的再按“指标口径”计算；说法命中口径裁定的名称或别名就用那一条，"
    "它写法里的条件（状态、is_test、join store、时间字段）一个都不能少，题目另说了时间口径（如“下单的”）"
    "只换时间字段；"
    "用户说法按“同义词”映射；多表关联优先用“关联”与“默认问数路径”；\n"
    '4. 列用中文别名（AS "门店数"），结果行数尽量少；过滤条件只按“过滤列（按表）”加，'
    "表里没有的列不要写；题目和口径（口径裁定 / 指标口径）都没提到的条件（如门店状态、订单状态）不要自己加，"
    "口径裁定里写了的状态条件（如销售额、含税销售额的 o.status = 'shipped'）必须加；\n"
    "5. 路径型列（值形如“大类/中类”，如 product.category，见“维度层级与取值”）：问“品类 / category”"
    "没指明中类时，SELECT 和 GROUP BY 都用最上层 split_part(列, '/', 1)，不要按整串分组；"
    "按某个上层取值过滤用 LIKE '上层/%'；\n"
    "6. 问“最多 / 最高 / 最少 / most / top”且没说要前几名时，可能有并列，必须返回所有并列项，"
    "禁止 ORDER BY … LIMIT 1。写法：WITH t AS (SELECT 分组, COUNT(*) AS n FROM … GROUP BY 分组) "
    "SELECT 分组, n FROM t WHERE n = (SELECT MAX(n) FROM t)；说了“前 N”才用 ORDER BY … LIMIT N；\n"
    "7. 枚举列在库里存英文 code（见 COMMENT 里的 code=标签），条件里用 code；\n"
    "8. 列出或找出门店、商品这类对象、或按“每个 / 各”门店、商品分组时，SELECT 和 GROUP BY 都带业务编号 code"
    "（再带名称 name）；"
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
        "近 30 天（含今天）": (d - timedelta(days=29), cur),
    }
    spans = "；".join(f"{k} [{a}, {b})" for k, (a, b) in rng.items())
    return (
        f"## 时间\n当前时间：{now:%Y-%m-%d %H:%M}（周{_WEEKDAY[d.weekday()]}）；"
        f"数据截至：{get_settings().data_cutoff}\n常用区间（左闭右开）：{spans}\n"
        f"“近 N 小时 / 近 N 天 / 截至目前”以当前时间 {cur} 为终点；任何时间窗（含“今天”“10 月”“2026 年每月”）"
        f"的上界都不超过当前时间 {cur}（库里有出库时间晚于当前时间的单，不能算进来）。"
        "SQL 里只写这些日期字面量，不要用 NOW() / CURRENT_DATE（库时间不是业务时钟）。"
    )


# ---- 值锚定 / 值校验（移植 NVIDIA auto-ontology text_to_sql，Apache-2.0）----
# 思路来自 agents/proactive_value_check.py（执行前查 WHERE 字面量是不是库里真实取值）和
# agents/empty_result_value_repair.py（0 行时把近似真实值回喂改一次，只一次）；
# _string_predicates / _like_predicates 改写自 db_probe/literal_check.py 的同名函数（sqlglot 版）。
# 本项目的取值规模小（门店、商品几百个），不探库做 DISTINCT，直接拿名称列表在 Python 里比。
_NAME_COLS = {"store": "name", "product": "name"}
_ANCHOR_MIN = 2  # 至少连续几个字一样才算提到


def _lcs(a: str, b: str) -> int:
    """最长公共子串长度。"""
    best, prev = 0, [0] * (len(b) + 1)
    for ca in a:
        cur = [0]
        for j, cb in enumerate(b):
            cur.append(prev[j] + 1 if ca == cb else 0)
        best = max(best, *cur)
        prev = cur
    return best


def _strip_suffix(v: str) -> str:
    return re.sub(r"(店|门店|分店)$", "", v)


async def _names(session: AsyncSession) -> dict[str, list[str]]:
    if require_ontology_id(session) != 1:
        return {}
    out = {}
    for t, c in _NAME_COLS.items():
        table = Base.metadata.tables[t]
        rows = await session.execute(select(table.c[c]).distinct())
        out[t] = [r[0] for r in rows if r[0]]
    return out


def nearest_values(lit: str, values: list[str], k: int = 5) -> list[str]:
    """跟字面量最像的真实值（按最长公共子串，至少 _ANCHOR_MIN 个字）。"""
    core = _strip_suffix(lit.strip("%"))
    scored = [(_lcs(core, v), v) for v in values]
    top = max((n for n, _ in scored), default=0)
    if top < _ANCHOR_MIN:
        return []
    return [v for n, v in scored if n == top][:k]


async def value_anchors(session: AsyncSession, question: str) -> list[str]:
    """问题里提到的门店 / 商品名 → 库里真实名称（auto-ontology value anchor 思路）。"""
    out = []
    for t, vals in (await _names(session)).items():
        q = re.sub(r"\s", "", question)
        scored = sorted(((_lcs(q, v), v) for v in vals), reverse=True)  # 带“店”比，简称“张江店”也能对上
        top = scored[0][0] if scored else 0
        if top >= 3:  # [推断] 2 个字的放宽试过（2 轮均值降 1），不放
            hits = [v for n, v in scored if n == top][:5]
            out.append(f"- {t}.{_NAME_COLS[t]} 最像的真实值：{'、'.join(hits)}")
    return out


def _string_predicates(tree: exp.Expression) -> list[tuple[exp.Column, exp.Literal]]:
    """正向 = / IN / LIKE 的 (列, 字符串字面量)；否定的不算（同 literal_check）。"""
    out = []
    for e in tree.find_all(exp.EQ, exp.In, exp.Like):
        if isinstance(e.parent, exp.Not) or not isinstance(e.this, exp.Column):
            continue
        lits = [e.expression] if not isinstance(e, exp.In) else list(e.expressions or [])
        out += [(e.this, x) for x in lits if isinstance(x, exp.Literal) and x.is_string]
    return out


async def literal_mismatches(session: AsyncSession, tree: exp.Expression) -> list[str]:
    """名称列上的字面量对不上真实值时给出候选（empty_result_value_repair 的回喂内容）。"""
    alias = {t.alias_or_name: t.name for t in tree.find_all(exp.Table)}
    names = None
    out = []
    for col, lit in _string_predicates(tree):
        table = alias.get(col.table) if col.table else None
        if table not in _NAME_COLS or col.name != _NAME_COLS[table]:
            continue
        names = names or await _names(session)
        v = lit.this
        pat = re.compile("^" + re.escape(v).replace("%", ".*").replace("_", ".") + "$")
        if any(pat.match(x) for x in names[table]):
            continue
        if near := nearest_values(v, names[table]):
            out.append(f"{table}.{col.name} = '{v}' 在库里不存在，最接近的真实值：{'、'.join(near)}")
    return out


def cap_time_upper(tree: exp.Expression, now: datetime) -> bool:
    """时间列 < / <= 字面量 晚于当前时间时改成当前时间（任何窗口上界不超过现在）。返回是否改了。"""
    cur = f"{now:%Y-%m-%d %H:%M:%S}"
    changed = False
    for e in list(tree.find_all(exp.LT, exp.LTE)):
        if not (isinstance(e.this, exp.Column) and e.this.name in _TIME_COLS):
            continue
        lit = e.expression
        node = lit.this if isinstance(lit, exp.Cast) else lit
        if not (isinstance(node, exp.Literal) and node.is_string):
            continue
        try:
            v = datetime.fromisoformat(node.this.strip()).replace(tzinfo=None)
        except ValueError:
            continue
        if v > now.replace(tzinfo=None):
            e.set("expression", exp.Literal.string(cur))
            if isinstance(e, exp.LTE):
                e.replace(exp.LT(this=e.this, expression=e.expression))
            changed = True
    return changed


_DOC_TITLES = {
    "anchors": "问题里提到的门店 / 商品（库里真实名称：写 WHERE 照抄；只对上一家就直接用，不要反问）",
    "caliber": "口径裁定（qa 裁定，跟“指标口径”冲突时以这里为准）",
    "session_scope": "本轮会话范围（查询时同时遵守行级权限；不能扩大权限）",
    "session_caliber": "会话口径（用户在工作台选的；指标按这里取口径变体）",
    "refuse": "拒答清单（问到这些直接拒答，不写 SQL）",
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


REFUSE_CODES = ("NO_DATA", "NO_PERMISSION", "OUT_OF_SCOPE", "NOT_SELECT")
_FENCE_JSON = re.compile(r"```json\s*\n?(.*?)```", re.S | re.I)


def parse_intent(text: str) -> tuple[str, Any]:
    """模型回复 → ("sql", sql) | ("clarify", {...}) | ("refuse", {...})。

    只认 ```json 块或整段就是 JSON 对象的回复，且键是 clarify / refuse；别的都当 SQL 交给 sql_guard。
    """
    raw = text or ""
    m = _FENCE_JSON.findall(raw)
    body = m[-1].strip() if m else raw.strip()
    if body.startswith("{"):
        try:
            obj = json.loads(body)
        except ValueError:
            obj = None
        if isinstance(obj, dict):
            c, r = obj.get("clarify"), obj.get("refuse")
            if isinstance(c, dict):
                opts = [str(o) for o in (c.get("options") or []) if str(o).strip()][:6]
                return "clarify", {
                    "term": str(c.get("term") or "")[:32],
                    "question": str(c.get("question") or "请再具体一点")[:200],
                    "options": opts,
                }
            if isinstance(r, dict):
                code = str(r.get("code") or "").upper()
                return "refuse", {
                    "code": code if code in REFUSE_CODES else "OUT_OF_SCOPE",
                    "reason": str(r.get("reason") or "这个问题暂时答不了")[:200],
                }
    return "sql", extract_sql(raw)


def clarify_snapshot(c: dict[str, Any]) -> dict[str, Any]:
    """反问 → answer 轮带 clarify（§3.1：澄清是 answer 里带 clarify），不执行 SQL、没有表格。"""
    opts = [{"value": f"o{i}", "label": o} for i, o in enumerate(c["options"], 1)]
    return {
        "answer": {
            "card": "clarify",  # [推断] 只有反问，没有表格卡
            "dataAsOf": get_settings().data_cutoff,
            "text": c["question"],
            "clarify": {"term": c["term"], "chosen": None, "options": opts},
            "suggest": {"followUps": [o["label"] for o in opts]},
        }
    }


def refuse_snapshot(r: dict[str, Any]) -> dict[str, Any]:
    """拒答 → denied 轮（形状同 QA_DEMO_TURNS 的 denied：lead/bold/tail/tag），多带 code。"""
    tag = "权限拦截" if r["code"] == "NO_PERMISSION" else "无法回答"
    return {"lead": "", "bold": r["reason"], "tail": "。", "tag": tag, "code": r["code"]}


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
    session: AsyncSession,
    conn: AsyncConnection,
    llm: LLMClient,
    user: Any,
    question: str,
    caliber_lines: list[str] | None = None,
    session_scope: str | None = None,
) -> tuple[str, dict[str, Any], int, int, str | None]:
    """返回 (kind, snapshot, row_count, elapsed_ms, sql)。

    SQL 只进 qa_turn.sql_text，不出接口；SQL 被拒抛 SqlRejected（403 SQL_REJECTED）。
    """
    ontology_id = require_ontology_id(session)
    if getattr(user, "ontology_id", None) != ontology_id:
        raise AppError("问数身份与工作空间不匹配", code="ONTOLOGY_SCOPE_CONFLICT", status_code=403)
    t0 = time.monotonic()
    ctx = await build_context(session)
    ctx["anchors"] = await value_anchors(session, question)
    ctx["session_scope"] = [session_scope] if session_scope else []
    ctx["session_caliber"] = caliber_lines or []  # 会话口径 → 指标变体（service.caliber_lines）
    messages = build_prompt(question, ctx)
    limit = ROW_LIMIT
    attempt, checked, repaired = 0, False, False
    while True:
        reply = await llm.generate_sql(messages)
        intent, val = parse_intent(reply)
        if intent != "sql":  # 反问 / 拒答：不执行 SQL
            elapsed = int((time.monotonic() - t0) * 1000)
            if intent == "clarify":
                return "answer", clarify_snapshot(val), 0, elapsed, None
            return "denied", refuse_snapshot(val), 0, elapsed, None
        sql = val
        tree = assert_single_select(sql)  # 先校验原 SQL，拒绝信息更准；被拒不重试
        if not checked:  # 口径校验不过，回喂改一次（只一次，改完不再查）
            checked = True
            if bad := caliber_violations(question, tree):
                messages = [
                    *messages,
                    {"role": "assistant", "content": reply},
                    {
                        "role": "user",
                        "content": "上面的 SQL 不符合口径裁定："
                        + "；".join(bad)
                        + "。请修正后只输出一条 SQL。",
                    },
                ]
                continue
        if cap_time_upper(tree, clock.local_now()):
            sql = tree.sql(dialect=DIALECT)
        try:
            async with conn.begin_nested():  # savepoint：执行报错不弄坏连接上的事务
                res = await run_guarded(conn, wrap_limit(tree, limit + 1), user)
                keys = list(res.keys())
                rows = [[_cell(v) for v in r] for r in res.fetchall()]
        except DBAPIError as e:
            state = getattr(e.orig, "sqlstate", None) or getattr(e.orig, "pgcode", None)
            if state == PG_QUERY_CANCELED:  # statement_timeout：不重试，504
                raise QaQueryTimeout() from None
            if attempt >= FIX_RETRIES:
                raise
            attempt += 1
            # 报错回喂再改一次（常见的自修正思路，非代码复制；只回喂数据库报错首行，不含数据）
            err = str(e.orig).splitlines()[0][:300] if e.orig else type(e).__name__
            messages = [
                *messages,
                {"role": "assistant", "content": reply},
                {"role": "user", "content": f"上面的 SQL 执行报错：{err}\n请修正后只输出一条 SQL。"},
            ]
            continue
        # 0 行：名称字面量对不上真实值时回喂一次（empty_result_value_repair，只一次；仍为空就照常返回空）
        if not rows and not repaired:
            repaired = True
            if bad := await literal_mismatches(session, tree):
                messages = [
                    *messages,
                    {"role": "assistant", "content": reply},
                    {
                        "role": "user",
                        "content": "上面的 SQL 查出 0 行："
                        + "；".join(bad)
                        + "。请改用真实值后只输出一条 SQL。",
                    },
                ]
                continue
        break
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
