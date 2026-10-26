"""动作类型读取（当前版本读展开表，`?ver=` 读发布快照 actionTypes 段）。

服务层统一产出 mock 形状的 dict
（字段名、中文枚举和前端 ACTION_TYPES / paramsOf / condsOf / writebackOf 一致），
路由再用 schemas 校验输出。
"""

import re
from collections.abc import Callable, Sequence
from datetime import timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.common.context import ViewContext
from app.core import clock
from app.core.errors import NotFound
from app.modules.action.models import ActionType
from app.modules.action.repo import ActionTypeRepo
from app.modules.action.sections import SECTION, action_def
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
      → 退回当前定义表 [推断：和前端一致，前端历史视图显示的就是当前 mock]；
      下线过滤：V.id ≥ retired_release_id 的类型不出现（§4.2）。
    """
    order = {r.code: i for i, r in enumerate(rows)}
    if not ctx.ver:
        return [to_def(r) for r in rows if r.retired_release_id is None]
    rel = await ReleaseRepo(session).by_ver_or_404(ctx.ver)
    hidden = {r.code for r in rows if r.retired_release_id is not None and rel.id >= r.retired_release_id}
    snap = (rel.snapshot or {}).get(section)
    defs: dict[str, dict[str, Any]] = snap if isinstance(snap, dict) else {r.code: to_def(r) for r in rows}
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
        return await defs_for_view(self.session, ctx, await self.repo.all_ordered(), SECTION, action_def)

    async def one(self, ctx: ViewContext, type_id: str) -> dict[str, Any]:
        return find(await self.defs(ctx), type_id, "动作类型不存在")

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
        return with_system(await self.one(ctx, type_id), await self.repo.systems())

    async def writeback(self, ctx: ViewContext, type_id: str) -> dict[str, Any]:
        systems = await self.repo.systems()
        a = with_system(await self.one(ctx, type_id), systems)
        wb = a.get("writeback") or {}
        return {
            "config": {"sysId": a.get("sysId"), "endpoint": a.get("endpoint"), "idemKey": a.get("idemKey")},
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
