"""动作类型读取（当前版本读展开表，`?ver=` 读发布快照 actionTypes 段）。

服务层统一产出 mock 形状的 dict
（字段名、中文枚举和前端 ACTION_TYPES / paramsOf / condsOf / writebackOf 一致），
路由再用 schemas 校验输出。
"""

import re
from collections.abc import Callable, Sequence
from datetime import timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import raiseload

from app.common.context import ViewContext
from app.common.pagination import Page, PageParams
from app.core import clock
from app.core.errors import AppError, NotFound
from app.core.schema import _fmt_dt
from app.modules.action.models import ActionParam, ActionType, ActionTypeRule
from app.modules.action.repo import ActionTypeRepo
from app.modules.action.sections import SECTION, action_def
from app.modules.dynamic.models import ActionRun, ActionStatus, Alert, AlertStatus
from app.modules.dynamic.rules_models import Rule
from app.modules.integration.models import ExtSystem
from app.modules.versioning.repo import ReleaseRepo

ALL = "全部"
INTERNAL = {
    "system": "本体（无外部写回）",
    "timeout": "3 秒",
    "retry": "本体内部编辑，失败直接报错，不重试",
    "idempotent": True,
}
UNKNOWN_FOLLOWUP = "系统按幂等键自动查询，查不到再人工对账"


async def defs_for_view(
    session: AsyncSession,
    ctx: ViewContext,
    rows: Sequence[Any],
    section: str,
    to_def: Callable[[Any], dict[str, Any]],
) -> list[dict[str, Any]]:
    """动作 / 事件共用：返回该视图下可见的定义（按 id 顺序）。

    - 当前视图：定义表里 retired_release_id 为空的行；
    - `?ver=V`：V 不存在 → 404；取 V 快照的 section；V 快照里没有这一段（骨架阶段 / 早于本模块上线的版本）
      → 返回空，不借用当前定义；
      下线过滤：V.id ≥ retired_release_id 的类型不出现（§4.2）。
    """
    order = {r.code: i for i, r in enumerate(rows)}
    if not ctx.ver:
        return [to_def(r) for r in rows if r.retired_release_id is None]
    rel = await ReleaseRepo(session).by_ver_or_404(ctx.ver)
    hidden = {r.code for r in rows if r.retired_release_id is not None and rel.id >= r.retired_release_id}
    snap = (rel.snapshot or {}).get(section)
    defs: dict[str, dict[str, Any]] = snap if isinstance(snap, dict) else {}
    out = [d for code, d in defs.items() if code not in hidden]
    return sorted(out, key=lambda d: (order.get(d["id"], len(order)), d["id"]))


def find(defs: list[dict[str, Any]], type_id: str, message: str) -> dict[str, Any]:
    for d in defs:
        if d["id"] == type_id:
            return d
    raise NotFound(message, details={"id": type_id})


def fmt_seconds(ms: int) -> str:
    """JS `${ms / 1000} 秒`：整数不带小数点。"""
    s = ms / 1000
    return f"{int(s) if s == int(s) else s} 秒"


def with_system(d: dict[str, Any], systems: dict[str, ExtSystem]) -> dict[str, Any]:
    """前端 withSystem：desc 的 {sys} 替换 + 系统名 / 超时 / 重试 / 幂等（随 ext_system 当前值）。"""
    s = systems.get(d.get("sysId") or "")
    desc = re.sub(r"\{sys\}", f" {s.name} " if s else "本体", d.get("desc") or "")
    desc = re.sub(r"^ | \Z", "", desc)
    extra = (
        {
            "system": s.name,
            "timeout": fmt_seconds(s.timeout_ms),
            "retry": s.retry_text,
            "idempotent": s.idempotent,
        }
        if s
        else INTERNAL
    )
    return {**d, "desc": desc, **extra}


def flow_of(a: dict[str, Any], s: ExtSystem | None, rule_action: dict[str, str]) -> list[dict[str, Any]]:
    """执行顺序时间线（ActionTypes.vue flow）。a 已经过 with_system。"""
    items: list[dict[str, Any]] = []

    def add(text: str, desc: str, status: str | None = None) -> None:
        items.append({"time": str(len(items) + 1), "text": text, "desc": desc, "status": status})

    if any(rule_action.get(r) == a["id"] for r in a.get("usedBy") or []):
        add("规则生成 ⇒ 待确认", "规则命中只生成动作，确认人确认后才提交；确认前可撤回", "warn")
    add("校验提交条件", f"{len(a.get('conds') or [])} 项，任一不满足即拒绝提交")
    if a.get("approval") != "免审":
        add("审批", a.get("approval") or "")
    if s:
        add(f"调用 {s.name}（同步）", f"超时 {a['timeout']}；幂等键 {s.idem_header}")
        add("外部明确返回失败 ⇒ 自动重试", s.retry_text, "error")
        add("超时或无响应 ⇒ 结果未知 · 待对账", f"不重试；{UNKNOWN_FOLLOWUP}", "unknown")
    add("提交本体编辑", "写回成功后才提交，保证本体与外部系统一致")
    add("执行副作用（异步）", "通知、事件；失败不影响动作结果")
    return items


class ActionTypeService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = ActionTypeRepo(session)

    async def defs(self, ctx: ViewContext) -> list[dict[str, Any]]:
        rows = (
            (
                await self.session.execute(
                    select(ActionType.code, ActionType.retired_release_id).order_by(ActionType.id)
                )
            ).all()
            if ctx.ver
            else await self.repo.all_ordered()
        )
        return await defs_for_view(self.session, ctx, rows, SECTION, action_def)

    async def one(self, ctx: ViewContext, type_id: str) -> dict[str, Any]:
        if ctx.ver:
            return find(await self.defs(ctx), type_id, "动作类型不存在")
        row = await self.repo.get_by_code_or_404(type_id)
        if row.retired_release_id is not None:
            raise NotFound("动作类型不存在", details={"id": type_id})
        return action_def(row)

    async def search_page(self, ctx: ViewContext, page: PageParams, group: str | None, kw: str | None):
        if ctx.ver:
            rows, groups = await self.search(ctx, group, kw)
            p = page.clamp(len(rows))
            return Page.of(rows[p.offset : p.offset + p.size], len(rows), page), groups
        if any(value and ("\x00" in value or len(value) > 200) for value in (kw, group)):
            raise AppError("搜索文字不能超过200字或包含空字符", code="VALIDATION_FAILED", status_code=422)
        statement = select(ActionType).options(raiseload("*")).where(ActionType.retired_release_id.is_(None))
        if group and group != ALL:
            statement = statement.where(ActionType.grp == group)
        if kw:
            statement = statement.where(ActionType.name.contains(kw, autoescape=True))
        rows, total = await self.repo.list_page(statement.order_by(ActionType.id), page)
        groups = [
            ALL,
            *list(
                await self.session.scalars(
                    select(ActionType.grp)
                    .where(ActionType.retired_release_id.is_(None))
                    .group_by(ActionType.grp)
                    .order_by(func.min(ActionType.id))
                )
            ),
        ]
        items = [
            {
                "id": r.code,
                "name": r.name,
                "api": r.api,
                "group": r.grp,
                "status": r.status.value,
                "version": r.version,
                "target": r.target,
                "targetApi": r.target_api,
                "targetKind": r.target_kind,
            }
            for r in rows
        ]
        return Page.of(items, total, page), groups

    async def params_page(self, ctx: ViewContext, type_id: str, page: PageParams):
        if ctx.ver:
            rows = (await self.one(ctx, type_id)).get("params") or []
            p = page.clamp(len(rows))
            return Page.of(rows[p.offset : p.offset + p.size], len(rows), page)
        parent = await self.session.scalar(
            select(ActionType.id).where(ActionType.code == type_id, ActionType.retired_release_id.is_(None))
        )
        if parent is None:
            raise NotFound("动作类型不存在", details={"id": type_id})
        statement = select(ActionParam).where(ActionParam.action_type_id == parent)
        total = int(await self.session.scalar(select(func.count()).select_from(statement.subquery())) or 0)
        p = page.clamp(total)
        rows = await self.session.scalars(
            statement.order_by(ActionParam.seq, ActionParam.id).limit(p.size).offset(p.offset)
        )
        return Page.of(
            [
                {
                    "name": r.name,
                    "api": r.api,
                    "type": r.type,
                    "required": r.required,
                    "def": r.default_text,
                    "rule": r.rule_text,
                }
                for r in rows
            ],
            total,
            page,
        )

    async def stats(self, type_id: str, days: int = 30) -> dict[str, Any]:
        if await self.session.scalar(select(ActionType.id).where(ActionType.code == type_id)) is None:
            raise NotFound("动作类型不存在", details={"id": type_id})
        until = clock.now()
        groups = (
            await self.session.execute(
                select(ActionRun.status, func.count(), func.sum(func.coalesce(ActionRun.elapsed_ms, 0)))
                .where(
                    ActionRun.action_type_code == type_id,
                    ActionRun.in_center.is_(False),
                    ActionRun.submitted_at >= until - timedelta(days=days),
                    ActionRun.submitted_at <= until,
                )
                .group_by(ActionRun.status)
            )
        ).all()
        counts = {status: count for status, count, _ in groups}
        total = sum(counts.values())
        ok = counts.get(ActionStatus.succeeded, 0) + counts.get(ActionStatus.reconciled, 0)
        pending = sum(
            counts.get(s, 0) for s in (ActionStatus.staged, ActionStatus.approving, ActionStatus.executing)
        )
        done = total - pending - counts.get(ActionStatus.withdrawn, 0)
        return {
            "asOf": _fmt_dt(until),
            "total": total,
            "ok": ok,
            "failed": counts.get(ActionStatus.failed, 0),
            "unknown": counts.get(ActionStatus.unknown, 0),
            "pending": pending,
            "confirm": counts.get(ActionStatus.staged, 0),
            "rate": ok / done if done else 0,
            "avgMs": int(sum(elapsed or 0 for _, _, elapsed in groups) / total + 0.5) if total else 0,
        }

    async def groups(self, defs: list[dict[str, Any]]) -> list[str]:
        """['全部'] + 分组（按首次出现顺序）；前端 ACTION_GROUPS 是常量，mock 里与此一致 [推断]。"""
        seen: list[str] = []
        for d in defs:
            if d["group"] not in seen:
                seen.append(d["group"])
        return [ALL, *seen]

    async def search(
        self, ctx: ViewContext, group: str | None, kw: str | None
    ) -> tuple[list[dict[str, Any]], list[str]]:
        defs = await self.defs(ctx)
        groups = await self.groups(defs)
        rows = [
            d
            for d in defs
            if (not group or group == ALL or d["group"] == group) and (not kw or kw in d["name"])
        ]
        return rows, groups

    async def detail(self, ctx: ViewContext, type_id: str) -> dict[str, Any]:
        from app.modules.action.models import ActionTypeVersion
        from app.modules.action.policy import parse, stored

        value = await self.one(ctx, type_id)
        if ctx.ver:
            raw = value.get("policy")
        else:
            definition = await self.session.scalar(
                select(ActionTypeVersion.definition)
                .join(ActionType)
                .where(ActionType.code == type_id, ActionTypeVersion.ver == value.get("version"))
            )
            raw = (definition or {}).get("policy")
        status = "unconfigured"
        if raw is not None:
            try:
                parse(raw)
                status = "configured"
            except ValueError:
                status = "invalid"
        return {
            **with_system(value, await self.repo.systems()),
            "policy": stored(raw).dump(),
            "policyStored": raw,
            "policyStatus": status,
        }

    async def used_by(self, ctx: ViewContext, type_id: str) -> list[dict[str, Any]]:
        """关系成员随定义版本；告警总数 / 待处理数始终取当前记录。"""
        ids = (await self.one(ctx, type_id)).get("usedBy") or []
        if not ids:
            return []
        modes: dict[str, str] = {}
        if ctx.ver:
            release = await ReleaseRepo(self.session).by_ver_or_404(ctx.ver)
            rules = (release.snapshot or {}).get("rules") or {}
        else:
            rows = (await self.session.scalars(select(Rule).where(Rule.code.in_(ids)))).all()
            rules = {r.code: {"name": r.name, "trigger": r.trigger, "autoStage": r.auto_stage} for r in rows}
            links = (
                await self.session.scalars(
                    select(ActionTypeRule).join(ActionType).where(ActionType.code == type_id)
                )
            ).all()
            modes = {r.rule_code: r.mode_text for r in links if r.mode_text}
        counts: dict[str, dict[str, int]] = {}
        for code, status, count in (
            await self.session.execute(
                select(Alert.rule_code, Alert.status, func.count())
                .where(Alert.rule_code.in_(ids))
                .group_by(Alert.rule_code, Alert.status)
            )
        ).all():
            c = counts.setdefault(code, {"alerts": 0, "openAlerts": 0})
            c["alerts"] += count
            if status == AlertStatus.open:
                c["openAlerts"] += count
        out = []
        for code in ids:
            rule = rules.get(code)
            mode = "规则定义不可用"
            trigger = "—"
            if rule:
                trigger = (rule.get("trigger") or {}).get("kind") or "—"
                stage = rule.get("autoStage") or {}
                mode = "处理告警时手动执行"
                if stage.get("enabled") and stage.get("actionTypeId") == type_id:
                    confirmer = stage.get("confirmer") or "确认人"
                    mode = f"命中后生成动作 · 待 {confirmer} 确认后提交"
            out.append(
                {
                    "ruleId": code,
                    "ruleName": rule.get("name") if rule else f"{code}（定义不可用）",
                    "trigger": trigger,
                    "mode": modes.get(code) or mode,
                    **counts.get(code, {"alerts": 0, "openAlerts": 0}),
                }
            )
        return out

    async def writeback(self, ctx: ViewContext, type_id: str) -> dict[str, Any]:
        systems = await self.repo.systems()
        a = with_system(await self.one(ctx, type_id), systems)
        wb = a.get("writeback") or {}
        return {
            "config": {"sysId": a.get("sysId"), "endpoint": a.get("endpoint"), "idemKey": a.get("idemKey"), "httpMapping": a.get("httpMapping")},
            "edits": wb.get("edits", []),
            "effects": wb.get("effects", []),
            "flow": flow_of(a, systems.get(a.get("sysId") or ""), await self.repo.rule_action()),
        }

    async def versions(self, ctx: ViewContext, type_id: str) -> list[dict[str, Any]]:
        """和详情一样按视图判断可见：已下线（或 ?ver= 那一版里没有）的类型 404。"""
        await self.one(ctx, type_id)
        at: ActionType = await self.repo.get_by_code_or_404(type_id)
        return [
            {
                "ver": v.ver,
                "release": v.release_ver,
                "status": "生效中" if v.ver == at.version else "已替代",
                "by": v.by_name,
                "at": v.at,
                "change": v.change_note,
            }
            for v in await self.repo.versions(at.id)
        ]


async def run_daily(session: AsyncSession, type_id: str, days: int) -> list[dict[str, Any]]:
    """近 N 天每日执行量（palantir-r2，读 action_run_daily_stat）：截至今天，缺的天补 0。
    运行数据不分版本，`?ver=` 不影响，按当前 action_type 表找类型（不存在 404）[推断]。"""
    repo = ActionTypeRepo(session)
    at = await repo.get_by_code_or_404(type_id)
    start, end = clock.last_days(days)
    rows = await repo.run_daily(at.id, start, end)
    out = []
    for i in range(days):
        d = start + timedelta(days=i)
        r = rows.get(d)
        out.append(
            {
                "date": d.strftime("%m-%d"),
                "ok": r.ok if r else 0,
                "failed": r.failed if r else 0,
                "unknown": r.unknown if r else 0,
            }
        )
    return out
