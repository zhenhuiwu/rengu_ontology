"""问数只读服务：ORM → 前端 mock 形状（字段、时间格式按 qa-api §9.3）。"""

import csv
import io
import zipfile
from datetime import datetime, time, timedelta
from typing import Any
from xml.sax.saxutils import escape

from sqlalchemy import exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.auth import CurrentUser, check_func, resolve_user, row_filter
from app.common.context import ViewContext
from app.common.ontology import require_ontology_id
from app.common.pagination import Page, PageParams
from app.core import clock
from app.core.errors import AppError, NotFound
from app.core.schema import _tz
from app.modules.admin.models import SysUser
from app.modules.qa import catalog
from app.modules.qa import repo as r
from app.modules.qa.models import (
    QaCert,
    QaEvalQuestion,
    QaEvalRun,
    QaFeedback,
    QaFreqQuestion,
    QaPage,
    QaSession,
    QaSynonymLink,
    QaTemplate,
    QaTurn,
    RunStatus,
)
from app.modules.qa.schemas import (
    CaliberTagOut,
    CertOut,
    EvalQOut,
    EvalRunDetailOut,
    EvalRunOut,
    FailCaseOut,
    FavoriteOut,
    FeedbackDetailOut,
    FeedbackOut,
    FixOut,
    FreqOut,
    KindStatOut,
    PageBriefOut,
    PageFullOut,
    SessionDetailOut,
    SessionOut,
    ShareMemberOut,
    ShareOut,
    SubOut,
)

# ---------------------------------------------------------------- 时间格式（§9.3）


def _local(v: datetime) -> datetime:
    return (v if v.tzinfo else v.replace(tzinfo=_tz())).astimezone(_tz())


def fmt_min(v: datetime | None) -> str | None:
    return _local(v).strftime("%Y-%m-%d %H:%M") if v else None


def fmt_date(v: datetime | None) -> str | None:
    return _local(v).strftime("%Y-%m-%d") if v else None


def fmt_md(v: datetime | None) -> str | None:
    return _local(v).strftime("%m-%d") if v else None


def fmt_hm(v: datetime | None) -> str | None:
    return _local(v).strftime("%H:%M") if v else None


def day_start(days_ago: int) -> datetime:
    return datetime.combine(clock.today() - timedelta(days=days_ago), time(), tzinfo=_tz())


def _ver(n: int | None) -> str:
    return f"v{n}" if n is not None else ""


# ---------------------------------------------------------------- 身份


async def effective_viewer(
    session: AsyncSession, user: CurrentUser, ctx: ViewContext
) -> tuple[int, SysUser, CurrentUser]:
    """数据身份：预览时是被预览人（?previewAs=用户编号），否则是当前用户。

    返回 (代理 id, SysUser, 查看人 CurrentUser)。

    预览需要「角色与权限 · 管理」，被预览人还须是当前本体的有效成员。
    """
    if ctx.preview_as:
        check_func(user, "roles", "manage")
    code = ctx.preview_as or user.id
    viewer = await resolve_user(session, code) if ctx.preview_as else user  # 不存在 / 停用 → 401
    u = await r.SysUserRepo(session).get_by_code(code)
    if u is None:
        raise NotFound("用户不存在", details={"id": code})
    return u.id, u, viewer


async def effective_user(session: AsyncSession, user: CurrentUser, ctx: ViewContext) -> tuple[int, SysUser]:
    pk, u, _ = await effective_viewer(session, user, ctx)
    return pk, u


# ---------------------------------------------------------------- 工作台


TAX_CALIBERS = {"不含税": "ex", "含税": "in"}  # 旧口径（W9 ex/in 同义），静态没有对应变体时按这个走


async def variant_index(session: AsyncSession, ver: str | None = None) -> dict[str, list[dict[str, Any]]]:
    """兼容QA调用方；同版本已发布的显式指标变体由静态公开查询维护。"""
    from app.modules.static.explorer import metric_variant_index

    return await metric_variant_index(session, ver)


def check_caliber(label: str | None, idx: dict[str, list[dict[str, Any]]]) -> str | None:
    """静态有变体时只收认识的标签（旧的 不含税 / 含税 总是收）；静态没有变体时不校验。"""
    if label is None or not idx or label in idx or label in TAX_CALIBERS:
        return label
    raise AppError(
        "口径不在指标口径变体里",
        code="VALIDATION_FAILED",
        status_code=422,
        details={"fields": [{"field": "caliber", "message": f"可选：{'、'.join(sorted(idx))}"}]},
    )


def caliber_lines(label: str | None, idx: dict[str, list[dict[str, Any]]]) -> list[str]:
    """会话口径 → 提示词里的一段：命中静态变体就点名每个指标该用哪个变体；否则按旧的含税 / 不含税。"""
    if not label:
        return []
    if label in idx:
        out = [f"- 用户选定口径「{label}」：下列指标只用这个变体，同名的其他口径变体不要用"]
        out += [
            f"- {v['name']}：`{v['api']}`（{v['code']} {v['version'] or ''}）".rstrip() for v in idx[label]
        ]
        return out
    if label in TAX_CALIBERS:
        return [f"- 用户选定口径「{label}」：金额类指标（销售额 / 客单价等）按{label}口径计算 [推断]"]
    return [f"- 用户选定口径「{label}」（静态指标里没有这个变体，仅供参考）"]


def session_out(s: QaSession) -> SessionOut:
    return SessionOut(id=s.code, title=s.title, date=fmt_date(s.last_at) or "")


def turn_out(t: QaTurn, applied: bool = False) -> dict[str, Any]:
    out: dict[str, Any] = {"id": t.code, "kind": t.kind.value}
    if t.asked_at:
        out["time"] = fmt_hm(t.asked_at)
    if t.question is not None:
        out["question"] = t.question
    out.update(t.snapshot or {})
    if t.caliber_variant and isinstance(out.get("answer"), dict):
        out["answer"] = {**out["answer"], "caliber": t.caliber_variant}  # W9 选过的澄清项，刷新后保留
    if t.vote:
        out["vote"] = t.vote  # W12 赞踩，刷新后保留
    if applied:
        out["applied"] = True  # [推断] 越权轮次已提交过待审批的申请（W16）→「已申请 · 待审批」
    out["pinnable"] = bool(not t.is_seed and t.kind.value == "answer" and t.status.value == "done" and t.sql_text and t.release_id and t.answered_ver)
    return out


def session_detail_out(s: QaSession, applied: set[int] | frozenset[int] = frozenset()) -> SessionDetailOut:
    return SessionDetailOut(
        id=s.code,
        title=s.title,
        date=fmt_date(s.last_at) or "",
        scope=s.scope,
        caliber=s.caliber,
        owner_id=s.owner.code,
        turns=[turn_out(t, t.id in applied) for t in s.turns],
    )


def favorite_out(f) -> FavoriteOut:
    return FavoriteOut(id=f.code, question=f.question, uses=f.uses, last_used=fmt_md(f.last_used))


# ---------------------------------------------------------------- 页面与模板


def card_out(c) -> dict[str, Any]:
    out: dict[str, Any] = {
        "key": c.card_key,
        "title": c.title,
        "viz": c.viz.value,
        "metric": c.metric_name or "",
        "version": _ver(c.metric_version),
    }
    out.update(c.result_cache or {})
    if c.pending_cr:
        out["pending"] = c.pending_cr
    return out


# 无权限卡片只留卡片框（标题、图形、口径），数值字段一律不出：
# 形状按 qa-api P3「范围外的卡片返回 {denied:true}，显示“无权限”」；前端 PageCard（mock/qa-pages.ts:12-20）
# 的数据字段是 value/unit/sub/series/labels/rows，文案见 PageDrawer.vue:128/283。
_CARD_FRAME_KEYS = ("key", "title", "viz", "metric", "version", "pending")

# [推断] 卡片判权用的对象类型：页面卡片按大区 / 门店范围取数，先按门店判；row_filter 现在是桩，不看对象类型。
CARD_SCOPE_OBJECT = "store"


def denied_card(card: dict[str, Any]) -> dict[str, Any]:
    out = {k: card[k] for k in _CARD_FRAME_KEYS if k in card}
    out["denied"] = True
    return out


async def cards_denied_for(viewer: CurrentUser, viewer_pk: int, page: QaPage) -> bool:
    """非拥有者（被共享人 / 预览他人看到共享页）且行级范围不是「不限制」→ 整页卡片标无权限。

    EXTENSION POINT (qa) [推断]：现在存的是拥有者权限下算出的卡片值，没法按查看人裁剪，所以只要
    `auth.row_filter` 不是 None 就整页标「无权限」（宁可看不到，不能看多；桩实现：本体管理员 None，
    其他人 DENY_ALL）。row_filter 接入真实范围后改成按查看人的范围逐张重算卡片（qa-api P3）。
    """
    if page.owner_id == viewer_pk:
        return False
    return await row_filter(viewer, CARD_SCOPE_OBJECT) is not None


def calibers_of(cards: list[dict[str, Any]]) -> list[CaliberTagOut]:
    """同 PageDrawer.vue：按「指标名 版本」去重，后出现的卡片覆盖 pending。"""
    m: dict[str, str | None] = {}
    for c in cards:
        m[f"{c['metric']} {c['version']}"] = c.get("pending")
    return [CaliberTagOut(label=k, pending=v) for k, v in m.items()]


def page_brief_out(p: QaPage) -> PageBriefOut:
    return PageBriefOut(
        id=p.code,
        name=p.name,
        cards=len(p.cards),
        updated_at=fmt_md(p.updated_at) or "",
        shared=p.share_mode.name != "private",
    )


def page_full_out(p: QaPage, *, deny_cards: bool = False) -> PageFullOut:
    cards = [card_out(c) for c in p.cards]
    if deny_cards:
        cards = [denied_card(c) for c in cards]
    sub = p.subscription
    return PageFullOut(
        id=p.code,
        name=p.name,
        owner=p.owner.name,
        owner_id=p.owner.code,
        owner_dept=p.owner.dept,
        scope=p.scope,
        source=p.source_ref,
        source_kind=p.source_kind.value,
        cards=cards,
        data_as_of=fmt_min(p.data_as_of),
        last_refresh=fmt_min(p.last_refresh),
        updated_at=fmt_min(p.updated_at) or "",
        refresh=p.refresh,
        sub=SubOut(
            on=sub.enabled,
            channel=sub.channel.value,
            receivers=list(sub.recipients or []),
            when=sub.send_when.value,
            only_changed=sub.only_changed,
        )
        if sub
        else None,
        share=ShareOut(
            mode=p.share_mode.value,
            members=[
                ShareMemberOut(user_id=m.user.code, name=m.user.name, dept=m.dept, perm=m.perm.value)
                for m in p.members
            ],
            link=p.share_link,
            allow_export=p.allow_export,
        ),
        status=p.status.value,
        status_note=p.status_note,
        views=p.views,
        calibers=calibers_of(cards),
    )


def template_out(t: QaTemplate) -> dict[str, Any]:
    return {
        "id": t.code,
        "name": t.name,
        "scene": t.scene,
        "cards": [{key: value for key, value in card_out(c).items() if key in _CARD_FRAME_KEYS} for c in t.cards],
        "provider": t.provider,
        "uses": t.uses,
        "updated_at": fmt_date(t.updated_at) or "",
        "official": t.official,
        "desc": t.description,
    }


async def page_view_out(session, page, user, ctx, *, temporary=False, metadata_only=False):
    from app.modules.qa.models import SharePerm
    from app.modules.qa.page_execution import projected_cards, template_capability

    cards = [denied_card(card_out(card)) for card in page.cards] if metadata_only else await projected_cards(session, user, page, ctx, temporary=temporary)
    owner = page.owner.code == user.id
    member_edit = any(member.user.code == user.id and member.perm == SharePerm.edit for member in page.members)
    writable = not ctx.read_only and user.ontology_role in ("owner", "editor")
    can_refresh, refresh_reason = await template_capability(session, page)
    return page_full_out(page, deny_cards=True).model_copy(update={
        "cards": cards, "calibers": calibers_of(cards), "lock_version": page.lock_version,
        "can_edit": writable and (owner or member_edit), "can_share": writable and owner,
        "can_refresh": writable and (owner or member_edit) and can_refresh,
        "refresh_unavailable_reason": refresh_reason,
        "schedule_active": False, "subscription_active": False,
    })


# ---------------------------------------------------------------- 调优


def cert_out(c: QaCert, metrics: catalog.Metrics) -> CertOut:
    return CertOut(
        id=c.code,
        question=c.question,
        variants=[v.question for v in c.variants],
        plan=c.sem_plan,
        plan_text=catalog.plan_text(c.sem_plan, metrics),
        status=c.status.value,
        cr=c.cr_code,
        certifier=c.certifier,
        certified_at=fmt_min(c.certified_at),
        hits=c.hits,
        source=c.source,
        note=c.note,
    )


def run_fields(x: QaEvalRun, fail_count: int) -> dict[str, Any]:
    return {
        "id": x.code,
        "time": fmt_min(x.run_at) if x.run_at else (x.time_label or ""),
        "trigger": x.trigger_label,
        "version": x.version_label,
        "scope": x.scope,
        "total": x.total,
        "passed": x.passed,
        "pass_rate": (x.passed / x.total) if x.total else 0,
        "fail_count": fail_count,
        "awaiting_execution": x.queue_job_id is not None and x.status == RunStatus.queued,
        "status": x.status.value,
        "operator": x.operator,
        "cr": x.cr_code,
        "crs": list(x.crs) if x.crs else None,
        "note": x.note,
    }


def run_out(x: QaEvalRun, fail_count: int) -> EvalRunOut:
    return EvalRunOut(**run_fields(x, fail_count))


def eval_q_out(q: QaEvalQuestion, metrics: catalog.Metrics) -> EvalQOut:
    return EvalQOut(
        id=q.code,
        question=q.question,
        group=q.group_name,
        expect=q.expect,
        expect_text=catalog.plan_text(q.expect, metrics),
        last=q.last_result.value if q.last_result else None,
        cr=q.cr_code,
    )


async def run_detail(
    session: AsyncSession, x: QaEvalRun, page: PageParams, ver: str | None = None
) -> EvalRunDetailOut:
    metrics, _ = await catalog.directory(session, ver, include_dims=False)
    rr = r.EvalRunRepo(session)
    rows, total = await rr.list_page(rr.fails_query(x.id), page)
    fails = Page[FailCaseOut].of(
        [
            FailCaseOut(
                qid=f.question.code,
                question=f.question.question,
                expect=catalog.plan_text(f.question.expect, metrics),
                actual=f.actual,
                kind=f.kind.value,
            )
            for f in rows
        ],
        total,
        page,
    )
    stats = [KindStatOut(kind=k.value, n=n) for k, n in await rr.kind_stats(x.id)]
    replay: list[EvalQOut] = []
    hints: dict[str, str] = {}
    if x.status == RunStatus.queued:
        crs = list(x.crs or ([x.cr_code] if x.cr_code else []))
        replay = [eval_q_out(q, metrics) for q in await r.EvalQuestionRepo(session).by_crs(crs)]
        # 没有持久化回放说明，不能把写死的示例 CR 文案当作真实执行信息。
    return EvalRunDetailOut(
        **run_fields(x, total), fails=fails, kind_stats=stats, replay_questions=replay, replay_hints=hints
    )


def feedback_fields(f: QaFeedback, user_code: str | None) -> dict[str, Any]:
    return {
        "id": f.code,
        "time": fmt_min(f.fed_back_at) or "",
        "user": f.user_name,
        "user_id": user_code,
        "dept": f.dept,
        "question": f.question,
        "answer": f.answer,
        "reason": f.reason,
        "fix": FixOut(kind=f.fix_kind.value, text=f.fix_text, target=f.fix_target),
        "status": f.status.value,
        "resolved_by": f.resolved_by,
        "handler": f.handler,
    }


def feedback_out(f: QaFeedback, user_code: str | None) -> FeedbackOut:
    return FeedbackOut(**feedback_fields(f, user_code))


async def cr_status(session: AsyncSession, code: str | None) -> str | None:
    """变更单状态（中文）；静态的 change_request 里没有这张单 → None。"""
    if not code:
        return None
    from app.modules.static.repo import change_request_statuses

    return (await change_request_statuses(session, [code])).get(code)


async def feedback_plan(session: AsyncSession, f: QaFeedback) -> tuple[Any, dict | None]:
    """同 FeedbackTab.vue existing / planFor：(已有认证问答, 语义计划)。"""
    existing = await r.CertRepo(session).by_question(f.fix_text)
    if existing is not None:
        return existing, existing.sem_plan
    fq = await r.FreqRepo(session).by_question(f.fix_text)
    eq = await r.EvalQuestionRepo(session).by_question(f.fix_text) if fq is None else None
    return None, (fq.sem_plan if fq else None) or (eq.expect if eq else None)


async def feedback_details(
    session: AsyncSession,
    rows: list[QaFeedback],
    user_codes: dict[int, str | None],
    metrics: catalog.Metrics | None = None,
) -> list[FeedbackDetailOut]:
    """固定批次查询，反馈条数增加不增加计划/同义词/CR/评测数查询次数。"""
    from app.modules.static.repo import change_request_statuses

    if not rows:
        return []
    if metrics is None:
        metrics, _ = await catalog.directory(session, include_dims=False)
    texts = {f.fix_text for f in rows}
    certs = {
        x.question: x
        for x in await session.execute(
            select(QaCert.question, QaCert.code, QaCert.status, QaCert.sem_plan).where(
                QaCert.question.in_(texts)
            )
        )
    }
    freqs = {
        question: plan
        for question, plan in await session.execute(
            select(QaFreqQuestion.question, QaFreqQuestion.sem_plan).where(QaFreqQuestion.question.in_(texts))
        )
    }
    evals = {
        question: plan
        for question, plan in await session.execute(
            select(QaEvalQuestion.question, QaEvalQuestion.expect).where(QaEvalQuestion.question.in_(texts))
        )
    }
    synonyms = {}
    for word, cr in await session.execute(
        select(QaSynonymLink.word, QaSynonymLink.cr_code)
        .where(QaSynonymLink.word.in_(texts))
        .order_by(QaSynonymLink.id)
    ):
        synonyms.setdefault(word, cr)  # 保持原by_word取最早记录的规则。
    counts = {
        cr: n
        for cr, n in await session.execute(
            select(QaEvalQuestion.cr_code, func.count())
            .where(QaEvalQuestion.cr_code.in_({f.resolved_by for f in rows if f.resolved_by}))
            .group_by(QaEvalQuestion.cr_code)
        )
    }
    statuses = await change_request_statuses(session, [cr for cr in synonyms.values() if cr])
    result = []
    for f in rows:
        existing = certs.get(f.fix_text)
        plan = (
            existing.sem_plan
            if existing
            else (freqs[f.fix_text] or None)
            if f.fix_text in freqs
            else evals.get(f.fix_text)
        )
        block = (
            ""
            if existing or plan
            else (
                "本体里还没有对应指标，需先在指标口径中新建"
                if f.fix_target and "新建" in f.fix_target
                else "还没有可用的语义计划，请先在评测题库里补一道题"
            )
        )
        cr = synonyms.get(f.fix_text)
        result.append(
            FeedbackDetailOut(
                **feedback_fields(f, user_codes.get(f.user_id)),
                cert_block=block,
                existing_cert=existing.code if existing else None,
                covered_by=cr,
                related_eval_count=counts.get(f.resolved_by, 0),
                existing_cert_question=existing.question if existing else None,
                existing_cert_status=existing.status.value if existing else None,
                plan=plan,
                plan_text=catalog.plan_text(plan, metrics) if plan else None,
                covered_cr_status=statuses.get(cr),
            )
        )
    return result


async def feedback_detail(
    session: AsyncSession, f: QaFeedback, user_code: str | None, metrics: catalog.Metrics | None = None
) -> FeedbackDetailOut:
    return (await feedback_details(session, [f], {f.user_id: user_code}, metrics))[0]


def freq_out(x: QaFreqQuestion, rank: int, metrics: catalog.Metrics) -> FreqOut:
    return FreqOut(
        id=x.code,
        rank=rank,
        question=x.question,
        hits7=x.hits7,
        hits30=x.hits30,
        users=x.users,
        plan=x.sem_plan,
        plan_text=catalog.plan_text(x.sem_plan, metrics),
        consistency=float(x.consistency),
        last_asked=fmt_min(x.last_asked),
        cert_id=x.cert.code if x.cert else None,
        cert_status=x.cert.status.value if x.cert else None,
    )


# ---------------------------------------------------------------- 工作台结果表（W10 / W11）


def turn_result(t: QaTurn) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]] | None:
    """只读已持久化的 answer.table；旧卡片未存结果时不可导出，不补演示行。"""
    ans = (t.snapshot or {}).get("answer") or {}
    tbl = ans.get("table") if isinstance(ans, dict) else None
    if not isinstance(tbl, dict):
        return None
    cols, rows = tbl.get("columns"), tbl.get("items")
    if not isinstance(cols, list) or not cols or not isinstance(rows, list):
        return None
    if any(
        not isinstance(c, dict) or not isinstance(c.get("key"), str) or not isinstance(c.get("title"), str)
        for c in cols
    ):
        return None
    if any(not isinstance(row, dict) for row in rows):
        return None
    return cols, rows, {"text": tbl.get("summary") or ""}


async def has_metric_references(session: AsyncSession, code: str) -> bool:
    """静态回滚用：是否存在QA指标软引用（含停用/历史记录，不检查自然语言或SQL文本）。"""
    from app.modules.qa.models import QaPageCard, QaQueryLog, QaTemplateCard

    ontology_id = require_ontology_id(session)
    canonical = catalog.METRIC_CODES.get(code, code) if ontology_id == 1 else code
    keys = {canonical, catalog.metric_key(canonical, ontology_id)}
    checks = []
    for model, plan_field in (
        (QaTurn, "sem_plan"),
        (QaPageCard, "sem_plan"),
        (QaTemplateCard, "sem_plan"),
        (QaCert, "sem_plan"),
        (QaQueryLog, "sem_plan"),
        (QaEvalQuestion, "expect"),
        (QaFreqQuestion, "sem_plan"),
    ):
        clauses = [getattr(model, plan_field)["metric"].as_string().in_(keys)]
        if hasattr(model, "metric_code"):
            clauses.append(model.metric_code.in_(keys))
        checks.append(exists(select(model.id).where(or_(*clauses))))
    return bool(await session.scalar(select(or_(*checks))))


def _cell(v: Any) -> str:
    return "" if v is None else str(v)


def export_csv(columns: list[dict[str, Any]], rows: list[dict[str, Any]]) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([c["title"] for c in columns])
    for row in rows:
        w.writerow([_cell(row.get(c["key"])) for c in columns])
    return buf.getvalue().encode("utf-8-sig")  # 带 BOM，Excel 直接打开不乱码


def export_xlsx(columns: list[dict[str, Any]], rows: list[dict[str, Any]], sheet: str = "结果") -> bytes:
    """最小 xlsx（标准库 zipfile 拼 OOXML，不引新依赖）：一张表，表头 + 全部行；数字按数字写。"""

    def col(i: int) -> str:
        s = ""
        i += 1
        while i:
            i, m = divmod(i - 1, 26)
            s = chr(65 + m) + s
        return s

    def cell(ref: str, v: Any) -> str:
        if isinstance(v, bool) or not isinstance(v, int | float):
            return f'<c r="{ref}" t="inlineStr"><is><t xml:space="preserve">{escape(_cell(v))}</t></is></c>'
        return f'<c r="{ref}"><v>{v}</v></c>'

    lines = [[c["title"] for c in columns], *[[r.get(c["key"]) for c in columns] for r in rows]]
    body = "".join(
        f'<row r="{ri + 1}">' + "".join(cell(f"{col(ci)}{ri + 1}", v) for ci, v in enumerate(line)) + "</row>"
        for ri, line in enumerate(lines)
    )
    hd = '<?xml version="1.0" encoding="UTF-8"?>'
    ox = "http://schemas.openxmlformats.org"
    ns = f'xmlns="{ox}/spreadsheetml/2006/main"'
    rel = f"{ox}/officeDocument/2006/relationships"
    rels = f'<Relationships xmlns="{ox}/package/2006/relationships">'
    ct = "application/vnd.openxmlformats-"
    sm = f"{ct}officedocument.spreadsheetml."
    files = {
        "[Content_Types].xml": f'{hd}<Types xmlns="{ox}/package/2006/content-types">'
        f'<Default Extension="rels" ContentType="{ct}package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        f'<Override PartName="/xl/workbook.xml" ContentType="{sm}sheet.main+xml"/>'
        '<Override PartName="/xl/worksheets/sheet1.xml" '
        f'ContentType="{sm}worksheet+xml"/></Types>',
        "_rels/.rels": f'{hd}{rels}<Relationship Id="rId1" Type="{rel}/officeDocument" '
        'Target="xl/workbook.xml"/></Relationships>',
        "xl/workbook.xml": f'{hd}<workbook {ns} xmlns:r="{rel}"><sheets>'
        f'<sheet name="{escape(sheet)}" sheetId="1" r:id="rId1"/></sheets></workbook>',
        "xl/_rels/workbook.xml.rels": f'{hd}{rels}<Relationship Id="rId1" Type="{rel}/worksheet" '
        'Target="worksheets/sheet1.xml"/></Relationships>',
        "xl/worksheets/sheet1.xml": f"{hd}<worksheet {ns}><sheetData>{body}</sheetData></worksheet>",
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, xml in files.items():
            z.writestr(name, xml)
    return buf.getvalue()
