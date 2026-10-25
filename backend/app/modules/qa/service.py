"""问数只读服务：ORM → 前端 mock 形状（字段、时间格式按 qa-api §9.3）。"""

from datetime import datetime, time, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.common.auth import CurrentUser, resolve_user
from app.common.context import ViewContext
from app.common.pagination import Page, PageParams
from app.core import clock
from app.core.errors import NotFound
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


async def effective_user(session: AsyncSession, user: CurrentUser, ctx: ViewContext) -> tuple[int, SysUser]:
    """数据身份：预览时是被预览人（?previewAs=用户编号），否则是当前用户。

    EXTENSION POINT (PDE): 预览需要「角色与权限 · 管理」，权限体系接入后在这里校验。
    """
    code = ctx.preview_as or user.id
    if ctx.preview_as:
        await resolve_user(session, code)  # 不存在 / 停用 → 401
    u = await r.SysUserRepo(session).get_by_code(code)
    if u is None:
        raise NotFound("用户不存在", details={"id": code})
    return u.id, u


# ---------------------------------------------------------------- 工作台


def session_out(s: QaSession) -> SessionOut:
    return SessionOut(id=s.code, title=s.title, date=fmt_date(s.last_at) or "")


def turn_out(t: QaTurn) -> dict[str, Any]:
    out: dict[str, Any] = {"id": t.code, "kind": t.kind.value}
    if t.asked_at:
        out["time"] = fmt_hm(t.asked_at)
    if t.question is not None:
        out["question"] = t.question
    out.update(t.snapshot or {})
    return out


def session_detail_out(s: QaSession) -> SessionDetailOut:
    return SessionDetailOut(
        id=s.code,
        title=s.title,
        date=fmt_date(s.last_at) or "",
        scope=s.scope,
        caliber=s.caliber.value,
        owner_id=s.owner.code,
        turns=[turn_out(t) for t in s.turns],
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


def page_full_out(p: QaPage) -> PageFullOut:
    cards = [card_out(c) for c in p.cards]
    sub = p.subscription
    return PageFullOut(
        id=p.code,
        name=p.name,
        owner=p.owner.name,
        owner_id=p.owner.code,
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
        "cards": [card_out(c) for c in t.cards],
        "provider": t.provider,
        "uses": t.uses,
        "updated_at": fmt_date(t.updated_at) or "",
        "official": t.official,
        "desc": t.description,
    }


# ---------------------------------------------------------------- 调优


def cert_out(c: QaCert) -> CertOut:
    return CertOut(
        id=c.code,
        question=c.question,
        variants=[v.question for v in c.variants],
        plan=c.sem_plan,
        plan_text=catalog.plan_text(c.sem_plan),
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
        "status": x.status.value,
        "operator": x.operator,
        "cr": x.cr_code,
        "crs": list(x.crs) if x.crs else None,
        "note": x.note,
    }


def run_out(x: QaEvalRun, fail_count: int) -> EvalRunOut:
    return EvalRunOut(**run_fields(x, fail_count))


def eval_q_out(q: QaEvalQuestion) -> EvalQOut:
    return EvalQOut(
        id=q.code,
        question=q.question,
        group=q.group_name,
        expect=q.expect,
        expect_text=catalog.plan_text(q.expect),
        last=q.last_result.value if q.last_result else None,
        cr=q.cr_code,
    )


async def run_detail(session: AsyncSession, x: QaEvalRun, page: PageParams) -> EvalRunDetailOut:
    rr = r.EvalRunRepo(session)
    rows, total = await rr.list_page(rr.fails_query(x.id), page)
    fails = Page(
        items=[
            FailCaseOut(
                qid=f.question.code,
                question=f.question.question,
                expect=catalog.plan_text(f.question.expect),
                actual=f.actual,
                kind=f.kind.value,
            )
            for f in rows
        ],
        total=total,
        page=page.page,
        size=page.size,
    )
    stats = [KindStatOut(kind=k.value, n=n) for k, n in await rr.kind_stats(x.id)]
    replay: list[EvalQOut] = []
    hints: dict[str, str] = {}
    if x.status == RunStatus.queued:
        crs = list(x.crs or ([x.cr_code] if x.cr_code else []))
        replay = [eval_q_out(q) for q in await r.EvalQuestionRepo(session).by_crs(crs)]
        hints = {c: catalog.REPLAY_HINT[c] for c in crs if c in catalog.REPLAY_HINT}
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


async def feedback_detail(session: AsyncSession, f: QaFeedback, user_code: str | None) -> FeedbackDetailOut:
    """同 FeedbackTab.vue：existing / planFor / certBlock / synCovered / relEvals。"""
    existing = await r.CertRepo(session).by_question(f.fix_text)
    plan = None
    if existing is None:
        fq = await r.FreqRepo(session).by_question(f.fix_text)
        eq = await r.EvalQuestionRepo(session).by_question(f.fix_text) if fq is None else None
        plan = (fq.sem_plan if fq else None) or (eq.expect if eq else None)
    if existing or plan:
        block = ""
    elif f.fix_target and "新建" in f.fix_target:
        block = "本体里还没有对应指标，需先在指标口径中新建"
    else:
        block = "还没有可用的语义计划，请先在评测题库里补一道题"
    syn = await r.SynonymRepo(session).by_word(f.fix_text)
    rel = await r.EvalQuestionRepo(session).count_cr(f.resolved_by) if f.resolved_by else 0
    return FeedbackDetailOut(
        **feedback_fields(f, user_code),
        cert_block=block,
        existing_cert=existing.code if existing else None,
        covered_by=syn.cr_code if syn else None,
        related_eval_count=rel,
    )


def freq_out(x: QaFreqQuestion, rank: int) -> FreqOut:
    return FreqOut(
        id=x.code,
        rank=rank,
        question=x.question,
        hits7=x.hits7,
        hits30=x.hits30,
        users=x.users,
        plan=x.sem_plan,
        plan_text=catalog.plan_text(x.sem_plan),
        consistency=float(x.consistency),
        last_asked=fmt_min(x.last_asked),
        cert_id=x.cert.code if x.cert else None,
        cert_status=x.cert.status.value if x.cert else None,
    )
