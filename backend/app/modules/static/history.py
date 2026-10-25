"""历史版本（?ver=）规则，static-api.md §2.7。输入输出都是 mock 形状的 dict，seed 快照和接口共用。

版本号按字符串字典序比较（格式固定 vYYYY.MM.N），与前端 `since <= ver` 一致。
ver=None 表示「当前已发布」：发布时 build_section 用它组装最新一节。
"""

from typing import Any

Obj = dict[str, Any]

LINK_RUNTIME = ("quality", "usage")
DIM_RUNTIME = ("usage",)
TERM_RUNTIME = ("refs",)


def strip(d: Obj, keys: tuple[str, ...]) -> Obj:
    return {k: v for k, v in d.items() if k not in keys}


def links_at(links: list[Obj], ver: str | None) -> list[Obj]:
    """LinkTypes.vue:37-41：已发布 且 since ≤ V。"""
    return [
        x for x in links if x["status"] == "已发布" and x.get("since") and (ver is None or x["since"] <= ver)
    ]


def term_at(e: Obj, ver: str | None) -> Obj:
    """Glossary.vue:37-46、:290：stoppedIn ≤ V → 已停用，否则已发布；历史只留 ver ≤ V 且非草稿的行。"""
    if ver is None:
        return dict(e)
    out = dict(e)
    out["status"] = "已停用" if e.get("stoppedIn") and e["stoppedIn"] <= ver else "已发布"
    out["history"] = [
        h for h in e["history"] if h["ver"] != "—" and h["ver"] <= ver and h["status"] != "草稿"
    ]
    return out


def terms_at(entries: list[Obj], ver: str | None) -> list[Obj]:
    return [term_at(e, ver) for e in entries if e.get("since") and (ver is None or e["since"] <= ver)]


def live(x: Obj, ver: str | None) -> bool:
    """Dimensions.vue:41 live()：无版本时 !until；有版本时 since ≤ V < until。"""
    if not ver:
        return not x.get("until")
    return (not x.get("since") or x["since"] <= ver) and (not x.get("until") or ver < x["until"])


def dimension_at(d: Obj, ver: str | None) -> Obj:
    """Dimensions.vue:97-107（levelsBefore）、:125-128（已发布版取值）、:219（映射）、:354（历史）。

    已发布视图：草稿单里新增的取值 / 映射不出现；看历史版本时再去掉带 diff 的行。
    """
    out = dict(d)
    b = d.get("levelsBefore")
    if b and ver and ver < b["ver"]:
        out["levels"] = b["levels"]
        out["qa"] = {**d["qa"], **(b.get("qa") or {})}
        for k in ("desc", "attr", "column"):
            if b.get(k) is not None:
                out[k] = b[k]
    out["values"] = [
        {**v, **(v.get("pub") or {})}
        for v in d["values"]
        if live(v, ver) and not v.get("draft") and (not ver or not v.get("diff"))
    ]
    out["mappings"] = [m for m in d["mappings"] if not m.get("draft") and (not ver or not m.get("diff"))]
    if ver:
        out["history"] = [h for h in d["history"] if h["ver"] != "—" and h["ver"] <= ver]
    return out


def dimensions_at(items: list[Obj], ver: str | None) -> list[Obj]:
    """Dimensions.vue:42：since ≤ V。"""
    return [dimension_at(d, ver) for d in items if ver is None or d["since"] <= ver]
