"""业务逻辑（dynamic-api.md §2.1 动作、§2.2 告警、§3 只读与锁）。不 commit；跨模块只通过 ports / contracts。

文案（日志 text / desc、悬停原因）逐字照前端 Alerts.vue、Actions.vue、mock/actions.ts buildLog。
"""

from collections.abc import Iterable, Sequence
from datetime import timedelta
from typing import Any

from sqlalchemy import Select, case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.auth import CurrentUser
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
from app.modules.dynamic.catalog import JsonRefCatalog
from app.modules.dynamic.errors import AlertLockedByAction, AlreadyResubmitted, NotAssignee, NotConfirmer
from app.modules.dynamic.models import (
    ActionRun,
    ActionStatus,
    Alert,
    AlertOp,
    AlertStatus,
    ReconcileResult,
    SourceType,
)
from app.modules.dynamic.ports import Ports
from app.modules.dynamic.repo import ActionRunRepo, AlertRepo
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
    StageIn,
)

SYSTEM = "系统"

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


async def fetch_page(session: AsyncSession, stmt: Select, pp: PageParams) -> tuple[list, int, PageParams]:
    """dynamic 唯一的分页切片处：count + offset/limit，返回 (本页行, total, 实际页码参数)。

    TODO(skeleton): 本分支基线 a70cba2 的骨架还没有收拢 helper。骨架分支 9521968 / 9c3e506（未合并）加了
    `PageParams.clamp(total)`、`BaseRepository.list_page`（先 count 再按收拢后的页取）和 `Page.of(...)`；
    合并后删掉本函数，两个 search 改成 `rows, total = await self.repo.list_page(stmt, page)` +
    `AlertPage.of / ActionRunPage.of(items, total, page, counts=...)`
    （越界收拢由骨架做，不是 dynamic 的 [推断]）。
    """
    total = int(await session.scalar(select(func.count()).select_from(stmt.order_by(None).subquery())) or 0)
    rows = list((await session.scalars(stmt.offset(pp.offset).limit(pp.size))).all())
    return rows, total, pp


def approver_for_alert(obj_kind: str) -> str:
    """Alerts.vue L173 写死：商品 → 品类经理 王敏，其他 → 区域运营 陈立军（[推断 P5] 未做）。"""
    return "品类经理 王敏" if obj_kind == "product" else "区域运营 陈立军"


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
        self, session: AsyncSession, user: CurrentUser, catalog: JsonRefCatalog, ports: Ports
    ) -> None:
        self.session = session
        self.user = user
        self.catalog = catalog
        self.ports = ports
        self.repo = ActionRunRepo(session)
        self.alerts = AlertRepo(session)

    # ---------- 输出 ----------
    async def to_out(self, runs: Sequence[ActionRun]) -> list[ActionRunOut]:
        alert_codes = await _code_map(self.session, Alert, (r.alert_id for r in runs))
        run_codes = await _code_map(
            self.session, ActionRun, [x for r in runs for x in (r.retry_of_id, r.retried_as_id)]
        )
        return [self._out(r, alert_codes, run_codes) for r in runs]

    def _out(self, r: ActionRun, alert_codes: dict[int, str], run_codes: dict[int, str]) -> ActionRunOut:
        alert_code = alert_codes.get(r.alert_id) if r.alert_id else None
        source: dict[str, Any] | str
        if r.in_center:
            src = {
                "type": r.source_label,
                "alertId": alert_code,
                "ruleId": r.rule_code,
                "ruleVersion": r.rule_version,
                "confirmer": r.confirmer,
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
            initiator=r.initiator,
            confirmer=r.confirmer,
            approver=r.approver,
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
        return bool(r.in_center and r.confirmer and r.confirmer != self.user.name)

    def _not_mine_text(self, r: ActionRun) -> str:
        head = f"该动作由规则 {r.rule_code} 暂存，" if r.rule_code else "该动作"
        return f"{head}指定确认人为 {r.confirmer}，只有确认人可以确认提交或撤回"

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
        return ActionCan(
            confirm=staged and mine,
            withdraw=staged and mine,
            approve=r.status == ActionStatus.approving,
            resubmit=r.status == ActionStatus.failed and not r.retried_as_id,
            reconcile=r.status == ActionStatus.unknown,
            reasons=reasons,
        )

    async def detail(self, r: ActionRun) -> ActionRunDetail:
        out = (await self.to_out([r]))[0]
        logs = await self.repo.logs(r.id)
        return ActionRunDetail(
            **out.model_dump(),
            log=[
                ActionLogOut(time=x.at, text=x.text, desc=x.actor, status=x.level.value if x.level else None)
                for x in logs
            ],
            can=self.can(r),
        )

    async def get(self, code: str) -> ActionRun:
        return await self.repo.get_by_code_or_404(code)

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
        base = self._filtered(in_center=in_center, **filters)
        stmt = base
        if tab is not None:
            stmt = stmt.where(ActionRun.status.in_(ACTION_TABS[tab]))
        if statuses:
            stmt = stmt.where(ActionRun.status.in_(statuses))
        order = seed_first_order(ActionRun)
        if tab == "unknown":
            order = (case((ActionRun.status == ActionStatus.unknown, 0), else_=1), *order)
        stmt = stmt.order_by(*order)
        rows, total, pp = await fetch_page(self.session, stmt, page)

        tab_counts = dict(
            (
                await self.session.execute(
                    select(ActionRun.status, func.count())
                    .where(ActionRun.in_center.is_(True))
                    .group_by(ActionRun.status)
                )
            ).all()
        )
        sub = base.order_by(None).subquery()
        status_counts = dict(
            (await self.session.execute(select(sub.c.status, func.count()).group_by(sub.c.status))).all()
        )

        def n(sts: Iterable[ActionStatus], counts: dict[Any, int]) -> int:
            return sum(counts.get(s, 0) for s in sts)

        return ActionRunPage(
            items=await self.to_out(rows),
            total=total,
            page=pp.page,
            size=pp.size,
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

        本体对象属性的修改（写回后的本体变更）依赖动作类型的 effects 定义（Palantir 模块，未合并）—— STUB。
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
            seq = await self._log(r, f"{system} 返回成功，外部单号 {r.ext_ref}", SYSTEM)
            await self._succeeded_side_effects(r, seq)
        elif res.status == SubmitStatus.FAILED:
            ensure_transition(ACTION_TRANSITIONS, r.status, ActionStatus.failed)
            r.status = ActionStatus.failed
            r.fail_reason = res.reason
            r.finished_at = now
            tail = f"，已自动重试 {res.retries} 次仍失败" if res.retries else ""
            await self._log(r, f"{system} 明确返回失败{tail}", res.reason, TimelineStatus.error)
        else:
            ensure_transition(ACTION_TRANSITIONS, r.status, ActionStatus.unknown)
            r.status = ActionStatus.unknown
            r.unknown_reason = res.reason
            sec = self.catalog.timeout_sec(r.sys_code)
            seq = await self._log(
                r,
                f"{system} {sec} 秒未返回结果，标记为结果未知（不重试写入）",
                f"系统 · {res.reason}" if res.reason else SYSTEM,
                TimelineStatus.warn,
            )
            await self._emit(r, EVENT_ACTION_OUTCOME_UNKNOWN, seq)
        await self.session.flush()

    def _type_of(self, *, name: str | None, code: str | None) -> dict[str, Any]:
        t = self.catalog.action_type(code) if code else self.catalog.action_type_by_name(name or "")
        if t is None or (name and code and t["name"] != name):
            raise validation_failed("typeId" if code else "action", f"未知动作：{code or name}")
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
        meta = self.catalog.action_meta(type_["name"]) or {}
        run = ActionRun(
            code=code,
            action_type_code=type_["id"],
            action_name=type_["name"],
            type_version=meta.get("version") or type_.get("version"),
            source_type=source_type,
            source_label=source_type.value,
            rule_code=rule_code,
            rule_version=rule_version,
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
            approver=approver or meta.get("approver"),
            idem_key=idem_key or idempotency_key_for(code),
            status=status,
            sys_code=meta.get("sysId") or type_.get("sysId"),
            retry_of_id=retry_of.id if retry_of else None,
            submitted_at=now,
            in_center=True,
            is_seed=False,
        )
        return await self.repo.create(run, actor=self.user.id)

    def _call_text(self, r: ActionRun) -> str:
        meta = self.catalog.action_meta(r.action_name) or {}
        return f"调用 {self.catalog.system_name(r.sys_code) or r.sys_code}：{meta.get('call', r.action_name)}"

    # ---------- 写：接口 ----------
    async def stage(self, body: StageIn) -> tuple[ActionRun, bool]:
        """POST /action-runs:stage → (记录, 是否新建)。只生成待确认，不调外部系统。"""
        t = self._type_of(name=body.action, code=body.type_id)
        src = body.source
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
        if key:
            old = await self._replay_or_conflict(
                key, {"action_type_code": t["id"], "obj_id": body.obj_id, "params_text": body.params}
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
            obj_kind=body.obj_kind or (self.catalog.action_meta(t["name"]) or {}).get("kind"),
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
        await self._log(
            run, "等待确认提交", f"确认人 {owner}，确认后进入 {run.approver} 审批", TimelineStatus.warn
        )
        return run, True

    async def confirm(self, code: str) -> ActionRun:
        r = await self.get(code)
        ensure_transition(ACTION_TRANSITIONS, r.status, ActionStatus.approving)
        if self._not_mine(r):
            raise NotConfirmer(self._not_mine_text(r))
        await self._check_criteria(
            r.action_type_code,
            {"objKind": r.obj_kind, "objId": r.obj_id, "params": r.params_text, "id": r.code},
        )
        r.status = ActionStatus.approving
        r.confirmed_at = clock.now()
        await self._log(r, "确认提交", f"确认人 {self.user.name}")
        await self._log(r, "等待审批", f"审批人 {r.approver}", TimelineStatus.warn)
        return r

    async def withdraw(self, code: str, reason: str) -> ActionRun:
        r = await self.get(code)
        ensure_transition(ACTION_TRANSITIONS, r.status, ActionStatus.withdrawn)
        if self._not_mine(r):
            raise NotConfirmer(self._not_mine_text(r))
        r.status = ActionStatus.withdrawn
        r.withdraw_reason = reason
        r.finished_at = clock.now()
        await self._log(r, f"撤回：{reason}", self.user.name)
        return r

    async def approve(self, code: str) -> ActionRun:
        """审批中 → 执行中 → submit。审批入口前端没有（[待定]），这里不校验审批人身份 [推断]。"""
        r = await self.get(code)
        ensure_transition(ACTION_TRANSITIONS, r.status, ActionStatus.executing)
        r.status = ActionStatus.executing
        r.approved_at = clock.now()
        await self._log(r, "审批通过", f"审批人 {r.approver}")
        await self._log(r, self._call_text(r), f"幂等键 {r.idem_key}")
        await self.session.flush()
        await self._submit(r)
        return r

    async def resubmit(self, code: str) -> tuple[ActionRun, ActionRun]:
        """失败 → 新动作（执行中，新幂等键 -rN，沿用原审批）→ submit；原记录写 retried_as。"""
        a = await self.get(code)
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
            rec, f"等待 {system} 返回", self.catalog.waiting_text(rec.sys_code), TimelineStatus.warn
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
        r = await self.get(code)
        if r.status != ActionStatus.unknown:
            raise InvalidTransition(details={"from": r.status.value, "to": ActionStatus.reconciled.value})
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
        seq = await self._log(r, text, f"{me} · {system} 外部单号 {r.ext_ref or '--'}")
        if rr == ReconcileResult.succeeded:
            # 只发 ActionExecuted；告警不自动转已解决（[推断 P10] 未做，照前端）
            await self._emit(r, EVENT_ACTION_EXECUTED, seq)
        return r


class DynamicReconciliationListener:
    """ReconciliationListener 实现：Palantir 定时对账查到结论时回调（§2.7）。

    查到也转「已对账」，不转「成功」。"""

    def __init__(self, session: AsyncSession, catalog: JsonRefCatalog, ports: Ports) -> None:
        self.session = session
        self.catalog = catalog
        self.ports = ports
        self.repo = ActionRunRepo(session)

    async def on_reconciled(self, action_run_id: str, result: ContractReconcileResult) -> None:
        r = await self.repo.get_by_code(action_run_id)
        if r is None:
            raise NotFound("动作不存在", details={"id": action_run_id})
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
            r, at=clock.now(), text=text, actor=f"系统 · {system} 外部单号 {r.ext_ref or '--'}"
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
        self, session: AsyncSession, user: CurrentUser, catalog: JsonRefCatalog, ports: Ports
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

    @staticmethod
    def _out(a: Alert, run: ActionRun | None) -> AlertOut:
        return AlertOut(
            id=a.code,
            level=a.level.value,
            rule_id=a.rule_code,
            obj_kind=a.obj_kind,
            obj_id=a.obj_id,
            obj_label=a.obj_label,
            store_id=a.store_id,
            store_label=a.store_label,
            value=a.value,
            threshold=a.threshold,
            triggered_at=a.triggered_at,
            cutoff=a.data_cutoff,
            status=a.status.value,
            assignee=a.assignee,
            action_ref=run.idem_key if run else None,
            action_status=run.status.value if run else None,
            action_id=run.code if run else None,
        )

    async def get(self, code: str) -> Alert:
        return await self.repo.get_by_code_or_404(code)

    async def _run_of(self, a: Alert) -> ActionRun | None:
        return await self.runs.get(a.action_run_id) if a.action_run_id else None

    @staticmethod
    def _locked(run: ActionRun | None) -> bool:
        return run is not None and run.status in IN_FLIGHT

    async def search(
        self, *, status: str, level: str | None, rule_id: str | None, kw: str | None, page: PageParams
    ) -> AlertPage:
        if status not in ALERT_TABS:
            raise validation_failed("status", "status 只能是 open / doing / closed / all")
        stmt = select(Alert)
        want = ALERT_TABS[status]
        if want is not None:
            stmt = stmt.where(Alert.status.in_(want))
        if level and level != "全部":
            stmt = stmt.where(Alert.level == level)
        if rule_id and rule_id != "全部":
            stmt = stmt.where(Alert.rule_code == rule_id)
        k = (kw or "").strip()
        if k:
            stmt = stmt.where(
                or_(Alert.code.contains(k, autoescape=True), Alert.obj_label.contains(k, autoescape=True))
            )
        stmt = stmt.order_by(*seed_first_order(Alert))
        rows, total, pp = await fetch_page(self.session, stmt, page)
        links = await self._links(rows)
        by_status = dict(
            (await self.session.execute(select(Alert.status, func.count()).group_by(Alert.status))).all()
        )
        counts = {
            k: sum(by_status.get(s, 0) for s in (v if v is not None else AlertStatus))
            for k, v in ALERT_TABS.items()
        }
        return AlertPage(
            items=[self._out(a, links.get(a.action_run_id) if a.action_run_id else None) for a in rows],
            total=total,
            page=pp.page,
            size=pp.size,
            counts=AlertCounts(**counts),
        )

    async def _recent7d(self, a: Alert) -> int:
        """近 7 天同一规则、同一对象的触发次数（含本条），
        窗口 (triggeredAt − 7 天, triggeredAt]（Alerts.vue L81）。
        前端对 R-012 额外 +2 是演示用硬编码，按 §2.2 不做。"""
        n = await self.session.scalar(
            select(func.count()).where(
                Alert.rule_code == a.rule_code,
                Alert.obj_id == a.obj_id,
                Alert.triggered_at > a.triggered_at - timedelta(days=7),
                Alert.triggered_at <= a.triggered_at,
            )
        )
        return int(n or 0)

    def _can(self, a: Alert, run: ActionRun | None, rule: dict[str, Any] | None) -> AlertCan:
        reasons: dict[str, str] = {}
        me = self.user.name
        locked = self._locked(run)
        closed = a.status in CLOSED
        mine_doing = a.status == AlertStatus.processing and a.assignee == me
        if locked and run is not None:  # Alerts.vue L282 / L294 的悬停文案
            reasons["transfer"] = f"关联动作{run.status.value}，有结果前不能转派"
            reasons["close"] = f"关联动作{run.status.value}，有结果前不能关闭"
        if a.status == AlertStatus.processing and a.assignee != me:
            reasons["close"] = reasons.get("close") or NotAssignee.message
            reasons["startAction"] = NotAssignee.message
        if mine_doing and not (rule and rule.get("action")):
            reasons["startAction"] = "规则未绑定动作"
        if mine_doing and run is not None:
            reasons["startAction"] = "已有关联动作"
        return AlertCan(
            claim=a.status == AlertStatus.open,
            transfer=not closed and not locked,
            close=mine_doing and not locked,
            start_action=mine_doing and bool(rule and rule.get("action")) and run is None,
            reasons=reasons,
        )

    async def detail(self, a: Alert) -> AlertDetail:
        run = await self._run_of(a)
        rule = self.catalog.rule(a.rule_code)
        logs = await self.repo.logs(a.id)
        return AlertDetail(
            **self._out(a, run).model_dump(),
            rule=rule,
            recent7d=await self._recent7d(a),
            locked=self._locked(run),
            log=[AlertLogOut(time=x.at, text=x.text, who=x.actor, abnormal=x.abnormal or None) for x in logs],
            can=self._can(a, run, rule),
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
        a.assignee = self.user.name
        await self._log(
            a, AlertOp.claim, "认领，状态 待处理 → 处理中", AlertStatus.open, AlertStatus.processing
        )

    async def claim(self, code: str) -> Alert:
        a = await self.get(code)
        await self._claim(a)
        return a

    async def _by_codes(self, codes: list[str]) -> list[Alert]:
        codes = list(dict.fromkeys(c for c in codes if c))
        if not codes:
            raise EmptyInput("请先勾选告警")
        rows = {a.code: a for a in await self.repo.by_codes(codes)}
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
        a = await self.get(code)
        if a.status in CLOSED:
            raise InvalidTransition(details={"from": a.status.value, "to": AlertStatus.processing.value})
        if self._locked(await self._run_of(a)):
            raise AlertLockedByAction()
        people = await self.repo.people()
        if to not in people:
            raise validation_failed("to", f"{to} 不在可转派人员中")
        if to == a.assignee:
            raise validation_failed("to", "目标人不能是当前处理人")
        frm = a.assignee
        prev = a.status
        a.assignee = to
        if a.status == AlertStatus.open:
            a.status = AlertStatus.processing
        await self._log(a, AlertOp.transfer, f"转派：{frm or '未认领'} → {to}", prev, a.status)
        return a

    async def _close(self, a: Alert, as_: str, reason: str) -> None:
        target = AlertStatus(as_)
        frm = a.status
        ensure_transition(ALERT_TRANSITIONS, a.status, target)
        a.status = target
        a.close_reason = reason
        a.assignee = a.assignee or self.user.name
        await self._log(a, AlertOp.close, f"关闭为{as_}：{reason}（{frm.value} → {as_}）", frm, target)

    async def close(self, code: str, as_: str, reason: str) -> Alert:
        a = await self.get(code)
        ensure_transition(ALERT_TRANSITIONS, a.status, AlertStatus(as_))
        if a.assignee != self.user.name:
            raise NotAssignee()
        if self._locked(await self._run_of(a)):
            raise AlertLockedByAction()
        await self._close(a, as_, reason)
        return a

    async def batch_close(self, codes: list[str], as_: str, reason: str) -> list[str]:
        """照前端 closable：只作用于勾选项中本人处理中的，不查在途动作（[推断 P3] 未做）。"""
        done = []
        for a in await self._by_codes(codes):
            if a.status == AlertStatus.processing and a.assignee == self.user.name:
                await self._close(a, as_, reason)
                done.append(a.code)
        return done

    async def start_action(self, code: str) -> tuple[Alert, ActionRun]:
        """告警页「发起动作」：处理人即确认人，直接审批中；幂等键 act-<告警号小写>。"""
        a = await self.get(code)
        if a.status != AlertStatus.processing:
            raise InvalidTransition("只有处理中的告警可以发起动作", details={"from": a.status.value})
        if a.assignee != self.user.name:
            raise NotAssignee()
        rule = self.catalog.rule(a.rule_code)
        if not rule or not rule.get("action"):
            raise InvalidTransition("规则未绑定动作", details={"ruleId": a.rule_code})
        if a.action_run_id:
            raise InvalidTransition("该告警已有关联动作", details={"actionId": (await self._run_of(a)).code})  # type: ignore[union-attr]
        acts = ActionService(self.session, self.user, self.catalog, self.ports)
        t = acts._type_of(name=rule["action"], code=None)
        key = f"act-{a.code.lower()}"
        params = f"{a.value}，阈值 {a.threshold}"  # [推断] 确认卡「触发值」字段
        old = await acts._replay_or_conflict(
            key, {"action_type_code": t["id"], "obj_id": a.obj_id, "alert_id": a.id}, include_seed=False
        )
        if old is None:
            await acts._check_criteria(t["id"], {"objKind": a.obj_kind, "objId": a.obj_id, "alertId": a.code})
            approver = approver_for_alert(a.obj_kind)
            run = await acts.create_run(
                type_=t,
                status=ActionStatus.approving,
                source_type=SourceType.alert,
                idem_key=key,
                initiator=self.user.name,
                confirmer=self.user.name,
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
            await acts._log(run, "等待审批", f"审批人 {approver}", TimelineStatus.warn)
        else:
            run = old
        a.action_run_id = run.id
        await self._log(
            a,
            AlertOp.start_action,
            f"发起动作「{run.action_name}」，待{run.approver}审批；动作成功后告警自动转为已解决",
        )
        await self.session.flush()
        return a, run
