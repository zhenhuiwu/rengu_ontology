"""R2 我的页面写接口 P2、P4–P10（qa-api §4），挂在 qa router 上（router.py 末尾 include）。

单独成文件是为了跟 qa-tuning 流的 router.py 改动不冲突；读接口 P1 / P3 / P11 / P12 仍在 router.py。
权限 [推断]：拥有者可做全部；共享成员「可编辑」可重命名 / 刷新 / 调整卡片 / 改刷新与订阅；
删除和改共享只有拥有者。其他人（含只读成员）对看得到的页面返回 403 NO_PERMISSION，看不到的 404。
"""

import csv
import io
import re
from copy import deepcopy
from datetime import datetime, timedelta
from typing import Any, Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, Query, Response
from pydantic import Field
from sqlalchemy import func, or_, select

from app.common.audit import audit
from app.common.auth import CurrentUserDep, require_func
from app.common.context import ViewContextDep, check_writable
from app.common.ontology import require_ontology_id
from app.common.pagination import PageParams
from app.core import clock
from app.core.errors import AppError, DuplicateKey, EmptyInput, Forbidden, NotFound, VersionConflict
from app.core.schema import ApiModel, _tz
from app.db.dialects import get_adapter
from app.db.session import SessionDep
from app.modules.admin.models import SysRole, SysRoleMember, SysUser, UserStatus
from app.modules.ontology.models import OntologyMember
from app.modules.qa import page_execution as execution
from app.modules.qa import repo as r
from app.modules.qa import service as svc
from app.modules.qa.models import (
    CardViz,
    PageSourceKind,
    PageStatus,
    QaPage,
    QaPageCard,
    QaPageShareMember,
    QaPageSubscription,
    QaSession,
    QaTemplate,
    QaTemplateCard,
    QaTurn,
    ShareMode,
    SharePerm,
    SubChannel,
    SubWhen,
    TurnKind,
    TurnStatus,
)
from app.modules.qa.schemas import PageFullOut, ShareOut, SubOut

pages = APIRouter(dependencies=[Depends(require_func("qa-pages", "view"))])

DEFAULT_REFRESH = {"freq": "手动", "time": "--", "onFail": "保留上次结果"}

# ---------------------------------------------------------------- 入参 / 出参


class RefreshPlanIn(ApiModel):
    freq: Literal["每小时", "每天", "每周", "每月", "手动"]
    time: str = Field(max_length=8)
    weekday: str | None = None
    monthday: int | None = Field(default=None, ge=1, le=31)
    on_fail: Literal["保留上次结果", "标记失败并通知"] = "保留上次结果"

    def plan(self) -> dict[str, Any]:
        out: dict[str, Any] = {"freq": self.freq, "time": self.time}
        if self.weekday is not None:
            out["weekday"] = self.weekday
        if self.monthday is not None:
            out["monthday"] = self.monthday
        out["onFail"] = self.on_fail
        return out


class TplRef(ApiModel):
    id: str
    caliber: str = ""
    refresh: RefreshPlanIn | None = None


class PageCreateIn(ApiModel):
    name: str = Field(min_length=1, max_length=30)
    scope: str | None = Field(default=None, max_length=60)
    template: TplRef | None = None
    source_session_id: str | None = Field(default=None, min_length=1, max_length=64)


class PageRenameIn(ApiModel):
    name: str = Field(min_length=1, max_length=30)


class RefreshOut(ApiModel):
    last_refresh: str | None = None
    data_as_of: str | None = None
    status: str
    status_note: str | None = None


class PinIn(ApiModel):
    turn_id: str
    table_id: str | None = None
    chart_id: str | None = None
    title: str = Field(min_length=1, max_length=60)


class CardsOrderIn(ApiModel):
    order: list[str]


class CardsOut(ApiModel):
    cards: list[dict[str, Any]]
    removed: list[str]


class SubIn(ApiModel):
    on: bool
    channel: Literal["邮件", "企业微信", "站内信"]
    receivers: list[str] = []
    when: Literal["每次刷新后", "每周一 09:00", "每天 09:00"]
    only_changed: bool = False


class ScheduleIn(ApiModel):
    refresh: RefreshPlanIn
    sub: SubIn


class ScheduleOut(ApiModel):
    refresh: dict[str, Any]
    sub: SubOut
    next_run: str


class MemberIn(ApiModel):
    user_id: str | None = Field(default=None, min_length=1, max_length=64)
    name: str = Field(default="", max_length=64)
    dept: str | None = None
    perm: Literal["可查看", "可编辑"] = "可查看"


class ShareIn(ApiModel):
    mode: Literal["仅自己", "指定成员", "所在部门"]
    members: list[MemberIn] = Field(default_factory=list, max_length=200)
    link: bool = False
    allow_export: bool = True
    lock_version: int = Field(ge=1)


class ShareSaveOut(ShareOut):
    link_url: str | None = None
    new_viewers: list[str] = []
    new_grants: list[str] = []
    lock_version: int


# ---------------------------------------------------------------- 小工具


async def _load(session, user, ctx, pid: str, need: Literal["edit", "owner"]) -> tuple[int, QaPage]:
    check_writable(ctx.ver, ctx.preview_as)
    user = await execution.current_viewer(session, user)
    if user.ontology_role not in ("owner", "editor"):
        raise Forbidden("当前本体工作空间为只读权限")
    await get_adapter(session).xact_lock(session, f"qa.page.edit.{pid}")
    pk, u, _ = await svc.effective_viewer(session, user, ctx)
    repo = r.PageRepo(session)
    p = await repo.detail(pid)
    if p is None or not await repo.is_visible(p.id, repo.visible_clause(pk, u.dept, include_shared=True)):
        raise NotFound("页面不存在", details={"id": pid})
    if p.owner_id == pk:
        return pk, p
    if need == "edit" and any(m.user_id == pk and m.perm == SharePerm.edit for m in p.members):
        return pk, p
    raise Forbidden(
        "只有页面拥有者" + ("或可编辑成员" if need == "edit" else "") + "能这样做", details={"id": pid}
    )


async def _out(session, pid: str, user, ctx) -> PageFullOut:
    session.expire_all()  # 关系集合重新加载（cards / members / subscription）
    p = await r.PageRepo(session).detail(pid)
    assert p is not None
    return await svc.page_view_out(session, p, user, ctx)


async def _check_name(session, owner_pk: int, name: str, exclude_pk: int | None = None) -> None:
    stmt = select(QaPage.id).where(QaPage.owner_id == owner_pk, QaPage.name == name)
    if exclude_pk is not None:
        stmt = stmt.where(QaPage.id != exclude_pk)
    if await session.scalar(stmt):
        raise DuplicateKey(f"已有同名页面「{name}」", details={"field": "name", "value": name})


def _status_of(cards: list[QaPageCard]) -> tuple[PageStatus, str | None]:
    pend = next((c.pending_cr for c in cards if c.pending_cr), None)
    return (PageStatus.caliber_pending, pend) if pend else (PageStatus.ok, None)


_WEEK = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


def next_run(plan: dict[str, Any], now: datetime | None = None) -> datetime | None:
    """按 now() 算下次刷新 [推断]：手动 → None；时间文字不是 HH:MM（如「整点」）按 00 分。"""
    now = (now or clock.now()).astimezone(_tz())
    freq = plan.get("freq")
    if freq == "手动":
        return None
    if freq == "每小时":
        return now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", str(plan.get("time") or ""))
    hh, mm = (int(m[1]), int(m[2])) if m else (8, 0)
    at = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if freq == "每周":
        wd = _WEEK.index(plan["weekday"]) if plan.get("weekday") in _WEEK else 0
        at += timedelta(days=(wd - now.weekday()) % 7)
        return at if at > now else at + timedelta(days=7)
    if freq == "每月":
        day = int(plan.get("monthday") or 1)
        y, mo = now.year, now.month
        for _ in range(13):
            try:
                cand = at.replace(year=y, month=mo, day=day)
            except ValueError:  # 本月没有这一天，顺延下月
                cand = None
            if cand and cand > now:
                return cand
            y, mo = (y + 1, 1) if mo == 12 else (y, mo + 1)
        return None
    return at if at > now else at + timedelta(days=1)  # 每天


def _next_code(codes: list[str]) -> str:
    nums = [int(c[1:]) for c in codes if re.fullmatch(r"P\d+", c)]
    return f"P{(max(nums) if nums else 0) + 1:03d}"


# ---------------------------------------------------------------- P2


@pages.post(
    "/pages",
    status_code=201,
    response_model=PageFullOut,
    response_model_exclude_none=True,
    summary="P2 新建页面",
)
async def create_page(body: PageCreateIn, session: SessionDep, user: CurrentUserDep, ctx: ViewContextDep):
    check_writable(ctx.ver, ctx.preview_as)
    user = await execution.current_viewer(session, user)
    if user.ontology_role not in ("owner", "editor"):
        raise Forbidden("当前本体工作空间为只读权限")
    if body.scope not in (None, "", "当前数据权限"):
        raise execution.unavailable("页面不能以范围名称替换原卡片查询条件")
    pk, _ = await svc.effective_user(session, user, ctx)
    name = body.name.strip()
    if not name:
        raise EmptyInput("页面名称不能为空", details={"field": "name"})
    await get_adapter(session).xact_lock(session, "qa.page.code")
    await _check_name(session, pk, name)
    code = _next_code(list((await session.scalars(select(QaPage.code))).all()))
    now = clock.now()
    p = QaPage(
        code=code,
        owner_id=pk,
        name=name,
        scope="原卡片条件 · 当前数据权限",
        source_kind=PageSourceKind.manual,
        source_ref="手动新建",
        status=PageStatus.ok,
        refresh=dict(DEFAULT_REFRESH),
        last_refresh=None,
        data_as_of=None,
        share_mode=ShareMode.private,
        share_link=False,
        allow_export=True,
        views=0,
    )
    if body.template is not None:
        if body.source_session_id:
            raise execution.unavailable("不能同时从模板和会话创建页面")
        repo = r.TemplateRepo(session)
        t = (
            await session.scalars(repo.list_query(None, None).where(QaTemplate.code == body.template.id))
        ).first()
        if t is None:
            raise NotFound(repo.not_found_message, details={"id": body.template.id})
        if body.template.caliber or (body.template.refresh and body.template.refresh.freq != "手动"):
            raise execution.unavailable("模板须保留原卡片条件与口径，仅支持手动刷新")
        if body.scope not in (None, "", "当前数据权限"):
            raise execution.unavailable("模板不能用范围名称替换原查询条件")
        caches, as_of = await execution.execute_cards(session, user, t.cards)
        permission = await execution.fingerprint(user)
        p.source_kind, p.source_ref = PageSourceKind.template, f"模板「{t.name}」"
        p.last_refresh, p.data_as_of = now, as_of
        p.cards = [
            QaPageCard(
                card_key=f"{code}-C{i}",
                sort_order=i,
                title=card.title,
                viz=card.viz,
                metric_name=card.metric_name,
                metric_code=card.metric_code,
                metric_version=card.metric_version,
                sem_plan=deepcopy(card.sem_plan),
                result_cache=cache,
                perm_fingerprint=permission,
            )
            for i, (card, cache) in enumerate(zip(t.cards, caches, strict=True), start=1)
        ]
        t.uses += 1
    if body.source_session_id:
        source = await session.scalar(
            select(QaSession).where(QaSession.code == body.source_session_id, QaSession.owner_id == pk)
        )
        if source is None:
            raise NotFound("会话不存在")
        turns = list(
            await session.scalars(
                select(QaTurn)
                .where(
                    QaTurn.session_id == source.id,
                    QaTurn.kind == TurnKind.answer,
                    QaTurn.status == TurnStatus.done,
                    QaTurn.sql_text.is_not(None),
                    QaTurn.is_seed.is_(False),
                    QaTurn.release_id.is_not(None),
                    QaTurn.answered_ver.is_not(None),
                )
                .order_by(QaTurn.seq)
            )
        )
        if not turns or len(turns) > execution.MAX_CARDS:
            raise execution.unavailable(f"会话须有1至{execution.MAX_CARDS}个可执行回答")
        cards = []
        release = await execution.ReleaseRepo(session).latest()
        for i, turn in enumerate(turns, start=1):
            plan = deepcopy(execution.turn_plan(turn, release))
            cards.append(
                QaPageCard(
                    card_key=f"{code}-C{i}",
                    sort_order=i,
                    title=(turn.question or source.title)[:60],
                    viz=CardViz.table,
                    source_turn_id=turn.id,
                    sem_plan={"execution": plan},
                    result_cache={},
                )
            )
        caches, as_of = await execution.execute_cards(session, user, cards)
        permission = await execution.fingerprint(user)
        for card, cache in zip(cards, caches, strict=True):
            card.result_cache, card.perm_fingerprint = cache, permission
        p.cards, p.source_kind, p.source_ref = cards, PageSourceKind.workbench, f"会话「{source.title}」"
        p.last_refresh, p.data_as_of = now, as_of
    p.next_run = next_run(p.refresh, now)
    p.subscription = QaPageSubscription(
        enabled=False,
        channel=SubChannel.email,
        recipients=[],
        send_when=SubWhen.after_refresh,
        only_changed=False,
    )
    session.add(p)
    await session.flush()
    await audit(
        session,
        user,
        action="qa.page.create",
        target=code,
        detail={"name": name, "template": body.template and body.template.id},
    )
    return await _out(session, code, user, ctx)


# ---------------------------------------------------------------- P4 / P5 / P6


@pages.patch(
    "/pages/{pid}", response_model=PageFullOut, response_model_exclude_none=True, summary="P4 重命名页面"
)
async def rename_page(
    pid: str, body: PageRenameIn, session: SessionDep, user: CurrentUserDep, ctx: ViewContextDep
):
    _, p = await _load(session, user, ctx, pid, "edit")
    name = body.name.strip()
    if not name:
        raise EmptyInput("页面名称不能为空", details={"field": "name"})
    if name != p.name:
        await _check_name(session, p.owner_id, name, exclude_pk=p.id)
        old, p.name = p.name, name
        await session.flush()
        await audit(session, user, action="qa.page.rename", target=pid, detail={"from": old, "to": name})
    return await _out(session, pid, user, ctx)


@pages.delete("/pages/{pid}", status_code=204, summary="P5 删除页面")
async def delete_page(pid: str, session: SessionDep, user: CurrentUserDep, ctx: ViewContextDep):
    _, p = await _load(session, user, ctx, pid, "owner")
    name = p.name
    await session.delete(p)  # 卡片 / 成员 / 订阅级联；定时刷新随页面消失；会话原始回答不动
    await session.flush()
    await audit(session, user, action="qa.page.delete", target=pid, detail={"name": name})
    return Response(status_code=204)


@pages.post(
    "/pages/{pid}:refresh",
    response_model=PageFullOut,
    response_model_exclude_none=True,
    summary="P6 立即刷新",
)
async def refresh_page(pid: str, session: SessionDep, user: CurrentUserDep, ctx: ViewContextDep):
    _, p = await _load(session, user, ctx, pid, "edit")
    actor = await execution.current_viewer(session, user)
    try:
        permission = await execution.fingerprint(actor)
        caches, as_of = await execution.execute_cards(session, actor, p.cards)
    except AppError as error:
        p.status, p.status_note = PageStatus.refresh_failed, error.message
    else:
        for card, cache in zip(p.cards, caches, strict=True):
            card.result_cache, card.perm_fingerprint = cache, permission
        p.status, p.status_note = _status_of(p.cards)
        p.last_refresh, p.data_as_of = clock.now(), as_of
    await session.flush()
    await audit(
        session,
        user,
        action="qa.page.refresh",
        target=pid,
        detail={"status": p.status.value, "cards": len(p.cards)},
    )
    return await _out(session, pid, actor, ctx)


# ---------------------------------------------------------------- P7 固定到页面


_VER = re.compile(r"^(.*?)\s*v(\d+)$")


def _metric_of(label: str | None) -> tuple[str, int | None]:
    m = _VER.match((label or "").strip())
    return (m[1], int(m[2])) if m else ((label or "").strip(), None)


def card_from_turn(t: QaTurn, title: str) -> dict[str, Any]:
    """把回答中持久化的结果表冻结为卡片（前 5 行）。"""
    ans = (t.snapshot or {}).get("answer") or {}
    card = ans.get("card")
    plan = {
        "question": t.question,
        "basis": ans.get("basis"),
        "period": ans.get("period"),
        "region": ans.get("region"),
    }
    if card in {"main", "store"} and not ans.get("table"):
        raise AppError(
            "这一轮未保存真实结果表，请重新提问后固定",
            code="TURN_NOT_PINNABLE",
            status_code=422,
            details={"turnId": t.code},
        )
    res = svc.turn_result(t)
    if res is None:
        raise AppError(
            "这一轮回答没有可固定的结果",
            code="TURN_NOT_PINNABLE",
            status_code=422,
            details={"turnId": t.code},
        )
    cols, rows, _ = res
    name, ver = _metric_of(ans.get("metric") or (ans.get("basis") or {}).get("指标"))
    keys = [c["key"] for c in cols]
    vkey = next((c["key"] for c in cols if c.get("align") == "right"), keys[1] if len(keys) > 1 else None)
    top = [[str(x.get(keys[0], "")), str(x.get(vkey, ""))] for x in rows[:5]] if keys and vkey else []
    return {
        "viz": CardViz.table,
        "metric_name": name or None,
        "metric_code": None,
        "metric_version": ver,
        "sem_plan": plan,
        "result_cache": {"rows": top},
    }


@pages.post(
    "/pages/{pid}/cards",
    status_code=201,
    response_model=PageFullOut,
    response_model_exclude_none=True,
    summary="P7 固定到页面",
)
async def pin_card(pid: str, body: PinIn, session: SessionDep, user: CurrentUserDep, ctx: ViewContextDep):
    if not body.title.strip():
        raise EmptyInput("卡片标题不能为空", details={"field": "title"})
    await get_adapter(session).xact_lock(session, f"qa.page.cards.{pid}")
    pk, p = await _load(session, user, ctx, pid, "edit")
    t = await session.scalar(
        select(QaTurn)
        .join(QaSession, QaTurn.session_id == QaSession.id)
        .where(QaTurn.code == body.turn_id, QaSession.owner_id == pk)
    )
    if t is None:
        raise NotFound("轮次不存在", details={"id": body.turn_id})
    f = card_from_turn(t, body.title)
    if len(p.cards) >= execution.MAX_CARDS:
        raise execution.unavailable(f"一个页面最多{execution.MAX_CARDS}张卡片")
    release = await execution.ReleaseRepo(session).latest()
    f["sem_plan"] = {"execution": deepcopy(execution.turn_plan(t, release))}
    nums = [int(m[1]) for c in p.cards if (m := re.fullmatch(rf"{re.escape(pid)}-C(\d+)", c.card_key))]
    key = f"{pid}-C{(max(nums) if nums else 0) + 1}"
    order = (max((c.sort_order for c in p.cards), default=0)) + 1
    card = QaPageCard(card_key=key, sort_order=order, title=body.title.strip(), source_turn_id=t.id, **f)
    actor = await execution.current_viewer(session, user)
    caches, as_of = await execution.execute_cards(session, actor, [card])
    card.result_cache = caches[0]
    card.perm_fingerprint = await execution.fingerprint(actor)
    p.cards.append(card)
    p.scope = "原卡片条件 · 当前数据权限"
    p.last_refresh, p.data_as_of = clock.now(), as_of
    if p.status != PageStatus.refresh_failed:
        p.status, p.status_note = _status_of(p.cards)
    await session.flush()
    await audit(session, user, action="qa.page.pin", target=pid, detail={"turn": body.turn_id, "card": key})
    return await _out(session, pid, user, ctx)


# ---------------------------------------------------------------- P8 / P9 / P10


@pages.put(
    "/pages/{pid}/cards", response_model=CardsOut, response_model_exclude_none=True, summary="P8 调整卡片"
)
async def reorder_cards(
    pid: str, body: CardsOrderIn, session: SessionDep, user: CurrentUserDep, ctx: ViewContextDep
):
    _, p = await _load(session, user, ctx, pid, "edit")
    have = {c.card_key: c for c in p.cards}
    unknown = [k for k in body.order if k not in have]
    if unknown or len(set(body.order)) != len(body.order):
        raise AppError(
            "卡片顺序有误", code="VALIDATION_FAILED", status_code=422, details={"unknown": unknown}
        )
    removed = [k for k in have if k not in body.order]
    for k in removed:
        p.cards.remove(have[k])
    for i, k in enumerate(body.order, start=1):
        have[k].sort_order = i
    if p.status != PageStatus.refresh_failed:
        p.status, p.status_note = _status_of([have[k] for k in body.order])
    await session.flush()
    if removed:
        await audit(session, user, action="qa.page.cards", target=pid, detail={"removed": removed})
    out = await _out(session, pid, user, ctx)
    return CardsOut(cards=out.cards, removed=removed)


@pages.put("/pages/{pid}/schedule", response_model=ScheduleOut, summary="P9 刷新与订阅")
async def put_schedule(
    pid: str, body: ScheduleIn, session: SessionDep, user: CurrentUserDep, ctx: ViewContextDep
):
    _, p = await _load(session, user, ctx, pid, "edit")
    if body.refresh.freq != "手动" or body.sub.on or body.refresh.on_fail != "保留上次结果":
        raise execution.unavailable("只能保存手动刷新且关闭订阅的设置")
    if body.sub.on and not body.sub.receivers:
        raise EmptyInput("请至少选择一位接收人", details={"field": "sub.receivers"})
    p.refresh = body.refresh.plan()
    p.next_run = next_run(p.refresh)
    sub = p.subscription or QaPageSubscription(page_id=p.id)
    sub.enabled = body.sub.on
    sub.channel = SubChannel(body.sub.channel)
    sub.recipients = list(dict.fromkeys(body.sub.receivers))  # 人名软引用（同 seed）
    sub.send_when = SubWhen(body.sub.when)
    sub.only_changed = body.sub.only_changed
    if p.subscription is None:
        session.add(sub)
    await session.flush()
    await audit(
        session,
        user,
        action="qa.page.schedule",
        target=pid,
        detail={"refresh": p.refresh, "sub": body.sub.on},
    )
    return ScheduleOut(
        refresh=p.refresh,
        sub=SubOut(
            on=sub.enabled,
            channel=sub.channel.value,
            receivers=list(sub.recipients),
            when=sub.send_when.value,
            only_changed=sub.only_changed,
        ),
        next_run=svc.fmt_min(p.next_run) or "不自动刷新",
    )


@pages.put(
    "/pages/{pid}/share", response_model=ShareSaveOut, response_model_exclude_none=True, summary="P10 共享"
)
async def put_share(pid: str, body: ShareIn, session: SessionDep, user: CurrentUserDep, ctx: ViewContextDep):
    _, p = await _load(session, user, ctx, pid, "owner")
    if p.lock_version != body.lock_version:
        raise VersionConflict(details={"expected": body.lock_version, "actual": p.lock_version})
    mode = ShareMode(body.mode)
    if mode == ShareMode.members and not body.members:
        raise EmptyInput("指定成员共享时至少添加一位成员", details={"field": "members"})
    candidates = list(await session.execute(_share_candidates(session)))
    chosen = []
    for member in body.members if mode == ShareMode.members else []:
        matches = (
            [candidate for candidate in candidates if candidate.code == member.user_id]
            if member.user_id
            else [candidate for candidate in candidates if candidate.name == member.name]
        )
        if len(matches) != 1:
            raise AppError(
                "共享成员不存在、未授权或姓名不唯一，请重新选择", code="USER_NOT_FOUND", status_code=422
            )
        candidate = matches[0]
        if candidate.id == p.owner_id or any(item[0].id == candidate.id for item in chosen):
            raise AppError("共享成员不能包含本人或重复用户", code="VALIDATION_FAILED", status_code=422)
        if member.perm == "可编辑" and candidate.role == "viewer":
            raise Forbidden("所选成员在当前本体只有查看权限")
        chosen.append((candidate, member))
    if mode == ShareMode.dept and not p.owner.dept:
        raise execution.unavailable("页面拥有者未登记部门，不能按部门共享")
    old_mode, old_link, old_export = p.share_mode, p.share_link, p.allow_export
    old_members = {m.user.code for m in p.members}
    new_viewers: list[str] = []
    if mode == ShareMode.dept and old_mode != ShareMode.dept:
        new_viewers.append(f"{p.owner.dept or '所在部门'} 全体")
    if mode == ShareMode.members:
        new_viewers += [
            f"{candidate.name}（{member.perm}）"
            for candidate, member in chosen
            if old_mode != ShareMode.members or candidate.code not in old_members
        ]
    link = body.link and mode != ShareMode.private
    grants: list[str] = []
    if mode != ShareMode.private and link and not (old_mode != ShareMode.private and old_link):
        grants.append("开启页面链接：仍须是已授权共享成员或同部门用户")
    if mode != ShareMode.private and body.allow_export and not (old_mode != ShareMode.private and old_export):
        grants.append("允许导出：可见人能下载卡片明细")
    p.share_mode, p.share_link, p.allow_export = mode, link, body.allow_export
    p.updated_at = clock.now()  # Member-only edits must advance the optimistic version as well.
    p.members.clear()
    await session.flush()
    for candidate, member in chosen:
        p.members.append(
            QaPageShareMember(user_id=candidate.id, perm=SharePerm(member.perm), dept=candidate.dept)
        )
    await session.flush()
    await audit(
        session,
        user,
        action="qa.page.share",
        target=pid,
        detail={"mode": body.mode, "newViewers": new_viewers, "newGrants": grants},
    )
    out = await _out(session, pid, user, ctx)
    return ShareSaveOut(
        **out.share.model_dump(),
        link_url=f"/use/qa/pages?ontology={require_ontology_id(session)}&page_id={pid}" if link else None,
        new_viewers=new_viewers,
        new_grants=grants,
        lock_version=out.lock_version,
    )


def _share_candidates(session):
    return (
        select(SysUser.id, SysUser.code, SysUser.name, SysUser.dept, OntologyMember.role)
        .join(OntologyMember, OntologyMember.user_id == SysUser.id)
        .join(SysRoleMember, SysRoleMember.user_id == SysUser.id)
        .join(SysRole, SysRole.id == SysRoleMember.role_id)
        .where(
            SysUser.status == UserStatus.active,
            OntologyMember.ontology_id == require_ontology_id(session),
            OntologyMember.role.in_(("owner", "editor", "viewer")),
            SysRole.func["qa-pages"].as_string().in_(("view", "manage")),
        )
        .distinct()
        .order_by(SysUser.code)
    )


@pages.get("/pages/{pid}/share-options")
async def share_options(
    pid: str,
    session: SessionDep,
    user: CurrentUserDep,
    page: int = Query(1, ge=1),
    size: int = Query(10, ge=1, le=100),
    kw: str = Query("", max_length=128),
):
    viewer = await execution.current_viewer(session, user)
    p = await r.PageRepo(session).detail(pid)
    if p is None or p.owner.code != viewer.id:
        raise NotFound("页面不存在")
    query = _share_candidates(session).where(SysUser.id != p.owner_id)
    if kw.strip():
        query = query.where(
            or_(
                func.strpos(SysUser.code, kw.strip()) > 0,
                func.strpos(SysUser.name, kw.strip()) > 0,
                func.strpos(SysUser.dept, kw.strip()) > 0,
            )
        )
    total = int(await session.scalar(select(func.count()).select_from(query.order_by(None).subquery())) or 0)
    params = PageParams(page=page, size=size).clamp(total)
    rows = await session.execute(query.offset(params.offset).limit(params.size))
    return {
        "items": [
            {
                "userId": row.code,
                "name": row.name,
                "dept": row.dept,
                "canEdit": row.role in ("owner", "editor"),
            }
            for row in rows
        ],
        "total": total,
        "page": params.page,
        "size": params.size,
    }


class SaveTemplateIn(ApiModel):
    name: str = Field(min_length=1, max_length=60)
    scene: str = Field(default="我的模板", min_length=1, max_length=32)


@pages.post("/pages/{pid}:template", status_code=201)
async def save_as_template(
    pid: str, body: SaveTemplateIn, session: SessionDep, user: CurrentUserDep, ctx: ViewContextDep
):
    _, p = await _load(session, user, ctx, pid, "owner")
    can_create, reason = await execution.template_capability(session, p)
    if not can_create:
        raise execution.unavailable(reason)
    name, scene = body.name.strip(), body.scene.strip()
    if not name or not scene:
        raise EmptyInput("模板名称和场景不能为空")
    await get_adapter(session).xact_lock(session, "qa.template.code")
    if await session.scalar(select(QaTemplate.id).where(QaTemplate.name == name)):
        raise DuplicateKey("已有同名模板")
    template = QaTemplate(
        code=f"TPL-{uuid4().hex[:16]}",
        name=name,
        scene=scene,
        provider=user.name,
        official=False,
        uses=0,
        created_by=user.id,
        cards=[
            QaTemplateCard(
                card_key=f"C{i}",
                sort_order=i,
                title=card.title,
                viz=card.viz,
                sem_plan=deepcopy(card.sem_plan),
                metric_name=card.metric_name,
                metric_code=card.metric_code,
                metric_version=card.metric_version,
                result_cache={},
            )
            for i, card in enumerate(p.cards, start=1)
        ],
    )
    session.add(template)
    await session.flush()
    await audit(
        session, user, action="qa.page.template", target=template.code, detail={"page": pid, "name": name}
    )
    return {"id": template.code, "name": template.name}


@pages.get("/pages/{pid}/export")
async def export_page(pid: str, session: SessionDep, user: CurrentUserDep, ctx: ViewContextDep):
    from app.common.sql_guard import _real_tables, assert_single_select, governed_tables
    from app.modules.qa.object_source import can_export_object

    pk, account, viewer = await svc.effective_viewer(session, user, ctx)
    viewer = await execution.current_viewer(session, viewer)
    repo = r.PageRepo(session)
    p = await repo.detail(pid)
    if p is None or not await repo.is_visible(
        p.id, repo.visible_clause(pk, account.dept, include_shared=True)
    ):
        raise NotFound("页面不存在")
    if ctx.read_only or (p.owner_id != pk and not p.allow_export):
        raise Forbidden("当前页面不允许导出")
    governed = governed_tables(require_ontology_id(session))
    release = await execution.ReleaseRepo(session).latest()
    for card in p.cards:
        plan = execution.validate_plan(card, release)
        for table in _real_tables(assert_single_select(plan["sql"])):
            binding = governed.get(table.name)
            if binding is None or not await can_export_object(session, viewer, binding.object_type):
                raise Forbidden("没有卡片涉及对象的导出权限")
    cards = await execution.projected_cards(session, viewer, p, ctx, temporary=True)
    if not cards or any(card.get("denied") for card in cards):
        raise Forbidden("当前权限无法安全导出页面结果")
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    from app.modules.admin.exports import csv_cell

    for card in cards:
        writer.writerow([csv_cell(card["title"])])
        writer.writerow(
            [
                "当前权限下的有界结果",
                f"最多{execution.ROW_LIMIT}行",
                "已截断" if card.get("truncated") else "未截断",
            ]
        )
        writer.writerow([csv_cell(column["title"]) for column in card.get("columns", [])])
        for row in card.get("items", []):
            writer.writerow([csv_cell(str(row.get(column["key"], ""))) for column in card.get("columns", [])])
        writer.writerow([])
    return Response(
        "\ufeff" + output.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="page-{p.id}.csv"',
            "Cache-Control": "no-store",
        },
    )


__all__ = ["card_from_turn", "next_run", "pages"]
