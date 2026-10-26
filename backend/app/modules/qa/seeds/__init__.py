"""qa 模块 seed（JSON 组 qa，export-mock 导出；后端不改这些 JSON）。

| 导出文件 | 表 | 说明 |
|---|---|---|
| QA_SESSION_LIST | qa_session | 103 个会话；拥有者 = 演示身份王敏 U010001；
  last_at = date 当天 00:00（北京）[推断]；scope 华东、caliber ex [推断]（列表 mock 只有标题和日期） |
| QA_DEMO_TURNS | qa_turn | 5 轮演示对话挂到 S0001 [推断]；snapshot = mock 去掉 id/kind/time/question |
| QA_FAVORITE_LIST | qa_favorite | 18 条；lastUsed "MM-DD" → 2026-MM-DD 00:00 [推断] |
| QA_SCOPE_OPTIONS / QA_CALIBER_OPTIONS | qa_session_option | 3 + 2 |
| MY_PAGES + OTHER_PAGES | qa_page / qa_page_card / qa_page_share_member / qa_page_subscription | 32 页；
  owner 按姓名 → sys_user（姓名唯一命中；不在 sys_user 的走 OWNER_REASSIGN 改派，qa-api §9.2）；
  共享成员不在 sys_user 的剔除（§9.2）。SYS_USERS 补了分享候选人（U010009–U010015）后，
  周婷 = U010010 直接命中，不再改派李娜 |
| PAGE_TEMPLATES | qa_template / qa_template_card | 22 个模板 |
| CERT_QAS | qa_cert / qa_cert_variant | 30 条 |
| EVAL_QS | qa_eval_question | 40 道 semplan 题
  （extra/qa/eval/questions.json 是评测题集文件，不登记为 seed） |
| EVAL_RUNS | qa_eval_run / qa_eval_fail | 21 次运行、222 条失败 |
| FEEDBACKS | qa_feedback | 26 条；user_id 只在姓名能在 sys_user 唯一找到时填
  （§9.2：李娜/何静/冯雪/赵磊/刘洋） |
| FREQ_QS | qa_freq_question | 32 条 |
| TUNED_SYNONYMS | qa_synonym_link | 2 条 |
| OBJ_TYPES + FIELD_GROUPS | qa_object_type_view | 4 行（region 只有 field_groups）；release_id 不填 |

种子不填任何 release_id（test_seed_fresh_db 里 ontology_release 为空），
版本号文字存在 answered_ver / version_label。
"""

from datetime import date, datetime, time
from typing import Any
from zoneinfo import ZoneInfo

from app.core.config import get_settings
from app.core.schema import parse_display_dt
from app.modules.admin.models import SysUser
from app.modules.admin.seeds import _names
from app.modules.qa import catalog
from app.modules.qa.models import (
    CardViz,
    CertStatus,
    EvalLast,
    FailKind,
    FbStatus,
    FixKind,
    PageSourceKind,
    PageStatus,
    QaCert,
    QaCertVariant,
    QaEvalFail,
    QaEvalQuestion,
    QaEvalRun,
    QaFavorite,
    QaFeedback,
    QaFreqQuestion,
    QaObjectTypeView,
    QaPage,
    QaPageCard,
    QaPageShareMember,
    QaPageSubscription,
    QaSession,
    QaSessionOption,
    QaSynonymLink,
    QaTemplate,
    QaTemplateCard,
    QaTurn,
    RunStatus,
    ShareMode,
    SharePerm,
    SubChannel,
    SubWhen,
    TurnKind,
)
from app.seeds import Ref, Row, SeedContext, SeedSpec

JSON_MODULE = "qa"

DEMO_USER = "U010001"  # 演示身份王敏（当前登录用户）
DEMO_SESSION = "S0001"  # [推断] QA_DEMO_TURNS 所属会话
# §9.2：mock 里出现、但 sys_user 里没有的页面拥有者 → 改派（只在姓名查不到时用；
# 周婷已由 SYS_USERS 补成 U010010，不再需要改派）
OWNER_REASSIGN: dict[str, str] = {}
SEED_YEAR = 2026


def user_code(name: str | None) -> str | None:
    """姓名 → sys_user.code；不在或重名返回 None（调用方决定剔除还是只存姓名）。"""
    codes = _names().get(name or "", [])
    return codes[0] if len(codes) == 1 else None


def _dt(s: str | None) -> datetime | None:
    return parse_display_dt(s) if s else None


def _day(d: str) -> datetime:
    """ "YYYY-MM-DD" → 当天 00:00（北京）。"""
    return datetime.combine(date.fromisoformat(d), time(0), tzinfo=ZoneInfo(get_settings().display_tz))


def _md(md: str) -> datetime:
    """ "MM-DD" → 2026-MM-DD 00:00 [推断]。"""
    return _day(f"{SEED_YEAR}-{md}")


# ---------------------------------------------------------------- 工作台


def _session(r: Row, _ctx: SeedContext) -> Row:
    return {
        "code": r["id"],
        "owner_id": DEMO_USER,
        "title": r["title"],
        "scope": "华东",
        "caliber": "ex",
        "last_at": _day(r["date"]),
    }


def _turn_rows(rows: list[Row]) -> list[Row]:
    out = []
    for seq, r in enumerate(rows, start=1):
        answer = r.get("answer") or {}
        asked = parse_display_dt(f"2026-10-25 {r['time']}") if r.get("time") else None
        snapshot = {k: v for k, v in r.items() if k not in ("id", "kind", "time", "question")}
        out.append(
            {
                "code": r["id"],
                "session_id": DEMO_SESSION,
                "seq": seq,
                "kind": TurnKind(r["kind"]).name,
                "question": r.get("question"),
                "snapshot": snapshot,
                "answered_ver": answer.get("answeredVer"),
                "asked_at": asked,
                "status": "done",
            }
        )
    return out


def _option(kind: str):
    def f(r: Row, ctx: SeedContext) -> Row:
        file = "QA_SCOPE_OPTIONS.json" if kind == "scope" else "QA_CALIBER_OPTIONS.json"
        order = [x["value"] for x in ctx.load(file)]
        return {
            "kind": kind,
            "option_value": r["value"],
            "label": r["label"],
            "sort_order": order.index(r["value"]) + 1,
        }

    return f


def _favorite(r: Row, _ctx: SeedContext) -> Row:
    return {
        "code": r["id"],
        "owner_id": DEMO_USER,
        "question": r["question"],
        "uses": r["uses"],
        "last_used": _md(r["lastUsed"]) if r.get("lastUsed") else None,
    }


# ---------------------------------------------------------------- 页面与模板


def _all_pages(ctx: SeedContext) -> list[Row]:
    return list(ctx.load("MY_PAGES.json")) + list(ctx.load("OTHER_PAGES.json"))


def page_owner_code(name: str) -> str:
    code = user_code(name) or OWNER_REASSIGN.get(name)
    if code is None:
        raise ValueError(f"qa seed：页面拥有者 {name!r} 不在 sys_user")
    return code


def _page(r: Row, _ctx: SeedContext) -> Row:
    share = r.get("share") or {}
    return {
        "code": r["id"],
        "owner_id": page_owner_code(r["owner"]),
        "name": r["name"],
        "scope": r.get("scope"),
        "source_kind": PageSourceKind(r["sourceKind"]).name,
        "source_ref": r.get("source"),
        "status": PageStatus(r["status"]).name,
        "status_note": r.get("statusNote"),
        "refresh": r["refresh"],
        "last_refresh": _dt(r.get("lastRefresh")),
        "data_as_of": _dt(r.get("dataAsOf")),
        "updated_at": _dt(r.get("updatedAt")),
        "share_mode": ShareMode(share.get("mode", "仅自己")).name,
        "share_link": bool(share.get("link")),
        "allow_export": bool(share.get("allowExport", True)),
        "views": r.get("views") or 0,
    }


RESULT_KEYS = ("value", "unit", "sub", "series", "labels", "rows")


def card_row(c: Row, i: int) -> Row:
    m = catalog.metric_by_name(c.get("metric"))
    return {
        "card_key": c["key"],
        "sort_order": i,
        "title": c["title"],
        "viz": CardViz(c["viz"]).name,
        "metric_name": c.get("metric"),
        "metric_code": catalog.metric_code_of_key(m["key"]) if m else None,
        "metric_version": catalog.version_int(c.get("version")),
        "pending_cr": c.get("pending"),
        "result_cache": {k: c[k] for k in RESULT_KEYS if k in c},
    }


def _page_cards(r: Row, _ctx: SeedContext) -> list[Row]:
    return [{**card_row(c, i), "page_id": r["id"]} for i, c in enumerate(r["cards"], start=1)]


def _share_members(r: Row, _ctx: SeedContext) -> list[Row]:
    out = []
    for m in (r.get("share") or {}).get("members", []):
        code = user_code(m["name"])
        if code is None:  # §9.2：不在 sys_user 的成员剔除
            continue
        out.append(
            {"page_id": r["id"], "user_id": code, "perm": SharePerm(m["perm"]).name, "dept": m.get("dept")}
        )
    return out


def _subscription(r: Row, _ctx: SeedContext) -> Row | None:
    sub = r.get("sub")
    if not sub:
        return None
    return {
        "page_id": r["id"],
        "enabled": bool(sub.get("on")),
        "channel": SubChannel(sub["channel"]).name,
        "recipients": list(sub.get("receivers") or []),
        "send_when": SubWhen(sub["when"]).name,
        "only_changed": bool(sub.get("onlyChanged")),
    }


def _template(r: Row, _ctx: SeedContext) -> Row:
    return {
        "code": r["id"],
        "name": r["name"],
        "scene": r["scene"],
        "description": r.get("desc") or "",
        "provider": r["provider"],
        "official": bool(r.get("official")),
        "uses": r.get("uses") or 0,
        "updated_at": _day(r["updatedAt"]),
    }


def _template_cards(r: Row, _ctx: SeedContext) -> list[Row]:
    return [{**card_row(c, i), "template_id": r["id"]} for i, c in enumerate(r["cards"], start=1)]


# ---------------------------------------------------------------- 调优


def _plan_metric(plan: dict[str, Any]) -> dict[str, Any]:
    m = catalog.t_metrics().get(plan.get("metric", ""))
    return {
        "metric_code": catalog.metric_code_of_key(plan.get("metric")),
        "metric_version": catalog.version_int(m["version"]) if m else None,
    }


def _cert(r: Row, _ctx: SeedContext) -> Row:
    return {
        "code": r["id"],
        "question": r["question"],
        "sem_plan": r["plan"],
        **_plan_metric(r["plan"]),
        "status": CertStatus(r["status"]).name,
        "cr_code": r.get("cr"),
        "certifier": r.get("certifier"),
        "certified_at": _dt(r.get("certifiedAt")),
        "hits": r.get("hits") or 0,
        "source": r["source"],
        "note": r.get("note"),
    }


def _variants(r: Row, _ctx: SeedContext) -> list[Row]:
    return [{"cert_id": r["id"], "question": v} for v in r.get("variants") or []]


def _eval_q(r: Row, _ctx: SeedContext) -> Row:
    return {
        "code": r["id"],
        "question": r["question"],
        "group_name": r["group"],
        "kind": "semplan",
        "expect": r["expect"],
        "last_result": EvalLast(r["last"]).name if r.get("last") else None,
        "cr_code": r.get("cr"),
    }


def _eval_run(r: Row, _ctx: SeedContext) -> Row:
    t = r.get("time") or ""
    is_dt = len(t) == 16 and t[10] == " "
    return {
        "code": r["id"],
        "run_at": parse_display_dt(t) if is_dt else None,
        "time_label": None if is_dt else t,
        "trigger_label": r["trigger"],
        "version_label": r.get("version"),
        "scope": r["scope"],
        "total": r["total"],
        "passed": r["passed"],
        "status": RunStatus(r["status"]).name,
        "operator": r["operator"],
        "cr_code": r.get("cr"),
        "crs": r.get("crs"),
        "note": r.get("note"),
    }


def _eval_fails(r: Row, _ctx: SeedContext) -> list[Row]:
    return [
        {"run_id": r["id"], "question_id": f["qid"], "actual": f["actual"], "kind": FailKind(f["kind"]).name}
        for f in r.get("fails") or []
    ]


def _feedback(r: Row, _ctx: SeedContext) -> Row:
    fix = r["fix"]
    return {
        "code": r["id"],
        "fed_back_at": parse_display_dt(r["time"]),
        "user_id": user_code(r["user"]),
        "user_name": r["user"],
        "dept": r.get("dept"),
        "question": r["question"],
        "answer": r.get("answer") or "",
        "reason": r.get("reason") or "",
        "fix_kind": FixKind(fix["kind"]).name,
        "fix_text": fix["text"],
        "fix_target": fix.get("target"),
        "status": FbStatus(r["status"]).name,
        "handler": r.get("handler"),
        "resolved_by": r.get("resolvedBy"),
    }


def _freq(r: Row, _ctx: SeedContext) -> Row:
    return {
        "code": r["id"],
        "question": r["question"],
        "hits7": r["hits7"],
        "hits30": r["hits30"],
        "users": r["users"],
        "sem_plan": r["plan"],
        "consistency": r["consistency"],
        "last_asked": _dt(r.get("lastAsked")),
        "cert_id": r.get("certId"),
    }


def _synonym(r: Row, _ctx: SeedContext) -> Row:
    return {"word": r["word"], "target": r["target"], "cr_code": r.get("cr")}


# ---------------------------------------------------------------- 对象展示定义


def _obj_types(_r: Any, ctx: SeedContext) -> list[Row]:
    groups = ctx.load("FIELD_GROUPS.json")
    types = list(ctx.load("OBJ_TYPES.json"))
    labels = ctx.load("KIND_LABEL.json")
    out = []
    for i, t in enumerate(types, start=1):
        out.append(
            {
                "kind": t["kind"],
                "label": t["label"],
                "unit": t.get("unit"),
                "kw_placeholder": t.get("kwPlaceholder"),
                "facet_defs": t.get("facets") or [],
                "column_defs": t.get("columns") or [],
                "field_groups": groups.get(t["kind"], []),
                "sort_order": i,
            }
        )
    known = {t["kind"] for t in types}
    for kind, fg in groups.items():
        if kind not in known:  # region：只有详情字段分组，不出现在对象搜索
            out.append(
                {
                    "kind": kind,
                    "label": labels.get(kind, kind),
                    "unit": None,
                    "kw_placeholder": None,
                    "facet_defs": [],
                    "column_defs": [],
                    "field_groups": fg,
                    "sort_order": len(out) + 1,
                }
            )
    return [{**o, "release_id": None} for o in out]


def _obj_rows(r: Row, ctx: SeedContext) -> list[Row] | None:
    # OBJ_TYPES 第一行时一次性产出全部（含 FIELD_GROUPS 里多出的 region）
    first = ctx.load("OBJ_TYPES.json")[0]
    return _obj_types(r, ctx) if r.get("kind") == first["kind"] else None


def _demo_turns(r: Row, ctx: SeedContext) -> list[Row] | None:
    rows = list(ctx.load("QA_DEMO_TURNS.json"))
    return _turn_rows(rows) if r.get("id") == rows[0]["id"] else None


PAGE_REF = {"page_id": Ref(QaPage, "code", nullable=False)}
USER = Ref(SysUser, "code")

SEEDS: list[SeedSpec] = [
    SeedSpec(QaSession, "QA_SESSION_LIST.json", ("code",), _session, refs={"owner_id": USER}),
    SeedSpec(
        QaTurn,
        "QA_DEMO_TURNS.json",
        ("code",),
        _demo_turns,
        refs={"session_id": Ref(QaSession, "code", nullable=False)},
    ),
    SeedSpec(QaFavorite, "QA_FAVORITE_LIST.json", ("code",), _favorite, refs={"owner_id": USER}),
    SeedSpec(QaSessionOption, "QA_SCOPE_OPTIONS.json", ("kind", "option_value"), _option("scope")),
    SeedSpec(QaSessionOption, "QA_CALIBER_OPTIONS.json", ("kind", "option_value"), _option("caliber")),
    SeedSpec(QaPage, "MY_PAGES.json", ("code",), _page, refs={"owner_id": USER}),
    SeedSpec(QaPage, "OTHER_PAGES.json", ("code",), _page, refs={"owner_id": USER}),
    *[
        SeedSpec(model, f, key, fn, refs={**PAGE_REF, **extra})
        for f in ("MY_PAGES.json", "OTHER_PAGES.json")
        for model, key, fn, extra in (
            (QaPageCard, ("page_id", "card_key"), _page_cards, {}),
            (
                QaPageShareMember,
                ("page_id", "user_id"),
                _share_members,
                {"user_id": Ref(SysUser, "code", False)},
            ),
            (QaPageSubscription, ("page_id",), _subscription, {}),
        )
    ],
    SeedSpec(QaTemplate, "PAGE_TEMPLATES.json", ("code",), _template),
    SeedSpec(
        QaTemplateCard,
        "PAGE_TEMPLATES.json",
        ("template_id", "card_key"),
        _template_cards,
        refs={"template_id": Ref(QaTemplate, "code", nullable=False)},
    ),
    SeedSpec(QaCert, "CERT_QAS.json", ("code",), _cert),
    SeedSpec(
        QaCertVariant,
        "CERT_QAS.json",
        ("cert_id", "question"),
        _variants,
        refs={"cert_id": Ref(QaCert, "code", nullable=False)},
    ),
    SeedSpec(QaEvalQuestion, "EVAL_QS.json", ("code",), _eval_q),
    SeedSpec(QaEvalRun, "EVAL_RUNS.json", ("code",), _eval_run),
    SeedSpec(
        QaEvalFail,
        "EVAL_RUNS.json",
        ("run_id", "question_id"),
        _eval_fails,
        refs={
            "run_id": Ref(QaEvalRun, "code", nullable=False),
            "question_id": Ref(QaEvalQuestion, "code", nullable=False),
        },
    ),
    SeedSpec(QaFeedback, "FEEDBACKS.json", ("code",), _feedback, refs={"user_id": USER}),
    SeedSpec(QaFreqQuestion, "FREQ_QS.json", ("code",), _freq, refs={"cert_id": Ref(QaCert, "code")}),
    SeedSpec(QaSynonymLink, "TUNED_SYNONYMS.json", ("word", "cr_code"), _synonym),
    SeedSpec(QaObjectTypeView, "OBJ_TYPES.json", ("kind", "release_id"), _obj_rows),
]
