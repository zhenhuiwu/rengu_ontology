"""模型 SQL 的确定性修正（sqlglot AST，MIT）：只改与题意 / 本体元数据确定冲突的地方，每处修正都留一句说明。

思路参考 NVIDIA auto-ontology 的 literal_check / proactive_value_check（Apache-2.0）和
SQLBot / WrenAI 的「生成后校验」（GPL/AGPL，只参考思路）。全部按列元数据判断，不认业务词：
- like_to_eq：取值匹配命中了精确值，模型却写 LIKE '%值%' → 改成 =；
- path_top_level：按层级路径列整串分组 → 按配置层级（默认第 1 级）split_part；
- exclude_placeholders：按列分组时排除占位值（'--'、空串）；
- strip_time_window：题目没有任何时间说法，模型自己加了日期窗 → 去掉（统计全部数据）；
- strip_unasked_enum：题目没提、口径也没要求的枚举条件 → 去掉；
- ensure_code：按对象名称分组的最外层查询补上业务编号列；
- scope_filter：会话范围解析成“列 = 值”后注入到用到这张表的每个 SELECT；
- fanout_violations：上层表的聚合 join 了明细表（按明细行重复计算）→ 回喂模型改写。
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from sqlglot import exp

from app.modules.qa.value_index import SEP, Col, Hit, ValueIndex, is_placeholder

_DATE_LIT = re.compile(r"^\s*\d{4}-\d{2}-\d{2}")


def _aliases(sel: exp.Select) -> dict[str, str]:
    """本层 SELECT 的 FROM / JOIN 别名 → 物理表名（不进子查询）。"""
    out = {}
    srcs = []
    frm = sel.args.get("from_") or sel.args.get("from")  # sqlglot ≥ 26 用 from_
    if frm:
        srcs.append(frm.this)
    srcs += [j.this for j in sel.args.get("joins") or []]
    for s in srcs:
        if isinstance(s, exp.Table):
            out[s.alias_or_name] = s.name
    return out


def _table_of(col: exp.Column, sel: exp.Select | None, all_alias: dict[str, str]) -> str | None:
    if col.table:
        if sel is not None and col.table in (a := _aliases(sel)):
            return a[col.table]
        return all_alias.get(col.table)
    if sel is not None:
        tabs = set(_aliases(sel).values())
        if len(tabs) == 1:
            return next(iter(tabs))
    return None


def _all_aliases(tree: exp.Expression) -> dict[str, str]:
    return {t.alias_or_name: t.name for t in tree.find_all(exp.Table)}


def _lit(e: exp.Expression | None) -> exp.Literal | None:
    node = e.this if isinstance(e, exp.Cast) else e
    return node if isinstance(node, exp.Literal) else None


def _conjuncts(where: exp.Expression) -> list[exp.Expression]:
    e = where.this if isinstance(where, exp.Where) else where
    while isinstance(e, exp.Paren):
        e = e.this
    if isinstance(e, exp.And):
        return _conjuncts(e.left) + _conjuncts(e.right)
    return [e]


def _drop(sel: exp.Select, drop: list[exp.Expression]) -> None:
    where = sel.args.get("where")
    if not where or not drop:
        return
    keep = [c for c in _conjuncts(where) if not any(c is d for d in drop)]
    if keep:
        sel.set("where", exp.Where(this=exp.and_(*[k.copy() for k in keep])))
    else:
        sel.set("where", None)


def _selects(tree: exp.Expression) -> list[exp.Select]:
    return list(tree.find_all(exp.Select))


def _outer(tree: exp.Expression) -> exp.Select | None:
    return tree if isinstance(tree, exp.Select) else None


# ---------------------------------------------------------------- 取值


def like_to_eq(tree: exp.Expression, hits: Iterable[Hit]) -> list[str]:
    """LIKE '%v%' / 'v%' 且 v 正好是该列命中的精确值 → '= v'。"""
    exact = {(h.col.table, h.col.column, h.value) for h in hits if not h.seg}
    if not exact:
        return []
    alias = _all_aliases(tree)
    notes = []
    for e in list(tree.find_all(exp.Like)):
        if not isinstance(e.this, exp.Column) or isinstance(e.parent, exp.Not):
            continue
        lit = _lit(e.expression)
        if not lit or not lit.is_string:
            continue
        core = lit.this.strip("%").rstrip(SEP)
        tab = _table_of(e.this, e.find_ancestor(exp.Select), alias)
        if (tab, e.this.name, core) in exact and core != lit.this:
            e.replace(exp.EQ(this=e.this.copy(), expression=exp.Literal.string(core)))
            notes.append(f"{tab}.{e.this.name} 按精确值“{core}”过滤")
    return notes


def path_top_level(
    tree: exp.Expression, idx: ValueIndex, hits: Iterable[Hit], levels_cfg: dict[str, int]
) -> list[str]:
    """按路径列整串分组 → split_part(列, '/', n)；题目点名了更深一级的取值时不动。"""
    deep = {h.col.key for h in hits if h.seg > 1}
    alias = _all_aliases(tree)
    notes = []
    for sel in _selects(tree):
        group = sel.args.get("group")
        if not group:
            continue
        for g in list(group.expressions):
            if not isinstance(g, exp.Column):
                continue
            tab = _table_of(g, sel, alias)
            c = idx.col(tab or "", g.name)
            if not c or not c.path or c.key in deep:
                continue
            n = int(levels_cfg.get(c.key, 1))

            def top(col: exp.Column, n: int = n) -> exp.Expression:
                return exp.func("split_part", col.copy(), exp.Literal.string(SEP), exp.Literal.number(n))

            for p in sel.expressions:
                inner = p.this if isinstance(p, exp.Alias) else p
                if isinstance(inner, exp.Column) and inner.name == g.name and inner.table == g.table:
                    if inner is p:  # 裸列：保留原列名，外层还按它引用
                        p.replace(exp.alias_(top(inner), g.name))
                    else:
                        inner.replace(top(inner))
            g.replace(top(g))
            notes.append(f"{c.label or c.column}按第 {n} 级汇总（题目没指明层级）")
    return notes


def exclude_placeholders(tree: exp.Expression, idx: ValueIndex) -> list[str]:
    alias = _all_aliases(tree)
    notes = []
    for sel in _selects(tree):
        group = sel.args.get("group")
        for g in list(group.expressions) if group else []:
            if not isinstance(g, exp.Column):
                continue
            c = idx.col(_table_of(g, sel, alias) or "", g.name)
            ph = c.placeholders if c else []
            if not ph:
                continue
            where = sel.args.get("where")
            if where and any(
                isinstance(x, exp.Column) and x.name == g.name for x in where.find_all(exp.Column)
            ):
                continue
            cond = exp.Not(this=exp.In(this=g.copy(), expressions=[exp.Literal.string(v) for v in ph]))
            sel.where(cond, copy=False)
            notes.append(f"{c.label or c.column}为空的（{'、'.join(repr(v) for v in ph)}）不计入")
    return notes


# ---------------------------------------------------------------- 时间 / 枚举


def strip_time_window(tree: exp.Expression, time_cols: frozenset[str]) -> bool:
    """去掉“时间列 比较 日期字面量”的顶层 AND 条件（含 BETWEEN）。返回是否改了。"""
    changed = False
    for sel in _selects(tree):
        where = sel.args.get("where")
        if not where:
            continue
        drop = []
        for c in _conjuncts(where):
            if isinstance(c, exp.Between):
                col, lits = c.this, [c.args.get("low"), c.args.get("high")]
            elif isinstance(c, exp.GT | exp.GTE | exp.LT | exp.LTE | exp.EQ):
                col, lits = c.this, [c.expression]
            else:
                continue
            if not (isinstance(col, exp.Column) and col.name in time_cols):
                continue
            if all((lt := _lit(x)) is not None and _DATE_LIT.match(str(lt.this)) for x in lits):
                drop.append(c)
        if drop:
            _drop(sel, drop)
            changed = True
    return changed


_DATE_UNITS = {"DAY", "WEEK", "MONTH", "QUARTER", "YEAR"}


def date_buckets(tree: exp.Expression) -> list[str]:
    """最外层 SELECT 里按日 / 周 / 月… 截断的时间桶显示成日期（带时区时间戳会显示成 UTC 的 16:00）。"""
    sel = _outer(tree)
    if sel is None:
        return []
    changed = False
    for x in sel.expressions:
        node = x.this if isinstance(x, exp.Alias) else x
        if not isinstance(node, exp.TimestampTrunc | exp.DateTrunc):
            continue
        unit = node.args.get("unit")
        if unit is None or unit.name.upper() not in _DATE_UNITS:
            continue
        cast = exp.Cast(this=node.copy(), to=exp.DataType.build("date"))
        if isinstance(x, exp.Alias):
            x.set("this", cast)
        else:
            node.replace(cast)
        changed = True
    return ["时间桶按日期显示"] if changed else []


def retarget_time(
    tree: exp.Expression, want: str, time_cols: frozenset[str], table_cols: dict[str, set[str]]
) -> list[str]:
    """题目点名了某个时间列（“下单的”）、时间窗却落在同表另一时间列上 → 时间窗改到点名的列。"""
    notes = []
    for sel in _selects(tree):
        where = sel.args.get("where")
        if not where:
            continue
        alias = _aliases(sel)
        for c in _conjuncts(where):
            if isinstance(c, exp.Between):
                col, lits = c.this, [c.args.get("low"), c.args.get("high")]
            elif isinstance(c, exp.GT | exp.GTE | exp.LT | exp.LTE | exp.EQ):
                col, lits = c.this, [c.expression]
            else:
                continue
            if not (isinstance(col, exp.Column) and col.name in time_cols and col.name != want):
                continue
            if not all((lt := _lit(x)) is not None and _DATE_LIT.match(str(lt.this)) for x in lits):
                continue
            tab = _table_of(col, sel, alias)
            if not tab or want not in table_cols.get(tab, ()):
                continue
            note = f"时间窗按题目改用 {tab}.{want}"
            col.set("this", exp.to_identifier(want))
            if note not in notes:
                notes.append(note)
    return notes


def strip_unasked_enum(
    tree: exp.Expression, enum_cols: dict[str, dict[str, str]], mentioned: str, required: set[str]
) -> list[str]:
    """枚举列（列名 → {code: 标签}）上的条件：题目没提 code / 标签、口径也没要求这些 code → 去掉。"""
    mentioned = mentioned.lower()
    notes = []
    for sel in _selects(tree):
        where = sel.args.get("where")
        if not where:
            continue
        drop = []
        for c in _conjuncts(where):
            node = c.this if isinstance(c, exp.Not) else c
            if not isinstance(node, exp.EQ | exp.NEQ | exp.In) or not isinstance(node.this, exp.Column):
                continue
            labels = enum_cols.get(node.this.name)
            if not labels:
                continue
            lits = [node.expression] if not isinstance(node, exp.In) else list(node.expressions or [])
            codes = [x.this for x in lits if isinstance(x, exp.Literal) and x.is_string]
            if not codes or len(codes) != len(lits):
                continue
            if any(code in required for code in codes):
                continue
            if any(code.lower() in mentioned or labels.get(code, "").lower() in mentioned for code in codes):
                continue
            if any(lab and lab.lower() in mentioned for lab in labels.values()):
                continue  # 题目提了这列的某个取值，条件是模型按题意写的
            drop.append(c)
            notes.append(f"去掉题目没提的条件 {c.sql(dialect='postgres')}")
        _drop(sel, drop)
    return notes


# ---------------------------------------------------------------- 编号 / 范围 / 扇出


def ensure_code(
    tree: exp.Expression, entity_cols: dict[str, set[str]], code_cols: dict[str, str]
) -> list[str]:
    """最外层按对象名称（或几乎每行不同的实体列）分组、没带业务编号 → SELECT / GROUP BY 补上编号列。

    不分组、不聚合、不去重的明细列表（SELECT 对象名称 … WHERE …）同样补上编号列。
    """
    sel = _outer(tree)
    if sel is None:
        return []
    if not sel.args.get("group"):
        return _ensure_code_list(sel, entity_cols, code_cols)
    alias = _aliases(sel)
    notes = []
    group = sel.args["group"].expressions
    for g in list(group):
        if not isinstance(g, exp.Column):
            continue
        tab = _table_of(g, sel, alias)
        if not tab or g.name not in entity_cols.get(tab, ()) or tab not in code_cols:
            continue
        code = code_cols[tab]
        if any(isinstance(x, exp.Column) and x.name == code and x.table == g.table for x in group):
            continue
        ref = exp.column(code, table=g.table or None)
        sel.set("expressions", [ref.copy(), *sel.expressions])
        sel.args["group"].append("expressions", ref.copy())
        notes.append(f"带上 {tab}.{code}")
    return notes


def _ensure_code_list(
    sel: exp.Select, entity_cols: dict[str, set[str]], code_cols: dict[str, str]
) -> list[str]:
    if sel.args.get("distinct") or any(x.find(exp.AggFunc) for x in sel.expressions):
        return []
    alias = _aliases(sel)
    cols = [x.unalias() for x in sel.expressions]
    notes = []
    for c in cols:
        if not isinstance(c, exp.Column):
            continue
        tab = _table_of(c, sel, alias)
        if not tab or c.name not in entity_cols.get(tab, ()) or tab not in code_cols:
            continue
        code = code_cols[tab]
        if any(isinstance(x, exp.Column) and x.name == code and x.table == c.table for x in cols):
            continue
        sel.set("expressions", [exp.column(code, table=c.table or None), *sel.expressions])
        notes.append(f"带上 {tab}.{code}")
        break
    return notes


def scope_filter(tree: exp.Expression, scope: Iterable[Hit]) -> list[str]:
    """会话范围“列 = 值”注入到每个直接用到这张表的 SELECT（已有同列条件的不重复加）。"""
    notes = []
    for h in scope:
        for sel in _selects(tree):
            for a, t in _aliases(sel).items():
                if t != h.col.table:
                    continue
                where = sel.args.get("where")
                if where and any(
                    x.name == h.col.column and (x.table in ("", a)) for x in where.find_all(exp.Column)
                ):
                    continue
                cond = exp.condition(h.sql(alias=a), dialect="postgres")
                sel.where(cond, copy=False)
                notes.append(h.sql())
    return list(dict.fromkeys(notes))


_AGG = (exp.Sum, exp.Avg, exp.Count)


def _divisor(node: exp.Expression) -> bool:
    """node 在某个除法的分母里。"""
    child, anc = node, node.parent
    while anc is not None and not isinstance(anc, exp.Select):
        if isinstance(anc, exp.Div) and anc.expression is child:
            return True
        child, anc = anc, anc.parent
    return False


def fanout_violations(tree: exp.Expression, children: dict[str, set[str]]) -> list[str]:
    """children：上层表 → 有外键指向它的明细表。同一 SELECT 里 join 了上层和明细，
    却对上层的列做 SUM / AVG（或 COUNT(*) 和明细列的聚合混用）→ 会按明细行重复计算。"""
    out = []
    for sel in _selects(tree):
        alias = _aliases(sel)
        tabs = set(alias.values())
        pairs = [(p, c) for p in tabs for c in children.get(p, ()) if c in tabs and c != p]
        if not pairs:
            continue
        aggs = [a for p in sel.expressions for a in p.find_all(*_AGG)]
        child_tabs = {c for _, c in pairs}
        used = {alias.get(x.table) for a in aggs for x in a.find_all(exp.Column)} & child_tabs
        frm = sel.args.get("from_") or sel.args.get("from")
        root = frm.this.name if frm and isinstance(frm.this, exp.Table) else None
        count_parents = {p for p, c in pairs if c in used}
        for a in aggs:
            if isinstance(a, exp.Count) and a.args.get("this") and isinstance(a.this, exp.Distinct):
                continue
            cols = list(a.find_all(exp.Column))
            owners = {alias.get(x.table) for x in cols}
            bad = None
            if isinstance(a, exp.Sum | exp.Avg) and cols and owners and owners <= {p for p, _ in pairs}:
                bad = next(iter(owners))
            elif isinstance(a, exp.Count) and isinstance(a.this, exp.Star) and root in count_parents:
                bad = root  # FROM 的是上层表，COUNT(*) 本意数上层行，却和明细列聚合混用
            elif isinstance(a, exp.Count) and isinstance(a.this, exp.Star) and _divisor(a) and count_parents:
                bad = sorted(count_parents)[0]  # “明细合计 / COUNT(*)”：本意是每个上层行的平均
            if bad:
                kids = "、".join(sorted(c for p, c in pairs if p == bad))
                out.append(
                    f"{a.sql(dialect='postgres')} 按 {bad} 的行计算，但同一查询 join 了明细表 {kids}，"
                    f"会按明细行重复计算；请把 {bad} 层的聚合放到不 join 明细表的子查询里，再与明细层结果组合"
                )
    return list(dict.fromkeys(out))


def col_meta_enum(cols: list[Col]) -> dict[str, dict[str, str]]:
    """Col 列表里的枚举列：列名 → {code: 标签}（同名列合并）。"""
    out: dict[str, dict[str, str]] = {}
    for c in cols:
        if c.labels:
            out.setdefault(c.column, {}).update({code: lab for lab, code in c.labels.items()})
    return out


__all__ = [
    "col_meta_enum",
    "date_buckets",
    "ensure_code",
    "exclude_placeholders",
    "fanout_violations",
    "is_placeholder",
    "like_to_eq",
    "path_top_level",
    "retarget_time",
    "scope_filter",
    "strip_time_window",
    "strip_unasked_enum",
]
