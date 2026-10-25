"""确定性生成（没有大模型也能用；大模型只在这个结果上改名字和说明）。

输出形状 = 前端 mock：对象类型 ≈ links.ts LinkObject + objectProps.ts ObjProp[]，关系 ≈ links.ts LinkType。
规则（参考 NVIDIA/auto-ontology semantic/deterministic.py、bridge_tables.py）：
- 每张表 → 一个对象类型；id = 表名，api = PascalCase，name = 表注释的第一句，没有注释 = 表名转词组；
- 列 → 属性：类型按列类型映射（introspect.column_kind）；主键 pk；name/title/*_name 列 title；
  外键列 desc 写「外键 → 目标（列）」（同 objectProps.ts 写法）；
- 外键 → N : 1 关系（外键列就是主键或有唯一约束 → 1 : 1）；
- 纯外键中间表（除主键 / 系统列外只有外键列，且指向 ≥ 2 张表）→ N : M 关系（via = 中间表），不生成对象。
"""

from __future__ import annotations

import re
from typing import Any

from app.modules.auto_ontology.introspect import Table

SYSTEM_COLUMNS = frozenset(
    {"id", "created_at", "updated_at", "created_by", "is_seed", "lock_version", "deleted_at", "deleted_by"}
)
TITLE_NAMES = ("name", "title", "display_name", "label")
DEFAULT_GROUP = "未分组"


def to_pascal(name: str) -> str:
    return "".join(p[:1].upper() + p[1:] for p in re.split(r"[^A-Za-z0-9]+", name) if p) or name


def to_term_name(name: str) -> str:
    """表名 / 列名的兜底显示名：order_line → Order Line（同 NVIDIA to_term_name）。"""
    return " ".join(p.capitalize() for p in re.split(r"[^A-Za-z0-9]+", name) if p) or name


def short_comment(comment: str | None, limit: int = 16) -> str | None:
    """注释的第一句（到 ，。；（( 为止），太长就不当名字用。"""
    if not comment:
        return None
    head = re.split(r"[，。；：,;:（(\n]", comment.strip(), maxsplit=1)[0].strip()
    return head if 0 < len(head) <= limit else None


def is_bridge(t: Table) -> bool:
    fk_cols = {c for f in t.fks for c in f.columns}
    targets = {f.ref_table for f in t.fks}
    if len(t.fks) < 2 or len(targets) < 2:
        return False
    rest = [c.name for c in t.columns if c.name not in fk_cols and c.name not in SYSTEM_COLUMNS]
    return not rest


def _prop_group(col: str, kind: str, is_pk: bool, fk_cols: set[str]) -> str:
    if is_pk or col in fk_cols or col.endswith(("_id", "_code", "_no")):
        return "标识"
    if kind in ("timestamp", "date"):
        return "时间"
    return "基本"


def _title_column(t: Table) -> str | None:
    names = [c.name for c in t.columns]
    for n in TITLE_NAMES:
        if n in names:
            return n
    return next((n for n in names if n.endswith("_name")), None)


def build_proposal(tables: list[Table], datasource_name: str) -> dict[str, Any]:
    bridges = [t for t in tables if is_bridge(t)]
    objects = [t for t in tables if t not in bridges]
    obj_names = {t.name for t in objects}
    display = {t.name: short_comment(t.comment) or to_term_name(t.name) for t in tables}

    object_types: list[dict[str, Any]] = []
    for t in objects:
        fk_target = {c: f for f in t.fks for c in f.columns if len(f.columns) == 1}
        title = _title_column(t)
        props = []
        for c in t.columns:
            is_pk = c.name in t.pk
            desc = c.comment or ""
            if c.name in fk_target:
                f = fk_target[c.name]
                arrow = f"外键 → {display.get(f.ref_table, f.ref_table)}（{f.ref_columns[0]}）"
                desc = f"{arrow}；{desc}" if desc else arrow
            p: dict[str, Any] = {
                "api": c.name,
                "name": short_comment(c.comment) or to_term_name(c.name),
                "type": c.kind,
                "group": _prop_group(c.name, c.kind, is_pk, set(fk_target)),
                "source": c.name,
                "desc": desc,
            }
            if is_pk:
                p["pk"] = True
            if c.name == title:
                p["title"] = True
            props.append(p)
        object_types.append(
            {
                "id": t.name,
                "name": display[t.name],
                "api": to_pascal(t.name),
                "group": DEFAULT_GROUP,
                "table": t.name,
                "datasource": datasource_name,
                "pk": t.pk[0] if len(t.pk) == 1 else None,
                "desc": t.comment or "",
                "props": props,
            }
        )

    links: list[dict[str, Any]] = []
    seen: set[str] = set()

    def _uid(base: str) -> str:
        uid, n = base, 2
        while uid in seen:
            uid, n = f"{base}_{n}", n + 1
        seen.add(uid)
        return uid

    for t in objects:
        unique = [set(t.pk)] + [set(u) for u in t.unique_sets]
        for f in t.fks:
            if f.ref_table not in obj_names or len(f.columns) != 1:
                continue  # 复合外键 / 指向中间表的外键：留给人工（warnings 里列出）
            col, ref_col = f.columns[0], f.ref_columns[0]
            col_meta = next(c for c in t.columns if c.name == col)
            stem = re.sub(r"_(id|code|no)$", "", col)
            links.append(
                {
                    "id": _uid(
                        f"{t.name}_{stem}" if stem != f.ref_table else f"{t.name}_belongs_to_{f.ref_table}"
                    ),
                    "name": short_comment(col_meta.comment) or display[f.ref_table],
                    "from": t.name,
                    "to": f.ref_table,
                    "card": "1 : 1" if {col} in unique else "N : 1",
                    "key": f"{col} = {f.ref_table}.{ref_col}",
                    "fromProp": col,
                    "toProp": ref_col,
                    "join": f"{t.name}.{col} = {f.ref_table}.{ref_col}",
                    "reverse": f"{display[t.name]}列表" if {col} not in unique else display[t.name],
                    "nullable": col_meta.nullable,
                    "desc": f"由外键 {t.name}.{col} → {f.ref_table}.{ref_col} 生成",
                    "status": "草稿",
                }
            )
    for b in bridges:
        single = [f for f in b.fks if len(f.columns) == 1 and f.ref_table in obj_names]
        if len({f.ref_table for f in single}) < 2:
            continue
        a, z = single[0], next(f for f in single[1:] if f.ref_table != single[0].ref_table)
        links.append(
            {
                "id": _uid(f"{a.ref_table}_{z.ref_table}_via_{b.name}"),
                "name": short_comment(b.comment) or display[z.ref_table],
                "from": a.ref_table,
                "to": z.ref_table,
                "card": "N : M",
                "key": (
                    f"{a.ref_columns[0]} = {b.name}.{a.columns[0]}；"
                    f"{b.name}.{z.columns[0]} = {z.ref_table}.{z.ref_columns[0]}"
                ),
                "fromProp": a.ref_columns[0],
                "toProp": z.ref_columns[0],
                "via": b.name,
                "join": (
                    f"{a.ref_table}.{a.ref_columns[0]} = {b.name}.{a.columns[0]} AND "
                    f"{b.name}.{z.columns[0]} = {z.ref_table}.{z.ref_columns[0]}"
                ),
                "reverse": display[a.ref_table],
                "nullable": True,
                "desc": f"由中间表 {b.name} 生成",
                "status": "草稿",
            }
        )
    return {"objectTypes": object_types, "linkTypes": links}


def structural_warnings(tables: list[Table]) -> list[str]:
    names = {t.name for t in tables}
    out: list[str] = []
    for t in tables:
        if not t.pk:
            out.append(f"表 {t.name} 没有主键，对象类型的主键属性需要人工指定")
        for f in t.fks:
            if len(f.columns) != 1:
                out.append(
                    f"表 {t.name} 的复合外键 {','.join(f.columns)} → {f.ref_table} 没有生成关系，需要人工补"
                )
            elif f.ref_table not in names:
                out.append(f"表 {t.name}.{f.columns[0]} 指向的 {f.ref_table} 不在本次范围内，没有生成关系")
    return out
