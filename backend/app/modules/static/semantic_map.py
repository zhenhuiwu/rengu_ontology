"""本体词表 semantic_map（static-api.md §3.7「给本体小白的自动建本体集成读本体词表」）。

进程内只读：带 ver 读该版快照，不带读当前生效版（最新发布）；没有任何发布返回 []，版本不存在 404。
返回 [{kind, id, api, parent, name, synonyms[]}]，kind ∈ object|property|link|metric|dimension|dimValue|term。
synonyms：指标取 synonyms（快照没有该键时为 []）、维度取 mappings 说法、知识取 synonyms，其余 []。
[推断] 维度说法的 targets 命中某取值的 name/code 时，该说法同时并入该 dimValue 的 synonyms
（再加取值自身 synonyms），这样「华东大区」能落到 big_region 的「华东」；草稿对象/指标、
已停用知识不进词表。词表只来自快照，不写死任何业务词。

static-t7 在上述 6 个键之外**追加**（老键不变）：
- object：table（物理表 = 快照 objectTypes.table）。
- property：type / column（物理列 = 属性来源字段 source）/ table（所属对象的物理表）。
- metric：status / unit / desc / formula / execDef{timeField?, defaultFilter?}（快照原样）；
  synonyms 来自定义行「同义词」（metric/def 项 synonyms 可写，随快照保存与回滚）。
- dimension：status / levels[{no, api, name, source, object, property, table, column}]（有序，
  no 从 1 起）/ defaultLevel{api, name}|None / qa（问数设置原样：defaultLevel、unspecified、
  nullAs、sort、note）/ binding{object, attr, property, table, column}。
- dimValue：level（层级名，原样）/ levelApi / levelNo / parentName（上级原文）/
  parentValue（上级取值的 id = key；同维度按 code/name/key 匹配，取层级更高者，找不到为 None）/
  status / count。
层级来源 source 的解析（通用规则，不认业务词）：纯标识符 = 维度表上的列；「对象.属性[ → …]」=
该属性的来源列 + 该对象表；「对象」= 该对象主键属性的来源列；其余（如「码表上级」）为 None。
time 维度的月取值 parent 是季度编码，但种子没有季度取值，所以 parentValue 为 None、parentName 原样。
"""

import re
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFound
from app.modules.versioning.repo import ReleaseRepo

Obj = dict[str, Any]
KINDS = ("object", "property", "link", "metric", "dimension", "dimValue", "term")
IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _uniq(words: list[Any]) -> list[str]:
    out: list[str] = []
    for w in words:
        if isinstance(w, str) and w.strip() and w not in out:
            out.append(w)
    return out


def _entry(kind: str, id_: Any, api: Any, parent: Any, name: Any, synonyms: list[Any] | None = None) -> Obj:
    return {
        "kind": kind,
        "id": id_,
        "api": api,
        "parent": parent,
        "name": name,
        "synonyms": _uniq(list(synonyms or [])),
    }


def _split_column(col: Any) -> tuple[str | None, str | None]:
    """「SCHEMA.TABLE.col」→ (SCHEMA.TABLE, col)；没有点号时只有列；空值 (None, None)。"""
    if not isinstance(col, str) or not col.strip():
        return None, None
    head, _, tail = col.strip().rpartition(".")
    return (head or None), tail


def _level_binding(source: Any, d: Obj, by_name: dict[str, str], props: Obj, tables: dict[str, str]) -> Obj:
    out: Obj = {"object": None, "property": None, "table": None, "column": None}
    head = source.split("→")[0].strip() if isinstance(source, str) else ""
    if IDENT.match(head):
        out.update(table=_split_column(d.get("column"))[0], column=head)
        return out
    obj_name, _, prop_name = head.partition(".")
    code = by_name.get(obj_name.strip())
    if code is None:
        return out
    items = list((props.get(code) or {}).values())
    p = next(
        (x for x in items if (x.get("name") == prop_name.strip() if prop_name else x.get("pk"))),
        None,
    )
    out.update(object=code, table=tables.get(code))
    if p is not None:
        out.update(property=p.get("api"), column=p.get("source"))
    return out


def _rank(c: Obj, no_of: dict[str, Obj], own: int | None) -> tuple[int, int]:
    n = (no_of.get(c.get("level")) or {}).get("no") or 0
    return (1 if own is None or n < own else 0, n)


def _dimension(did: str, d: Obj, maps: list[Obj], ctx: tuple) -> list[Obj]:
    by_name, props, tables = ctx
    levels = [lv for lv in d.get("levels") or [] if isinstance(lv, dict)]
    lv_out = [
        {
            "no": i,
            "api": lv.get("api"),
            "name": lv.get("name"),
            "source": lv.get("source"),
            **_level_binding(lv.get("source"), d, by_name, props, tables),
        }
        for i, lv in enumerate(levels, 1)
    ]
    qa = dict(d.get("qa") or {})
    default = next((x for x in lv_out if qa.get("defaultLevel") in (x["name"], x["api"])), None)
    table, col = _split_column(d.get("column"))
    obj = d.get("object") or None
    prop = next((p for p in (props.get(obj) or {}).values() if col and p.get("source") == col), None)
    dim = _entry("dimension", did, d.get("api"), None, d.get("name"), [m["phrase"] for m in maps])
    dim.update(
        status=d.get("status"),
        levels=lv_out,
        defaultLevel={"api": default["api"], "name": default["name"]} if default else None,
        qa=qa,
        binding={
            "object": obj,
            "attr": d.get("attr"),
            "property": prop.get("api") if prop else None,
            "table": table,
            "column": col,
        },
    )
    out = [dim]
    no_of = {x["name"]: x for x in lv_out}
    values = [v for v in d.get("values") or [] if isinstance(v, dict)]
    for v in values:
        hit = [m["phrase"] for m in maps if {v.get("name"), v.get("code")} & set(m.get("targets") or [])]
        words = [*(v.get("synonyms") or []), *hit]
        e = _entry("dimValue", v.get("key", v.get("code")), v.get("code"), d.get("api"), v.get("name"), words)
        lv = no_of.get(v.get("level"))
        own_no = lv["no"] if lv else None
        parent = v.get("parent")
        cands = [
            c
            for c in values
            if c is not v and parent and parent in (c.get("code"), c.get("name"), c.get("key"))
        ]

        # 优先层级更高（no 更小）里最近的一层；都不在更高层时退回任一匹配。
        up = max(cands, key=lambda c, own=own_no: _rank(c, no_of, own)) if cands else None
        e.update(
            level=v.get("level"),
            levelApi=lv["api"] if lv else None,
            levelNo=own_no,
            parentName=parent,
            parentValue=up.get("key", up.get("code")) if up else None,
            status=v.get("status"),
            count=v.get("count"),
        )
        out.append(e)
    return out


def entries_from_snapshot(snap: Obj) -> list[Obj]:
    """由快照 objectTypes/properties/links/metrics/dimensions/terms 节拼出词表（纯函数）。"""
    out: list[Obj] = []
    objects = {k: v for k, v in (snap.get("objectTypes") or {}).items() if v.get("status") != "草稿"}
    for code, o in objects.items():
        out.append({**_entry("object", code, o.get("api"), None, o.get("name")), "table": o.get("table")})
    all_props = snap.get("properties") or {}
    tables = {code: o.get("table") for code, o in objects.items()}
    for code, props in all_props.items():
        if code not in objects:
            continue
        for api, p in (props or {}).items():
            e = _entry("property", api, p.get("api", api), objects[code].get("api"), p.get("name"))
            e.update(type=p.get("type"), column=p.get("source"), table=tables.get(code))
            out.append(e)
    for lid, x in (snap.get("links") or {}).items():
        out.append(_entry("link", lid, x.get("api"), None, x.get("name")))
    for mid, m in (snap.get("metrics") or {}).items():
        if m.get("status") == "草稿":
            continue
        e = _entry("metric", mid, m.get("api"), None, m.get("name"), m.get("synonyms"))
        e.update(
            status=m.get("status"),
            unit=m.get("unit"),
            desc=m.get("desc") or "",
            formula=m.get("formula"),
            execDef=dict(m.get("execDef") or {}),
        )
        out.append(e)
    by_name = {o.get("name"): code for code, o in objects.items() if o.get("name")}
    ctx = (by_name, all_props, tables)
    for did, d in (snap.get("dimensions") or {}).items():
        maps = [m for m in d.get("mappings") or [] if m.get("phrase")]
        out.extend(_dimension(did, d, maps, ctx))
    for tid, t in (snap.get("terms") or {}).items():
        if t.get("status") == "已停用":
            continue
        out.append(_entry("term", tid, t.get("code", tid), None, t.get("name"), t.get("synonyms")))
    return out


async def semantic_map(session: AsyncSession, ver: str | None = None) -> list[Obj]:
    repo = ReleaseRepo(session)
    release = await (repo.by_ver(ver) if ver else repo.latest())
    if release is None:
        if ver:
            raise NotFound("版本不存在", details={"ver": ver})
        return []
    return entries_from_snapshot(dict(release.snapshot or {}))
