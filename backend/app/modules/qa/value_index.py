"""问数取值索引：把题目里的说法对到库里的真实取值（列 = 值），和本体维度绑定。

思路（只参考思路，不抄代码）：
- WrenAI / SQLBot 的「取值检索」：建表时把低基数文本列的 distinct 值建索引，问题里命中的值作为
  “精确取值”进 prompt，模型照抄 `列 = '值'`，不再猜 LIKE（WrenAI AGPL、SQLBot GPL，仅思路）。
- Vanna 的 `get_related_ddl / documentation` 检索：命中什么就只给什么（MIT，思路）。

数据来源（本体驱动，不写死任何业务词）：
1. 主来源：静态本体已发布快照的语义映射 `semantic_map`（维度取值 + 同义词 + 维度映射短语）；
2. 兜底：受治理物理表里低基数文本列 / 名称列 / 枚举列的 distinct 值（语义映射没覆盖到的列）；
3. 物理列和维度的绑定由取值重合度推出（哪个维度的取值在这列里出现得最多、最专一），
   不靠列名 / 表名；绑定到的“层级”取重合最多的层级名（维度取值 id 的前缀 “层级:编码”）。

结果三类：
- hits：题目说法 → (表, 列, 物理值, 路径段号)；同一说法对上多列时都给；
- missing：说法是本体里某维度在“该列绑定层级”上的取值，但库里这列没有这个值（→ 越界 / 无数据）；
- concepts：对象名 / 属性名 / 维度名 / 层级名 / 列注释词，匹配时跳过（对象名、维度名这类词是概念，不是取值）。
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

SEP = "/"  # 路径列分隔符
PATH_RATIO = 0.8  # ≥ 80% 的取值带分隔符 → 路径列
LOWCARD = 300  # 普通文本列 distinct 上限（超过不建索引）
NAMECARD = 3000  # 名称列 distinct 上限
MIN_TERM = 2  # 说法至少 2 个字
_PLACEHOLDER = re.compile(r"^[\s\-_/.·—–~*#?？]*$")


def norm(s: str) -> str:
    """全角转半角、小写、去空白。"""
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", s or "")).lower()


def is_placeholder(v: str) -> bool:
    return bool(_PLACEHOLDER.match(v or ""))


@dataclass
class Col:
    table: str
    column: str
    label: str = ""  # 列注释词 / 本体属性名（展示用）
    values: list[str] = field(default_factory=list)  # 物理取值（枚举列是 code）
    labels: dict[str, str] = field(default_factory=dict)  # 枚举：标签 → code
    path: bool = False
    is_name: bool = False
    entity: bool = False  # 几乎每行一个值（名称、负责人这类）：按它分组等于按对象分组
    dim: str | None = None  # 绑定的本体维度 api
    dim_name: str = ""
    level: str | None = None  # 绑定的层级名
    levels: list[str] = field(default_factory=list)  # 该维度出现过的层级（按出现顺序）

    @property
    def key(self) -> str:
        return f"{self.table}.{self.column}"

    @property
    def placeholders(self) -> list[str]:
        return [v for v in self.values if is_placeholder(v)]

    def segments(self, level: int) -> set[str]:
        return {
            p[level - 1]
            for v in self.values
            if not is_placeholder(v) and len(p := v.split(SEP)) >= level and p[level - 1]
        }


@dataclass(frozen=True)
class SemVal:
    dim: str
    dim_name: str
    level: str | None
    name: str
    words: tuple[str, ...]  # 名称 + 同义词 + 映射短语（已去掉概念词）


@dataclass(frozen=True)
class Hit:
    phrase: str
    col: Col
    value: str  # 物理值（路径列是那一段）
    seg: int = 0  # 0 = 整值；n = 路径第 n 段
    via: str = "value"  # value / label / semantic

    def sql(self, alias: str | None = None) -> str:
        ref = f"{alias or self.col.table}.{self.col.column}"
        v = self.value.replace("'", "''")
        if self.seg:
            return f"split_part({ref}, '{SEP}', {self.seg}) = '{v}'"
        return f"{ref} = '{v}'"


@dataclass(frozen=True)
class Missing:
    phrase: str
    sem: SemVal
    col: Col


@dataclass
class Resolution:
    hits: list[Hit] = field(default_factory=list)
    missing: list[Missing] = field(default_factory=list)
    spans: list[tuple[int, int]] = field(default_factory=list)  # 命中的说法在规范化题目里的位置

    def by_col(self) -> dict[str, list[Hit]]:
        out: dict[str, list[Hit]] = {}
        for h in self.hits:
            out.setdefault(h.col.key, []).append(h)
        return out

    def unique(self) -> list[Hit]:
        """一个说法只对上一列的命中（多列命中时有歧义，不做确定性改写）。"""
        per = Counter(h.phrase for h in self.hits)
        return [h for h in self.hits if per[h.phrase] == 1]


@dataclass
class ValueIndex:
    cols: list[Col] = field(default_factory=list)
    sem: list[SemVal] = field(default_factory=list)
    concepts: set[str] = field(default_factory=set)
    dims: dict[str, str] = field(default_factory=dict)  # 维度 api → 名称
    level_names: set[str] = field(default_factory=set)

    def col(self, table: str, column: str) -> Col | None:
        return next((c for c in self.cols if c.table == table and c.column == column), None)

    # ------------------------------------------------------------------ 绑定
    def bind(self) -> None:
        """每列绑定到取值重合度最高的维度：Σ 1 / (该维度里对上这个值的取值个数)。"""
        for c in self.cols:
            vals = {norm(v) for v in c.values} | ({norm(s) for s in c.segments(1)} if c.path else set())
            score: Counter[str] = Counter()
            lv: dict[str, Counter[str]] = {}
            for v in vals:
                by_dim: dict[str, list[SemVal]] = {}
                for s in self.sem:
                    if v in s.words:
                        by_dim.setdefault(s.dim, []).append(s)
                for d, ss in by_dim.items():
                    score[d] += 1 / len(ss)
                    for s in ss:
                        lv.setdefault(d, Counter())[s.level or ""] += 1 / len(ss)
            if not score:
                continue
            d, best = score.most_common(1)[0]
            if best < 1:
                continue
            c.dim, c.dim_name = d, self.dims.get(d, d)
            c.level = lv[d].most_common(1)[0][0] or None
            c.levels = list(dict.fromkeys(s.level for s in self.sem if s.dim == d and s.level))

    # ------------------------------------------------------------------ 解析
    def terms(self) -> dict[str, list[tuple[str, Any]]]:
        """规范化说法 → [(类型, 载荷)]。"""
        out: dict[str, list[tuple[str, Any]]] = {}

        def add(word: str, kind: str, payload: Any) -> None:
            w = norm(word)
            if len(w) >= MIN_TERM and w not in self.concepts and not is_placeholder(w):
                out.setdefault(w, []).append((kind, payload))

        for c in self.cols:
            if c.path:
                depth = max((len(v.split(SEP)) for v in c.values), default=1)
                for n in range(1, depth + 1):
                    for s in c.segments(n):
                        add(s, "value", (c, s, n))
            else:
                for v in c.values:
                    add(v, "value", (c, v, 0))
            for lab, code in c.labels.items():
                add(lab, "label", (c, code, 0))
        for s in self.sem:
            for w in s.words:
                add(w, "semantic", s)
        return out

    def resolve(self, question: str) -> Resolution:
        """贪心最长优先、不重叠地匹配说法；物理值优先，语义取值再经列绑定落到物理值。"""
        q = norm(question)
        res = Resolution()
        taken = [False] * len(q)
        terms = self.terms()
        for w in sorted(terms, key=len, reverse=True):
            start = 0
            while (i := q.find(w, start)) >= 0:
                start = i + 1
                if any(taken[i : i + len(w)]):
                    continue
                if self._accept(w, terms[w], res):
                    for k in range(i, i + len(w)):
                        taken[k] = True
                    res.spans.append((i, i + len(w)))
                break
        return res

    def _accept(self, w: str, items: list[tuple[str, Any]], res: Resolution) -> bool:
        phys = [p for k, p in items if k in ("value", "label")]
        if phys:
            seen = set()
            for c, v, seg in phys:
                if (c.key, v, seg) not in seen:
                    seen.add((c.key, v, seg))
                    res.hits.append(Hit(w, c, v, seg, "value"))
            return True
        sems = [p for k, p in items if k == "semantic"]
        got = False
        for s in sems:
            for c in self.cols:
                if c.dim != s.dim:
                    continue
                words = set(s.words)
                vals = [v for v in c.values if norm(v) in words]
                if vals:
                    res.hits += [Hit(w, c, v, 0, "semantic") for v in vals[:3]]
                    got = True
                    continue
                if c.path:
                    segs = [x for x in c.segments(1) if norm(x) in words]
                    if segs:
                        res.hits += [Hit(w, c, x, 1, "semantic") for x in segs[:3]]
                        got = True
                        continue
                if s.level and s.level == c.level and norm(s.name) == w:
                    # 点名的是这一层的正式名称，库里却没有 → 越界（同义词 / 映射短语不算，避免误伤）
                    res.missing.append(Missing(w, s, c))
                    got = True
        return got


# ---------------------------------------------------------------------- 构建


def semantic_entries(rows: list[dict[str, Any]]) -> tuple[list[SemVal], dict[str, str], set[str], set[str]]:
    """semantic_map 行 → (取值, 维度名, 概念词, 层级名)。"""
    dims = {r["api"]: r.get("name") or r["api"] for r in rows if r.get("kind") == "dimension"}
    concepts = {
        norm(x)
        for r in rows
        if r.get("kind") in ("object", "property", "dimension", "metric")
        for x in [r.get("name") or ""]
        if x
    }
    levels = set()
    vals = []
    for r in rows:
        if r.get("kind") != "dimValue":
            continue
        rid = str(r.get("id") or "")
        level = rid.split(":", 1)[0] if ":" in rid else None
        if level:
            levels.add(level)
        vals.append((r, level))
    concepts |= {norm(x) for x in levels}
    out = []
    for r, level in vals:
        words = tuple(
            dict.fromkeys(
                w
                for w in (norm(x) for x in [r.get("name") or "", *(r.get("synonyms") or [])])
                if w and w not in concepts
            )
        )
        if words:
            d = r.get("parent") or ""
            out.append(SemVal(d, dims.get(d, d), level, r.get("name") or "", words))
    return out, dims, concepts, levels


def build(
    cols: list[Col],
    semantic_rows: list[dict[str, Any]] | None,
    extra_concepts: set[str] = frozenset(),  # type: ignore[assignment]
) -> ValueIndex:
    sem, dims, concepts, levels = semantic_entries(semantic_rows or [])
    for c in cols:
        vs = [v for v in c.values if not is_placeholder(v)]
        c.path = len(vs) >= 2 and sum(SEP in v for v in vs) >= PATH_RATIO * len(vs)
    idx = ValueIndex(cols=cols, sem=sem, dims=dims, level_names=levels)
    idx.concepts = concepts | {norm(x) for x in extra_concepts if x}
    idx.bind()
    return idx


# ---------------------------------------------------------------------- 给模型看的话


def prompt_lines(res: Resolution) -> list[str]:
    out = []
    by_phrase: dict[str, list[Hit]] = {}
    for h in res.hits:
        by_phrase.setdefault(h.phrase, []).append(h)
    for p, hs in by_phrase.items():
        conds = list(dict.fromkeys(h.sql() for h in hs))
        if len(conds) == 1:
            how = "层级路径列，按这一段过滤" if hs[0].seg else "精确值，用 =，不要 LIKE"
            out.append(f"- “{p}” → {conds[0]}（{how}）")
        else:
            out.append(f"- “{p}” → 可能是 {' 或 '.join(conds[:5])}（按题意选一个；选不出就反问）")
    for m in res.missing:
        out.append(
            f"- “{m.phrase}”：是本体「{m.sem.dim_name}」的{m.sem.level or ''}取值，"
            f"但库里 {m.col.key} 没有这个值 → 拒答 OUT_OF_SCOPE，不要换成别的值"
        )
    return out


def hierarchy_lines(idx: ValueIndex, levels_cfg: dict[str, int], self_refs: list[str]) -> list[str]:
    out = []
    for c in idx.cols:
        if not c.path:
            continue
        n = int(levels_cfg.get(c.key, 1))
        names = f"（层级 {' > '.join(c.levels)}）" if c.levels else ""
        out.append(
            f"- {c.key} 是层级路径列，值形如“上层{SEP}下层”{names}：题目没指明层级时按第 {n} 级"
            f" split_part({c.column}, '{SEP}', {n}) 汇总（SELECT 和 GROUP BY 都用它）；"
            f"点名某一级的取值时按“取值匹配”里那一段过滤"
        )
    out += [f"- {r}：表内自关联层级，题目没指明层级时只看顶层（上级为空的行）" for r in self_refs]
    return out
