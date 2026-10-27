"""规则与自动化 / 情景推演 / 生命周期的业务逻辑（dynamic-api.md §2.3–2.5、§4、§6.3–6.5）。

- 出参是前端 mock 形状的 dict（RuleDef、Hit、Scenario、Lifecycle…）；分页用骨架 Page.of；
- 规则 / 生命周期草稿与静态 CR 同事务登记并发布写回；
- 规则有限条件推演/保留事件回放只读真实DB；没有执行口径明确422；预测推演未接入时503。
"""

import json
from copy import deepcopy
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.auth import CurrentUser
from app.common.pagination import Page, PageParams
from app.core.clock import now
from app.core.errors import AppError, NotFound
from app.core.schema import _fmt_dt, _fmt_dt_seconds, parse_display_dt
from app.db.dialects import get_adapter
from app.modules.action.models import ActionType
from app.modules.dynamic.cr_link import CrLink, lifecycle_sections, rule_sections
from app.modules.dynamic.models import ActionRun, ActionStatus, Alert, AlertLevel
from app.modules.dynamic.repo import ScenarioImpactRepo, ScenarioRepo, recent_rule_hits
from app.modules.dynamic.row_scope import scoped_instances
from app.modules.dynamic.rules_models import (
    HitResult,
    LcStateKind,
    Lifecycle,
    LifecycleChange,
    LifecycleDraft,
    LifecycleTransition,
    LifecycleVersion,
    Rule,
    RuleDraft,
    RuleHit,
    RuleOutput,
    RuleStatus,
    Scenario,
    ScenarioImpact,
    ScenarioResult,
    ScenarioStatus,
)
from app.modules.dynamic.rules_schemas import (
    LifecycleDraftIn,
    ReplayIn,
    RuleCreateIn,
    RuleDraftIn,
    ScenarioRunIn,
)
from app.modules.dynamic.scenario_templates import (
    defaults,
    driver_fields,
    scope_options,
    template_of,
    templates,
)
from app.modules.dynamic.scenario_templates import no_driver as tpl_no_driver
from app.modules.dynamic.scenario_templates import validate as tpl_validate
from app.modules.dynamic.sections import lifecycle_def, lifecycle_head, rule_def, version_row
from app.modules.static.models import ChangeRequest, CRStatus, ObjectStatus, ObjectType
from app.modules.static.schemas import TargetIn
from app.modules.versioning.models import OntologyRelease

# 仅兼容旧测试/历史说明；真实查询使用now()滚动窗口，不使用这些历史常量。
CUT_7D = "2026-10-18 23:59:59"
CUT_30D = "2026-09-25 00:00:00"
SINCE_7D = "2026-10-19"


def fail(code: str, message: str, status: int, **details: Any) -> AppError:
    return AppError(message, code=code, status_code=status, details=details or None)


def validation(fields: list[tuple[str, str]]) -> AppError:
    return AppError(
        "参数校验失败",
        code="VALIDATION_FAILED",
        status_code=422,
        details={"fields": [{"field": f, "message": m} for f, m in fields]},
    )


def sec(dt: datetime) -> str:
    return _fmt_dt_seconds(dt)


# ======================================================================
# 规则
# ======================================================================


def dedupe_reason(d: dict[str, Any]) -> str:
    """rules.ts dedupeReason。"""
    dd, cd = d["dedupe"], d["cooldown"]
    if "事件" in dd or "单号" in dd:
        return f"同一{dd.replace(' ID', '', 1)}已命中过"
    skip = {"规则", "日期", "周", "小时", "生效日期", "批次"}
    dims = "".join(x.strip() for x in dd.split("+") if x.strip() not in skip)
    return f"同一{dims}已命中过" if cd == "不重复" else f"同一{dims} {cd}内已命中"


class RuleService:
    def __init__(self, session: AsyncSession, user: CurrentUser) -> None:
        self.session = session
        self.user = user
        self.cr_states: dict[str, CRStatus] = {}

    # ---------- 读 ----------
    async def _actions(self) -> dict[int, str]:
        return {i: c for i, c in (await self.session.execute(select(ActionType.id, ActionType.code))).all()}

    async def get(self, code: str) -> Rule:
        r = await self.session.scalar(
            select(Rule).where(Rule.code == code).execution_options(populate_existing=True)
        )
        if r is None:
            raise NotFound("规则不存在", details={"id": code})
        return r

    async def _drafts(self, rule_ids: set[int] | None = None) -> dict[int, RuleDraft]:
        if rule_ids is not None and not rule_ids:
            return {}
        statement = select(RuleDraft).order_by(RuleDraft.id)
        if rule_ids is not None:
            statement = statement.where(RuleDraft.rule_id.in_(rule_ids))
        rows = (await self.session.scalars(statement)).all()
        codes = {d.cr_code for d in rows}
        if codes:
            self.cr_states.update(
                dict(
                    (
                        await self.session.execute(
                            select(ChangeRequest.code, ChangeRequest.status).where(
                                ChangeRequest.code.in_(codes)
                            )
                        )
                    ).all()
                )
            )
        return {d.rule_id: d for d in rows}

    @staticmethod
    def display_status(r: Rule, d: RuleDraft | None, cr_status: CRStatus | None = None) -> dict[str, Any]:
        """有真实变更单时使用其审核状态；旧孤立草稿仍显示草稿中。"""
        if d is None:
            return {"text": "草稿中" if r.status == RuleStatus.changing else r.status.value, "cr": None}
        return {"text": "审核中" if cr_status == CRStatus.in_review else "草稿中", "cr": d.cr_code}

    def _def(self, r: Rule, actions: dict[int, str]) -> dict[str, Any]:
        return rule_def(r, actions.get(r.action_type_id) if r.action_type_id else None)

    async def list_rules(
        self,
        params: PageParams,
        *,
        trigger: str | None,
        status: str | None,
        kw: str | None,
        obj_kind: str | None = None,
    ) -> Page[dict[str, Any]]:
        has_draft = select(RuleDraft.id).where(RuleDraft.rule_id == Rule.id).exists()
        statement = select(Rule).where(or_(Rule.version.is_not(None), has_draft))
        if obj_kind:
            if "\x00" in obj_kind or len(obj_kind) > 64:
                raise validation([("objKind", "对象类型编号无效")])
            statement = statement.where(Rule.obj_kind == obj_kind)
        if trigger and trigger != "全部":
            if trigger not in ("定时", "事件"):
                return Page.of([], 0, params)
            statement = statement.where(Rule.trigger["kind"].as_string() == trigger)
        if status and status != "全部":
            cr_state = (
                select(ChangeRequest.status)
                .select_from(RuleDraft)
                .outerjoin(ChangeRequest, ChangeRequest.code == RuleDraft.cr_code)
                .where(RuleDraft.rule_id == Rule.id)
                .order_by(RuleDraft.id.desc())
                .limit(1)
                .scalar_subquery()
            )
            if status == "草稿中":
                statement = statement.where(
                    or_(
                        has_draft & or_(cr_state.is_(None), cr_state != CRStatus.in_review),
                        ~has_draft & (Rule.status == RuleStatus.changing),
                    )
                )
            elif status == "审核中":
                statement = statement.where(has_draft, cr_state == CRStatus.in_review)
            else:
                stored_status = next((s for s in RuleStatus if s.value == status), None)
                if stored_status is None:
                    return Page.of([], 0, params)
                statement = statement.where(~has_draft, Rule.status == stored_status)
        if kw:
            if "\x00" in kw or len(kw) > 200:
                raise validation([("kw", "搜索文字不能超过200字或包含空字符")])
            statement = statement.where(
                or_(Rule.name.contains(kw, autoescape=True), Rule.code.icontains(kw, autoescape=True))
            )
        total = int(await self.session.scalar(select(func.count()).select_from(statement.subquery())) or 0)
        page = params.clamp(total)
        rows = list(
            await self.session.scalars(statement.order_by(Rule.id).limit(page.size).offset(page.offset))
        )
        ids = {r.id for r in rows}
        drafts = await self._drafts(ids)
        action_ids = {r.action_type_id for r in rows if r.action_type_id is not None}
        actions = (
            dict(
                (
                    await self.session.execute(
                        select(ActionType.id, ActionType.code).where(ActionType.id.in_(action_ids))
                    )
                ).all()
            )
            if action_ids
            else {}
        )
        out = []
        weekly = await recent_rule_hits(self.session, until=now(), rule_ids=ids, user=self.user)
        for r in rows:
            draft = drafts.get(r.id)
            st = self.display_status(r, draft, self.cr_states.get(draft.cr_code) if draft else None)
            out.append(
                {
                    **self._def(r, actions),
                    "weekly": weekly.get(r.id, 0),
                    "displayStatus": st["text"],
                    "cr": st["cr"],
                }
            )
        return Page.of(out, total, params)

    async def meta(self) -> dict[str, Any]:
        from app.modules.action.models import TypeStatus
        from app.modules.dynamic.catalog import get_catalog
        from app.modules.event.models import EventPayloadField, EventType

        rows = (
            await self.session.scalars(select(Rule).where(Rule.version.is_not(None)).order_by(Rule.id))
        ).all()
        drafts = await self._drafts()
        statuses = list(
            dict.fromkeys(
                self.display_status(
                    r, drafts.get(r.id), self.cr_states.get(drafts[r.id].cr_code) if r.id in drafts else None
                )["text"]
                for r in rows
            )
        )
        catalog = await get_catalog(self.session, self.user)
        confirmers = [p["name"] for p in catalog.data["people"] if catalog.person(p["name"])]
        sources = {"告警.对象", "告警.ID", "命中.对象", "规则.命中说明", "固定值"}
        for r in rows:
            stage = r.auto_stage or {}
            legacy = stage.get("confirmer")
            if legacy and catalog.person(legacy):
                confirmers.append(legacy)
            sources.update(m["from"] for m in stage.get("mapping", []) if isinstance(m.get("from"), str))
        for field in await self.session.scalars(
            select(EventPayloadField)
            .join(EventType, EventType.id == EventPayloadField.event_type_id)
            .where(EventType.status != TypeStatus.draft, EventType.retired_release_id.is_(None))
        ):
            sources.update([f"事件.{field.api}", f"事件.{field.name}"])
        return {
            "confirmers": list(dict.fromkeys(confirmers)),
            "mappingSources": sorted(sources),
            "triggers": ["全部", "定时", "事件"],
            "statuses": ["全部", *statuses],
        }

    async def draft_def(self, r: Rule, d: RuleDraft | None, actions: dict[int, str]) -> dict[str, Any] | None:
        if d is None:
            return None
        return {**self._def(r, actions), **d.patch, "version": d.version}

    async def detail(self, code: str, *, view: str | None) -> dict[str, Any]:
        r = await self.get(code)
        actions = await self._actions()
        d = (await self._drafts({r.id})).get(r.id)
        base = self._def(r, actions)
        draft = await self.draft_def(r, d, actions)
        st = self.display_status(r, d, self.cr_states.get(d.cr_code) if d else None)
        hits7 = (
            await recent_rule_hits(self.session, until=now(), rule_ids={r.id}, user=self.user)
        ).get(r.id, 0)
        base["weekly"] = hits7
        if draft is not None:
            draft["weekly"] = hits7
        pending = await self.session.scalar(
            await scoped_instances(
                select(func.count())
                .select_from(ActionRun)
                .where(ActionRun.rule_code == r.code, ActionRun.status == ActionStatus.staged),
                self.user, ActionRun.obj_kind, ActionRun.obj_id,
            )
        )
        return {
            **base,
            "displayStatus": st["text"],
            "cr": st["cr"],
            "draft": draft,
            "view": "draft" if view == "draft" and draft else "published",
            "editable": view == "draft" and draft is not None,
            "editTip": "" if view == "draft" and draft else "已发布版本只读，请先发起变更",
            "dedupeReason": dedupe_reason(base),
            "hits7d": hits7,
            "stagedPending": pending,
        }

    async def _hits(self, r: Rule) -> list[RuleHit]:
        return list(
            (
                await self.session.scalars(
                    await scoped_instances(
                        select(RuleHit).where(RuleHit.rule_id == r.id).order_by(RuleHit.seq),
                        self.user, RuleHit.obj_kind, RuleHit.obj_id,
                    )
                )
            ).all()
        )

    async def _alerts(self, ids: set[int]) -> dict[int, Alert]:
        if not ids:
            return {}
        return {a.id: a for a in (await self.session.scalars(select(Alert).where(Alert.id.in_(ids)))).all()}

    @staticmethod
    def hit_out(h: RuleHit, rule_code: str, alert: Alert | None) -> dict[str, Any]:
        """Hit（rules.ts）：告警命中的 result = 告警当前状态；其余 = 已通知 / 已去重。"""
        return {
            "id": h.code,
            "ruleId": rule_code,
            "at": sec(h.at),
            "objKind": h.obj_kind,
            "objId": h.obj_id,
            "objLabel": h.obj_label,
            "dims": list(h.dims or []),
            "value": h.value,
            "threshold": h.threshold,
            "alertId": alert.code if alert else None,
            "result": alert.status.value if (alert and h.result == HitResult.alert) else h.result.value,
        }

    async def hit_dicts(self, r: Rule) -> list[dict[str, Any]]:
        hits = await self._hits(r)
        alerts = await self._alerts({h.alert_id for h in hits if h.alert_id})
        return [self.hit_out(h, r.code, alerts.get(h.alert_id) if h.alert_id else None) for h in hits]

    async def hits(self, code: str, params: PageParams, *, range_: str) -> Page[dict[str, Any]]:
        r = await self.get(code)
        until = now()
        since = until - timedelta(days=7 if range_ == "7d" else 30)
        statement = select(RuleHit).where(RuleHit.rule_id == r.id, RuleHit.at >= since, RuleHit.at <= until)
        statement = await scoped_instances(statement, self.user, RuleHit.obj_kind, RuleHit.obj_id)
        total = int(await self.session.scalar(select(func.count()).select_from(statement.subquery())) or 0)
        page = params.clamp(total)
        hits = list(
            await self.session.scalars(
                statement.order_by(RuleHit.at.desc(), RuleHit.id.desc()).limit(page.size).offset(page.offset)
            )
        )
        alerts = await self._alerts({h.alert_id for h in hits if h.alert_id})
        return Page.of(
            [self.hit_out(h, r.code, alerts.get(h.alert_id) if h.alert_id else None) for h in hits],
            total,
            params,
        )

    async def hit(self, code: str) -> tuple[Rule, RuleHit, dict[str, Any], Alert | None]:
        h = await self.session.scalar(
            await scoped_instances(
                select(RuleHit).where(RuleHit.code == code), self.user, RuleHit.obj_kind, RuleHit.obj_id,
            )
        )
        if h is None:
            raise NotFound("命中记录不存在", details={"id": code})
        r = await self.session.get(Rule, h.rule_id)
        assert r is not None
        alert = (await self._alerts({h.alert_id})).get(h.alert_id) if h.alert_id else None
        return r, h, self.hit_out(h, r.code, alert), alert

    async def versions(self, code: str) -> list[dict[str, Any]]:
        from app.modules.dynamic.rules_models import RuleVersion

        r = await self.get(code)
        d = (await self._drafts()).get(r.id)
        rows = (
            await self.session.scalars(
                select(RuleVersion).where(RuleVersion.rule_id == r.id).order_by(RuleVersion.version.desc())
            )
        ).all()
        out = []
        if d is not None:
            out.append(
                {
                    "ver": d.version,
                    "status": "草稿",
                    "by": d.by,
                    "at": _fmt_dt(d.at) if d.at else None,
                    "change": d.change,
                    "cr": d.cr_code,
                }
            )
        for v in rows:
            out.append(
                {
                    "ver": v.version,
                    "status": "生效中" if v.version == r.version else "已替代",
                    "by": v.by,
                    "at": _fmt_dt(v.at),
                    "change": v.change,
                }
            )
        return out

    async def replay(self, code: str, body: ReplayIn) -> dict[str, Any]:
        from app.modules.dynamic.rule_runtime import run_rule
        from app.modules.static.permissions import check_cr

        r = await self.get(code)
        base = self._def(r, await self._actions())
        definition = base
        if body.version == "draft":
            draft = (await self._drafts({r.id})).get(r.id)
            if draft is None:
                raise fail("RULE_DRAFT_MISSING", "规则没有已保存草稿", 422)
            cr = await self.session.scalar(select(ChangeRequest).where(ChangeRequest.code == draft.cr_code))
            if cr is None or cr.status == CRStatus.published:
                raise fail("RULE_DRAFT_UNAVAILABLE", "草稿缺少有效变更单，不能执行", 422)
            await check_cr(self.session, cr, self.user)
            definition = {**base, **deepcopy(draft.patch), "version": draft.version}
        elif r.version is None:
            raise fail("RULE_NOT_PUBLISHED", "规则没有当前已发布版本，请选择已保存草稿", 422)
        return await run_rule(self.session, self.user, base, definition, body)

    async def _next_code(self) -> str:
        await get_adapter(self.session).xact_lock(self.session, "dynamic.rule.code")
        codes = (await self.session.scalars(select(Rule.code))).all()
        n = max((int(c[2:]) for c in codes if c.startswith("R-") and c[2:].isdigit()), default=0)
        return f"R-{n + 1:03d}"

    async def create(self, body: RuleCreateIn) -> dict[str, Any]:
        """新建规则：未发布（version=null，不进快照），状态变更中；传 cr 时同时开草稿（v1，改动为空）。"""
        name = (body.name or "").strip() or "未命名规则"
        kind = body.obj_kind or await self._first_obj_kind()
        if not kind or not await self.session.scalar(
            select(ObjectType.id).where(ObjectType.code == kind, ObjectType.status == ObjectStatus.published)
        ):
            raise validation([("objKind", "请选择已发布的真实对象类型（当前规则模型编号上限32字）")])
        r = Rule(
            code=await self._next_code(),
            version=None,
            name=name,
            desc="",
            owner=self.user.name,
            status=RuleStatus.changing,
            core=False,
            obj_kind=kind,
            level=AlertLevel.medium,
            output=RuleOutput.alert,
            conds=[],
            logic="且",
            trigger={"kind": "定时", "cron": "0 8 * * *", "text": "每天 08:00"},
            auto_stage={"enabled": False, "actionTypeId": "", "mapping": [], "confirmer": ""},
            is_seed=False,
        )
        self.session.add(r)
        await self.session.flush()
        if body.cr:
            await self._open(r, body.cr)
            cur = await self.draft_def(r, (await self._drafts())[r.id], await self._actions())
            assert cur is not None
            await CrLink(self.session, self.user).put(
                body.cr, self._target(r), {"config": rule_sections(None, cur)["config"]}, None
            )
        return await self.detail(r.code, view="draft" if body.cr else None)

    async def _first_obj_kind(self) -> str | None:
        """仅使用当前规则模型能存储的真实已发布类型；空库不能凭空退回product。"""
        return await self.session.scalar(
            select(ObjectType.code)
            .where(ObjectType.status == ObjectStatus.published, func.length(ObjectType.code) <= 32)
            .order_by(ObjectType.id)
            .limit(1)
        )

    @staticmethod
    def _target(r: Rule) -> TargetIn:
        return TargetIn(kind="rule", id=r.code, is_new=r.version is None, name=r.name)

    async def _open(self, r: Rule, cr: str) -> RuleDraft:
        d = (await self._drafts()).get(r.id)
        if d is not None:
            if d.cr_code != cr:
                msg = f"该规则已在变更单 {d.cr_code} 的草稿里"
                raise fail("TARGET_IN_OTHER_CR", msg, 409, crId=d.cr_code)
            return d
        from app.modules.dynamic.rules_models import RuleVersion

        historical = await self.session.scalar(
            select(func.max(RuleVersion.version)).where(RuleVersion.rule_id == r.id)
        )
        d = RuleDraft(
            rule_id=r.id,
            cr_code=cr,
            version=max(historical or 0, r.version or 0) + 1,
            patch={},
            by=self.user.name,
        )
        self.session.add(d)
        if r.status != RuleStatus.disabled:  # 停用规则开草稿不改状态，放弃后仍是停用（不被误启用）
            r.status = RuleStatus.changing
        await self.session.flush()
        return d

    async def open_draft(self, code: str, cr: str) -> dict[str, Any]:
        """发起变更 / 加入已有变更单：登记规则草稿（幂等；已在别的草稿单里 409）。"""
        r = await self.get(code)
        await self._open(r, cr)
        await CrLink(self.session, self.user).join(cr, self._target(r))
        return await self.detail(code, view="draft")

    async def discard_draft(self, code: str, cr: str | None) -> None:
        """放弃本规则草稿：删草稿行；从未发布过的新规则连同规则一起删。"""
        r = await self.get(code)
        d = (await self._drafts()).get(r.id)
        if d is None or (cr and cr != d.cr_code):
            raise NotFound("该规则没有草稿", details={"id": code, "cr": cr})
        # 同一事务：先删变更单里的登记和草稿行（单子已提交 → CR_LOCKED，整体回滚，不留孤儿），再删规则草稿
        await CrLink(self.session, self.user).discard(d.cr_code, "rule", r.code)
        await self.session.delete(d)
        if r.version is None:
            from app.modules.dynamic.rules_models import RuleVersion

            has_history = await self.session.scalar(
                select(RuleVersion.id).where(RuleVersion.rule_id == r.id).limit(1)
            )
            if has_history is None:
                await self.session.flush()
                await self.session.delete(r)
        elif r.status == RuleStatus.changing:
            r.status = RuleStatus.published
        await self.session.flush()

    async def save_draft(self, code: str, body: RuleDraftIn) -> dict[str, Any]:
        """R7 保存到草稿：只有在变更单里的规则能改；自动暂存开启时必填参数都要有取值来源。"""
        r = await self.get(code)
        drafts = await self._drafts()
        d = drafts.get(r.id)
        if d is None or (body.cr and body.cr != d.cr_code):
            raise fail("STATE_INVALID", "已发布版本只读，请先发起变更", 409)
        actions = await self._actions()
        cur = await self.draft_def(r, d, actions)
        assert cur is not None
        st = body.auto_stage
        errs: list[tuple[str, str]] = []
        if len(body.thresholds) > len(cur["conds"]):
            errs.append(("thresholds", "阈值个数超过条件个数"))
        for i, t in enumerate(body.thresholds):
            if not t.strip():
                errs.append((f"thresholds[{i}]", "阈值不能为空"))
        at: ActionType | None = None
        if st.action_type_id:
            at = await self.session.scalar(select(ActionType).where(ActionType.code == st.action_type_id))
            if (
                at is None
                or at.target_kind != r.obj_kind
                or at.status.value == "草稿"
                or at.retired_release_id is not None
            ):
                errs.append(("autoStage.actionTypeId", "只能选目标对象为规则对象、且不是草稿的动作类型"))
        if st.enabled and not st.action_type_id:
            errs.append(("autoStage.actionTypeId", "开启自动暂存需选择动作类型"))
        if st.enabled and not st.confirmer:
            errs.append(("autoStage.confirmer", "开启自动暂存需指定在用确认人"))
        from app.modules.dynamic.catalog import get_catalog

        catalog = await get_catalog(self.session, self.user)
        if st.confirmer and catalog.person(st.confirmer) is None:
            errs.append(("autoStage.confirmer", "确认人不能定位唯一在用用户，请重新选择"))
        sources = (await self.meta())["mappingSources"]
        for i, m in enumerate(st.mapping):
            if m.from_ and m.from_ not in sources and not m.from_.startswith("固定值："):
                errs.append((f"autoStage.mapping[{i}].from", "取值来源不在可选列表里"))
        if errs:
            raise validation(errs)
        if st.enabled and at is not None:
            mapped = {m.param for m in st.mapping if m.from_}
            missing = [p.name for p in at.params if p.required and p.name not in mapped]
            if missing:
                raise fail(
                    "STAGE_MAPPING_MISSING",
                    f"自动暂存动作缺少必填参数的取值来源：{'、'.join(missing)}",
                    422,
                    missing=missing,
                )
        conds = [
            {**c, "value": body.thresholds[i]} if i < len(body.thresholds) else c
            for i, c in enumerate(cur["conds"])
        ]
        auto = {
            "enabled": st.enabled,
            "actionTypeId": st.action_type_id,
            "confirmer": st.confirmer,
            "mapping": [{"param": m.param, "from": m.from_} for m in st.mapping],
        }
        d.patch = {**d.patch, "conds": conds, "autoStage": auto}
        d.by, d.at = self.user.name, now()
        await self.session.flush()
        draft = await self.draft_def(r, d, actions)
        assert draft is not None
        pub = None if r.version is None else self._def(r, actions)
        lv = await CrLink(self.session, self.user).put(
            d.cr_code, self._target(r), rule_sections(pub, draft), body.lock_version
        )
        return {
            "cr": d.cr_code,
            "lockVersion": lv and lv["lockVersion"],
            "crChanges": lv and lv["changes"],
            "draft": draft,
            "message": f"已保存到草稿 {d.cr_code}，发布前不影响线上规则",
        }


# ======================================================================
# 情景推演
# ======================================================================


class ScenarioService:
    def __init__(self, session: AsyncSession, user: CurrentUser) -> None:
        self.session = session
        self.user = user

    async def get(self, code: str) -> Scenario:
        s = await self.session.scalar(select(Scenario).where(Scenario.code == code))
        if s is None:
            raise NotFound("方案不存在", details={"id": code})
        return s

    async def _vers(self, ids: set[int] | None = None) -> dict[int, str]:
        stmt = select(OntologyRelease.id, OntologyRelease.ver)
        if ids is not None:
            if not ids:
                return {}
            stmt = stmt.where(OntologyRelease.id.in_(ids))
        return {i: v for i, v in (await self.session.execute(stmt)).all()}

    async def _result(self, s: Scenario) -> ScenarioResult | None:
        return await self.session.scalar(select(ScenarioResult).where(ScenarioResult.scenario_id == s.id))

    @staticmethod
    def _impacts(rows: list[ScenarioImpact]) -> list[dict[str, Any]]:
        return [
            {
                "sku": x.sku,
                "name": x.name,
                "category": x.category,
                "storeId": x.store_id,
                "storeName": x.store_name,
                "baseSales": x.base_sales,
                "sales": x.sales,
                "stockoutBase": _n(x.stockout_base),
                "stockout": _n(x.stockout),
                "suggest": x.suggest,
            }
            for x in rows
        ]

    @staticmethod
    def result_out(res: ScenarioResult) -> dict[str, Any]:
        return {
            "ranAt": sec(res.ran_at),
            "weeks": res.weeks,
            "baseSales": res.base_sales,
            "sales": res.sales,
            "baseMargin": res.base_margin,
            "margin": res.margin,
            "kpis": res.kpis,
        }

    def lock_reason(self, s: Scenario) -> str:
        if s.owner != self.user.name:
            return f"{s.owner} 的方案，只读（不能修改、运行或转为动作）；如需调整请“复制为新方案”"
        if s.status == ScenarioStatus.running:
            return "推演运行中，完成后可修改"
        if s.staged:
            return f"已转为 {len(s.staged)} 条暂存动作，配置锁定；如需调整请“复制为新方案”"
        return ""

    async def out(self, s: Scenario, vers: dict[int, str], *, full: bool = False) -> dict[str, Any]:
        d: dict[str, Any] = {
            "id": s.code,
            "name": s.name,
            "owner": s.owner,
            "createdAt": sec(s.created_on),
            "baseVer": vers.get(s.base_release_id) if s.base_release_id else None,
            "status": s.status.value,
            "templateCode": s.template_code,
            "scope": s.scope,
            "drivers": s.drivers,
            "staged": list(s.staged or []),
        }
        if s.fail_reason:
            d["failReason"] = s.fail_reason
        if full:
            res = await self._result(s)
            d["result"] = self.result_out(res) if res else None
            d["lockReason"] = self.lock_reason(s)
        return d

    async def list_scenarios(
        self, params: PageParams, *, status: str | None, kw: str | None
    ) -> Page[dict[str, Any]]:
        stmt = select(Scenario)
        if status and status != "全部":
            try:
                wanted = ScenarioStatus(status)
            except ValueError:
                raise validation([("status", "未知方案状态")]) from None
            stmt = stmt.where(Scenario.status == wanted)
        if k := (kw or "").strip():
            stmt = stmt.where(
                or_(Scenario.name.contains(k, autoescape=True), Scenario.code.icontains(k, autoescape=True))
            )
        rows, total = await ScenarioRepo(self.session).list_page(
            stmt.order_by(Scenario.created_on.desc(), Scenario.id.desc()), params
        )
        vers = await self._vers({s.base_release_id for s in rows if s.base_release_id is not None})
        return Page.of([await self.out(s, vers) for s in rows], total, params)

    async def detail(self, code: str) -> dict[str, Any]:
        return await self.out(await self.get(code), await self._vers(), full=True)

    async def result(self, code: str) -> dict[str, Any]:
        s = await self.get(code)
        if s.status == ScenarioStatus.running:
            return {"status": s.status.value}  # 路由据此回 202，前端继续轮询
        if s.status == ScenarioStatus.failed:
            raise fail("SCENARIO_FAILED", s.fail_reason or "推演失败", 409)
        res = await self._result(s)
        if res is None:
            raise NotFound("方案还没有推演结果", details={"id": code})
        return self.result_out(res)

    async def impacts(self, code: str, params: PageParams) -> Page[dict[str, Any]]:
        scenario = await self.get(code)
        rows, total = await ScenarioImpactRepo(self.session).list_page(
            select(ScenarioImpact)
            .where(ScenarioImpact.scenario_id == scenario.id)
            .order_by(ScenarioImpact.seq, ScenarioImpact.id),
            params,
        )
        return Page.of(self._impacts(rows), total, params)

    async def _next_code(self) -> str:
        await get_adapter(self.session).xact_lock(self.session, "dynamic.scenario.code")
        codes = (await self.session.scalars(select(Scenario.code))).all()
        n = max((int(c.split("-")[1]) for c in codes if c.startswith("SCN-") and c[4:].isdigit()), default=0)
        return f"SCN-{n + 1:04d}"

    async def create(self, from_: str | None) -> dict[str, Any]:
        src = await self.get(from_) if from_ else None
        cur = await self.session.scalar(
            select(OntologyRelease).order_by(OntologyRelease.published_at.desc()).limit(1)
        )
        tpl = template_of(src.template_code if src else None)
        s = Scenario(
            code=await self._next_code(),
            name=f"{src.name}（副本）" if src else "新方案",
            owner=self.user.name,
            created_on=now(),
            base_release_id=cur.id if cur else None,
            status=ScenarioStatus.draft,
            template_code=tpl["code"],
            scope=dict(src.scope) if src else defaults(tpl["scope"]),
            drivers=dict(src.drivers) if src else defaults(driver_fields(tpl)),
            staged=[],
            is_seed=False,
        )
        self.session.add(s)
        await self.session.flush()
        return await self.out(s, await self._vers(), full=True)

    def _own(self, s: Scenario) -> None:
        if s.owner != self.user.name:
            raise fail(
                "SCENARIO_NOT_OWNER",
                f"{s.owner} 的方案，只读（不能修改、运行或转为动作）；如需调整请“复制为新方案”",
                403,
            )

    async def rename(self, code: str, name: str) -> dict[str, Any]:
        s = await self.get(code)
        self._own(s)
        if not name.strip():
            raise validation([("name", "名称不能为空")])
        s.name = name.strip()
        await self.session.flush()
        return await self.out(s, await self._vers(), full=True)

    async def run(self, code: str, body: ScenarioRunIn) -> dict[str, Any]:
        s = await self.get(code)
        self._own(s)
        if s.status == ScenarioStatus.running:
            raise fail("SCENARIO_RUNNING", "推演运行中，完成后可修改", 409)
        if s.staged:
            raise fail(
                "SCENARIO_STAGED_LOCKED",
                f"已转为 {len(s.staged)} 条暂存动作，配置锁定；如需调整请“复制为新方案”",
                409,
            )
        tpl = template_of(s.template_code)
        opts = await scope_options(self.session, self.user, tpl)
        _scope, drivers, errs = tpl_validate(tpl, body.scope, body.drivers, opts)
        if errs:
            raise validation(errs)
        if tpl_no_driver(tpl, drivers):
            raise fail("SCENARIO_NO_DRIVER", tpl["noDriverHint"], 422)
        raise fail("SCENARIO_EXECUTOR_UNAVAILABLE", "真实推演数据集与执行器尚未配置，未运行方案", 503)

    async def compute(self, code: str) -> None:
        """后台任务：按 start 时写入的 scope / drivers 计算并落库（运行中 → 已完成 / 失败）。
        EXTENSION POINT (PDE): 真实推演引擎接任务队列时替换这里；
        当前为进程内后台任务，进程重启会留下“运行中” [推断]"""
        s = await self.get(code)
        if s.status != ScenarioStatus.running:
            return
        try:
            # SAVEPOINT：_compute 中途数据库出错只回滚这次计算，会话仍可用来写失败状态
            async with self.session.begin_nested():
                await self._compute(s)
        except Exception as e:  # 推演失败落到方案上，前端轮询 /result 拿到失败原因
            s = await self.get(code)
            s.status, s.fail_reason = ScenarioStatus.failed, f"推演失败：{e}"[:500]
            await self.session.flush()

    async def _compute(self, s: Scenario) -> None:
        raise fail("SCENARIO_EXECUTOR_UNAVAILABLE", "真实推演数据集与执行器尚未配置", 503)

    async def compare(self, ids: list[str]) -> dict[str, Any]:
        if not ids or len(ids) > 3:
            raise fail("COMPARE_LIMIT", "最多对比 3 个已完成的方案", 422)
        vers = await self._vers()
        out = []
        for code in ids:
            s = await self.get(code)
            if s.status != ScenarioStatus.done:
                raise fail("COMPARE_LIMIT", f"{s.code} 未完成，只能对比已完成的方案", 422)
            out.append(await self.out(s, vers, full=True))
        same = len({(o["templateCode"], json.dumps(o["scope"], sort_keys=True)) for o in out}) == 1
        return {"items": out, "sameScope": same}

    async def _plan(self, s: Scenario) -> list[dict[str, Any]]:
        res = await self._result(s)
        if res is None:
            return []
        raise fail("SCENARIO_EXECUTOR_UNAVAILABLE", "真实推演建议执行器尚未配置，未生成动作建议", 503)

    async def plan(self, code: str) -> list[dict[str, Any]]:
        return await self._plan(await self.get(code))

    async def stage(self, code: str, keys: list[str], actions: Any) -> dict[str, Any]:
        """S9：每条建议调 ActionService.stage 生成待确认动作（source=推演）；之后方案锁定。"""
        from app.modules.dynamic.schemas import SourceIn, StageIn

        s = await self.get(code)
        self._own(s)
        if s.status != ScenarioStatus.done:
            raise fail("STATE_INVALID", "推演完成后才能转为暂存动作", 409)
        if s.staged:
            raise fail("SCENARIO_STAGED_LOCKED", f"已转为 {len(s.staged)} 条暂存动作，请到动作中心确认", 409)
        res = await self._result(s)
        assert res is not None
        if res.scope != s.scope or res.drivers != s.drivers:
            raise fail("SCENARIO_CONFIG_DIRTY", "配置改动后需重新运行推演", 409)
        plan = {p["key"]: p for p in await self._plan(s)}
        if not plan:
            raise fail("STATE_INVALID", "没有可转为动作的建议", 409)
        bad = [k for k in keys if k not in plan]
        if not keys or bad:
            raise validation(
                [("keys", "至少勾选一条建议" if not keys else f"不在建议清单里：{'、'.join(bad)}")]
            )
        ids = []
        for k in dict.fromkeys(keys):
            p = plan[k]
            run, _ = await actions.stage(
                StageIn(
                    action=p["action"],
                    source=SourceIn(
                        type="推演", scenario_id=s.code, scenario_name=s.name, confirmer=self.user.name
                    ),
                    obj_kind=p["objKind"],
                    obj_id=p["objId"],
                    obj_label=p["objLabel"],
                    params=p["params"],
                )
            )
            ids.append(run.code)
        s.staged = ids
        await self.session.flush()
        return {"staged": ids, "message": f"已暂存 {len(ids)} 条动作到动作中心（待确认）"}

    async def meta(self) -> dict[str, Any]:
        tpl = template_of(None)

        async def values(column):
            return sorted(
                v for v in await self.session.scalars(select(column).distinct()) if isinstance(v, str)
            )

        areas = await values(Scenario.scope["area"].as_string())
        categories = await values(Scenario.scope["category"].as_string())
        promos = await values(Scenario.drivers["promo"].as_string())
        definitions = deepcopy(templates())
        for definition in definitions:
            for field in definition["scope"]:
                if field["key"] in ("area", "category"):
                    field["options"] = areas if field["key"] == "area" else categories
        return {
            "executorConfigured": False,
            "unavailableReason": "真实推演数据集与执行器尚未配置；仅可查看已存历史结果",
            "templates": definitions,
            "areas": areas,
            "categories": categories,
            "promoTypes": promos,
            "baseDrivers": defaults(driver_fields(tpl)),
            "statuses": ["全部", *[x.value for x in ScenarioStatus]],
        }


def _n(x: float) -> float | int:
    return int(x) if float(x).is_integer() else x


# ======================================================================
# 生命周期
# ======================================================================


class LifecycleService:
    def __init__(self, session: AsyncSession, user: CurrentUser) -> None:
        self.session = session
        self.user = user

    async def get(self, object_id: str) -> Lifecycle:
        lc = await self.session.scalar(select(Lifecycle).where(Lifecycle.code == object_id))
        if lc is None:
            raise NotFound("该对象类型没有生命周期", details={"id": object_id})
        return lc

    async def _versions(self, lc: Lifecycle, at: datetime | None = None) -> list[dict[str, Any]]:
        rows = (
            await self.session.scalars(
                select(LifecycleVersion)
                .where(LifecycleVersion.lifecycle_id == lc.id)
                .order_by(LifecycleVersion.version.desc())
            )
        ).all()
        vers = {
            i: v
            for i, v in (await self.session.execute(select(OntologyRelease.id, OntologyRelease.ver))).all()
        }
        rows = [v for v in rows if at is None or v.at <= at]
        eff = rows[0].version if at is not None and rows else lc.version
        out = []
        for v in rows:
            d: dict[str, Any] = {
                "ver": v.version,
                "status": "生效中" if v.version == eff else "已替代",
                "by": v.by,
                "at": _fmt_dt(v.at),
                "change": v.change,
            }
            if v.release_id:
                d["release"] = vers.get(v.release_id)
            if v.undo:
                d["undo"] = v.undo
            out.append(d)
        return out

    async def detail(
        self, object_id: str, *, view: str | None, cr: str | None, release: OntologyRelease | None
    ) -> dict[str, Any]:
        """L1。?ver= 历史版本 = 该版发布时生效的状态机（lifecycleAsOf，取发布前最近的生命周期版本快照）。"""
        lc = await self.get(object_id)
        if release is not None:
            at = release.published_at
            vs = await self._versions(lc, at)
            if vs and vs[0]["ver"] != lc.version:
                v = await version_row(self.session, lc, vs[0]["ver"])
                assert v is not None and v.snapshot is not None
                head = {**lifecycle_head(lc), "version": v.version, "updatedAt": _fmt_dt(v.at)}
                return {**head, **v.snapshot, "versions": vs, "draft": None, "readonly": True}
            return {
                **(await lifecycle_def(self.session, lc)),
                "versions": vs,
                "draft": None,
                "readonly": True,
            }
        out = {**(await lifecycle_def(self.session, lc)), "versions": await self._versions(lc)}
        until = now()
        counts = dict(
            (
                await self.session.execute(
                    select(LifecycleTransition.tr_code, func.count())
                    .join(LifecycleChange, LifecycleChange.transition_id == LifecycleTransition.id)
                    .where(
                        LifecycleChange.lifecycle_id == lc.id,
                        LifecycleChange.at >= until - timedelta(days=7),
                        LifecycleChange.at <= until,
                    )
                    .group_by(LifecycleTransition.tr_code)
                )
            ).all()
        )
        out["transitions"] = [{**t, "weekly": counts.get(t["id"], 0)} for t in out["transitions"]]
        draft = None
        if view == "draft":
            q = select(LifecycleDraft).where(LifecycleDraft.lifecycle_id == lc.id)
            if cr:
                q = q.where(LifecycleDraft.cr_code == cr)
            d = await self.session.scalar(q.order_by(LifecycleDraft.id.desc()).limit(1))
            link = CrLink(self.session, self.user)
            if d and await link.exists(d.cr_code) and not await link.registered(d.cr_code, "object", lc.code):
                d = None  # 变更单里已放弃本对象（static DELETE /targets），残留草稿行不再算数
            draft = {"cr": d.cr_code, "edits": d.edits} if d else {"cr": cr, "edits": empty_edits()}
        out["draft"] = draft
        return out

    async def changes(self, object_id: str, params: PageParams, *, illegal: bool) -> Page[dict[str, Any]]:
        lc = await self.get(object_id)
        statement = select(LifecycleChange).where(LifecycleChange.lifecycle_id == lc.id)
        if illegal:
            statement = statement.where(LifecycleChange.transition_id.is_(None))
        total = int(await self.session.scalar(select(func.count()).select_from(statement.subquery())) or 0)
        page = params.clamp(total)
        rows = list(
            await self.session.scalars(
                statement.order_by(LifecycleChange.at.desc(), LifecycleChange.code.desc())
                .limit(page.size)
                .offset(page.offset)
            )
        )
        return Page.of(await self.change_rows(rows), total, params)

    async def _changes(
        self, lc: Lifecycle, *, illegal: bool, since: datetime | None = None, until: datetime | None = None
    ) -> list[dict[str, Any]]:
        q = select(LifecycleChange).where(LifecycleChange.lifecycle_id == lc.id)
        if illegal:
            q = q.where(LifecycleChange.transition_id.is_(None))
        if since is not None:
            q = q.where(LifecycleChange.at >= since)
        if until is not None:
            q = q.where(LifecycleChange.at <= until)
        rows = (
            await self.session.scalars(q.order_by(LifecycleChange.at.desc(), LifecycleChange.code.desc()))
        ).all()
        return await self.change_rows(rows)

    async def change_rows(self, rows: list[LifecycleChange]) -> list[dict[str, Any]]:
        if not rows:
            return []
        trs = {
            i: c
            for i, c in (
                await self.session.execute(
                    select(LifecycleTransition.id, LifecycleTransition.tr_code).where(
                        LifecycleTransition.id.in_({r.transition_id for r in rows if r.transition_id})
                    )
                )
            ).all()
        }
        alerts = {
            i: c
            for i, c in (
                await self.session.execute(
                    select(Alert.id, Alert.code).where(Alert.id.in_({r.alert_id for r in rows if r.alert_id}))
                )
            ).all()
        }
        return [
            {
                "id": r.code,
                "at": sec(r.at),
                "objId": r.obj_id,
                "objLabel": r.obj_label,
                "from": r.from_state,
                "to": r.to_state,
                "trId": trs.get(r.transition_id) if r.transition_id else None,
                "by": r.by,
                "alertId": alerts.get(r.alert_id) if r.alert_id else None,
            }
            for r in rows
        ]

    async def illegal(self, object_id: str, since: str | None) -> list[dict[str, Any]]:
        """lifecycle.ts illegalOf：近 7 天非法迁移按「原 → 新」汇总
        （= SM-ORD 命中 = transition 为空的变化）。"""
        lc = await self.get(object_id)
        if not lc.rule_code:
            return []
        until = now()
        try:
            cut = until - timedelta(days=7) if since in (None, "", "7d") else parse_display_dt(since)
        except (ValueError, TypeError):
            raise validation([("since", "须为7d或有效日期时间")]) from None
        groups: dict[str, dict[str, Any]] = {}
        for c in await self._changes(lc, illegal=True, since=cut, until=until):
            g = groups.setdefault(
                f"{c['from']} → {c['to']}",
                {"from": c["from"], "to": c["to"], "count": 0, "alertId": None, "lastAt": c["at"]},
            )
            g["count"] += 1
            if not g["alertId"] and c["alertId"]:
                g["alertId"] = c["alertId"]
            if c["at"] > g["lastAt"]:
                g["lastAt"] = c["at"]
        return sorted(groups.values(), key=lambda g: -g["count"])

    async def save_draft(self, object_id: str, body: LifecycleDraftIn) -> dict[str, Any]:
        """L3–L6：整份草稿改动校验后保存（键 = 变更单号:对象类型）。"""
        lc = await self.get(object_id)
        if not body.cr:
            raise fail("STATE_INVALID", "已发布版本只读，请先发起变更", 409)
        cur = await lifecycle_def(self.session, lc)
        errs = validate_lc_edits(cur, body)
        if errs:
            raise validation(errs)
        edits = {
            "addStates": [{**s, "key": s.get("key") or s["name"]} for s in body.add_states],
            "addTransitions": body.add_transitions,
            "editTransitions": body.edit_transitions,
            "removeTransitions": body.remove_transitions,
        }
        from app.modules.dynamic.writeback import apply_edits, validate_transition_refs

        historical = await self.session.scalar(
            select(func.max(LifecycleVersion.version)).where(LifecycleVersion.lifecycle_id == lc.id)
        )
        try:
            await validate_transition_refs(self.session, edits)
            apply_edits(
                {"states": cur["states"], "transitions": cur["transitions"]},
                edits,
                code=lc.code,
                version=max(historical or 0, lc.version or 0) + 1,
            )
        except AppError as error:
            raise validation([("edits", error.message)]) from None
        d = await self.session.scalar(
            select(LifecycleDraft).where(
                LifecycleDraft.lifecycle_id == lc.id, LifecycleDraft.cr_code == body.cr
            )
        )
        if d is None:
            d = LifecycleDraft(lifecycle_id=lc.id, cr_code=body.cr, edits=edits, is_seed=False)
            self.session.add(d)
        else:
            d.edits = edits
        await self.session.flush()
        target = TargetIn(kind="object", id=lc.code, name=lc.object_name)
        lv = await CrLink(self.session, self.user).put(
            body.cr, target, lifecycle_sections(cur, edits), body.lock_version
        )
        return {
            "cr": body.cr,
            "edits": edits,
            "lockVersion": lv and lv["lockVersion"],
            "crChanges": lv and lv["changes"],
        }


def empty_edits() -> dict[str, Any]:
    return {"addStates": [], "addTransitions": [], "editTransitions": {}, "removeTransitions": []}


def validate_lc_edits(cur: dict[str, Any], body: LifecycleDraftIn) -> list[tuple[str, str]]:
    errs: list[tuple[str, str]] = []
    names = {s["name"] for s in cur["states"]}
    keys = {s["key"] for s in cur["states"]}
    kinds = {k.value for k in LcStateKind}
    for i, s in enumerate(body.add_states):
        if "key" in s and not isinstance(s["key"], str):
            errs.append((f"addStates[{i}].key", "状态编号必须是文本"))
        if not isinstance(s.get("name"), str):
            errs.append((f"addStates[{i}].name", "状态名称必须是文本"))
        if not isinstance(s.get("kind"), str) or s["kind"] not in kinds:
            errs.append((f"addStates[{i}].kind", f"状态类型须为 {' / '.join(sorted(kinds))} 之一"))
        if errs and errs[-1][0].startswith(f"addStates[{i}]"):
            continue
        n = str(s.get("name") or "").strip()
        if not n:
            errs.append((f"addStates[{i}].name", "状态名称必填"))
        elif n in names:
            errs.append((f"addStates[{i}].name", f"状态“{n}”已存在"))
        names.add(n)
        keys.add(str(s.get("key") or n))
    tr_ids = {t["id"] for t in cur["transitions"]}
    pairs = {(t["from"], t["to"]) for t in cur["transitions"] if t["id"] not in body.remove_transitions}
    for i, t in enumerate(body.add_transitions):
        f, to, by = t.get("from"), t.get("to"), str(t.get("by") or "").strip()
        bad = [k for k in ("from", "to") if t.get(k) is not None and not isinstance(t.get(k), str)]
        errs += [(f"addTransitions[{i}].{k}", "须是状态编号（文本）") for k in bad]
        if bad:
            continue
        if not f or f not in keys:
            errs.append((f"addTransitions[{i}].from", "原状态必填且须存在"))
        if not to or to not in keys:
            errs.append((f"addTransitions[{i}].to", "新状态必填且须存在"))
        if not by:
            errs.append((f"addTransitions[{i}].by", "触发来源必填"))
        if (f, to) in pairs:
            errs.append((f"addTransitions[{i}]", f"迁移“{f} → {to}”已存在"))
        pairs.add((f, to))
    for k, e in body.edit_transitions.items():
        if k not in tr_ids:
            errs.append((f"editTransitions.{k}", "迁移不存在"))
        elif "by" in e and not str(e["by"] or "").strip():
            errs.append((f"editTransitions.{k}.by", "触发来源必填"))
    for k in body.remove_transitions:
        if k not in tr_ids:
            errs.append((f"removeTransitions.{k}", "迁移不存在"))
    return errs


__all__ = ["LifecycleService", "RuleService", "ScenarioService"]
