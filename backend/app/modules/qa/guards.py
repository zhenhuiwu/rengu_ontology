"""确定性反问 / 拒答规则（不调模型）：全部从本体元数据 + 取值索引推出，不认具体业务词。

- time_expressed：题目里有没有时间说法（通用中英文时间表达，不是业务词表）；
- multi_time_clarify：一句里出现两个时间列的说法（来自列注释 / context.json timeHints）
  → 反问按哪个时间口径；
- unknown_value_clarify：“X + 列名 / 维度名”，X 不是库里任何取值（也不是取值的一部分）
  → 反问 X 指这列的哪个值；
- ambiguous_dim_clarify：“按 / 各 / 每个 / 分 + X”，X 对不上任何维度 / 层级 / 列名，
  但它的字同时出现在 ≥2 个维度或层级名里 → 反问指哪一个；
- bare_metric_clarify：只有指标、没有时间 / 维度 / 取值 / 疑问词 → 反问（答案取决于范围）；
- missing_value_refuse：点名的取值是本体维度在该列绑定层级上的正式取值，但库里没有 → OUT_OF_SCOPE；
- scope_conflict_refuse：会话范围解析成某列的值，题目点名了同一列的其他值 → OUT_OF_SCOPE。
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

from app.modules.qa.value_index import Col, Hit, Resolution, ValueIndex, norm

# 通用时间表达（语言层面，不是业务词）
_TIME = re.compile(
    r"今天|昨天|前天|明天|今日|昨日|本周|上周|这周|下周|本月|上月|上个月|这个月|下个月|今年|去年|前年|明年|"
    r"本季|上季|季度|近\s*\d+|近[一二三四五六七八九十两半]+|最近|过去|截至|截止|以来|至今|迄今|"
    r"\d+\s*[年月日号]|[一二三四五六七八九十]+月|周[一二三四五六日天末]|星期|"
    r"每天|每日|每周|每月|每年|按天|按日|按月|按周|按年|逐日|逐月|当天|当日|当月|当周|"
    r"小时|上午|下午|凌晨|晚上|早上|节假日|春节|国庆|元旦|"
    r"\bq[1-4]\b|\btoday\b|\byesterday\b|\bweek|\bmonth|\byear|\bquarter|\bdaily\b|\bhour|"
    r"\blast\s+\d+|\bsince\b|\b(?:19|20)\d{2}\b|\bytd\b|\bmtd\b|"
    r"\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\b",
    re.I,
)
# 疑问 / 聚合说法：有这些说明要的是一个确定的数
_ASK = re.compile(
    r"多少|几|哪|什么|吗|是否|总|合计|平均|最高|最低|最多|最少|排名|前\s*\d|列出|列一下|有哪些|占比|比例|率|"
    r"\?|？|\bhow\b|\bwhat\b|\bwhich\b|\bwho\b|\btotal\b|\baverage\b|\bavg\b|\bcount\b|\bmost\b|\btop\b|"
    r"\blist\b|\bnumber\b|\bmax|\bmin",
    re.I,
)
_GROUP = re.compile(r"按|各|每个|每一|每家|每种|分别|分[^钟别享]|\bby\b|\bper\b|\beach\b", re.I)
_GROUP_LEAD = re.compile(r"(?:按照|按|各个|各|每个|每一个|每|分)")
_STOP = set("的了和与及在是看按各每分个家种个查问下给我们咱你把对比")


def time_expressed(question: str) -> bool:
    return bool(_TIME.search(question))


def _clarify(term: str, question: str, options: Iterable[str]) -> dict[str, Any] | None:
    opts = list(dict.fromkeys(o for o in options if o))[:6]
    if len(opts) < 2:
        return None
    return {"term": term, "question": question, "options": opts}


def multi_time_clarify(
    question: str, time_hints: dict[str, tuple[str, ...]], labels: dict[str, str]
) -> dict | None:
    """time_hints：时间列 → 说法；labels：时间列 → 展示名。两个以上时间列的说法同时出现 → 反问。"""
    q = norm(question)
    hit = [c for c, ws in time_hints.items() if any(w and norm(w) in q for w in ws)]
    if len(hit) < 2:
        return None
    names = [labels.get(c) or c for c in hit]
    return _clarify("时间口径", f"题目里同时出现了{'、'.join(names)}，请问按哪个时间统计？", names)


def _covered(res: Resolution, i: int, j: int) -> bool:
    return any(a < j and i < b for a, b in res.spans)


def _partial(idx: ValueIndex, x: str) -> bool:
    """x 是某个已索引取值 / 维度取值说法的一部分（简称）。"""
    if any(x in norm(v) for c in idx.cols for v in c.values):
        return True
    if any(x in norm(lab) for c in idx.cols for lab in c.labels):
        return True
    return any(x in w for s in idx.sem for w in s.words)


def _is_concept(idx: ValueIndex, x: str) -> bool:
    return any(x in c or c in x for c in idx.concepts if len(c) >= 2)


def unknown_value_clarify(
    question: str,
    idx: ValueIndex,
    res: Resolution,
    col_labels: dict[str, list[Col]],
    time_words: Iterable[str] = (),
) -> dict | None:
    """“X + 列名”（如“某某渠道”），X 是 2 个字、不是任何取值 / 取值简称 / 概念词 → 反问 X 指哪个值。"""
    q = norm(question)
    tw = {norm(w) for w in time_words if w}
    for lab in sorted(col_labels, key=len, reverse=True):
        cols = [c for c in col_labels[lab] if c.values and not c.is_name]
        if not cols:
            continue
        for m in re.finditer(re.escape(lab), q):
            i = m.start()
            if i < 2:
                continue
            x = q[i - 2 : i]
            if not re.fullmatch(r"[\u4e00-\u9fff]{2}", x) or set(x) & _STOP:
                continue
            if _covered(res, i - 2, i) or _partial(idx, x) or _is_concept(idx, x) or _TIME.search(x):
                continue
            if any(x in w or w in x for w in tw):
                continue
            opts = [v for c in cols for v in c.values if v and not v.isspace()]
            return _clarify(x + lab, f"“{x}{lab}”在库里没有对应的取值，请问指的是哪一个？", opts)
    return None


def ambiguous_dim_clarify(
    question: str, idx: ValueIndex, res: Resolution, dim_words: list[str]
) -> dict | None:
    """“按 / 各 / 每个 + X”：X 的前缀（2–4 字）对不上任何概念 / 维度 / 列名 / 取值，
    但 X 的第 2 个字同时出现在 ≥2 个维度或层级名里（“地区”→ 大区 / 片区 / 区域）→ 反问。"""
    q = norm(question)
    words = [norm(w) for w in dim_words if w]
    for m in _GROUP_LEAD.finditer(q):
        i = m.end()
        x = q[i : i + 2]
        if (
            not re.fullmatch(r"[\u4e00-\u9fff]{2}", x)
            or _covered(res, i, i + 2)
            or _TIME.search(q[i : i + 3])
        ):
            continue
        prefixes = [q[i : i + n] for n in (2, 3, 4) if i + n <= len(q)]
        if any(_is_concept(idx, p) or _partial(idx, p) for p in prefixes):
            continue
        if any(p in w for p in prefixes for w in words):
            continue
        cands = [w for w in words if x[1] in w]
        cands = list(dict.fromkeys(cands))
        if len(cands) >= 2:
            return _clarify(x, f"“{x}”可以指好几个层级，请问按哪一个？", cands)
    return None


def bare_metric_clarify(
    question: str,
    res: Resolution,
    metric_words: Iterable[str],
    options: list[str],
    *,
    has_default: bool = False,
) -> dict | None:
    """说了指标，但没有时间、没有分组、没有点名取值、也没有疑问 / 聚合说法 → 反问看什么范围。"""
    if has_default:
        return None
    q = question.lower()
    if not any(w and w.lower() in q for w in metric_words):
        return None
    if time_expressed(question) or res.hits or res.missing:
        return None
    if _GROUP.search(question) or _ASK.search(question):
        return None
    return _clarify(question.strip()[:20], "想看哪个时间范围、按什么维度？", options)


def missing_value_refuse(res: Resolution) -> dict | None:
    if not res.missing:
        return None
    m = res.missing[0]
    return {
        "code": "OUT_OF_SCOPE",
        "reason": f"“{m.phrase}”是{m.sem.dim_name}的{m.sem.level or ''}取值，但当前数据里没有它的记录",
    }


def scope_conflict_refuse(scope: list[Hit], res: Resolution) -> dict | None:
    """会话范围是 col = v，题目点名了同一列的其他取值 → 越界。"""
    for s in scope:
        for h in res.hits:
            if h.col.key == s.col.key and h.seg == s.seg and h.value != s.value:
                same = [x for x in res.hits if x.phrase == h.phrase]
                if any(x.col.key == s.col.key and x.value == s.value for x in same):
                    continue
                return {
                    "code": "OUT_OF_SCOPE",
                    "reason": f"本轮会话范围是“{s.value}”，问题问的“{h.phrase}”不在范围内",
                }
    return None


def overrule_clarify(
    clarify: Any,
    idx: ValueIndex,
    scope: list[Hit],
    caliber_names: dict[str, list[str]],
) -> str | None:
    """模型反问了已有默认答案的事 → 回喂一句让它直接回答（只在调用方回喂一次）。

    - 选项是某维度的层级名（≥2 个）：层级有默认（hierarchyLevel / 顶层），不用问；
    - 选项是会话范围那一列的取值（≥2 个）：范围已经限定，按范围统计；
    - 选项是同一组口径裁定的说法（≥2 个）：口径裁定给了默认算法，按裁定算。
    返回 None 表示照常反问。
    """
    if not isinstance(clarify, dict):
        return None
    opts = [str(o.get("label") if isinstance(o, dict) else o).strip() for o in clarify.get("options") or []]
    opts = [o for o in opts if o]
    if len(opts) < 2:
        return None
    if sum(o in idx.level_names for o in opts) >= 2:
        return "题目没指明层级时按“层级默认”那一条的默认层级汇总，不要反问层级；请直接输出 SQL。"
    for s in scope:
        vals = set(s.col.values) | set(s.col.labels)
        if sum(o in vals for o in opts) >= 2:
            return f"本轮会话范围已限定 {s.sql()}，请直接按这个范围统计，不要反问；请直接输出 SQL。"
    terms = {nm: term for term, names in caliber_names.items() for nm in names}
    if sum(o in terms for o in opts) >= 2:
        return "口径裁定已经给出这个说法的默认算法，请按裁定计算并在列名标明口径，不要反问；请直接输出 SQL。"
    return None
