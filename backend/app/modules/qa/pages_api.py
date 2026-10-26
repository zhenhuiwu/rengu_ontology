"""R2 我的页面写接口 P2、P4–P10（qa-api §4），挂在 qa router 上（router.py 末尾 include）。

单独成文件是为了跟 qa-tuning 流的 router.py 改动不冲突；读接口 P1 / P3 / P11 / P12 仍在 router.py。
权限 [推断]：拥有者可做全部；共享成员「可编辑」可重命名 / 刷新 / 调整卡片 / 改刷新与订阅；
删除和改共享只有拥有者。其他人（含只读成员）对看得到的页面返回 403 NO_PERMISSION，看不到的 404。
"""

import re
from datetime import datetime, timedelta
from typing import Any, Literal

from fastapi import APIRouter, Depends, Response
from pydantic import Field
from sqlalchemy import select

from app.common.audit import audit
from app.common.auth import CurrentUserDep, require_func
from app.common.context import ViewContextDep
from app.core import clock
from app.core.errors import AppError, DuplicateKey, EmptyInput, Forbidden, NotFound
from app.core.schema import ApiModel, _tz, parse_display_dt
from app.db.session import SessionDep
from app.modules.admin.models import SysUser
from app.modules.qa import catalog
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
    QaTurn,
    ShareMode,
    SharePerm,
    SubChannel,
    SubWhen,
)
from app.modules.qa.schemas import PageFullOut, ShareOut, SubOut

pages = APIRouter(dependencies=[Depends(require_func("qa-pages", "view"))])

DEFAULT_REFRESH = {"freq": "每天", "time": "08:00", "onFail": "保留上次结果"}
LINK_BASE = "https://bi.example.com/p/"  # 同 PageDrawer.copyLink [推断]

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
    name: str
    dept: str | None = None
    perm: Literal["可查看", "可编辑"] = "可查看"


class ShareIn(ApiModel):
    mode: Literal["仅自己", "指定成员", "所在部门"]
    members: list[MemberIn] = []
    link: bool = False
    allow_export: bool = True


class ShareSaveOut(ShareOut):
    link_url: str | None = None
    new_viewers: list[str] = []
    new_grants: list[str] = []


# ---------------------------------------------------------------- 小工具


def _cutoff() -> datetime:
    return parse_display_dt(catalog.load("PAGES_CUTOFF"))


async def _load(session, user, ctx, pid: str, need: Literal["edit", "owner"]) -> tuple[int, QaPage]:
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


async def _out(session, pid: str) -> PageFullOut:
    session.expire_all()  # 关系集合重新加载（cards / members / subscription）
    p = await r.PageRepo(session).detail(pid)
    assert p is not None
    return svc.page_full_out(p)


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


def _clone_card(c, key: str, i: int) -> QaPageCard:
    return QaPageCard(
        card_key=key,
        sort_order=i,
        title=c.title,
        viz=c.viz,
        sem_plan=c.sem_plan,
        metric_name=c.metric_name,
        metric_code=c.metric_code,
        metric_version=c.metric_version,
        pending_cr=c.pending_cr,
        result_cache=dict(c.result_cache or {}),
    )


# ---------------------------------------------------------------- P2


@pages.post(
    "/pages",
    status_code=201,
    response_model=PageFullOut,
    response_model_exclude_none=True,
    summary="P2 新建页面",
)
async def create_page(body: PageCreateIn, session: SessionDep, user: CurrentUserDep, ctx: ViewContextDep):
    pk, _ = await svc.effective_user(session, user, ctx)
    name = body.name.strip()
    if not name:
        raise EmptyInput("页面名称不能为空", details={"field": "name"})
    await _check_name(session, pk, name)
    code = _next_code(list((await session.scalars(select(QaPage.code))).all()))
    now = clock.now()
    p = QaPage(
        code=code,
        owner_id=pk,
        name=name,
        scope=body.scope,
        source_kind=PageSourceKind.manual,
        source_ref="手动新建",
        status=PageStatus.ok,
        refresh=dict(DEFAULT_REFRESH),
        last_refresh=now,
        data_as_of=_cutoff(),
        share_mode=ShareMode.private,
        share_link=False,
        allow_export=True,
        views=0,
    )
    if body.template is not None:
        repo = r.TemplateRepo(session)
        t = (
            await session.scalars(repo.list_query(None, None).where(QaTemplate.code == body.template.id))
        ).first()
        if t is None:
            raise NotFound(repo.not_found_message, details={"id": body.template.id})
        p.source_kind = PageSourceKind.template
        p.source_ref = f"模板「{t.name}」· {body.template.caliber}"[:200]
        if body.template.refresh is not None:
            p.refresh = body.template.refresh.plan()
        p.cards = [_clone_card(c, f"{code}-C{i}", i) for i, c in enumerate(t.cards, start=1)]
        p.status, p.status_note = _status_of(p.cards)
        t.uses = (t.uses or 0) + 1  # [推断] 用一次模板计一次「使用次数」
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
    return await _out(session, code)


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
    return await _out(session, pid)


@pages.delete("/pages/{pid}", status_code=204, summary="P5 删除页面")
async def delete_page(pid: str, session: SessionDep, user: CurrentUserDep, ctx: ViewContextDep):
    _, p = await _load(session, user, ctx, pid, "owner")
    name = p.name
    await session.delete(p)  # 卡片 / 成员 / 订阅级联；定时刷新随页面消失；会话原始回答不动
    await session.flush()
    await audit(session, user, action="qa.page.delete", target=pid, detail={"name": name})
    return Response(status_code=204)


@pages.post(
    "/pages/{pid}:refresh", response_model=RefreshOut, response_model_exclude_none=True, summary="P6 立即刷新"
)
async def refresh_page(pid: str, session: SessionDep, user: CurrentUserDep, ctx: ViewContextDep):
    """[推断] 卡片值是冻结的快照（seed / 固定时算好），刷新只更新时间和状态（同 MyPages.refresh）；
    真实按语义计划重算、超 30 秒返回 202 {jobId} 留给后续（handoff qa.md）。"""
    _, p = await _load(session, user, ctx, pid, "edit")
    p.last_refresh = clock.now()
    p.data_as_of = _cutoff()
    if p.status == PageStatus.refresh_failed:
        p.status, p.status_note = _status_of(p.cards)
    p.next_run = next_run(p.refresh)
    await session.flush()
    return RefreshOut(
        last_refresh=svc.fmt_min(p.last_refresh),
        data_as_of=svc.fmt_min(p.data_as_of),
        status=p.status.value,
        status_note=p.status_note,
    )


# ---------------------------------------------------------------- P7 固定到页面


_VER = re.compile(r"^(.*?)\s*v(\d+)$")


def _metric_of(label: str | None) -> tuple[str, int | None]:
    m = _VER.match((label or "").strip())
    return (m[1], int(m[2])) if m else ((label or "").strip(), None)


def card_from_turn(t: QaTurn, title: str) -> dict[str, Any]:
    """把一轮回答冻结成卡片字段 [推断]：主卡（复购按省份）→ 柱状图；门店卡 / 链路结果表 → 表格（前 5 行）。"""
    ans = (t.snapshot or {}).get("answer") or {}
    card = ans.get("card")
    plan = {
        "question": t.question,
        "basis": ans.get("basis"),
        "period": ans.get("period"),
        "region": ans.get("region"),
    }
    if card == "main":
        variant = t.caliber_variant if t.caliber_variant in ("ex", "in") else "ex"
        m = catalog.load("QA_METRIC")[variant]
        provs = catalog.load("QA_PROVINCES")
        return {
            "viz": CardViz.bar,
            "metric_name": m["name"],
            "metric_code": m["id"],
            "metric_version": catalog.version_int(m["version"]),
            "sem_plan": {**plan, "caliber": variant, "dims": ["省份"]},
            "result_cache": {"labels": [x["province"] for x in provs], "series": [x["sales"] for x in provs]},
        }
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
    pk, p = await _load(session, user, ctx, pid, "edit")
    t = await session.scalar(
        select(QaTurn)
        .join(QaSession, QaTurn.session_id == QaSession.id)
        .where(QaTurn.code == body.turn_id, QaSession.owner_id == pk)
    )
    if t is None:
        raise NotFound("轮次不存在", details={"id": body.turn_id})
    f = card_from_turn(t, body.title)
    nums = [int(m[1]) for c in p.cards if (m := re.fullmatch(rf"{re.escape(pid)}-C(\d+)", c.card_key))]
    key = f"{pid}-C{(max(nums) if nums else 0) + 1}"
    order = (max((c.sort_order for c in p.cards), default=0)) + 1
    p.cards.append(
        QaPageCard(card_key=key, sort_order=order, title=body.title.strip(), source_turn_id=t.id, **f)
    )
    if p.status != PageStatus.refresh_failed:
        p.status, p.status_note = _status_of(p.cards)
    await session.flush()
    await audit(session, user, action="qa.page.pin", target=pid, detail={"turn": body.turn_id, "card": key})
    return await _out(session, pid)


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
    out = await _out(session, pid)
    return CardsOut(cards=out.cards, removed=removed)


@pages.put("/pages/{pid}/schedule", response_model=ScheduleOut, summary="P9 刷新与订阅")
async def put_schedule(
    pid: str, body: ScheduleIn, session: SessionDep, user: CurrentUserDep, ctx: ViewContextDep
):
    _, p = await _load(session, user, ctx, pid, "edit")
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
    mode = ShareMode(body.mode)
    if mode == ShareMode.members and not body.members:
        raise EmptyInput("指定成员共享时至少添加一位成员", details={"field": "members"})
    # 按姓名映射用户（同名取编号最小的一个 [推断]）；不在用户表 → 422 USER_NOT_FOUND
    names = list(dict.fromkeys(m.name for m in body.members)) if mode == ShareMode.members else []
    found: dict[str, SysUser] = {}
    if names:
        for u in (
            await session.scalars(
                select(SysUser).where(SysUser.name.in_(names)).order_by(SysUser.code.desc())
            )
        ).all():
            found[u.name] = u
    missing = [n for n in names if n not in found]
    if missing:
        raise AppError("成员不在用户表中", code="USER_NOT_FOUND", status_code=422, details={"names": missing})
    old_mode, old_link, old_export = p.share_mode, p.share_link, p.allow_export
    old_members = {m.user.name for m in p.members}
    new_viewers: list[str] = []
    if mode == ShareMode.dept and old_mode != ShareMode.dept:
        new_viewers.append(f"{p.owner.dept or '所在部门'} 全体")
    perm_of = {m.name: m for m in body.members}
    if mode == ShareMode.members:
        new_viewers += [
            f"{n}（{perm_of[n].perm}）"
            for n in names
            if old_mode != ShareMode.members or n not in old_members
        ]
    link = body.link and mode != ShareMode.private
    grants: list[str] = []
    if mode != ShareMode.private and link and not (old_mode != ShareMode.private and old_link):
        grants.append("开启链接访问：有链接且有数据权限的人都能打开")
    if mode != ShareMode.private and body.allow_export and not (old_mode != ShareMode.private and old_export):
        grants.append("允许导出：可见人能下载卡片明细")
    p.share_mode, p.share_link, p.allow_export = mode, link, body.allow_export
    p.members.clear()
    await session.flush()
    for n in names:
        m = perm_of[n]
        p.members.append(
            QaPageShareMember(user_id=found[n].id, perm=SharePerm(m.perm), dept=m.dept or found[n].dept)
        )
    await session.flush()
    await audit(
        session,
        user,
        action="qa.page.share",
        target=pid,
        detail={"mode": body.mode, "newViewers": new_viewers, "newGrants": grants},
    )
    out = await _out(session, pid)
    return ShareSaveOut(
        **out.share.model_dump(),
        link_url=f"{LINK_BASE}{pid}" if link else None,
        new_viewers=new_viewers,
        new_grants=grants,
    )


__all__ = ["card_from_turn", "next_run", "pages"]
