"""问数（qa）只读接口第一批，挂在 /api/qa/v1（app/api.py）。字段形状对齐前端 mock。

版本快照（M3）：qa 不登记任何 SnapshotSectionBuilder——SECTION_OWNERS 里没有 qa 的 section，
qa 只订阅 X4 release.published（qa-api §8 X4、§9.2 qa_object_type_view、§12 #31）。
"""

from collections.abc import Callable
from typing import Annotated, Literal
from urllib.parse import quote

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Response
from sqlalchemy import func, select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from app.common.audit import audit
from app.common.auth import CurrentUserDep, require_func
from app.common.context import ViewContextDep, readonly_extra
from app.common.pagination import MAX_PAGE_SIZE, Page, PageParams
from app.core import clock
from app.core.errors import AppError, DuplicateKey, EmptyInput, ExportForbidden, NotFound
from app.core.state import InvalidTransition as StateInvalid
from app.db.dialects import get_adapter
from app.db.session import SessionDep
from app.modules.qa import catalog, chain
from app.modules.qa import repo as r
from app.modules.qa import service as svc
from app.modules.qa.eval import job as eval_job
from app.modules.qa.llm import LLMClient, get_llm
from app.modules.qa.models import (
    CertStatus,
    EvalLast,
    FbStatus,
    FixKind,
    PageSourceKind,
    PageStatus,
    PermRequestStatus,
    QaCert,
    QaCertVariant,
    QaEvalRun,
    QaFeedback,
    QaFreqQuestion,
    QaPermissionRequest,
    QaQueryLog,
    QaSession,
    QaSynonymLink,
    QaTemplate,
    QaTurn,
    RunStatus,
    TurnKind,
)
from app.modules.qa.schemas import (
    AskIn,
    AskOut,
    CertCreateIn,
    CertOut,
    CertToggleIn,
    CertVariantsIn,
    EvalQListOut,
    EvalRunDetailOut,
    EvalRunIn,
    EvalRunOut,
    FavoriteOut,
    FbCertOut,
    FbSynonymIn,
    FbSynonymOut,
    FeedbackDetailOut,
    FreqOut,
    GroupCountOut,
    MetricRefOut,
    ObjectTypeOut,
    OptionOut,
    PageFullOut,
    PageListOut,
    PermRequestIn,
    PermRequestOut,
    PlanOptionsOut,
    RecalcIn,
    SessionCreateIn,
    SessionDetailOut,
    SessionOptionsOut,
    SessionOut,
    SessionPatchIn,
    SynonymOut,
    TemplateOut,
    TMetricOut,
    TuningSummaryOut,
    TurnResultOut,
    VoteIn,
    VoteOut,
)

router = APIRouter(tags=["qa"])


class QaSqlFailed(AppError):
    """[推断] W6：LLM 生成的 SQL 执行失败（库报错 / statement_timeout）。"""

    status_code = 422
    code = "QA_SQL_FAILED"
    message = "生成的查询执行失败，请换个问法再试"


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
    """骨架 `PageParamsDep`（pagination._page_params）的同款，只是默认条数按接口不同
    （qa-api §2–§7：12 / 10 / 15，同前端各页 PAGE_SIZE）。page 不设下限：0、负数由 `PageParams`
    收成 1（CONVENTIONS §3）；size 照骨架校验 1–MAX_PAGE_SIZE。越界页码由 list_page / Page.of 收拢。"""

    def _dep(
        page: Annotated[int, Query()] = 1,
        size: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = default,
    ) -> PageParams:
        return PageParams(page=page, size=size)

    return _dep


P10 = Annotated[PageParams, Depends(page_dep(10))]
P12 = Annotated[PageParams, Depends(page_dep(12))]
P15 = Annotated[PageParams, Depends(page_dep(15))]

_RANGE_DAYS = {"today": 0, "7d": 6, "30d": 29}  # 同 QaSessionPanel：daysAgo <= N


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
    return Page.of([svc.session_out(s) for s in rows], total, p)


@router.get(
    "/sessions/{sid}", dependencies=_WB, response_model=SessionDetailOut, summary="W3 会话详情（全部轮次）"
)
async def get_session(sid: str, session: SessionDep, user: CurrentUserDep, ctx: ViewContextDep):
    pk, _ = await svc.effective_user(session, user, ctx)
    s = await r.SessionRepo(session).detail(sid)
    # [推断] 非拥有者打开分享会话需按打开人重算（第一批未实现），先按不存在处理
    if s is None or s.owner_id != pk:
        raise NotFound("会话不存在", details={"id": sid})
    return await _detail_out(session, s)


async def _detail_out(session, s: QaSession) -> SessionDetailOut:
    """W3 详情；越权轮次带上是否已申请（W16 待审批）。"""
    ids = [t.id for t in s.turns if t.kind == TurnKind.denied]
    applied: set[int] = set()
    if ids:
        stmt = select(QaPermissionRequest.source_turn_id).where(
            QaPermissionRequest.source_turn_id.in_(ids),
            QaPermissionRequest.status == PermRequestStatus.pending,
        )
        applied = {x for x in (await session.scalars(stmt)).all() if x is not None}
    return svc.session_detail_out(s, applied)


async def _own_session(session, pk: int, sid: str) -> QaSession:
    s = await r.SessionRepo(session).detail(sid)
    if s is None or s.owner_id != pk:
        raise NotFound("会话不存在", details={"id": sid})
    return s


async def _caliber(session: AsyncSession, label: str | None) -> str | None:
    return svc.check_caliber(label, await svc.variant_index(session))


@router.post(
    "/sessions", dependencies=_WB, status_code=201, response_model=SessionDetailOut, summary="W2 新建会话"
)
async def create_session(
    body: SessionCreateIn, session: SessionDep, user: CurrentUserDep, ctx: ViewContextDep
):
    pk, _ = await svc.effective_user(session, user, ctx)
    codes = (await session.scalars(select(QaSession.code).where(QaSession.code.startswith("S")))).all()
    nums = [int(c[1:]) for c in codes if c[1:].isdigit()]
    s = QaSession(
        code=f"S{(max(nums) if nums else 0) + 1:04d}",  # [推断] 会话号 = S + 最大序号 + 1（同 seed S0001…）
        owner_id=pk,
        title="新会话",  # 同 useQaSession.newSession；第一问后改成问题前 20 字（W6）
        scope=body.scope,
        caliber=await _caliber(session, body.caliber),
        last_at=clock.now(),
    )
    session.add(s)
    await session.flush()
    return await _detail_out(session, await _own_session(session, pk, s.code))


@router.patch(
    "/sessions/{sid}", dependencies=_WB, response_model=SessionDetailOut, summary="W4 重命名 / 改范围口径"
)
async def patch_session(
    sid: str, body: SessionPatchIn, session: SessionDep, user: CurrentUserDep, ctx: ViewContextDep
):
    pk, _ = await svc.effective_user(session, user, ctx)
    s = await _own_session(session, pk, sid)
    if body.title is not None and body.title.strip():
        s.title = body.title.strip()  # 同 QaSessionPanel.saveRename：trim 后为空不改
    scope = body.scope if body.scope is not None else s.scope
    caliber = await _caliber(session, body.caliber) if "caliber" in body.model_fields_set else s.caliber
    if scope != s.scope or caliber != s.caliber:
        s.scope, s.caliber = scope, caliber
        seq = (await session.scalar(select(func.max(QaTurn.seq)).where(QaTurn.session_id == s.id)) or 0) + 1
        now = clock.now()
        # 同 Workbench.vue tips：追加系统轮次，之前的答案保留各自依据
        session.add(
            QaTurn(
                code=f"{s.code}-T{seq:03d}",
                session_id=s.id,
                seq=seq,
                kind=TurnKind.system,
                snapshot={"text": f"从这里开始按 大区 = {scope}、口径 = {caliber or '不限'} 回答"},
                asked_at=now,
            )
        )
        s.last_at = now
    await session.flush()
    session.expire(s)
    return await _detail_out(session, await _own_session(session, pk, sid))


@router.delete("/sessions/{sid}", dependencies=_WB, status_code=204, summary="W5 删除会话")
async def delete_session(sid: str, session: SessionDep, user: CurrentUserDep, ctx: ViewContextDep):
    pk, _ = await svc.effective_user(session, user, ctx)
    await session.delete(await _own_session(session, pk, sid))  # 轮次级联删；页面卡片不受影响
    await session.flush()
    return Response(status_code=204)


@router.post(
    "/sessions/{sid}/turns",
    dependencies=_WB,
    status_code=202,
    response_model=AskOut,
    summary="W6 提问（同步跑最小 NL2SQL 链路）",
)
async def ask(
    sid: str,
    body: AskIn,
    session: SessionDep,
    user: CurrentUserDep,
    ctx: ViewContextDep,
    llm: Annotated[LLMClient, Depends(get_llm)],
    conn: Annotated[AsyncConnection, Depends(chain.get_qa_conn)],
):
    pk, _ = await svc.effective_user(session, user, ctx)
    s = await r.SessionRepo(session).detail(sid)
    if s is None or s.owner_id != pk:
        raise NotFound("会话不存在", details={"id": sid})
    question = body.question.strip()
    try:
        lines = svc.caliber_lines(s.caliber, await svc.variant_index(session)) if s.caliber else []
        kind, snapshot, rows, elapsed, sql = await chain.answer(
            session, conn, llm, user, question, caliber_lines=lines
        )
    except DBAPIError as e:  # 生成的 SQL 过了网关但执行出错（列不存在、超时等）：不落轮次，前端提示换个问法
        raise QaSqlFailed(details={"reason": type(e.orig).__name__ if e.orig else type(e).__name__}) from e
    seq = (await session.scalar(select(func.max(QaTurn.seq)).where(QaTurn.session_id == s.id)) or 0) + 1
    now = clock.now()
    turn = QaTurn(
        code=f"{s.code}-T{seq:03d}",  # [推断] 轮次编号 = 会话号-T序号
        session_id=s.id,
        seq=seq,
        kind=TurnKind(kind),
        question=question,
        refs=body.refs,
        snapshot=snapshot,
        sql_text=sql,
        asked_at=now,
        elapsed_ms=elapsed,
    )
    session.add(turn)
    s.last_at = now
    if seq == 1:
        s.title = question[:20]  # 第一问把会话标题设成问题前 20 字（§10）
    await session.flush()
    session.add(
        QaQueryLog(
            turn_id=turn.id,
            user_id=pk,
            question=question,
            norm_question=question.lower(),
            asked_at=now,
            elapsed_ms=elapsed,
            row_count=rows,
            kind=kind,
        )
    )
    await session.flush()
    return AskOut(
        turn_id=turn.code, stream_url=f"/api/qa/v1/turns/{turn.code}/stream", turn=svc.turn_out(turn)
    )


@router.get("/favorites", dependencies=_WB, response_model=Page[FavoriteOut], summary="W13 常用问题")
async def list_favorites(
    session: SessionDep, user: CurrentUserDep, ctx: ViewContextDep, p: P10, q: str | None = None
):
    pk, _ = await svc.effective_user(session, user, ctx)
    repo = r.FavoriteRepo(session)
    rows, total = await repo.list_page(repo.list_query(pk, _kw(q)), p)
    return Page.of([svc.favorite_out(f) for f in rows], total, p)


@router.delete("/favorites/{fid}", dependencies=_WB, status_code=204, summary="W15 移出常用问题")
async def delete_favorite(fid: str, session: SessionDep, user: CurrentUserDep, ctx: ViewContextDep):
    pk, _ = await svc.effective_user(session, user, ctx)
    f = await r.FavoriteRepo(session).get_by_code(fid)
    if f is None or f.owner_id != pk:
        raise NotFound("常用问题不存在", details={"id": fid})
    await session.delete(f)
    await session.flush()
    return Response(status_code=204)


async def _own_turn(session, pk: int, tid: str) -> QaTurn:
    """轮次必须在有效用户自己的会话里；否则 404（不暴露存在性）[推断]。"""
    t = await session.scalar(
        select(QaTurn)
        .join(QaSession, QaTurn.session_id == QaSession.id)
        .where(QaTurn.code == tid, QaSession.owner_id == pk)
    )
    if t is None:
        raise NotFound("轮次不存在", details={"id": tid})
    return t


class TurnNotApplicable(AppError):
    status_code, code, message = 422, "VALIDATION_FAILED", "这一轮不支持该操作"


def _answer(t: QaTurn) -> dict:
    a = (t.snapshot or {}).get("answer")
    return a if isinstance(a, dict) else {}


@router.post("/turns/{tid}:recalc", dependencies=_WB, summary="W9 切换口径")
async def recalc_turn(
    tid: str, body: RecalcIn, session: SessionDep, user: CurrentUserDep, ctx: ViewContextDep
):
    # [推断] 第一期只有示例主回答卡（card=main）有澄清条：同一份语义计划只换指标，
    # 数值按前端 QaAnswer 同一公式（含税 = 不含税 × (1+税率)）由前端换算；
    # 后台记成这一轮的变体并返回该轮 Answer
    pk, _ = await svc.effective_user(session, user, ctx)
    t = await _own_turn(session, pk, tid)
    if t.kind != TurnKind.answer or _answer(t).get("card") != "main":
        raise TurnNotApplicable(details={"id": tid, "reason": "只有带澄清条的回答能切换口径"})
    t.caliber_variant = body.caliber
    await session.flush()
    return svc.turn_out(t)["answer"]


@router.put("/turns/{tid}/vote", dependencies=_WB, response_model=VoteOut, summary="W12 赞踩")
async def vote_turn(tid: str, body: VoteIn, session: SessionDep, user: CurrentUserDep, ctx: ViewContextDep):
    pk, u = await svc.effective_user(session, user, ctx)
    t = await _own_turn(session, pk, tid)
    if t.kind != TurnKind.answer:
        raise TurnNotApplicable(details={"id": tid, "reason": "只有回答能赞踩"})
    old, new = t.vote or "", body.vote
    if old == "down" and new != "down":
        # [推断] 取消踩：撤掉这一轮还没被处理（待处理）的那条反馈；已处理的保留
        for f in (
            await session.scalars(
                select(QaFeedback).where(QaFeedback.turn_id == t.id, QaFeedback.status == FbStatus.open)
            )
        ).all():
            await session.delete(f)
    if new == "down" and old != "down":
        codes = (await session.scalars(select(QaFeedback.code))).all()
        nums = [int(c[3:]) for c in codes if c.startswith("FB-") and c[3:].isdigit()]
        a = _answer(t)
        question = t.question or ""
        session.add(
            QaFeedback(
                code=f"FB-{(max(nums) if nums else 0) + 1}",  # [推断] 接着种子 FB-248 往后编
                turn_id=t.id,
                fed_back_at=clock.now(),
                user_id=pk,
                user_name=u.name,
                question=question,
                # [推断] 回答摘要：链路回答取 text，示例卡取指标名
                answer=a.get("text")
                or a.get("metric")
                or f"{a.get('period', '')} {a.get('region', '')}".strip(),
                reason="",  # 前端踩没有填原因的入口
                fix_kind=FixKind.cert,  # [推断] 默认建议转认证问答，调优页可改
                fix_text=question[:64],
                status=FbStatus.open,
            )
        )
    t.vote = new
    await session.flush()
    return VoteOut(id=t.code, vote=t.vote)


@router.get("/turns/{tid}/result", dependencies=_WB, response_model=TurnResultOut, summary="W10 结果表翻页")
async def turn_result(tid: str, session: SessionDep, user: CurrentUserDep, ctx: ViewContextDep, p: P10):
    pk, _ = await svc.effective_user(session, user, ctx)
    t = await _own_turn(session, pk, tid)
    res = svc.turn_result(t)
    if res is None:
        raise TurnNotApplicable(details={"id": tid, "reason": "这一轮没有结果表"})
    cols, rows, summary = res
    c = p.clamp(len(rows))
    return TurnResultOut.of(rows[c.offset : c.offset + c.size], len(rows), p, columns=cols, summary=summary)


_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@router.get("/turns/{tid}/export", dependencies=_WB, summary="W11 导出结果")
async def export_turn(
    tid: str,
    session: SessionDep,
    user: CurrentUserDep,
    ctx: ViewContextDep,
    format: Literal["xlsx", "csv"] = "xlsx",
):
    if ctx.preview_as:
        raise ExportForbidden(details={"reason": "预览中不能导出"})  # [推断] 第 12 节 #2
    pk, _ = await svc.effective_user(session, user, ctx)
    t = await _own_turn(session, pk, tid)
    res = svc.turn_result(t)
    if res is None:
        raise TurnNotApplicable(details={"id": tid, "reason": "这一轮没有结果表"})
    cols, rows, _ = res
    base = (_answer(t).get("exportFile") or f"{t.code}.xlsx").rsplit(".", 1)[0]
    if format == "csv":
        data, mime = svc.export_csv(cols, rows), "text/csv; charset=utf-8"
    else:
        data, mime = svc.export_xlsx(cols, rows), _XLSX
    name = f"{base}.{format}"
    detail = {"format": format, "rows": len(rows)}
    await audit(session, user, action="qa.turn.export", target=t.code, detail=detail)
    return Response(
        content=data,
        media_type=mime,
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}"},
    )


@router.post(
    "/permission-requests",
    dependencies=_WB,
    status_code=201,
    response_model=PermRequestOut,
    summary="W16 申请数据权限",
    openapi_extra=readonly_extra("history", "preview"),  # 只读白名单（1.1）
)
async def create_permission_request(
    body: PermRequestIn, session: SessionDep, user: CurrentUserDep, ctx: ViewContextDep
):
    # [推断] 申请人记有效用户（预览时 = 被预览人，跟轮次归属一致）；第 12 节 #4 待定
    pk, _ = await svc.effective_user(session, user, ctx)
    turn_pk = None
    if body.source_turn_id:
        t = await session.scalar(
            select(QaTurn)
            .join(QaSession, QaTurn.session_id == QaSession.id)
            .where(QaTurn.code == body.source_turn_id, QaSession.owner_id == pk)
        )
        if t is None:
            raise NotFound("轮次不存在", details={"id": body.source_turn_id})
        turn_pk = t.id
    codes = (await session.scalars(select(QaPermissionRequest.code))).all()
    nums = [int(c[2:]) for c in codes if c.startswith("AP") and c[2:].isdigit()]
    req = QaPermissionRequest(
        code=f"AP{(max(nums) if nums else 0) + 1:04d}",  # [推断] 申请号 AP0001…
        requester_id=pk,
        scope=body.scope,
        object_label=body.object,
        reason=body.reason.strip(),
        duration_days=body.duration,
        source_turn_id=turn_pk,
        approver=catalog.load("QA_APPLY").get("approver"),  # [推断] 第一期固定路由给 QA_APPLY.approver
        status=PermRequestStatus.pending,
    )
    session.add(req)
    await session.flush()
    return PermRequestOut(id=req.code, status=req.status.value, approver=req.approver)


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
    return PageListOut.of(items, total, p, from_workbench_count=wb_total)


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
    return Page.of([svc.template_out(t) for t in rows], total, p)


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
    return Page.of([svc.cert_out(c) for c in rows], total, p)


@tuning.get("/certs/{cid}", response_model=CertOut, response_model_exclude_none=True, summary="T4")
async def get_cert(cid: str, session: SessionDep, user: CurrentUserDep):
    return svc.cert_out(await r.CertRepo(session).get_by_code_or_404(cid))


def _pending_crs() -> set[str]:
    # T_METRICS 里的 cr 就是影响该指标、还没发布的变更单（同 T2）
    return {m["cr"] for m in catalog.t_metrics().values() if m.get("cr")}


def _metric_of(key: str) -> dict:
    m = catalog.t_metrics().get(key)
    if m is None:
        raise CertBadPlan(details={"metric": key})
    return m


class CertBadPlan(AppError):
    status_code, code, message = 422, "VALIDATION_FAILED", "语义计划里的指标不存在"


async def _new_cert(session, question: str, plan: dict, source: str, **kw) -> QaCert:
    if await session.scalar(select(QaCert.id).where(func.lower(QaCert.question) == question.lower())):
        raise DuplicateKey(f"已有认证问答「{question}」", details={"question": question})
    m = _metric_of(plan["metric"])
    codes = (await session.scalars(select(QaCert.code))).all()
    nums = [int(c[3:]) for c in codes if c.startswith("CQ-") and c[3:].isdigit()]
    c = QaCert(
        code=f"CQ-{(max(nums) if nums else 0) + 1:03d}",  # 同前端 addCert：CQ-最大号+1
        question=question,
        sem_plan=plan,
        metric_code=catalog.metric_code_of_key(plan["metric"]),
        metric_version=int(m["version"].lstrip("v") or 0),
        source=source,
        variants=[],
        **kw,
    )
    session.add(c)
    await session.flush()
    # 同前端 addCert：同一问题的高频问题挂上这条认证问答
    for f in (await session.scalars(select(QaFreqQuestion).where(QaFreqQuestion.question == question))).all():
        f.cert_id = c.id
    await session.flush()
    await session.refresh(c, ["variants"])
    return c


_CERT = {"response_model": CertOut, "response_model_exclude_none": True}


@tuning.post("/certs", status_code=201, **_CERT, summary="T5 新建认证问答")
async def create_cert(body: CertCreateIn, session: SessionDep, user: CurrentUserDep):
    q = body.question.strip()
    if not q:
        raise EmptyInput("问题不能为空")
    plan = body.plan.model_dump(exclude_none=True)
    m = _metric_of(plan["metric"])
    cr = m.get("cr") if m.get("cr") in _pending_crs() else None
    c = await _new_cert(
        session,
        q,
        plan,
        "手动新建",
        status=CertStatus.pending,  # 新建只登记，复核后点「认证」才生效
        cr_code=cr,
        note=f"{m['name']}受 {cr} 影响（未发布），认证后发布还需按新口径重新认证"
        if cr
        else "新建问答，复核语义计划后点“认证”",
    )
    await audit(session, user, action="qa.cert.create", target=c.code)
    return svc.cert_out(c)


def _certify(c: QaCert, name: str) -> None:
    m = catalog.t_metrics().get(c.sem_plan.get("metric"), {})
    pending = c.cr_code if c.cr_code in _pending_crs() else None
    c.certifier, c.certified_at = name, clock.now()
    if pending:
        c.status = CertStatus.recert
        c.note = f"已按 {m.get('version', '')} 认证；{pending} 发布后需再次认证"
    else:
        c.status, c.cr_code, c.note = CertStatus.certified, None, None


@tuning.post("/certs/{cid}:certify", **_CERT, summary="T6 认证")
async def certify_cert(cid: str, session: SessionDep, user: CurrentUserDep, ctx: ViewContextDep):
    _, u = await svc.effective_user(session, user, ctx)
    c = await r.CertRepo(session).get_by_code_or_404(cid)
    if c.status not in (CertStatus.pending, CertStatus.recert):
        raise StateInvalid(details={"id": cid, "status": c.status.value})
    _certify(c, u.name)
    await session.flush()
    await audit(session, user, action="qa.cert.certify", target=c.code)
    return svc.cert_out(c)


@tuning.patch("/certs/{cid}", **_CERT, summary="T7 停用 / 启用")
async def toggle_cert(cid: str, body: CertToggleIn, session: SessionDep, user: CurrentUserDep):
    c = await r.CertRepo(session).get_by_code_or_404(cid)
    # 照前端 CertTab.toggle：停用 → 已停用；启用一律 → 已认证（第 12 节待定）
    c.status = CertStatus.certified if body.enabled else CertStatus.disabled
    await session.flush()
    await audit(session, user, action="qa.cert.toggle", target=c.code, detail={"enabled": body.enabled})
    return svc.cert_out(c)


@tuning.put("/certs/{cid}/variants", **_CERT, summary="T8 同问法")
async def put_variants(cid: str, body: CertVariantsIn, session: SessionDep, user: CurrentUserDep):
    c = await r.CertRepo(session).get_by_code_or_404(cid)
    want = [v.strip() for v in body.variants if v.strip()]
    if len({v.lower() for v in want}) != len(want):
        raise DuplicateKey("已有相同问法", details={"variants": want})
    taken = (
        await session.scalars(
            select(QaCertVariant.question).where(
                QaCertVariant.cert_id != c.id,
                func.lower(QaCertVariant.question).in_([v.lower() for v in want]),
            )
        )
    ).all()
    if taken:
        raise DuplicateKey("同问法已挂在别的认证问答上", details={"variants": list(taken)})
    keep = set(want)
    for v in list(c.variants):
        if v.question not in keep:
            c.variants.remove(v)
    await session.flush()
    have = {v.question for v in c.variants}
    for v in want:
        if v not in have:
            c.variants.append(QaCertVariant(question=v))
    await session.flush()
    await session.refresh(c, ["variants"])
    await audit(session, user, action="qa.cert.variants", target=c.code)
    return svc.cert_out(c)


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
    return Page.of([svc.run_out(x, counts.get(x.id, 0)) for x in rows], total, p)


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
    return EvalQListOut.of([svc.eval_q_out(q) for q in rows], total, p, group_counts=groups)


@tuning.get(
    "/feedbacks",
    response_model=Page[FeedbackDetailOut],  # [推断] 行内按钮要 certBlock / coveredBy，列表直接给详情字段
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
    return Page.of([await svc.feedback_detail(session, f, codes.get(f.user_id)) for f in rows], total, p)


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
    c = p.clamp(total)  # 先数 total 再按收拢后的页码取（越界 → 最后一页，CONVENTIONS §3）
    rows = (await session.execute(stmt.offset(c.offset).limit(c.size))).all()
    return Page.of([svc.freq_out(x, rank) for x, rank in rows], total, p)


@tuning.post("/freq/{fid}:certify", **_CERT, summary="T19 高频问题设为认证问答")
async def certify_freq(fid: str, session: SessionDep, user: CurrentUserDep, ctx: ViewContextDep):
    _, u = await svc.effective_user(session, user, ctx)
    f = await r.FreqRepo(session).get_by_code_or_404(fid)
    if f.cert_id is not None:
        raise StateInvalid(details={"id": fid, "cert": f.cert.code if f.cert else None})
    m = _metric_of(f.sem_plan.get("metric"))
    cr = m.get("cr") if m.get("cr") in _pending_crs() else None
    c = await _new_cert(
        session, f.question, f.sem_plan, "高频问题推荐", hits=f.hits30, cr_code=cr, status=CertStatus.pending
    )
    _certify(c, u.name)  # 同 T6：有未发布 CR → 待重新认证
    await session.flush()
    await audit(session, user, action="qa.freq.certify", target=c.code, detail={"freq": fid})
    return svc.cert_out(c)


@tuning.get("/synonyms", response_model=list[SynonymOut], response_model_exclude_none=True, summary="T20")
async def list_synonyms(session: SessionDep, user: CurrentUserDep):
    return [
        SynonymOut(word=s.word, target=s.target, cr=s.cr_code) for s in await r.SynonymRepo(session).all()
    ]


# ---------------------------------------------------------------- T11 运行评测 / T15–T17 反馈处理（写）

_TM = [Depends(require_func("qa-tuning", "manage"))]


class EvalGroupInvalid(AppError):
    status_code, code, message = 422, "VALIDATION_FAILED", "评测分组不存在"


async def next_run_code(session: AsyncSession) -> str:
    """ER-最大号+1（同前端 startRun）。并发安全：先拿事务级咨询锁串行取号，锁持有到本事务结束，
    后到的请求要等前一个提交（或回滚）后才读最大号；`code` 唯一约束兜底。"""
    await get_adapter(session).xact_lock(session, "qa.eval_run.code")
    codes = (await session.scalars(select(QaEvalRun.code))).all()
    nums = [int(c[3:]) for c in codes if c.startswith("ER-") and c[3:].isdigit()]
    return f"ER-{(max(nums) if nums else 0) + 1:04d}"


@tuning.post(
    "/eval/runs",
    status_code=202,
    dependencies=_TM,
    response_model=EvalRunOut,
    response_model_exclude_none=True,
    summary="T11 运行评测（后台逐题比对，不改口径）",
)
async def start_run(
    body: EvalRunIn,
    session: SessionDep,
    user: CurrentUserDep,
    ctx: ViewContextDep,
    llm: Annotated[LLMClient, Depends(get_llm)],
    runner: Annotated[Callable, Depends(eval_job.get_runner)],
    tasks: BackgroundTasks,
):
    _, u = await svc.effective_user(session, user, ctx)
    group = None if body.group in eval_job.ALL else body.group
    if group is not None and group not in catalog.load("EVAL_GROUPS"):
        raise EvalGroupInvalid(details={"group": body.group})
    n = int(
        await session.scalar(select(func.count()).select_from(eval_job.questions_query(group).subquery()))
        or 0
    )
    run = QaEvalRun(
        code=await next_run_code(session),
        run_at=clock.now(),
        trigger_label="手动运行",
        version_label=catalog.load("CUR_VERSION"),
        scope=f"全部 {n} 题" if group is None else f"{group}相关 {n} 题",
        total=n,
        passed=0,
        status=RunStatus.running,
        operator=u.name,
    )
    session.add(run)
    await session.flush()
    await audit(session, user, action="qa.eval.run", target=run.code, detail={"group": body.group})

    async def _go() -> None:  # 响应前 get_session 已提交，后台任务一定读得到这条运行
        runner(run.code, group, llm, user)

    tasks.add_task(_go)
    return svc.run_out(run, 0)


async def _fb(session, fid: str) -> QaFeedback:
    return await r.FeedbackRepo(session).get_by_code_or_404(fid)


async def _fb_out(session, f: QaFeedback) -> FeedbackDetailOut:
    codes = await r.FeedbackRepo(session).user_codes([f.user_id] if f.user_id else [])
    return await svc.feedback_detail(session, f, codes.get(f.user_id))


def _need_open(f: QaFeedback, *allowed: FbStatus) -> None:
    if f.status not in (allowed or (FbStatus.open,)):
        raise StateInvalid(details={"id": f.code, "status": f.status.value})


async def _syn_to_cr(session: AsyncSession, user, f: QaFeedback, body: FbSynonymIn):
    """T15 词还没在变更单里：走静态 X3——新建草稿（或加入 body.join 指定的草稿），目标 {kind:'knowledge'}，
    同义词作为 synonyms 段的一行 add（保留该段已有行）。目标知识按 fix.target 去掉版本号后的名称找；
    找不到 → isNew，id 由 qa 给 [推断]。
    别的单子已占用该知识 → 静态 409 TARGET_IN_OTHER_CR（details.crId）原样返回。"""
    import re

    from app.modules.static.cr_write import CrWriteService
    from app.modules.static.models import ChangeRequest, CrItem, Knowledge
    from app.modules.static.schemas import CRCreateIn, ItemIn, ItemsPutIn, TargetIn

    name = re.sub(r"\s*v\d+$", "", (f.fix_target or f.fix_text).strip()) or f.fix_text
    k = await session.scalar(select(Knowledge).where(Knowledge.name == name).limit(1))
    t = (
        TargetIn(kind="knowledge", id=k.code, name=k.name)
        if k is not None
        else TargetIn(kind="knowledge", id=f"K-{f.code}"[:64], is_new=True, name=name)
    )
    w = CrWriteService(session)
    if body.join:
        code = await w.add_target(body.join, user, t)
    else:
        title = (body.title or f"补同义词：{f.fix_text}")[:40]
        code = await w.create(
            user, CRCreateIn(title=title, reason=f"问数反馈 {f.code}：{f.reason}", target=t)
        )
    cr_id = await session.scalar(select(ChangeRequest.id).where(ChangeRequest.code == code))
    old = (
        await session.scalars(
            select(CrItem)
            .where(
                CrItem.cr_id == cr_id,
                CrItem.target_kind == "knowledge",
                CrItem.target_id == t.id,
                CrItem.section == "synonyms",
            )
            .order_by(CrItem.seq, CrItem.id)
        )
    ).all()
    rows = [ItemIn(key=i.item_key, op=i.op, before=i.before, after=i.after) for i in old]
    if f.fix_text not in {i.key for i in rows}:
        rows.append(ItemIn(key=f.fix_text, op="add", after=f.fix_text))
    await w.put_items(code, user, ItemsPutIn(target=t, section="synonyms", rows=rows))
    syn = QaSynonymLink(word=f.fix_text, target=name, cr_code=code)
    session.add(syn)
    await session.flush()
    return syn


@tuning.post(
    "/feedbacks/{fid}:to-synonym",
    dependencies=_TM,
    response_model=FbSynonymOut,
    response_model_exclude_none=True,
    summary="T15 转为同义词（已在变更单里 → 关联；否则新建 / 加入草稿变更单）",
)
async def feedback_to_synonym(
    fid: str, body: FbSynonymIn, session: SessionDep, user: CurrentUserDep, ctx: ViewContextDep
):
    _, u = await svc.effective_user(session, user, ctx)
    f = await _fb(session, fid)
    _need_open(f)
    syn = await r.SynonymRepo(session).by_word(f.fix_text)
    if syn is None or not syn.cr_code:
        syn = await _syn_to_cr(session, user, f, body)
    st = (
        FbStatus.await_release
        if await svc.cr_status(session, syn.cr_code) == "已通过"
        else FbStatus.to_synonym
    )
    same = (
        await session.scalars(
            select(QaFeedback).where(
                QaFeedback.status == FbStatus.open,
                QaFeedback.fix_kind == FixKind.synonym,
                QaFeedback.fix_text == f.fix_text,
            )
        )
    ).all()
    targets = {x.id: x for x in [f, *same]}.values()  # 同一个词的待处理反馈一起改
    for x in targets:
        x.status, x.resolved_by, x.cr_code, x.handler = st, syn.cr_code, syn.cr_code, u.name
    await session.flush()
    affected = sorted(x.code for x in targets)
    await audit(
        session,
        user,
        action="qa.feedback.to_synonym",
        target=f.code,
        detail={"cr": syn.cr_code, "affected": affected},
    )
    return FbSynonymOut(cr_id=syn.cr_code, affected=affected, feedback=await _fb_out(session, f))


class CertNoPlan(AppError):
    status_code, code, message = 422, "CERT_NO_PLAN", "还没有可用的语义计划"


@tuning.post(
    "/feedbacks/{fid}:to-cert",
    dependencies=_TM,
    response_model=FbCertOut,
    response_model_exclude_none=True,
    summary="T16 转为认证问答（已有 → 加同问法；否则按语义计划新建并认证）",
)
async def feedback_to_cert(fid: str, session: SessionDep, user: CurrentUserDep, ctx: ViewContextDep):
    _, u = await svc.effective_user(session, user, ctx)
    f = await _fb(session, fid)
    _need_open(f)
    existing, plan = await svc.feedback_plan(session, f)
    if existing is not None:
        c, mode = existing, "variant"
    elif plan:
        m = _metric_of(plan.get("metric"))
        cr = m.get("cr") if m.get("cr") in _pending_crs() else None
        c = await _new_cert(session, f.fix_text, plan, "反馈纠错", cr_code=cr, status=CertStatus.pending)
        _certify(c, u.name)  # [推断] 同前端 toCert / T19：确认卡「认证」即认证；有未发布 CR → 待重新认证
        mode = "new"
    else:
        raise CertNoPlan(details={"id": fid, "question": f.fix_text})
    added: QaCertVariant | None = None
    have = {v.question.lower() for v in c.variants}
    if f.question != c.question and f.question.lower() not in have and len(f.question) <= 40:
        taken = await session.scalar(
            select(QaCertVariant.id).where(func.lower(QaCertVariant.question) == f.question.lower())
        )
        if taken is None:  # 同问法全局唯一：已挂在别处就不加
            added = QaCertVariant(question=f.question)
            c.variants.append(added)
    f.status, f.cert_id, f.resolved_by, f.handler = FbStatus.to_cert, c.id, c.code, u.name
    await session.flush()
    f.cert_variant_id = added.id if added is not None else None  # 记下来，T17 重开时撤销
    await audit(
        session, user, action="qa.feedback.to_cert", target=f.code, detail={"cert": c.code, "mode": mode}
    )
    return FbCertOut(cert_id=c.code, mode=mode, feedback=await _fb_out(session, f))


@tuning.post(
    "/feedbacks/{fid}:ignore",
    dependencies=_TM,
    response_model=FeedbackDetailOut,
    response_model_exclude_none=True,
    summary="T17 忽略",
)
async def feedback_ignore(fid: str, session: SessionDep, user: CurrentUserDep, ctx: ViewContextDep):
    _, u = await svc.effective_user(session, user, ctx)
    f = await _fb(session, fid)
    _need_open(f)
    f.status, f.handler = FbStatus.ignored, u.name
    await session.flush()
    await audit(session, user, action="qa.feedback.ignore", target=f.code)
    return await _fb_out(session, f)


@tuning.post(
    "/feedbacks/{fid}:reopen",
    dependencies=_TM,
    response_model=FeedbackDetailOut,
    response_model_exclude_none=True,
    summary="T17 重新打开",
)
async def feedback_reopen(fid: str, session: SessionDep, user: CurrentUserDep):
    """`已忽略` / `已转认证问答` → `待处理`。

    [推断] 前端只在 `已忽略` 行给「重新打开」；后端也接受 `已转认证问答`，用来撤销误转：
    删掉 T16 加上的同问法（`cert_variant_id`，被 T8 删过就跳过）。T16 新建的认证问答**保留**，
    同前端 mock `reopen` 只改反馈、不动认证问答（mock 没写这种情况）。
    """
    f = await _fb(session, fid)
    _need_open(f, FbStatus.ignored, FbStatus.to_cert)
    if f.cert_variant_id is not None:
        v = await session.get(QaCertVariant, f.cert_variant_id)
        f.cert_variant_id = None
        if v is not None:
            await session.delete(v)
    f.status, f.handler, f.resolved_by, f.cr_code, f.cert_id = FbStatus.open, None, None, None, None
    await session.flush()
    await audit(session, user, action="qa.feedback.reopen", target=f.code)
    return await _fb_out(session, f)


router.include_router(tuning)

# R2 我的页面写接口 P2、P4–P10（qa-pages 流，独立文件，见 pages_api.py）
from app.modules.qa.pages_api import pages as _pages_router  # noqa: E402

router.include_router(_pages_router)

# R3/R4 对象搜索 / 详情 O2–O7、O9（qa-pages 流，独立文件，见 objects_api.py）
from app.modules.qa.objects_api import objects as _objects_router  # noqa: E402

router.include_router(_objects_router)

# 通用对象搜索 / 详情（元数据驱动，独立文件，见 explore_api.py 与 generic-object-explorer.md）
from app.modules.qa.explore_api import explore as _explore_router  # noqa: E402

router.include_router(_explore_router)
