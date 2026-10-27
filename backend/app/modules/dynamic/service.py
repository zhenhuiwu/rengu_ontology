"""业务逻辑（dynamic-api.md §2.1 动作、§2.2 告警、§3 只读与锁）。不 commit；跨模块只通过 ports / contracts。

文案（日志 text / desc、悬停原因）逐字照前端 Alerts.vue、Actions.vue、mock/actions.ts buildLog。
"""

import json
from collections.abc import Iterable, Sequence
from datetime import timedelta
from types import SimpleNamespace
from typing import Any

from sqlalchemy import Select, String, and_, case, cast, column, func, or_, select, true, type_coerce
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.auth import CurrentUser, check_func, resolve_user
from app.common.pagination import PageParams
from app.contracts.actions import (
    EVENT_ACTION_EXECUTED,
    EVENT_ACTION_OUTCOME_UNKNOWN,
    ReconcileOutcome,
    SubmitResult,
    SubmitStatus,
    idempotency_key_for,
    new_resubmit_key,
)
from app.contracts.actions import ReconcileResult as ContractReconcileResult
from app.core import clock
from app.core.enums import TimelineStatus
from app.core.errors import AppError, EmptyInput, IdempotencyConflict, NotFound, SubmissionCriteriaFailed
from app.core.state import InvalidTransition, Transitions, ensure_transition
from app.modules.dynamic.catalog import DatabaseRefCatalog
from app.modules.dynamic.errors import (
    AlertLockedByAction,
    AlreadyResubmitted,
    NotApprover,
    NotAssignee,
    NotConfirmer,
)
from app.modules.dynamic.models import (
    ActionRun,
    ActionStatus,
    Alert,
    AlertLevel,
    AlertOp,
    AlertStatus,
    ReconcileResult,
    SourceType,
)
from app.modules.dynamic.ports import Ports
from app.modules.dynamic.repo import ActionRunRepo, AlertRepo
from app.modules.dynamic.row_scope import scoped_instances, visible_references
from app.modules.dynamic.schemas import (
    ActionCan,
    ActionLogOut,
    ActionRunDetail,
    ActionRunOut,
    ActionRunPage,
    ActionTabCounts,
    AlertCan,
    AlertCounts,
    AlertDetail,
    AlertLogOut,
    AlertOut,
    AlertPage,
    DimOut,
    StageIn,
)
from app.modules.machine.principal import SERVICE_PREFIX, actor_of

SYSTEM = "系统"
HUMAN_CHECK_TEXT = "需确认人人工核对的提交条件"

# ---------------- 状态表 ----------------

ALERT_TRANSITIONS: Transitions[AlertStatus] = {
    AlertStatus.open: {AlertStatus.processing},
    AlertStatus.processing: {AlertStatus.resolved, AlertStatus.ignored},
}
ACTION_TRANSITIONS: Transitions[ActionStatus] = {
    ActionStatus.staged: {ActionStatus.approving, ActionStatus.withdrawn},
    ActionStatus.approving: {ActionStatus.executing},
    ActionStatus.executing: {ActionStatus.succeeded, ActionStatus.failed, ActionStatus.unknown},
    ActionStatus.unknown: {ActionStatus.reconciled},
}
# 关联动作在途（Alerts.vue IN_FLIGHT）：告警不能转派、关闭
IN_FLIGHT = frozenset(
    {ActionStatus.staged, ActionStatus.approving, ActionStatus.executing, ActionStatus.unknown}
)
CLOSED = frozenset({AlertStatus.resolved, AlertStatus.ignored})
ALERT_TABS: dict[str, frozenset[AlertStatus] | None] = {
    "open": frozenset({AlertStatus.open}),
    "doing": frozenset({AlertStatus.processing}),
    "closed": CLOSED,
    "all": None,
}
# Actions.vue TABS
ACTION_TABS: dict[str, tuple[ActionStatus, ...]] = {
    "pending": (ActionStatus.staged,),
    "records": (
        ActionStatus.approving,
        ActionStatus.executing,
        ActionStatus.succeeded,
        ActionStatus.failed,
        ActionStatus.unknown,
        ActionStatus.withdrawn,
        ActionStatus.reconciled,
    ),
    "unknown": (ActionStatus.unknown, ActionStatus.reconciled),
}
RECONCILE_LABEL = {"外部已成功": ReconcileResult.succeeded, "外部未执行": ReconcileResult.not_executed}


def validation_failed(field: str, message: str) -> AppError:
    return AppError(
        "参数校验失败",
        code="VALIDATION_FAILED",
        status_code=422,
        details={"fields": [{"field": field, "message": message}]},
    )


def approver_for_action(catalog: Any, action: str | None) -> str | None:
    """审批人取已发布动作配置解析的真实用户；不使用演示审批路由。"""
    meta = catalog.action_meta(action) if action else None
    return (meta or {}).get("approver") or None


def kind_label(catalog: Any, kind: str) -> str:
    """对象类型显示名（对象类型目录；未登记的类型原样返回编号）。"""
    return (catalog.meta().get("objKindLabel") or {}).get(kind) or kind


async def _rule_names(session: AsyncSession, codes: Iterable[str | None]) -> dict[str, str]:
    from app.modules.dynamic.rules_models import Rule

    want = {c for c in codes if c}
    if not want:
        return {}
    return dict((await session.execute(select(Rule.code, Rule.name).where(Rule.code.in_(want)))).all())


async def _code_map(session: AsyncSession, model: Any, ids: Iterable[int | None]) -> dict[int, str]:
    want = {i for i in ids if i is not None}
    if not want:
        return {}
    rows = (await session.execute(select(model.id, model.code).where(model.id.in_(want)))).all()
    return {r[0]: r[1] for r in rows}


def seed_first_order(model: Any) -> tuple[Any, ...]:
    """[推断] 列表顺序：业务新写入的在前（新的在上，= 前端 LIST.unshift），
    种子行按 mock 原顺序（= 写入顺序 id）。"""
    return (model.is_seed.asc(), case((model.is_seed, model.id), else_=-model.id).asc())


# =====================================================================
# 动作
# =====================================================================


class ActionService:
    def __init__(
        self, session: AsyncSession, user: CurrentUser, catalog: DatabaseRefCatalog, ports: Ports
    ) -> None:
        self.session = session
        self.user = user
        self.catalog = catalog
        self.ports = ports
        self.repo = ActionRunRepo(session)
        self.alerts = AlertRepo(session)
        self._service_names: dict[str, str] = {}

    def person_label(self, value: str | None) -> str | None:
        if value and value.startswith(SERVICE_PREFIX):
            # 服务账号执行主体 `api:{编号}` → 「服务账号 {名称或编号}」，不把原始主体串给人看
            code = value[len(SERVICE_PREFIX) :]
            name = self._service_names.get(code)
            if name is None and self.user.is_service_account and self.user.id == code:
                name = self.user.name
            return f"服务账号 {name or code}"
        person = self.catalog.person(value)
        return person["name"] if person and person["id"] == value else value

    async def load_service_names(self, values: Iterable[str | None]) -> None:
        """预取 `api:{编号}` 的服务账号名称（只读本体小白的 ServiceAccount）；查不到就显示编号。"""
        codes = {
            v[len(SERVICE_PREFIX) :] for v in values if v and v.startswith(SERVICE_PREFIX)
        } - self._service_names.keys()
        if not codes:
            return
        from app.modules.admin.models import ServiceAccount

        rows = await self.session.execute(
            select(ServiceAccount.code, ServiceAccount.name).where(ServiceAccount.code.in_(codes))
        )
        self._service_names.update({code: name for code, name in rows.all()})

    # ---------- 输出 ----------
    async def to_out(self, runs: Sequence[ActionRun]) -> list[ActionRunOut]:
        await self.load_service_names(x for r in runs for x in (r.initiator, r.confirmer, r.approver))
        alert_codes = await _code_map(self.session, Alert, (r.alert_id for r in runs))
        run_codes = await _code_map(
            self.session, ActionRun, [x for r in runs for x in (r.retry_of_id, r.retried_as_id)]
        )
        result = [self._out(r, alert_codes, run_codes) for r in runs]
        from app.modules.dynamic.run_visibility import parameters_visibility

        hidden = await parameters_visibility(self.session, self.user, runs)
        for row, out in zip(runs, result, strict=True):
            out.params_unavailable = hidden.get(row.id)
            if out.params_unavailable:
                out.params = out.summary = out.fail_reason = out.unknown_reason = out.reconcile = None
        if runs:
            from app.modules.integration.execution import CLAIMED, QUEUED
            from app.modules.integration.models import CallKind, ReconcileTask, WritebackCall

            phases = dict(
                (
                    await self.session.execute(
                        select(WritebackCall.action_run_id, WritebackCall.msg)
                        .outerjoin(ReconcileTask, ReconcileTask.action_run_id == WritebackCall.action_run_id)
                        .where(
                            WritebackCall.action_run_id.in_([r.id for r in runs]),
                            WritebackCall.kind == CallKind.write,
                            WritebackCall.msg.in_((QUEUED, CLAIMED)),
                            or_(WritebackCall.msg == QUEUED, ReconcileTask.manual.is_not(True)),
                        )
                    )
                ).all()
            )
            for row, out in zip(runs, result, strict=True):
                out.dispatch_phase = (
                    {QUEUED: "queued", CLAIMED: "claimed"}.get(phases.get(row.id))
                    if row.status == ActionStatus.unknown
                    else None
                )
        return result

    def _out(self, r: ActionRun, alert_codes: dict[int, str], run_codes: dict[int, str]) -> ActionRunOut:
        alert_code = alert_codes.get(r.alert_id) if r.alert_id else None
        source: dict[str, Any] | str
        if r.in_center:
            src = {
                "type": r.source_label,
                "alertId": alert_code,
                "ruleId": r.rule_code,
                "ruleVersion": r.rule_version,
                "confirmer": self.person_label(r.confirmer),
                "session": r.qa_session,
                "sessionTitle": r.qa_session_title,
                "scenarioId": r.scenario_code,
                "scenarioName": r.scenario_name,
            }
            source = {k: v for k, v in src.items() if v is not None}
        else:
            source = r.source_label
        if r.reconcile_result is None:
            reconcile = None
        elif not r.in_center and r.is_seed and r.reconcile_note:
            reconcile = r.reconcile_note  # 执行记录 mock 的对账原文
        else:
            reconcile = r.reconcile_result.value
        return ActionRunOut(
            id=r.code,
            type_id=r.action_type_code,
            action=r.action_name,
            type_version=r.type_version,
            source=source,
            obj_kind=r.obj_kind,
            obj_id=r.obj_id,
            obj_label=r.obj_label,
            initiator=self.person_label(r.initiator),
            confirmer=self.person_label(r.confirmer),
            approver=self.person_label(r.approver),
            status=r.status.value,
            submitted_at=r.submitted_at,
            at=r.submitted_at,
            idem_key=r.idem_key,
            sys_id=r.sys_code,
            system=self.catalog.system_name(r.sys_code),
            ext_ref=r.ext_ref,
            params=r.params_text,
            summary=r.params_text,
            ms=r.elapsed_ms,
            code=r.result_code,
            retries=r.retry_count,
            fail_reason=r.fail_reason,
            unknown_reason=r.unknown_reason,
            reconcile=reconcile,
            retry_of=run_codes.get(r.retry_of_id) if r.retry_of_id else None,
            retried_as=run_codes.get(r.retried_as_id) if r.retried_as_id else None,
            alert_id=alert_code,
            rule_id=r.rule_code,
        )

    def _not_mine(self, r: ActionRun) -> bool:
        """Actions.vue L87：只有 source.confirmer 存在时才限定本人。"""
        if not r.in_center or not r.confirmer:
            return False
        actor = self.catalog.person(r.confirmer)
        return actor is None or actor["id"] != self.user.id

    def is_approver(self, r: ActionRun) -> bool:
        actor = self.catalog.person(r.approver)
        return actor is not None and actor["id"] == self.user.id

    def _not_mine_text(self, r: ActionRun) -> str:
        head = f"该动作由规则 {r.rule_code} 暂存，" if r.rule_code else "该动作"
        return f"{head}指定确认人为 {self.person_label(r.confirmer)}，只有确认人可以确认提交或撤回"

    def can(self, r: ActionRun) -> ActionCan:
        reasons: dict[str, str] = {}
        mine = not self._not_mine(r)
        staged = r.status == ActionStatus.staged
        if staged and not mine:
            reasons["confirm"] = reasons["withdraw"] = self._not_mine_text(r)
        if r.status == ActionStatus.failed and r.retried_as_id:
            reasons["resubmit"] = "该动作已重新提交过"
        if r.status == ActionStatus.reconciled and r.reconcile_result == ReconcileResult.not_executed:
            reasons["resubmit"] = "已对账 · 外部未执行，如仍需执行请重新发起"
        approver = self.is_approver(r)
        if r.status == ActionStatus.approving and not approver:
            reasons["approve"] = f"该动作审批人为 {r.approver}，只有审批人可以审批"
        manage = self.user.func.get("action-center") == "manage" and self.user.ontology_role in (
            "owner",
            "editor",
        )
        return ActionCan(
            confirm=manage and staged and mine,
            withdraw=manage and staged and mine,
            approve=manage and r.status == ActionStatus.approving and approver,
            resubmit=manage and r.status == ActionStatus.failed and not r.retried_as_id,
            reconcile=manage and r.status == ActionStatus.unknown,
            reasons=reasons,
        )

    async def detail(self, r: ActionRun) -> ActionRunDetail:
        out = (await self.to_out([r]))[0]
        logs = await self.repo.logs(r.id)
        if out.params_unavailable:
            logs = []
        dispatch_phase = out.dispatch_phase
        can = self.can(r)
        if can.confirm or can.approve or can.resubmit or can.reconcile:
            try:
                await self._operation_access(r)
            except AppError as exc:
                for operation in ("confirm", "approve", "resubmit", "reconcile"):
                    setattr(can, operation, False)
                    can.reasons[operation] = exc.message
        if dispatch_phase:
            can.reconcile = False
            can.reasons["reconcile"] = "动作尚在执行队列或正在调用外部系统，请等待执行结果"
        return ActionRunDetail(
            **{**out.model_dump(), "dispatch_phase": dispatch_phase},
            log=[
                ActionLogOut(
                    time=x.at,
                    text=x.text,
                    desc=self.person_label(x.actor),
                    status=x.level.value if x.level else None,
                )
                for x in logs
            ],
            can=can,
        )

    async def get(self, code: str) -> ActionRun:
        stmt = await scoped_instances(
            select(ActionRun).where(ActionRun.code == code), self.user, ActionRun.obj_kind, ActionRun.obj_id
        )
        row = await self.session.scalar(stmt)
        if row is None:
            raise NotFound("动作不存在或无权查看")
        return row

    async def _operation_access(self, run: ActionRun) -> None:
        from app.modules.action.execution_options import action_operation_objects
        from app.modules.dynamic.run_visibility import params_unavailable
        from app.modules.integration.action_edits import execution_definition, object_operation

        reason = await params_unavailable(self.session, self.user, run)
        if reason:
            raise AppError(reason, code="ACTION_REFERENCE_UNAVAILABLE", status_code=403)
        definition = await execution_definition(self.session, run)
        for obj in await action_operation_objects(self.session, definition):
            await object_operation(self.session, self.user, obj, definition)

    # ---------- 列表 ----------
    def _filtered(
        self,
        *,
        action: str | None,
        action_type: str | None,
        sys_id: str | None,
        obj_kind: str | None,
        obj_id: str | None,
        alert_id: str | None,
        rule_id: str | None,
        session_id: str | None,
        kw: str | None,
        in_center: bool | None,
    ) -> Select[tuple[ActionRun]]:
        stmt = select(ActionRun)
        if in_center is not None:
            stmt = stmt.where(ActionRun.in_center.is_(in_center))
        if action:
            stmt = stmt.where(ActionRun.action_name == action)
        if action_type:
            stmt = stmt.where(ActionRun.action_type_code == action_type)
        if sys_id:
            stmt = stmt.where(ActionRun.sys_code == sys_id)
        if obj_kind:
            stmt = stmt.where(ActionRun.obj_kind == obj_kind)
        if obj_id:
            stmt = stmt.where(ActionRun.obj_id == obj_id)
        if alert_id:
            stmt = stmt.where(ActionRun.alert_id.in_(select(Alert.id).where(Alert.code == alert_id)))
        if rule_id:
            stmt = stmt.where(ActionRun.rule_code == rule_id)
        if session_id:
            stmt = stmt.where(ActionRun.qa_session == session_id)
        k = (kw or "").strip()
        if k:
            stmt = stmt.where(
                or_(
                    ActionRun.code.contains(k, autoescape=True),
                    ActionRun.obj_label.contains(k, autoescape=True),
                    ActionRun.idem_key.contains(k.lower(), autoescape=True),
                    ActionRun.ext_ref.contains(k, autoescape=True),
                    ActionRun.alert_id.in_(select(Alert.id).where(Alert.code.contains(k, autoescape=True))),
                    ActionRun.rule_code == k,
                    ActionRun.qa_session == k,
                )
            )
        return stmt

    async def search(
        self,
        *,
        tab: str | None,
        status: str | None,
        scope: str | None,
        page: PageParams,
        **filters: Any,
    ) -> ActionRunPage:
        if tab is not None and tab not in ACTION_TABS:
            raise validation_failed("tab", "tab 只能是 pending / records / unknown")
        statuses: list[ActionStatus] = []
        for s in [x.strip() for x in (status or "").split(",") if x.strip() and x.strip() != "全部"]:
            try:
                statuses.append(ActionStatus(s))
            except ValueError as e:
                raise validation_failed("status", f"未知状态：{s}") from e
        if tab is not None:
            in_center: bool | None = True
        else:
            scope = scope or "all"
            if scope not in ("center", "runs", "all"):
                raise validation_failed("scope", "scope 只能是 center / runs / all")
            in_center = {"center": True, "runs": False, "all": None}[scope]
        base = await scoped_instances(
            self._filtered(in_center=in_center, **filters), self.user, ActionRun.obj_kind, ActionRun.obj_id
        )
        stmt = base
        if tab is not None:
            stmt = stmt.where(ActionRun.status.in_(ACTION_TABS[tab]))
        if statuses:
            stmt = stmt.where(ActionRun.status.in_(statuses))
        order = seed_first_order(ActionRun)
        if tab == "unknown":
            order = (case((ActionRun.status == ActionStatus.unknown, 0), else_=1), *order)
        stmt = stmt.order_by(*order)
        rows, total = await self.repo.list_page(stmt, page)

        count_stmt = await scoped_instances(
            select(ActionRun.status, func.count())
            .where(ActionRun.in_center.is_(True))
            .group_by(ActionRun.status),
            self.user,
            ActionRun.obj_kind,
            ActionRun.obj_id,
        )
        tab_counts = dict((await self.session.execute(count_stmt)).all())
        sub = base.order_by(None).subquery()
        status_counts = dict(
            (await self.session.execute(select(sub.c.status, func.count()).group_by(sub.c.status))).all()
        )

        def n(sts: Iterable[ActionStatus], counts: dict[Any, int]) -> int:
            return sum(counts.get(s, 0) for s in sts)

        return ActionRunPage.of(
            await self.to_out(rows),
            total,
            page,
            counts=ActionTabCounts(**{k: n(v, tab_counts) for k, v in ACTION_TABS.items()}),
            status_counts={
                ActionStatus.staged.value: n([ActionStatus.staged], status_counts),
                ActionStatus.unknown.value: n([ActionStatus.unknown], status_counts),
            },
        )

    # ---------- 写：内部步骤 ----------
    async def _log(
        self, r: ActionRun, text: str, desc: str | None = None, level: TimelineStatus | None = None
    ) -> int:
        log = await self.repo.add_log(r, at=clock.now(), text=text, actor=desc, level=level)
        return log.seq

    async def _check_criteria(self, type_code: str, ctx: dict[str, Any]) -> None:
        failed = await self.ports.checker.check(type_code, ctx)
        if failed:
            raise SubmissionCriteriaFailed(failed)
        # 外部 Webhook 动作中无法机器校验的自然语言条件：交确认人人工核对（见 checker.run）。
        self.human_checks = list(getattr(self.ports.checker, "human_checks", None) or [])

    def _event_payload(self, r: ActionRun) -> dict[str, object]:
        return {
            "actionRunId": r.code,
            "typeId": r.action_type_code,
            "idemKey": r.idem_key,
            "extRef": r.ext_ref,
            "objKind": r.obj_kind,
            "objId": r.obj_id,
            "status": r.status.value,
        }

    async def _emit(self, r: ActionRun, event_type: str, seq: int) -> None:
        # eventId 由动态生成、全局唯一（来源 + 表 + 主键 + 版本），见 §2.7
        await self.ports.emitter.emit(
            f"dynamic:action_run:{r.code}:{seq}:{event_type}", event_type, self._event_payload(r)
        )

    async def _succeeded_side_effects(self, r: ActionRun, seq: int) -> None:
        """动作成功：发 ActionExecuted；关联告警 处理中 → 已解决（Alerts.vue L182 的承诺）。

        事件发射器在同一事务按执行版本应用受控业务属性，再持久化成功事件。
        """
        await self._emit(r, EVENT_ACTION_EXECUTED, seq)
        linked = (
            await self.session.scalars(
                select(Alert).where(Alert.action_run_id == r.id, Alert.status == AlertStatus.processing)
            )
        ).all()
        for a in linked:
            a.status = AlertStatus.resolved
            await self.alerts.add_log(
                a,
                at=clock.now(),
                actor=SYSTEM,
                op=AlertOp.action_result,
                text="关联动作执行成功，状态 处理中 → 已解决",
                from_status=AlertStatus.processing.value,
                to_status=AlertStatus.resolved.value,
            )

    async def _submit(self, r: ActionRun) -> None:
        """执行中 → 调 submit → 成功 / 失败 / 结果未知（重试、超时判定在适配器内）。"""
        system = self.catalog.system_name(r.sys_code) or r.sys_code or ""
        res: SubmitResult = await self.ports.submitter.submit(r.code, r.idem_key)
        now = clock.now()
        r.elapsed_ms = res.elapsed_ms
        r.result_code = res.code
        r.retry_count = res.retries
        if res.status == SubmitStatus.SUCCESS:
            ensure_transition(ACTION_TRANSITIONS, r.status, ActionStatus.succeeded)
            r.status = ActionStatus.succeeded
            r.ext_ref = res.receipt_id
            r.finished_at = now
            text = f"{system} 返回成功，外部单号 {r.ext_ref}" if r.sys_code and r.ext_ref else "动作执行成功"
            seq = await self._log(r, text, SYSTEM)
            await self._succeeded_side_effects(r, seq)
        elif res.status == SubmitStatus.FAILED:
            ensure_transition(ACTION_TRANSITIONS, r.status, ActionStatus.failed)
            r.status = ActionStatus.failed
            r.fail_reason = res.reason
            r.finished_at = now
            tail = f"，已自动重试 {res.retries} 次仍失败" if res.retries else ""
            await self._log(
                r, f"{system or '动作执行器'} 明确返回失败{tail}", res.reason, TimelineStatus.error
            )
        else:
            ensure_transition(ACTION_TRANSITIONS, r.status, ActionStatus.unknown)
            r.status = ActionStatus.unknown
            r.unknown_reason = res.reason
            sec = self.catalog.timeout_sec(r.sys_code)
            seq = await self._log(
                r,
                "已持久排队，等待执行器调用外部系统"
                if res.code == "QUEUED"
                else f"{system} {sec} 秒未返回结果，标记为结果未知（不重试写入）"
                if r.sys_code
                else "动作执行结果暂无法确认，不自动重发",
                f"系统 · {res.reason}" if res.reason else SYSTEM,
                TimelineStatus.warn,
            )
            await self._emit(r, EVENT_ACTION_OUTCOME_UNKNOWN, seq)
        await self.session.flush()

    def _type_of(self, *, name: str | None, code: str | None) -> dict[str, Any]:
        t = self.catalog.action_type(code) if code else self.catalog.action_type_by_name(name or "")
        if t is None or (name and code and t["name"] != name):
            raise validation_failed("typeId" if code else "action", f"未知动作：{code or name}")
        if (self.catalog.action_meta(t["id"]) or {}).get("approvalConfigured") is False:
            raise AppError(
                "动作审批配置未指定可用审批人", code="ACTION_APPROVAL_NOT_CONFIGURED", status_code=503
            )
        return t

    async def _replay_or_conflict(
        self, key: str, same: dict[str, Any], *, include_seed: bool = True
    ) -> ActionRun | None:
        """[推断 P9] 同一幂等键：内容相同返回已有记录，不同 409 IDEMPOTENCY_CONFLICT。

        include_seed=False：忽略种子历史（RUNS 里有大量 act-al-* 键与当前告警同号，唯一索引也豁免种子行）。
        """
        for old in await self.repo.by_idem_key(key):
            if not include_seed and old.is_seed:
                continue
            mine = {k: getattr(old, k) for k in same}
            if mine == same:
                return old
            raise IdempotencyConflict(details={"idemKey": key, "id": old.code})
        return None

    async def create_run(
        self,
        *,
        type_: dict[str, Any],
        status: ActionStatus,
        source_type: SourceType,
        idem_key: str | None,
        initiator: str,
        confirmer: str | None,
        approver: str | None,
        obj_kind: str | None,
        obj_id: str | None,
        obj_label: str | None,
        params: str | None,
        alert: Alert | None = None,
        rule_code: str | None = None,
        rule_version: str | None = None,
        qa_session: str | None = None,
        qa_session_title: str | None = None,
        scenario_code: str | None = None,
        scenario_name: str | None = None,
        retry_of: ActionRun | None = None,
    ) -> ActionRun:
        now = clock.now()
        code = await self.repo.next_code(clock.today().strftime("%Y%m%d"))
        meta = self.catalog.action_meta(type_["id"]) or {}
        if confirmer:
            actor = self.catalog.person(confirmer)
            if actor is None:
                raise validation_failed("source.confirmer", "确认人不能定位唯一在用用户，请重新选择")
            confirmer = actor["id"]
        run = ActionRun(
            code=code,
            action_type_code=type_["id"],
            action_name=type_["name"],
            type_version=meta.get("version") or type_.get("version"),
            source_type=source_type,
            source_label=source_type.value,
            rule_code=rule_code,
            # Rule catalogs expose integer versions; historical action versions remain stored text.
            rule_version=str(rule_version) if type(rule_version) is int else rule_version,
            alert_id=alert.id if alert else None,
            qa_session=qa_session,
            qa_session_title=qa_session_title,
            scenario_code=scenario_code,
            scenario_name=scenario_name,
            obj_kind=obj_kind,
            obj_id=obj_id,
            obj_label=obj_label,
            params=None,
            params_text=params,
            initiator=initiator,
            confirmer=confirmer,
            approver=(meta.get("approverId") or approver or meta.get("approver"))
            if not approver or approver == meta.get("approver")
            else approver,
            idem_key=idem_key or f"ont-{self.user.ontology_id}-{idempotency_key_for(code)}",
            status=status,
            sys_code=meta.get("sysId") or type_.get("sysId"),
            retry_of_id=retry_of.id if retry_of else None,
            submitted_at=now,
            in_center=True,
            is_seed=False,
        )
        return await self.repo.create(run, actor=actor_of(self.user))

    def _call_text(self, r: ActionRun) -> str:
        if not r.sys_code:
            return "执行绑定的本体操作"
        meta = self.catalog.action_meta(r.action_type_code) or {}
        return f"调用 {self.catalog.system_name(r.sys_code) or r.sys_code}：{meta.get('call', r.action_name)}"

    # ---------- 写：接口 ----------
    async def validate_launch(self, code: str, kind: str, identity: str, params: Any):
        from app.modules.action.condition_expr import Unavailable
        from app.modules.dynamic.action_options import launch_definition
        from app.modules.integration.action_edits import existing_row
        from app.modules.integration.execution import preflight

        definition, obj, governed = await launch_definition(
            self.session, self.user, self.catalog, code, identity=identity
        )
        if kind != obj.code:
            raise validation_failed("objKind", "目标对象类型与动作定义不匹配")
        await existing_row(self.session, self.user, obj, governed, identity)
        probe = SimpleNamespace(
            action_type_code=code,
            type_version=definition["version"],
            sys_code=definition.get("sysId"),
            obj_kind=kind,
            obj_id=identity,
            params=None,
            params_text=params,
            code="pending",
            ontology_id=self.user.ontology_id,
        )
        try:
            await preflight(self.session, self.user, probe)
        except Unavailable as exc:
            raise validation_failed("params", str(exc)) from None

    async def stage(self, body: StageIn) -> tuple[ActionRun, bool]:
        """POST /action-runs:stage → (记录, 是否新建)。只生成待确认，不调外部系统。"""
        t = self._type_of(name=body.action, code=body.type_id)
        meta = self.catalog.action_meta(t["id"]) or {}
        if body.approver and body.approver not in (meta.get("approver"), meta.get("approverId")):
            raise validation_failed("approver", "审批人必须使用已发布动作配置，不能由请求覆盖")
        src = body.source
        from app.modules.action import policy as action_policy

        action_policy.gate(
            await action_policy.load(self.session, t["id"], meta.get("version") or t.get("version")),
            action_policy.caller_of(src.type, self.user),
        )
        if src.type == "手动":
            await self.validate_launch(t["id"], body.obj_kind, body.obj_id, body.params)
            body.obj_label = body.obj_id
            # 服务账号（机器接口）不是在用用户：不指定确认人，超限转人工后由有权限的人确认。
            src.confirmer = None if self.user.is_service_account else self.user.id
            if not body.idempotency_key:
                raise validation_failed("idempotencyKey", "手动发起须提供请求幂等键")
        alert: Alert | None = None
        if src.alert_id:
            alert = await self.alerts.get_by_code(src.alert_id)
            if alert is None:
                raise validation_failed("source.alertId", f"告警不存在：{src.alert_id}")
        rule_code = src.rule_id or (alert.rule_code if alert else None)
        confirmer = src.confirmer
        if src.type == "规则" and not confirmer and rule_code:
            rule = self.catalog.rule(rule_code)
            confirmer = rule.get("confirmer") if rule else None  # [推断] 规则固定确认人
        key = f"act-{alert.code.lower()}" if alert else None
        if src.type == "手动":
            key = f"manual-{self.user.ontology_id}-{self.user.id}-{body.idempotency_key}"
        if key and self.user.ontology_id != 1:
            key = f"ont-{self.user.ontology_id}-{key}"
        if key:
            old = await self._replay_or_conflict(
                key,
                {
                    "action_type_code": t["id"],
                    "obj_id": body.obj_id,
                    "params_text": body.params,
                    "confirmer": src.confirmer,
                },
            )
            if old is not None:
                return old, False
        await self._check_criteria(
            t["id"],
            {"objKind": body.obj_kind, "objId": body.obj_id, "params": body.params, "alertId": src.alert_id},
        )
        st = SourceType(src.type)
        initiator = f"规则 {rule_code}" if st == SourceType.rule and rule_code else self.user.name
        run = await self.create_run(
            type_=t,
            status=ActionStatus.staged,
            source_type=st,
            idem_key=key,
            initiator=initiator,
            confirmer=confirmer,
            approver=body.approver,
            obj_kind=body.obj_kind or (self.catalog.action_meta(t["id"]) or {}).get("kind"),
            obj_id=body.obj_id,
            obj_label=body.obj_label,
            params=body.params,
            alert=alert,
            rule_code=rule_code,
            rule_version=src.rule_version or (alert.rule_version if alert else None),
            qa_session=src.session,
            qa_session_title=src.session_title,
            scenario_code=src.scenario_id,
            scenario_name=src.scenario_name,
        )
        # buildLog 的前几行
        if st == SourceType.rule:
            tail = f"（告警 {alert.code}）" if alert else ""
            await self._log(run, f"规则 {rule_code} {run.rule_version} 命中{tail}，自动暂存动作", SYSTEM)
        elif st == SourceType.alert:
            await self._log(run, f"告警 {src.alert_id} 命中规则 {rule_code} {run.rule_version}", SYSTEM)
        elif st == SourceType.qa:
            await self._log(run, f"问数会话 {src.session}「{src.session_title}」建议", initiator)
        elif st == SourceType.scenario:
            await self._log(
                run, f"情景推演 {src.scenario_id}「{src.scenario_name}」选定方案，转为暂存动作", initiator
            )
        else:
            await self._log(run, "手动发起", initiator)
        conf = f" · 确认人 {confirmer}" if confirmer else ""
        await self._log(
            run,
            f"生成动作「{run.action_name}」· 动作类型 {run.type_version}",
            f"发起人 {initiator}{conf} · 幂等键 {run.idem_key}",
        )
        owner = confirmer or initiator
        human = getattr(self, "human_checks", None) or []
        if human:
            self._human_runs = {*getattr(self, "_human_runs", set()), run.code}
            await self._log(
                run,
                f"{HUMAN_CHECK_TEXT}（{len(human)} 项）",
                "；".join(f"{h['name']}：{h['rule'] or h['reason']}" for h in human)[:900],
                TimelineStatus.warn,
            )
        await self._log(
            run,
            "等待确认提交",
            f"确认人 {owner}，确认后按已发布配置免审提交"
            if meta.get("approvalRequired") is False
            else f"确认人 {owner}，确认后进入 {run.approver} 审批",
            TimelineStatus.warn,
        )
        return run, True

    async def apply_policy(self, r: ActionRun) -> ActionRun:
        """刚暂存的记录按执行策略分流：auto 且在上限内 → 系统代为确认并免审执行；否则保持待确认。"""
        from app.modules.action import policy as action_policy

        self.policy_mode, self.policy_overflow = None, None
        policy = await action_policy.load(self.session, r.action_type_code, r.type_version)
        self.policy_mode = policy.mode
        if policy.mode != "auto" or r.status != ActionStatus.staged:
            return r
        if r.code in getattr(self, "_human_runs", set()):
            self.policy_overflow = "提交条件含需人工核对的自然语言规则，不能自动执行"
            await self._log(
                r, f"{action_policy.OVERFLOW_TEXT}：{self.policy_overflow}", f"执行策略 · 动作类型 {r.type_version}",
                TimelineStatus.warn,
            )
            return r
        caller = action_policy.caller_of(r.source_type.value, self.user)
        caller_id = r.rule_code if caller == "规则" and r.rule_code else self.user.id
        meta = self.catalog.action_meta(r.action_type_code) or {}
        free = bool(meta.get("approvalConfigured") and meta.get("approvalRequired") is False)
        ok, reason, note = await action_policy.auto_decision(self.session, r, policy, caller, caller_id, free)
        if not ok:
            self.policy_overflow = reason
            await self._log(
                r, f"{action_policy.OVERFLOW_TEXT}：{reason}", f"执行策略 · 动作类型 {r.type_version}",
                TimelineStatus.warn,
            )
            return r
        await self._operation_access(r)
        ensure_transition(ACTION_TRANSITIONS, r.status, ActionStatus.approving)
        r.status = ActionStatus.approving
        r.confirmed_at = clock.now()
        if self.user.is_service_account:
            # 服务账号不是在用用户：确认人留空；执行主体 `api:{编号}` 记入 created_by / 日志，
            # 后台执行链（machine.principal.resolve_actor）按它重建身份。
            note = f"{note} · 执行主体 {self.person_label(actor_of(self.user))}"
        else:
            r.confirmer = r.confirmer or self.user.id
        await self._log(r, action_policy.AUTO_TEXT, note)
        r.status = ActionStatus.executing
        r.approved_at = clock.now()
        await self._log(r, "按动作配置免审", f"执行策略 · 动作类型 {r.type_version}")
        await self._submit(r)
        return r

    async def confirm(self, code: str) -> ActionRun:
        from app.modules.integration.reconcile import lock_run

        await self.get(code)
        r = await lock_run(self.session, code)
        await self._operation_access(r)
        ensure_transition(ACTION_TRANSITIONS, r.status, ActionStatus.approving)
        if self._not_mine(r):
            raise NotConfirmer(self._not_mine_text(r))
        await self._check_criteria(
            r.action_type_code,
            {"objKind": r.obj_kind, "objId": r.obj_id, "params": r.params_text, "id": r.code},
        )
        r.status = ActionStatus.approving
        r.confirmed_at = clock.now()
        r.confirmer = r.confirmer or self.user.id
        await self._log(r, "确认提交", f"确认人 {self.user.name}")
        meta = self.catalog.action_meta(r.action_type_code) or {}
        if meta.get("approvalConfigured") and meta.get("approvalRequired") is False:
            r.status = ActionStatus.executing
            r.approved_at = clock.now()
            await self._log(r, "按动作配置免审", self.user.name)
            await self._submit(r)
            return r
        await self._log(r, "等待审批", f"审批人 {self.person_label(r.approver)}", TimelineStatus.warn)
        return r

    async def withdraw(self, code: str, reason: str) -> ActionRun:
        from app.modules.integration.reconcile import lock_run

        await self.get(code)
        r = await lock_run(self.session, code)
        ensure_transition(ACTION_TRANSITIONS, r.status, ActionStatus.withdrawn)
        if self._not_mine(r):
            raise NotConfirmer(self._not_mine_text(r))
        r.status = ActionStatus.withdrawn
        r.withdraw_reason = reason
        r.finished_at = clock.now()
        await self._log(r, f"撤回：{reason}", self.user.name)
        return r

    async def approve(self, code: str) -> ActionRun:
        """审批中 → 执行中 → submit。审批入口前端没有（[待定]）。
        审批人必须对应当前真实用户编号；旧姓名仅在能定位唯一在用用户时兼容。"""
        from app.modules.integration.reconcile import lock_run

        await self.get(code)
        r = await lock_run(self.session, code)
        await self._operation_access(r)
        ensure_transition(ACTION_TRANSITIONS, r.status, ActionStatus.executing)
        if not self.is_approver(r):
            raise NotApprover(f"该动作审批人为 {r.approver}，只有审批人可以审批")
        r.status = ActionStatus.executing
        r.approved_at = clock.now()
        await self._log(r, "审批通过", f"审批人 {self.person_label(r.approver)}")
        await self._log(r, self._call_text(r), f"幂等键 {r.idem_key}")
        await self.session.flush()
        await self._submit(r)
        return r

    async def resubmit(self, code: str) -> tuple[ActionRun, ActionRun]:
        """失败 → 新动作（执行中，新幂等键 -rN，沿用原审批）→ submit；原记录写 retried_as。"""
        from app.modules.integration.reconcile import lock_run

        await self.get(code)
        a = await lock_run(self.session, code)
        await self._operation_access(a)
        if a.status != ActionStatus.failed:
            raise InvalidTransition(
                "已对账 · 外部未执行，如仍需执行请重新发起"
                if a.reconcile_result == ReconcileResult.not_executed
                else None,
                details={"from": a.status.value, "to": ActionStatus.executing.value},
            )
        if a.retried_as_id:
            raise AlreadyResubmitted(
                details={"retriedAs": (await self.repo.get_or_404(a.retried_as_id)).code}
            )
        new_key = new_resubmit_key(a.idem_key)
        keep = a.source_type in (SourceType.alert, SourceType.rule, SourceType.alert_ops)
        t = self.catalog.action_type(a.action_type_code) or {
            "id": a.action_type_code,
            "name": a.action_name,
            "version": a.type_version,
            "sysId": a.sys_code,
        }
        alert = await self.alerts.get(a.alert_id) if a.alert_id else None
        rec = await self.create_run(
            type_=t,
            status=ActionStatus.executing,
            source_type=a.source_type if keep else SourceType.manual,
            idem_key=new_key,
            initiator=self.user.name,
            confirmer=None,
            approver=a.approver,
            obj_kind=a.obj_kind,
            obj_id=a.obj_id,
            obj_label=a.obj_label,
            params=a.params_text,
            alert=alert if keep else None,
            rule_code=a.rule_code if keep else None,
            rule_version=a.rule_version if keep else None,
            retry_of=a,
        )
        rec.source_label = a.source_label if keep else SourceType.manual.value
        rec.type_version = a.type_version
        rec.sys_code = a.sys_code
        rec.params = a.params
        rec.biz_key = a.biz_key
        rec.in_center = a.in_center
        await self._check_criteria(
            rec.action_type_code,
            {
                "objKind": rec.obj_kind,
                "objId": rec.obj_id,
                "params": rec.params_text,
            },
        )
        system = self.catalog.system_name(rec.sys_code) or rec.sys_code
        me = self.user.name
        await self._log(rec, f"重新提交自 {a.code}（上次失败：{a.fail_reason or '--'}）", me)
        await self._log(
            rec,
            f"生成动作「{rec.action_name}」· 动作类型 {rec.type_version}",
            f"发起人 {me} · 幂等键 {new_key}",
        )
        await self._log(rec, self._call_text(rec), f"沿用原审批 {rec.approver}")
        await self._log(
            rec,
            f"等待 {system} 返回" if rec.sys_code else "准备执行本体操作",
            self.catalog.waiting_text(rec.sys_code) if rec.sys_code else SYSTEM,
            TimelineStatus.warn,
        )
        a.retried_as_id = rec.id
        await self._log(a, f"已重新提交为 {rec.code}", f"{me} · 新幂等键 {new_key}")
        # [推断] 告警的关联动作跟到新动作（在途锁、成功后自动转已解决都按新动作）
        if alert is not None and alert.action_run_id == a.id:
            alert.action_run_id = rec.id
        await self.session.flush()
        await self._submit(rec)
        return rec, a

    async def reconcile(self, code: str, result: str, note: str) -> ActionRun:
        await self.get(code)
        r = await self.session.scalar(
            select(ActionRun)
            .where(ActionRun.code == code)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if r is None:
            raise NotFound("动作不存在", details={"id": code})
        await self._operation_access(r)
        if r.status != ActionStatus.unknown:
            raise InvalidTransition(details={"from": r.status.value, "to": ActionStatus.reconciled.value})
        from app.modules.integration.execution import CLAIMED, QUEUED
        from app.modules.integration.models import CallKind, ReconcileTask, WritebackCall

        if await self.session.scalar(
            select(WritebackCall.id)
            .outerjoin(ReconcileTask, ReconcileTask.action_run_id == WritebackCall.action_run_id)
            .where(
                WritebackCall.action_run_id == r.id,
                WritebackCall.kind == CallKind.write,
                WritebackCall.msg.in_((QUEUED, CLAIMED)),
                or_(WritebackCall.msg == QUEUED, ReconcileTask.manual.is_not(True)),
            )
        ):
            raise AppError(
                "动作尚在执行队列或正在调用外部系统，不能提前标记对账结果",
                code="ACTION_STILL_QUEUED",
                status_code=409,
            )
        me = self.user.name
        system = self.catalog.system_name(r.sys_code) or r.sys_code
        if result == "仍无法确认":
            await self._log(
                r,
                f"对账：仍无法确认（{note}）",
                f"{me} · 保持结果未知，系统继续每 15 分钟按幂等键查询",
                TimelineStatus.unknown,
            )
            await self.ports.scheduler.reopen_reconcile(r.code)
            return r
        rr = RECONCILE_LABEL[result]
        r.status = ActionStatus.reconciled
        r.reconcile_result = rr
        r.reconcile_note = note
        r.reconcile_by = me
        r.finished_at = clock.now()
        text = (
            f"对账：外部已成功（{note}）"
            if rr == ReconcileResult.succeeded
            else f"对账：外部未执行，动作未生效（{note}）"
        )
        actor = f"{me} · {system} 外部单号 {r.ext_ref}" if r.sys_code and r.ext_ref else me
        seq = await self._log(r, text, actor)
        if rr == ReconcileResult.succeeded:
            # 只发 ActionExecuted；告警不自动转已解决（[推断 P10] 未做，照前端）
            await self._emit(r, EVENT_ACTION_EXECUTED, seq)
        await self.ports.scheduler.resolve_reconcile(r.code, ReconcileOutcome(rr.name), me)
        return r


class DynamicReconciliationListener:
    """ReconciliationListener 实现：Palantir 定时对账查到结论时回调（§2.7）。

    查到也转「已对账」，不转「成功」。"""

    def __init__(self, session: AsyncSession, catalog: DatabaseRefCatalog, ports: Ports) -> None:
        self.session = session
        self.catalog = catalog
        self.ports = ports
        self.repo = ActionRunRepo(session)

    async def on_reconciled(self, action_run_id: str, result: ContractReconcileResult) -> None:
        r = await self.session.scalar(
            select(ActionRun)
            .where(ActionRun.code == action_run_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if r is None:
            raise NotFound("动作不存在", details={"id": action_run_id})
        if (
            r.status == ActionStatus.reconciled
            and r.reconcile_result is not None
            and r.reconcile_result.name == result.outcome.value
            and (not result.receipt_id or r.ext_ref == result.receipt_id)
        ):
            return
        if r.status != ActionStatus.unknown:
            raise InvalidTransition(details={"from": r.status.value, "to": ActionStatus.reconciled.value})
        ok = result.outcome == ReconcileOutcome.succeeded
        r.status = ActionStatus.reconciled
        r.reconcile_result = ReconcileResult.succeeded if ok else ReconcileResult.not_executed
        r.reconcile_by = SYSTEM
        r.finished_at = clock.now()
        if ok and result.receipt_id:
            r.ext_ref = result.receipt_id
        system = self.catalog.system_name(r.sys_code) or r.sys_code
        text = (
            "对账：外部已成功（按幂等键自动查询）"
            if ok
            else "对账：外部未执行，动作未生效（按幂等键自动查询）"
        )
        log = await self.repo.add_log(
            r,
            at=clock.now(),
            text=text,
            actor=f"系统 · {system} 外部单号 {r.ext_ref}" if r.sys_code and r.ext_ref else SYSTEM,
        )
        if ok:
            await self.ports.emitter.emit(
                f"dynamic:action_run:{r.code}:{log.seq}:{EVENT_ACTION_EXECUTED}",
                EVENT_ACTION_EXECUTED,
                {
                    "actionRunId": r.code,
                    "typeId": r.action_type_code,
                    "idemKey": r.idem_key,
                    "extRef": r.ext_ref,
                },
            )
        await self.session.flush()


# =====================================================================
# 告警
# =====================================================================


class AlertService:
    def __init__(
        self, session: AsyncSession, user: CurrentUser, catalog: DatabaseRefCatalog, ports: Ports
    ) -> None:
        self.session = session
        self.user = user
        self.catalog = catalog
        self.ports = ports
        self.repo = AlertRepo(session)
        self.runs = ActionRunRepo(session)

    async def _links(self, alerts: Sequence[Alert]) -> dict[int, ActionRun]:
        ids = {a.action_run_id for a in alerts if a.action_run_id}
        if not ids:
            return {}
        rows = (await self.session.scalars(select(ActionRun).where(ActionRun.id.in_(ids)))).all()
        return {r.id: r for r in rows}

    def _out(self, a: Alert, run: ActionRun | None) -> AlertOut:
        person = self.catalog.person(a.assignee)
        return AlertOut(
            id=a.code,
            level=a.level.value,
            rule_id=a.rule_code,
            obj_kind=a.obj_kind,
            obj_kind_label=kind_label(self.catalog, a.obj_kind),
            obj_id=a.obj_id,
            obj_label=a.obj_label,
            dims=[
                DimOut(
                    obj_kind=d["objKind"],
                    id=d["id"],
                    label=d["label"],
                    kind_label=kind_label(self.catalog, d["objKind"]),
                )
                for d in (a.dims or [])
            ],
            value=a.value,
            threshold=a.threshold,
            triggered_at=a.triggered_at,
            cutoff=a.data_cutoff,
            status=a.status.value,
            assignee=person["name"] if person else a.assignee,
            assignee_id=person["id"] if person else None,
            assigned_to_me=self._mine(a),
            can_claim=(
                self.user.func.get("alerts") == "manage"
                and self.user.ontology_role in ("owner", "editor")
                and a.status == AlertStatus.open
            ),
            can_close=(
                self.user.func.get("alerts") == "manage"
                and self.user.ontology_role in ("owner", "editor")
                and a.status == AlertStatus.processing
                and self._mine(a)
                and not self._locked(run)
            ),
            action_ref=run.idem_key if run else None,
            action_status=run.status.value if run else None,
            action_id=run.code if run else None,
        )

    async def get(self, code: str, *, lock: bool = False) -> Alert:
        stmt = await scoped_instances(
            select(Alert).where(Alert.code == code), self.user, Alert.obj_kind, Alert.obj_id
        )
        if lock:
            stmt = stmt.with_for_update().execution_options(populate_existing=True)
        row = await self.session.scalar(stmt)
        if row is None:
            raise NotFound("告警不存在或无权查看")
        return row

    async def _visible_out(self, alert: Alert, run: ActionRun | None) -> AlertOut:
        return (await self._visible_list([alert], {run.id: run} if run else {}))[0]

    async def _visible_list(self, alerts, links):
        rows = [self._out(alert, links.get(alert.action_run_id)) for alert in alerts]
        visible = await visible_references(
            self.session, self.user, {(dim.obj_kind, dim.id) for row in rows for dim in row.dims}
        )
        for row in rows:
            row.dims = [dim for dim in row.dims if (dim.obj_kind, dim.id) in visible]
        return rows

    def _mine(self, a: Alert) -> bool:
        person = self.catalog.person(a.assignee)
        return person is not None and person["id"] == self.user.id

    async def people(self, code: str) -> list[dict[str, str]]:
        alert = await self.get(code)
        from app.common.ontology import require_ontology_id
        from app.modules.admin.models import SysUser, UserStatus
        from app.modules.ontology.models import OntologyMember

        candidates = await self.session.execute(
            select(SysUser.code, SysUser.name)
            .join(OntologyMember, OntologyMember.user_id == SysUser.id)
            .where(
                SysUser.status == UserStatus.active,
                OntologyMember.ontology_id == require_ontology_id(self.session),
                OntologyMember.role.in_(("owner", "editor")),
            )
            .order_by(SysUser.code)
        )
        result = []
        for person_id, name in candidates:
            try:
                person = await resolve_user(self.session, person_id)
                check_func(person, "alerts", "manage")
            except AppError as exc:
                if exc.status_code not in (401, 403):
                    raise
                continue
            visible = await scoped_instances(
                select(Alert.id).where(Alert.id == alert.id), person, Alert.obj_kind, Alert.obj_id
            )
            if await self.session.scalar(visible):
                result.append({"id": person_id, "name": name})
        return result

    async def _run_of(self, a: Alert) -> ActionRun | None:
        return await self.runs.get(a.action_run_id) if a.action_run_id else None

    @staticmethod
    def _locked(run: ActionRun | None) -> bool:
        return run is not None and run.status in IN_FLIGHT

    @staticmethod
    def _multi(raw: str | None) -> list[str]:
        """逗号分隔多选；"全部" / 空 = 不筛选。"""
        items = (x.strip() for x in (raw or "").split(","))
        return list(dict.fromkeys(x for x in items if x and x != "全部"))

    @staticmethod
    def _dims(raw: Sequence[str] | None) -> list[tuple[str, str]]:
        """dim=objKind:id（可重复）：告警 dims 含该维度值才命中。
        同一 objKind 多值为或，不同 objKind 为且。"""
        out = []
        for item in raw or []:
            kind, sep, ident = item.partition(":")
            if not sep or not kind.strip() or not ident.strip():
                raise validation_failed("dim", f"维度筛选格式应为 对象类型:编号：{item}")
            out.append((kind.strip(), ident.strip()))
        return out

    OBJECT_FILTER_MAX_IDS = 5000

    async def _object_filter(self, kinds: list[str], filters: dict[str, Any]) -> Any:
        """filters=<JSON>：告警对象的业务属性筛选。复用 static / 通用对象浏览同一套绑定、
        可筛选校验（Bound.check）、取值校验（Bound.cond，422 规则一致）和对象行级范围（Bound.stmt）。
        结果条件：Alert.obj_kind = objKind 且 Alert.obj_id ∈ 命中的对象编号。同一请求内缓存。"""
        key = json.dumps([kinds, filters], sort_keys=True, ensure_ascii=False, default=str)
        cache = self.__dict__.setdefault("_object_filter_cache", {})
        if key in cache:
            return cache[key]
        if len(kinds) != 1:
            raise validation_failed("objKind", "按对象属性筛选（filters）时须且只能指定一个对象类型 objKind")
        from app.modules.qa.explore_api import ExploreIn, _bind

        kind = kinds[0]
        try:
            _, bound = await _bind(self.session, kind)
        except NotFound:
            raise validation_failed("objKind", f"对象类型不存在：{kind}") from None
        if bound is None:
            raise validation_failed("filters", f"对象类型 {kind} 未接入数据，不能按属性筛选")
        bound.check(filters, "筛选")
        objects = bound.where(await bound.stmt(self.user), ExploreIn(filters=filters)).subquery()
        if bound.source is None:
            ids: Any = select(cast(objects.c["__id"], String))
        else:  # 外部数据源不能跨库子查询：先取编号（上限保护），再 IN
            async with bound.read(self.session) as reader:
                rows = (
                    await reader.execute(select(objects.c["__id"]).limit(self.OBJECT_FILTER_MAX_IDS + 1))
                ).all()
            if len(rows) > self.OBJECT_FILTER_MAX_IDS:
                raise validation_failed(
                    "filters", f"命中对象超过 {self.OBJECT_FILTER_MAX_IDS} 个，请收窄筛选条件"
                )
            ids = [str(r[0]) for r in rows]
        cache[key] = and_(Alert.obj_kind == kind, Alert.obj_id.in_(ids))
        return cache[key]

    async def _filtered(
        self,
        stmt: Select,
        *,
        status: str,
        level: str | None,
        rule_id: str | None,
        obj_kind: str | None = None,
        dim: Sequence[str] | None = None,
        kw: str | None,
        filters: dict[str, Any] | None = None,
        skip: str | None = None,
    ) -> Select:
        """告警列表 / 分面共用的筛选；skip = 分面自身那一项（分面计数不受自己的选择影响）。"""
        if status not in ALERT_TABS:
            raise validation_failed("status", "status 只能是 open / doing / closed / all")
        stmt = await scoped_instances(stmt, self.user, Alert.obj_kind, Alert.obj_id)
        want = ALERT_TABS[status]
        if want is not None and skip != "status":
            stmt = stmt.where(Alert.status.in_(want))
        levels = []
        for lv in self._multi(level):
            try:
                levels.append(AlertLevel(lv))
            except ValueError:
                raise validation_failed("level", "level 只能是 高 / 中 / 低") from None
        if levels and skip != "level":
            stmt = stmt.where(Alert.level.in_(levels))
        rules = self._multi(rule_id)
        if rules and skip != "ruleId":
            stmt = stmt.where(Alert.rule_code.in_(rules))
        kinds = self._multi(obj_kind)
        if kinds and skip != "objKind":
            stmt = stmt.where(Alert.obj_kind.in_(kinds))
        if filters:  # 按对象属性筛选：总是生效（含对象类型分面），否则计数会混入别的对象类型
            stmt = stmt.where(await self._object_filter(kinds, filters))
        by_kind: dict[str, list[str]] = {}
        for kind, ident in self._dims(dim):
            by_kind.setdefault(kind, []).append(ident)
        dims = type_coerce(Alert.dims, JSONB)
        for kind, idents in by_kind.items():
            if skip == f"dim:{kind}":
                continue
            stmt = stmt.where(
                or_(*[dims.contains([{"objKind": kind, "id": i}]) for i in dict.fromkeys(idents)])
            )
        k = (kw or "").strip()
        if k:
            stmt = stmt.where(
                or_(Alert.code.contains(k, autoescape=True), Alert.obj_label.contains(k, autoescape=True))
            )
        return stmt

    async def search(
        self,
        *,
        status: str,
        level: str | None,
        rule_id: str | None,
        kw: str | None,
        page: PageParams,
        obj_kind: str | None = None,
        dim: Sequence[str] | None = None,
        filters: dict[str, Any] | None = None,
    ) -> AlertPage:
        stmt = await self._filtered(
            select(Alert),
            status=status,
            level=level,
            rule_id=rule_id,
            obj_kind=obj_kind,
            dim=dim,
            kw=kw,
            filters=filters,
        )
        stmt = stmt.order_by(*seed_first_order(Alert))
        rows, total = await self.repo.list_page(stmt, page)
        links = await self._links(rows)
        count_stmt = await scoped_instances(
            select(Alert.status, func.count()).group_by(Alert.status), self.user, Alert.obj_kind, Alert.obj_id
        )
        by_status = dict((await self.session.execute(count_stmt)).all())
        counts = {
            k: sum(by_status.get(s, 0) for s in (v if v is not None else AlertStatus))
            for k, v in ALERT_TABS.items()
        }
        return AlertPage.of(
            await self._visible_list(rows, links),
            total,
            page,
            counts=AlertCounts(**counts),
        )

    async def facets(
        self,
        *,
        status: str,
        level: str | None,
        rule_id: str | None,
        kw: str | None,
        obj_kind: str | None = None,
        dim: Sequence[str] | None = None,
        filters: dict[str, Any] | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        """告警分面：等级 / 规则 / 对象类型 / 各维度值的计数，按其余已选条件过滤（不含自身那一项）。
        只统计当前用户行级可见的告警；维度值每个对象类型取计数最高的 limit 个。"""
        f = {
            "status": status,
            "level": level,
            "rule_id": rule_id,
            "obj_kind": obj_kind,
            "dim": dim,
            "kw": kw,
            "filters": filters,
        }

        async def count(column, skip: str) -> list[tuple[Any, int]]:
            stmt = await self._filtered(select(column, func.count()), skip=skip, **f)
            rows = await self.session.execute(stmt.group_by(column).order_by(func.count().desc(), column))
            return [(v, n) for v, n in rows.all()]

        levels = dict(await count(Alert.level, "level"))
        rule_rows = await count(Alert.rule_code, "ruleId")
        names = await _rule_names(self.session, [c for c, _ in rule_rows])
        kinds = await count(Alert.obj_kind, "objKind")

        elem = func.jsonb_array_elements(Alert.dims).table_valued(column("value", JSONB)).alias("d")
        dim_kind, dim_id = elem.c.value["objKind"].astext, elem.c.value["id"].astext
        dim_label = func.max(elem.c.value["label"].astext)
        selected = {k for k, _ in self._dims(dim)}
        dim_out: dict[str, list[dict[str, Any]]] = {}
        for skip in [None, *[f"dim:{k}" for k in sorted(selected)]]:
            stmt = await self._filtered(
                select(dim_kind, dim_id, dim_label, func.count()).select_from(Alert).join(elem, true()),
                skip=skip,
                **f,
            )
            if skip:
                stmt = stmt.where(dim_kind == skip[4:])
            elif selected:
                stmt = stmt.where(dim_kind.not_in(selected))
            stmt = stmt.group_by(dim_kind, dim_id).order_by(dim_kind, func.count().desc(), dim_id)
            for kind, ident, label, n in (await self.session.execute(stmt)).all():
                vals = dim_out.setdefault(kind, [])
                if len(vals) < limit:
                    vals.append({"id": ident, "label": label or ident, "count": n})
        return {
            "level": [{"value": lv.value, "count": levels.get(lv, 0)} for lv in AlertLevel],
            "ruleId": [{"value": c, "label": names.get(c) or c, "count": n} for c, n in rule_rows],
            "objKind": [{"value": k, "label": kind_label(self.catalog, k), "count": n} for k, n in kinds],
            "dims": [
                {"objKind": k, "label": kind_label(self.catalog, k), "values": dim_out[k]}
                for k in sorted(dim_out)
            ],
        }

    async def _recent7d(self, a: Alert) -> int:
        """近 7 天同一规则、同一对象的触发次数（含本条），
        窗口 (triggeredAt − 7 天, triggeredAt]（Alerts.vue L81）。
        前端对 R-012 额外 +2 是演示用硬编码，按 §2.2 不做。"""
        n = await self.session.scalar(
            select(func.count()).where(
                Alert.rule_code == a.rule_code,
                Alert.obj_kind == a.obj_kind,
                Alert.obj_id == a.obj_id,
                Alert.triggered_at > a.triggered_at - timedelta(days=7),
                Alert.triggered_at <= a.triggered_at,
            )
        )
        return int(n or 0)

    def _can(self, a: Alert, run: ActionRun | None, rule: dict[str, Any] | None) -> AlertCan:
        reasons: dict[str, str] = {}
        locked = self._locked(run)
        closed = a.status in CLOSED
        mine_doing = a.status == AlertStatus.processing and self._mine(a)
        if locked and run is not None:  # Alerts.vue L282 / L294 的悬停文案
            reasons["transfer"] = f"关联动作{run.status.value}，有结果前不能转派"
            reasons["close"] = f"关联动作{run.status.value}，有结果前不能关闭"
        if a.status == AlertStatus.processing and not self._mine(a):
            reasons["close"] = reasons.get("close") or NotAssignee.message
            reasons["startAction"] = NotAssignee.message
        if mine_doing and not (rule and rule.get("action")):
            reasons["startAction"] = "规则未绑定动作"
        meta = self.catalog.action_meta((rule or {}).get("actionTypeId") or "")
        approval_ready = bool(meta and meta.get("approvalConfigured"))
        if mine_doing and rule and rule.get("action") and not approval_ready:
            reasons["startAction"] = "动作审批配置未指定可用审批人"
        if mine_doing and run is not None:
            reasons["startAction"] = "已有关联动作"
        manage = self.user.func.get("alerts") == "manage" and self.user.ontology_role in ("owner", "editor")
        return AlertCan(
            claim=manage and a.status == AlertStatus.open,
            transfer=manage and not closed and not locked,
            close=manage and mine_doing and not locked,
            start_action=manage
            and mine_doing
            and bool(rule and rule.get("action"))
            and approval_ready
            and run is None,
            reasons=reasons,
        )

    async def detail(self, a: Alert) -> AlertDetail:
        run = await self._run_of(a)
        rule = self.catalog.rule(a.rule_code)
        logs = await self.repo.logs(a.id)
        can = self._can(a, run, rule)
        params = []
        if can.start_action:
            from app.modules.dynamic.action_options import launch_definition

            try:
                definition, _, _ = await launch_definition(
                    self.session, self.user, self.catalog, rule["actionTypeId"], identity=a.obj_id
                )
                params = [{k: v for k, v in p.items() if not k.startswith("_")} for p in definition["params"]]
            except AppError as exc:
                can.start_action = False
                can.reasons["startAction"] = exc.message
        return AlertDetail(
            **(await self._visible_out(a, run)).model_dump(),
            rule=rule,
            approver=approver_for_action(self.catalog, (rule or {}).get("actionTypeId")),
            recent7d=await self._recent7d(a),
            locked=self._locked(run),
            log=[AlertLogOut(time=x.at, text=x.text, who=x.actor, abnormal=x.abnormal or None) for x in logs],
            can=can,
            action_params=params,
        )

    async def _log(
        self, a: Alert, op: AlertOp, text: str, frm: AlertStatus | None = None, to: AlertStatus | None = None
    ) -> None:
        await self.repo.add_log(
            a,
            at=clock.now(),
            actor=self.user.name,
            op=op,
            text=text,
            from_status=frm.value if frm else None,
            to_status=to.value if to else None,
        )

    async def _claim(self, a: Alert) -> None:
        ensure_transition(ALERT_TRANSITIONS, a.status, AlertStatus.processing)
        a.status = AlertStatus.processing
        a.assignee = self.user.id
        await self._log(
            a, AlertOp.claim, "认领，状态 待处理 → 处理中", AlertStatus.open, AlertStatus.processing
        )

    async def claim(self, code: str) -> Alert:
        a = await self.get(code, lock=True)
        await self._claim(a)
        return a

    async def _by_codes(self, codes: list[str]) -> list[Alert]:
        codes = list(dict.fromkeys(c for c in codes if c))
        if not codes:
            raise EmptyInput("请先勾选告警")
        stmt = await scoped_instances(
            select(Alert).where(Alert.code.in_(codes)), self.user, Alert.obj_kind, Alert.obj_id
        )
        rows = {
            a.code: a
            for a in await self.session.scalars(
                stmt.order_by(Alert.id).with_for_update().execution_options(populate_existing=True)
            )
        }
        missing = [c for c in codes if c not in rows]
        if missing:
            raise NotFound("告警不存在", details={"ids": missing})
        return [rows[c] for c in codes]

    async def batch_claim(self, codes: list[str]) -> list[str]:
        """只处理勾选项中待处理的（Alerts.vue batchClaim）。"""
        done = []
        for a in await self._by_codes(codes):
            if a.status == AlertStatus.open:
                await self._claim(a)
                done.append(a.code)
        return done

    async def transfer(self, code: str, to: str) -> Alert:
        a = await self.get(code, lock=True)
        if a.status in CLOSED:
            raise InvalidTransition(details={"from": a.status.value, "to": AlertStatus.processing.value})
        if self._locked(await self._run_of(a)):
            raise AlertLockedByAction()
        people = await self.people(code)
        exact = [p for p in people if p["id"] == to]
        matches = exact or [p for p in people if p["name"] == to]
        if len(matches) != 1:
            raise validation_failed("to", "目标人不存在、不唯一或无权处理此告警")
        target = matches[0]
        previous = self.catalog.person(a.assignee)
        if previous and target["id"] == previous["id"]:
            raise validation_failed("to", "目标人不能是当前处理人")
        frm = a.assignee
        prev = a.status
        a.assignee = target["id"]
        if a.status == AlertStatus.open:
            a.status = AlertStatus.processing
        await self._log(
            a,
            AlertOp.transfer,
            f"转派：{(previous or {}).get('name') or frm or '未认领'} → {target['name']}（{target['id']}）",
            prev,
            a.status,
        )
        return a

    async def _close(self, a: Alert, as_: str, reason: str) -> None:
        target = AlertStatus(as_)
        frm = a.status
        ensure_transition(ALERT_TRANSITIONS, a.status, target)
        a.status = target
        a.close_reason = reason
        a.assignee = a.assignee or self.user.id
        await self._log(a, AlertOp.close, f"关闭为{as_}：{reason}（{frm.value} → {as_}）", frm, target)

    async def close(self, code: str, as_: str, reason: str) -> Alert:
        a = await self.get(code, lock=True)
        ensure_transition(ALERT_TRANSITIONS, a.status, AlertStatus(as_))
        if not self._mine(a):
            raise NotAssignee()
        if self._locked(await self._run_of(a)):
            raise AlertLockedByAction()
        await self._close(a, as_, reason)
        return a

    async def batch_close(self, codes: list[str], as_: str, reason: str) -> list[str]:
        """与单条关闭保持相同处理人和在途动作限制；锁定项跳过并返回实际关闭数。"""
        done = []
        for a in await self._by_codes(codes):
            if (
                a.status == AlertStatus.processing
                and self._mine(a)
                and not self._locked(await self._run_of(a))
            ):
                await self._close(a, as_, reason)
                done.append(a.code)
        return done

    async def start_action(self, code: str, params: dict | None = None) -> tuple[Alert, ActionRun]:
        """告警页「发起动作」：处理人即确认人，直接审批中；幂等键 act-<告警号小写>。"""
        a = await self.get(code, lock=True)
        if a.status != AlertStatus.processing:
            raise InvalidTransition("只有处理中的告警可以发起动作", details={"from": a.status.value})
        if not self._mine(a):
            raise NotAssignee()
        rule = self.catalog.rule(a.rule_code)
        if not rule or not rule.get("action"):
            raise InvalidTransition("规则未绑定动作", details={"ruleId": a.rule_code})
        if a.action_run_id:
            raise InvalidTransition("该告警已有关联动作", details={"actionId": (await self._run_of(a)).code})  # type: ignore[union-attr]
        acts = ActionService(self.session, self.user, self.catalog, self.ports)
        t = acts._type_of(name=rule["action"], code=rule.get("actionTypeId"))
        key = f"act-{a.code.lower()}"
        if self.user.ontology_id != 1:
            key = f"ont-{self.user.ontology_id}-{key}"
        from app.modules.dynamic.action_options import launch_definition
        from app.modules.integration.execution import preflight

        definition, _, _ = await launch_definition(
            self.session, self.user, self.catalog, t["id"], identity=a.obj_id
        )
        params = json.dumps(params or {}, ensure_ascii=False, allow_nan=False)
        probe = SimpleNamespace(
            action_type_code=t["id"],
            type_version=definition["version"],
            sys_code=definition.get("sysId"),
            obj_kind=a.obj_kind,
            obj_id=a.obj_id,
            params=None,
            params_text=params,
            code="pending",
            ontology_id=self.user.ontology_id,
        )
        await preflight(self.session, self.user, probe)
        old = await acts._replay_or_conflict(
            key, {"action_type_code": t["id"], "obj_id": a.obj_id, "alert_id": a.id}, include_seed=False
        )
        if old is None:
            await acts._check_criteria(
                t["id"], {"objKind": a.obj_kind, "objId": a.obj_id, "alertId": a.code, "params": params}
            )
            approver = approver_for_action(self.catalog, t["id"])
            run = await acts.create_run(
                type_=t,
                status=ActionStatus.approving,
                source_type=SourceType.alert,
                idem_key=key,
                initiator=self.user.name,
                confirmer=self.user.id,
                approver=approver,
                obj_kind=a.obj_kind,
                obj_id=a.obj_id,
                obj_label=a.obj_label,
                params=params,
                alert=a,
                rule_code=a.rule_code,
                rule_version=a.rule_version or rule.get("version"),
            )
            now = clock.now()
            run.confirmed_at = now
            me = self.user.name
            await acts._log(run, f"告警 {a.code} 命中规则 {a.rule_code} {run.rule_version}", SYSTEM)
            await acts._log(
                run,
                f"生成动作「{run.action_name}」· 动作类型 {run.type_version}",
                f"发起人 {me} · 确认人 {me} · 幂等键 {key}",
            )
            await acts._log(run, "确认提交", f"确认人 {me}")
            meta = self.catalog.action_meta(t["id"]) or {}
            if meta.get("approvalConfigured") and meta.get("approvalRequired") is False:
                run.status, run.approved_at = ActionStatus.executing, clock.now()
                await acts._log(run, "按动作配置免审", me)
                await acts._submit(run)
            else:
                await acts._log(run, "等待审批", f"审批人 {approver}", TimelineStatus.warn)
        else:
            run = old
        a.action_run_id = run.id
        outcome = (
            f"待{acts.person_label(run.approver)}审批"
            if run.status == ActionStatus.approving
            else f"现为{run.status.value}"
        )
        await self._log(
            a,
            AlertOp.start_action,
            f"发起动作「{run.action_name}」，{outcome}；动作成功后告警自动转为已解决",
        )
        await self.session.flush()
        return a, run
