"""问数（qa）只读接口第一批，挂在 /api/qa/v1（app/api.py）。字段形状对齐前端 mock。

版本快照（M3）：qa 不登记任何 SnapshotSectionBuilder——SECTION_OWNERS 里没有 qa 的 section，
qa 只订阅 X4 release.published（qa-api §8 X4、§9.2 qa_object_type_view、§12 #31）。
"""

from collections.abc import Callable
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query

from app.common.auth import CurrentUserDep, require_func
from app.common.context import ViewContextDep
from app.common.pagination import MAX_PAGE_SIZE, Page, PageParams
from app.core.errors import NotFound
from app.db.session import SessionDep
from app.modules.qa import catalog
from app.modules.qa import repo as r
from app.modules.qa import service as svc
from app.modules.qa.models import (
    CertStatus,
    EvalLast,
    FbStatus,
    FixKind,
    PageSourceKind,
    PageStatus,
    QaTemplate,
)
from app.modules.qa.schemas import (
    CertOut,
    EvalQListOut,
    EvalRunDetailOut,
    EvalRunOut,
    FavoriteOut,
    FeedbackDetailOut,
    FeedbackOut,
    FreqOut,
    GroupCountOut,
    MetricRefOut,
    ObjectTypeOut,
    OptionOut,
    PageFullOut,
    PageListOut,
    PlanOptionsOut,
    SessionDetailOut,
    SessionOptionsOut,
    SessionOut,
    SynonymOut,
    TemplateOut,
    TMetricOut,
    TuningSummaryOut,
)

router = APIRouter(tags=["qa"])

# 功能权限（qa-api §1.3 权限表；页面 key 同前端 nav.ts:25/26/29/58）。require_func 现在是骨架桩，
# 挂上不生效，等 PDE 实现后自动生效。调优子路由统一挂在 tuning 上。
_WB = [Depends(require_func("qa-workbench", "view"))]
_PG = [Depends(require_func("qa-pages", "view"))]
_OBJ = [Depends(require_func("object-search", "view"))]
tuning = APIRouter(prefix="/tuning", dependencies=[Depends(require_func("qa-tuning", "view"))])


def _kw(v: str | None) -> str | None:
    """关键词先 trim 再匹配（同前端 kw.trim()）；trim 后为空 = 不筛选。"""
    v = v.strip() if v else v
    return v or None


def page_dep(default: int) -> Callable[..., PageParams]:
    """各接口默认条数不同（qa-api §2–§7：12 / 10 / 15）。"""

    def _dep(
        page: Annotated[int, Query(ge=1)] = 1,
        size: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = default,
    ) -> PageParams:
        return PageParams(page=page, size=size)

    return _dep


P10 = Annotated[PageParams, Depends(page_dep(10))]
P12 = Annotated[PageParams, Depends(page_dep(12))]
P15 = Annotated[PageParams, Depends(page_dep(15))]

_RANGE_DAYS = {"today": 0, "7d": 6, "30d": 29}  # 同 QaSessionPanel：daysAgo <= N


def _page(items, total: int, p: PageParams) -> dict:
    return {"items": items, "total": total, "page": p.page, "size": p.size}


# ---------------------------------------------------------------- 工作台


@router.get("/sessions", dependencies=_WB, response_model=Page[SessionOut], summary="W1 会话列表")
async def list_sessions(
    session: SessionDep,
    user: CurrentUserDep,
    ctx: ViewContextDep,
    p: P12,
    q: str | None = None,
    range_: Annotated[Literal["all", "today", "7d", "30d"], Query(alias="range")] = "all",
):
    pk, _ = await svc.effective_user(session, user, ctx)
    since = svc.day_start(_RANGE_DAYS[range_]) if range_ in _RANGE_DAYS else None
    repo = r.SessionRepo(session)
    rows, total = await repo.list_page(repo.list_query(pk, _kw(q), since), p)
    return _page([svc.session_out(s) for s in rows], total, p)


@router.get(
    "/sessions/{sid}", dependencies=_WB, response_model=SessionDetailOut, summary="W3 会话详情（全部轮次）"
)
async def get_session(sid: str, session: SessionDep, user: CurrentUserDep, ctx: ViewContextDep):
    pk, _ = await svc.effective_user(session, user, ctx)
    s = await r.SessionRepo(session).detail(sid)
    # [推断] 非拥有者打开分享会话需按打开人重算（第一批未实现），先按不存在处理
    if s is None or s.owner_id != pk:
        raise NotFound("会话不存在", details={"id": sid})
    return svc.session_detail_out(s)


@router.get("/favorites", dependencies=_WB, response_model=Page[FavoriteOut], summary="W13 常用问题")
async def list_favorites(
    session: SessionDep, user: CurrentUserDep, ctx: ViewContextDep, p: P10, q: str | None = None
):
    pk, _ = await svc.effective_user(session, user, ctx)
    repo = r.FavoriteRepo(session)
    rows, total = await repo.list_page(repo.list_query(pk, _kw(q)), p)
    return _page([svc.favorite_out(f) for f in rows], total, p)


@router.get(
    "/session-options", dependencies=_WB, response_model=SessionOptionsOut, summary="W17 范围 / 口径下拉"
)
async def session_options(session: SessionDep, user: CurrentUserDep):
    repo = r.OptionRepo(session)
    opts = lambda rows: [OptionOut(value=o.option_value, label=o.label) for o in rows]  # noqa: E731
    return SessionOptionsOut(
        scopes=opts(await repo.by_kind("scope")),
        calibers=opts(await repo.by_kind("caliber")),
        metric={k: MetricRefOut(**v) for k, v in catalog.load("QA_METRIC").items()},
    )


# ---------------------------------------------------------------- 页面与模板


@router.get(
    "/pages",
    dependencies=_PG,
    response_model=PageListOut,
    response_model_exclude_none=True,
    summary="P1 我的页面",
)
async def list_pages(
    session: SessionDep,
    user: CurrentUserDep,
    ctx: ViewContextDep,
    p: P15,
    kw: str | None = None,
    src: PageSourceKind | None = None,
    share: Literal["已共享", "仅自己"] | None = None,
    status: PageStatus | None = None,
    view: Literal["brief", "full"] = "full",
):
    pk, u, viewer = await svc.effective_viewer(session, user, ctx)
    repo = r.PageRepo(session)
    visible = repo.visible_clause(pk, u.dept, include_shared=ctx.preview_as is not None)
    full = view == "full"
    shared = None if share is None else share == "已共享"
    rows, total = await repo.list_page(
        repo.list_query(visible, kw=_kw(kw), src=src, shared=shared, status=status, full=full), p
    )
    wb_total = await r.count_of(session, repo.list_query(visible, src=PageSourceKind.workbench))
    if full:  # 卡片判权同 P3（共享页在预览时会出现在列表里）
        items = [svc.page_full_out(x, deny_cards=await svc.cards_denied_for(viewer, pk, x)) for x in rows]
    else:
        items = [svc.page_brief_out(x) for x in rows]
    return {**_page(items, total, p), "from_workbench_count": wb_total}


@router.get(
    "/pages/{pid}",
    dependencies=_PG,
    response_model=PageFullOut,
    response_model_exclude_none=True,
    summary="P3 页面详情",
)
async def get_page(pid: str, session: SessionDep, user: CurrentUserDep, ctx: ViewContextDep):
    pk, u, viewer = await svc.effective_viewer(session, user, ctx)
    repo = r.PageRepo(session)
    x = await repo.detail(pid)
    if x is None or not await repo.is_visible(x.id, repo.visible_clause(pk, u.dept, include_shared=True)):
        raise NotFound("页面不存在", details={"id": pid})
    # EXTENSION POINT (qa) [推断]：卡片存的是拥有者权限下算出的值。查看人不是拥有者（被共享人，或预览
    # 他人时打开的共享页）且 auth.row_filter(查看人) 不是 None（桩：非本体管理员一律 DENY_ALL）时，整页卡片
    # 标 {denied:true}（「无权限」），不出数值；拥有者看自己的页面不变。row_filter 接入真实范围后，
    # 改成按查看人的范围逐张重算卡片（qa-api P3「卡片按查看人重算」），不再整页标无权限。
    return svc.page_full_out(x, deny_cards=await svc.cards_denied_for(viewer, pk, x))


@router.get("/templates", dependencies=_PG, response_model=Page[TemplateOut], summary="P11 模板库")
async def list_templates(
    session: SessionDep, user: CurrentUserDep, p: P15, kw: str | None = None, scene: str | None = None
):
    repo = r.TemplateRepo(session)
    rows, total = await repo.list_page(repo.list_query(_kw(kw), scene), p)
    return _page([svc.template_out(t) for t in rows], total, p)


@router.get("/templates/{tid}", dependencies=_PG, response_model=TemplateOut, summary="P12 模板详情")
async def get_template(tid: str, session: SessionDep, user: CurrentUserDep):
    repo = r.TemplateRepo(session)
    t = (await session.scalars(repo.list_query(None, None).where(QaTemplate.code == tid))).first()
    if t is None:
        raise NotFound(repo.not_found_message, details={"id": tid})
    return svc.template_out(t)


# ---------------------------------------------------------------- 对象类型


@router.get(
    "/objects/types",
    dependencies=_OBJ,
    response_model=list[ObjectTypeOut],
    response_model_exclude_none=True,
    summary="O1 对象类型",
)
async def object_types(session: SessionDep, user: CurrentUserDep, ctx: ViewContextDep):
    rows = await r.ObjectTypeRepo(session).current()
    return [
        ObjectTypeOut(
            kind=x.kind,
            label=x.label,
            unit=x.unit,
            kw_placeholder=x.kw_placeholder,
            facets=x.facet_defs,
            columns=x.column_defs,
        )
        for x in rows
        if x.column_defs
    ]


# ---------------------------------------------------------------- 调优


@tuning.get("/summary", response_model=TuningSummaryOut, summary="T1 调优页签计数")
async def tuning_summary(session: SessionDep, user: CurrentUserDep, ctx: ViewContextDep):
    return TuningSummaryOut(
        version=ctx.ver or catalog.load("CUR_VERSION"),
        data_as_of=catalog.load("TUNING_CUTOFF"),  # [推断] 跟前端 TUNING_CUTOFF 一致（文档写 now()）
        recert_count=await r.CertRepo(session).count_status(CertStatus.recert),
        open_feedback=await r.FeedbackRepo(session).count_open(),
        uncert_freq=await r.FreqRepo(session).count_uncert(),
        eval_total=await r.EvalQuestionRepo(session).total(),
    )


@tuning.get("/plan-options", response_model=PlanOptionsOut, response_model_exclude_none=True, summary="T2")
async def plan_options(user: CurrentUserDep):
    return PlanOptionsOut(
        metrics=[
            TMetricOut(key=m["key"], name=m["name"], version=m["version"], cr=m.get("cr"))
            for m in catalog.t_metrics().values()
        ],
        dims=catalog.load("TUNING_DIMS"),
        times=catalog.load("TUNING_TIMES"),
    )


@tuning.get("/certs", response_model=Page[CertOut], response_model_exclude_none=True, summary="T3 认证问答")
async def list_certs(
    session: SessionDep,
    user: CurrentUserDep,
    p: P15,
    kw: str | None = None,
    status: CertStatus | None = None,
    metric: str | None = None,
):
    repo = r.CertRepo(session)
    rows, total = await repo.list_page(repo.list_query(_kw(kw), status, metric), p)
    return _page([svc.cert_out(c) for c in rows], total, p)


@tuning.get("/certs/{cid}", response_model=CertOut, response_model_exclude_none=True, summary="T4")
async def get_cert(cid: str, session: SessionDep, user: CurrentUserDep):
    return svc.cert_out(await r.CertRepo(session).get_by_code_or_404(cid))


@tuning.get(
    "/eval/runs",
    response_model=Page[EvalRunOut],
    response_model_exclude_none=True,
    summary="T9 评测运行",
)
async def list_runs(session: SessionDep, user: CurrentUserDep, p: P15, trigger: str | None = None):
    repo = r.EvalRunRepo(session)
    rows, total = await repo.list_page(repo.list_query(trigger), p)
    counts = await repo.fail_counts([x.id for x in rows])
    return _page([svc.run_out(x, counts.get(x.id, 0)) for x in rows], total, p)


@tuning.get(
    "/eval/runs/{rid}",
    response_model=EvalRunDetailOut,
    response_model_exclude_none=True,
    summary="T10",
)
async def get_run(rid: str, session: SessionDep, user: CurrentUserDep, p: P12):
    x = await r.EvalRunRepo(session).get_by_code_or_404(rid)
    return await svc.run_detail(session, x, p)


@tuning.get(
    "/eval/questions",
    response_model=EvalQListOut,
    response_model_exclude_none=True,
    summary="T12 评测题",
)
async def list_eval_questions(
    session: SessionDep,
    user: CurrentUserDep,
    p: P15,
    group: str | None = None,
    last: EvalLast | None = None,  # type: ignore[valid-type]
    kw: str | None = None,
):
    repo = r.EvalQuestionRepo(session)
    rows, total = await repo.list_page(repo.list_query(group, last, _kw(kw)), p)
    counts = await repo.group_counts()
    groups = [GroupCountOut(group=g, n=counts.get(g, 0)) for g in catalog.load("EVAL_GROUPS")]
    return {**_page([svc.eval_q_out(q) for q in rows], total, p), "group_counts": groups}


@tuning.get(
    "/feedbacks",
    response_model=Page[FeedbackOut],
    response_model_exclude_none=True,
    summary="T13 用户反馈",
)
async def list_feedbacks(
    session: SessionDep,
    user: CurrentUserDep,
    p: P15,
    kw: str | None = None,
    status: FbStatus | None = None,
    fix_kind: Annotated[FixKind | None, Query(alias="fixKind")] = None,
):
    repo = r.FeedbackRepo(session)
    rows, total = await repo.list_page(repo.list_query(_kw(kw), status, fix_kind), p)
    codes = await repo.user_codes([f.user_id for f in rows if f.user_id])
    return _page([svc.feedback_out(f, codes.get(f.user_id)) for f in rows], total, p)


@tuning.get(
    "/feedbacks/{fid}",
    response_model=FeedbackDetailOut,
    response_model_exclude_none=True,
    summary="T14",
)
async def get_feedback(fid: str, session: SessionDep, user: CurrentUserDep):
    repo = r.FeedbackRepo(session)
    f = await repo.get_by_code_or_404(fid)
    codes = await repo.user_codes([f.user_id] if f.user_id else [])
    return await svc.feedback_detail(session, f, codes.get(f.user_id))


@tuning.get("/freq", response_model=Page[FreqOut], response_model_exclude_none=True, summary="T18 高频问题")
async def list_freq(
    session: SessionDep,
    user: CurrentUserDep,
    p: P15,
    period: Literal["7", "30"] = "30",
    cert: Literal["未认证", "已认证"] | None = None,
    kw: str | None = None,
):
    repo = r.FreqRepo(session)
    certified = None if cert is None else cert == "已认证"
    stmt, _ = repo.list_query(int(period), certified, _kw(kw))
    total = await r.count_of(session, stmt)
    rows = (await session.execute(stmt.offset(p.offset).limit(p.size))).all()
    return _page([svc.freq_out(x, rank) for x, rank in rows], total, p)


@tuning.get("/synonyms", response_model=list[SynonymOut], response_model_exclude_none=True, summary="T20")
async def list_synonyms(session: SessionDep, user: CurrentUserDep):
    return [
        SynonymOut(word=s.word, target=s.target, cr=s.cr_code) for s in await r.SynonymRepo(session).all()
    ]


router.include_router(tuning)
