"""本体词表 semantic_map（static-api.md §3.7「给本体小白的自动建本体集成读本体词表」）。

进程内只读：带 ver 读该版快照，不带读当前生效版（最新发布）；没有任何发布返回 []，版本不存在 404。
返回 [{kind, id, api, parent, name, synonyms[]}]，kind ∈ object|property|link|metric|dimension|dimValue|term。
synonyms：指标取 synonyms（快照没有该键时为 []）、维度取 mappings 说法、知识取 synonyms，其余 []。
[推断] 维度说法的 targets 命中某取值的 name/code 时，该说法同时并入该 dimValue 的 synonyms
（再加取值自身 synonyms），这样「华东大区」能落到 big_region 的「华东」；草稿对象/指标、
已停用知识不进词表。词表只来自快照，不写死任何业务词。
"""

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFound
from app.modules.versioning.repo import ReleaseRepo

Obj = dict[str, Any]
KINDS = ("object", "property", "link", "metric", "dimension", "dimValue", "term")


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


def entries_from_snapshot(snap: Obj) -> list[Obj]:
    """由快照 objectTypes/properties/links/metrics/dimensions/terms 节拼出词表（纯函数）。"""
    out: list[Obj] = []
    objects = {k: v for k, v in (snap.get("objectTypes") or {}).items() if v.get("status") != "草稿"}
    for code, o in objects.items():
        out.append(_entry("object", code, o.get("api"), None, o.get("name")))
    for code, props in (snap.get("properties") or {}).items():
        if code not in objects:
            continue
        for api, p in (props or {}).items():
            out.append(_entry("property", api, p.get("api", api), objects[code].get("api"), p.get("name")))
    for lid, x in (snap.get("links") or {}).items():
        out.append(_entry("link", lid, x.get("api"), None, x.get("name")))
    for mid, m in (snap.get("metrics") or {}).items():
        if m.get("status") == "草稿":
            continue
        out.append(_entry("metric", mid, m.get("api"), None, m.get("name"), m.get("synonyms")))
    for did, d in (snap.get("dimensions") or {}).items():
        maps = [m for m in d.get("mappings") or [] if m.get("phrase")]
        out.append(_entry("dimension", did, d.get("api"), None, d.get("name"), [m["phrase"] for m in maps]))
        for v in d.get("values") or []:
            hit = [m["phrase"] for m in maps if {v.get("name"), v.get("code")} & set(m.get("targets") or [])]
            words = [*(v.get("synonyms") or []), *hit]
            out.append(
                _entry(
                    "dimValue", v.get("key", v.get("code")), v.get("code"), d.get("api"), v.get("name"), words
                )
            )
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
