"""单个对象实例的动态摘要（问数 / AI 只读）：未关闭告警、近期规则命中、生命周期、近期动作。

可见性与各列表接口完全一致：
- 对象本身先过 visible_references（与告警维度同一行级 + 唯一编码谓词），不可见 = 404，不区分“不存在”；
- 每一段再各自走 scoped_instances（告警 / 命中 / 动作沿用各自列表的实现；生命周期变化按对象类型 + objId）；
- 路由入口要求 object-types view；告警 / 规则命中 / 动作段另需 alerts / rules / action-center view，
  缺权限的段返回 {"available": false, "reason": "no-permission"}。
全部按元数据（对象类型目录、生命周期定义、数据绑定）取值，不写死任何业务对象。"""

import re
from typing import Any

from sqlalchemy import func, literal, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.auth import CurrentUser, check_func
from app.common.pagination import PageParams
from app.core.errors import AppError, Forbidden, NotFound
from app.modules.dynamic.models import Alert
from app.modules.dynamic.object_behavior import object_head
from app.modules.dynamic.row_scope import scoped_instances, visible_references
from app.modules.dynamic.rules_models import Lifecycle, LifecycleChange, Rule, RuleHit
from app.modules.dynamic.rules_service import LifecycleService, RuleService
from app.modules.dynamic.service import ALERT_TABS, ActionService, AlertService, kind_label

SUMMARY_MAX_LIMIT = 50
NO_PERMISSION = {"available": False, "reason": "no-permission"}


def _state_key(bound: Any, field: str) -> str | None:
    """生命周期 field 是展示文本（如「状态（status）」）：按属性编码、括号里的编码、显示名对上已接入属性。"""
    m = re.search(r"[（(]\s*([^（）()]+?)\s*[）)]\s*$", field)
    for want in (field, m.group(1) if m else None):
        if want and want in bound.out:
            return want
    return next((k for k in bound.out if getattr(bound.props.get(k), "display_name", None) == field), None)


def _dump(model: Any) -> Any:
    return model.model_dump(by_alias=True, mode="json")


class ObjectDynamicSummary:
    def __init__(self, session: AsyncSession, user: CurrentUser, catalog: Any, ports: Any) -> None:
        self.session = session
        self.user = user
        self.catalog = catalog
        self.alerts = AlertService(session, user, catalog, ports)
        self.actions = ActionService(session, user, catalog, ports)

    def _can(self, page_key: str) -> bool:
        try:
            check_func(self.user, page_key, "view")
        except Forbidden:
            return False
        return True

    async def get(self, kind: str, obj_id: str, *, limit: int) -> dict[str, Any]:
        await object_head(self.session, kind)  # 对象类型不存在 → 404
        if (kind, obj_id) not in await visible_references(self.session, self.user, [(kind, obj_id)]):
            raise NotFound("对象不存在", details={"objKind": kind, "id": obj_id})
        sections = {
            "alerts": ("alerts", self._alerts),
            "ruleHits": ("rules", self._hits),
            "lifecycle": ("object-types", self._lifecycle),
            "actionRuns": ("action-center", self._actions),
        }
        allowed = {name: self._can(page) for name, (page, _) in sections.items()}
        if not any(allowed.values()):
            raise Forbidden("没有此页面的操作权限", details={"pages": [p for p, _ in sections.values()]})
        out: dict[str, Any] = {
            "objKind": kind,
            "objKindLabel": kind_label(self.catalog, kind),
            "id": obj_id,
            "limit": limit,
        }
        for name, (_, load) in sections.items():
            out[name] = await load(kind, obj_id, limit) if allowed[name] else dict(NO_PERMISSION)
        labels = [
            x.get("objLabel")
            for key in ("alerts", "ruleHits", "actionRuns")
            for x in out[key].get("latest", [])
        ]
        labels += [x.get("objLabel") for x in out["lifecycle"].get("changes", [])]
        out["objLabel"] = next((x for x in labels if x), obj_id)
        return out

    async def _alerts(self, kind: str, obj_id: str, limit: int) -> dict[str, Any]:
        """未关闭（待处理 + 处理中）告警：条数、各状态条数、最近 limit 条（同 GET /alerts 的范围与输出）。"""
        open_statuses = set(ALERT_TABS["open"] or ()) | set(ALERT_TABS["doing"] or ())
        base = await self.alerts._filtered(
            select(Alert), status="all", level=None, rule_id=None, obj_kind=kind, kw=None
        )
        base = base.where(Alert.obj_id == obj_id, Alert.status.in_(open_statuses))
        sub = base.subquery()
        by_status = {
            s.value: n
            for s, n in (
                await self.session.execute(select(sub.c.status, func.count()).group_by(sub.c.status))
            )
        }
        rows = list(
            await self.session.scalars(base.order_by(Alert.triggered_at.desc(), Alert.id.desc()).limit(limit))
        )
        items = await self.alerts._visible_list(rows, await self.alerts._links(rows))
        return {
            "available": True,
            "openCount": sum(by_status.values()),
            "byStatus": by_status,
            "latest": [_dump(x) for x in items],
        }

    async def _hits(self, kind: str, obj_id: str, limit: int) -> dict[str, Any]:
        """规则命中（只追加）：总条数 + 最近 limit 条（同规则命中列表的行级范围与输出）。"""
        stmt = await scoped_instances(
            select(RuleHit).where(RuleHit.obj_kind == kind, RuleHit.obj_id == obj_id),
            self.user,
            RuleHit.obj_kind,
            RuleHit.obj_id,
        )
        total = int(await self.session.scalar(select(func.count()).select_from(stmt.subquery())) or 0)
        hits = list(
            await self.session.scalars(stmt.order_by(RuleHit.at.desc(), RuleHit.id.desc()).limit(limit))
        )
        rules = dict(
            (
                await self.session.execute(
                    select(Rule.id, Rule.code).where(Rule.id.in_({h.rule_id for h in hits}))
                )
            ).all()
        )
        alert_ids = {h.alert_id for h in hits if h.alert_id}
        alerts = (
            {a.id: a for a in await self.session.scalars(select(Alert).where(Alert.id.in_(alert_ids)))}
            if alert_ids
            else {}
        )
        return {
            "available": True,
            "total": total,
            "latest": [
                RuleService.hit_out(
                    h, rules.get(h.rule_id, ""), alerts.get(h.alert_id) if h.alert_id else None
                )
                for h in hits
            ],
        }

    async def _lifecycle(self, kind: str, obj_id: str, limit: int) -> dict[str, Any]:
        """生命周期（对象类型配置了才有）：当前状态、最近变化、非法迁移（transition 为空）条数与最近几条。
        当前状态优先读对象数据里的生命周期字段（同对象详情的绑定与行级范围），取不到退回最近一次变化。"""
        lc = await self.session.scalar(select(Lifecycle).where(Lifecycle.code == kind))
        if lc is None:
            return {"available": True, "defined": False}
        base = await scoped_instances(
            select(LifecycleChange).where(
                LifecycleChange.lifecycle_id == lc.id, LifecycleChange.obj_id == obj_id
            ),
            self.user,
            literal(kind),
            LifecycleChange.obj_id,
        )
        order = (LifecycleChange.at.desc(), LifecycleChange.code.desc())
        svc = LifecycleService(self.session, self.user)
        changes = list(await self.session.scalars(base.order_by(*order).limit(limit)))
        illegal = base.where(LifecycleChange.transition_id.is_(None))
        illegal_total = int(
            await self.session.scalar(select(func.count()).select_from(illegal.subquery())) or 0
        )
        illegal_rows = list(await self.session.scalars(illegal.order_by(*order).limit(limit)))
        total = int(await self.session.scalar(select(func.count()).select_from(base.subquery())) or 0)
        state, source = await self._object_state(kind, obj_id, lc.field), "object"
        if state is None and changes:
            state, source = changes[0].to_state, "lastChange"
        return {
            "available": True,
            "defined": True,
            "lifecycleId": lc.code,
            "field": lc.field,
            "current": {
                "state": state,
                "source": source if state is not None else None,  # object = 对象数据；lastChange = 变化记录
                "lastChangeTo": changes[0].to_state if changes else None,
            },
            "changeTotal": total,
            "changes": await svc.change_rows(changes),
            "illegalTotal": illegal_total,
            "illegal": await svc.change_rows(illegal_rows),
        }

    async def _object_state(self, kind: str, obj_id: str, field: str) -> str | None:
        from app.modules.qa.explore_api import _bind

        try:
            _, bound = await _bind(self.session, kind)
            key = _state_key(bound, field) if bound is not None else None
            if bound is None or key is None:
                return None
            stmt = (await bound.stmt(self.user)).where(bound.id_col == bound.id_value(obj_id))
            async with bound.read(self.session) as reader:
                row = (await reader.execute(stmt.limit(1))).mappings().first()
        except AppError:  # 未接入 / 无对象读取权限 / 数据源不可用：退回变化记录
            return None
        return None if row is None or row[key] is None else str(row[key])

    async def _actions(self, kind: str, obj_id: str, limit: int) -> dict[str, Any]:
        """动作执行：总条数 + 最近 limit 条（直接复用 GET /action-runs 的 search）。"""
        page = await self.actions.search(
            tab=None,
            status=None,
            scope="all",
            page=PageParams(page=1, size=limit),
            action=None,
            action_type=None,
            sys_id=None,
            obj_kind=kind,
            obj_id=obj_id,
            alert_id=None,
            rule_id=None,
            session_id=None,
            kw=None,
        )
        return {
            "available": True,
            "total": page.total,
            "statusCounts": page.status_counts,
            "latest": [_dump(x) for x in page.items],
        }
