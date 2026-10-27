"""Durable action dispatch. A committed claim precedes every external POST."""

import logging
import time
import uuid

import httpx
from pydantic import ConfigDict, Field, ValidationError, field_validator
from sqlalchemy import select

from app.common.auth import check_func, resolve_user
from app.contracts.actions import ReconcileOutcome, ReconcileResult, SubmitResult, SubmitStatus
from app.core import clock
from app.core.errors import AppError, Conflict
from app.core.schema import ApiModel
from app.jobs.scope import scoped_sessions, workspace_ids
from app.modules.action.checker import DatabaseSubmissionChecker, structured_params
from app.modules.biz import governance as _governance  # noqa: F401
from app.modules.dynamic.models import ActionRun, ActionStatus
from app.modules.integration.action_edits import business_edits, execution_definition, target_access
from app.modules.integration.adapters import get_adapter
from app.modules.integration.models import CallKind, CallResult, ExtSystem, WritebackCall
from app.modules.integration.reconcile import DatabaseReconcileScheduler, lock_run, resolved, to_manual

QUEUED = "ACTION_DISPATCH_QUEUED"
CLAIMED = "ACTION_DISPATCH_CLAIMED"
logger = logging.getLogger(__name__)


class ExecutionResponse(ApiModel):
    model_config = ConfigDict(extra="forbid")
    status: SubmitStatus
    receipt_id: str | None = Field(default=None, max_length=128)
    reason: str | None = Field(default=None, max_length=512)
    code: str | None = Field(default=None, max_length=128)

    @field_validator("receipt_id", "reason", "code")
    @classmethod
    def safe_text(cls, value):
        if value is not None and any(ord(c) < 32 or ord(c) == 127 for c in value):
            raise ValueError("Response text cannot contain control characters")
        return value


async def preflight(session, user, run):
    check_func(user, "action-center", "manage")
    definition = await execution_definition(session, run)
    from app.modules.integration.action_edits import resolved_params
    run.params = await resolved_params(session, user, definition,
        structured_params(run.params if run.params is not None else run.params_text))
    await target_access(session, user, run, definition)
    checker = DatabaseSubmissionChecker(session, user)
    failed = (await checker.run(definition, {"objKind": run.obj_kind, "objId": run.obj_id,
                  "params": run.params, "actionRunId": run.code}))['failed']
    if failed:
        raise AppError("动作参数或提交条件未通过", code="SUBMISSION_CRITERIA_FAILED",
                       status_code=422, details={"failed": failed})
    await business_edits(session, user, run, definition)
    from app.modules.integration.action_effects import preflight_effects
    await preflight_effects(session, user, run, definition)
    return definition


def submit_adapter(system, definition):
    try:
        adapter = get_adapter(system, "submit")
    except AppError as error:
        raise AppError("外部动作执行通道未配置或不可用", code="ACTION_EXECUTOR_UNAVAILABLE", status_code=503) from error
    if definition["id"] not in adapter.config.actions:
        raise AppError("动作不在此外部系统的执行授权白名单", code="ACTION_EXECUTOR_UNAVAILABLE", status_code=503)
    # The external system must explicitly implement our stable header contract.
    if not system.idempotent or system.idem_header.lower() != "idempotency-key":
        raise AppError("真实执行要求外部系统支持 Idempotency-Key 去重", code="ACTION_EXECUTOR_UNAVAILABLE", status_code=503)
    return adapter


class DatabaseActionSubmitter:
    def __init__(self, session, user):
        self.session, self.user = session, user

    async def submit(self, action_run_id, idempotency_key):
        # Do not refresh here: the caller has just changed the locked run to executing.
        run = await self.session.scalar(select(ActionRun).where(ActionRun.code == action_run_id).with_for_update())
        if run is None or run.idem_key != idempotency_key or run.status != ActionStatus.executing:
            raise Conflict("执行记录或幂等状态不匹配", code="STATE_INVALID")
        definition = await preflight(self.session, self.user, run)
        if not run.sys_code:
            return SubmitResult(SubmitStatus.SUCCESS, code="INTERNAL", elapsed_ms=0)
        system = await self.session.scalar(select(ExtSystem).where(ExtSystem.code == run.sys_code))
        if system is None:
            raise AppError("外部系统不存在", code="ACTION_EXECUTOR_UNAVAILABLE", status_code=503)
        adapter = submit_adapter(system, definition)
        target_url = f"{adapter.origin}{adapter.path('submit')}"
        if len(target_url) > 255:
            raise AppError("真实执行地址超过调用账本长度限制", code="ACTION_EXECUTOR_UNAVAILABLE", status_code=503)
        old = await self.session.scalar(select(WritebackCall).where(
            WritebackCall.action_run_id == run.id, WritebackCall.kind == CallKind.write))
        if old is not None:
            raise Conflict("该执行记录已提交，不能再次排队", code="IDEMPOTENCY_CONFLICT")
        run.params = structured_params(run.params if run.params is not None else run.params_text)
        self.session.add(WritebackCall(code=f"EXEC-{uuid.uuid4().hex}", sys_id=system.id,
            action_run_id=run.id, kind=CallKind.write, at=clock.now(), op_name=run.action_name,
            target_label=run.obj_label or run.code, method="POST", path=target_url,
            idem_key=run.idem_key, elapsed_ms=0, http_status=None, result=CallResult.unknown,
            attempt=0, actor=self.user.id, msg=QUEUED))
        await self.session.flush()
        return SubmitResult(SubmitStatus.UNKNOWN, reason="已持久排队，尚未调用外部系统；等待执行器处理",
                            code="QUEUED", elapsed_ms=0)


async def queued_call(session, run_id):
    return await session.scalar(select(WritebackCall).where(WritebackCall.action_run_id == run_id,
        WritebackCall.kind == CallKind.write, WritebackCall.msg == QUEUED))


async def dispatch_one(call_id, ontology_id: int):
    sessions = scoped_sessions(ontology_id)
    # Claim in its own committed transaction. A restart never repeats a claimed POST.
    async with sessions() as session, session.begin():
        code = await session.scalar(select(ActionRun.code).join(WritebackCall,
            WritebackCall.action_run_id == ActionRun.id).where(WritebackCall.id == call_id))
        if not code:
            return
        run = await lock_run(session, code)
        call = await session.get(WritebackCall, call_id, with_for_update=True)
        if call.msg != QUEUED or run.status != ActionStatus.unknown:
            return
        try:
            user = await resolve_user(session, call.actor)
            definition = await preflight(session, user, run)
            system = await session.get(ExtSystem, call.sys_id)
            adapter = submit_adapter(system, definition)
            if call.path != f"{adapter.origin}{adapter.path('submit')}" or call.idem_key != run.idem_key:
                raise Conflict("排队后的执行配置已变更", code="ACTION_DISPATCH_CONFIG_CHANGED")
        except AppError as exc:
            call.msg, call.result = exc.code, CallResult.failed
            run.status, run.fail_reason, run.finished_at = ActionStatus.failed, exc.message, clock.now()
            task = await DatabaseReconcileScheduler(session).ensure(run)
            resolved(task, ReconcileOutcome.not_executed.value, "系统")
            return
        call.msg, call.attempt, call.at = CLAIMED, 1, clock.now()
        payload = {"ontologyId": ontology_id, "actionRunId": run.code, "actionType": definition["api"],
                   "typeVersion": run.type_version, "idemKey": run.idem_key,
                   "objKind": run.obj_kind, "objId": run.obj_id, "params": run.params}
        key = run.idem_key
    started = time.perf_counter()
    status, response_code, result = None, None, None
    headers = {"Accept": "application/json", "Idempotency-Key": key, "X-Ontology-Id": str(ontology_id)}
    if adapter.config.token:
        headers["Authorization"] = f"Bearer {adapter.config.token.get_secret_value()}"
    try:
        async with httpx.AsyncClient(timeout=max(.1, min(system.timeout_ms / 1000, 60)),
                                     follow_redirects=False, trust_env=False) as client:
            response = await client.post(call.path, json=payload, headers=headers)
        status = response.status_code
        if not 200 <= status < 300 or len(response.content) > 65_536:
            raise ValueError
        result = ExecutionResponse.model_validate(response.json())
        if result.status == SubmitStatus.SUCCESS and not (result.receipt_id and result.receipt_id.strip()):
            raise ValueError
        response_code = result.code
    except (httpx.HTTPError, ValidationError, ValueError):
        result = ExecutionResponse(status=SubmitStatus.UNKNOWN,
            reason="外部调用超时、连接失败或响应无法确认；只对账，不重发业务写入")
    elapsed = round((time.perf_counter() - started) * 1000)
    # Remote outcome is retained even when applying local edits/events fails.
    async with sessions() as session, session.begin():
        run = await lock_run(session, code)
        call = await session.get(WritebackCall, call_id, with_for_update=True)
        call.http_status, call.elapsed_ms = status, elapsed
        call.result = {SubmitStatus.SUCCESS: CallResult.succeeded, SubmitStatus.FAILED: CallResult.failed,
                       SubmitStatus.UNKNOWN: CallResult.unknown}[result.status]
        call.msg = result.reason or result.status.value
        run.elapsed_ms, run.result_code = elapsed, response_code
        if result.receipt_id:
            run.ext_ref = result.receipt_id
        task = await DatabaseReconcileScheduler(session).ensure(run)
        if result.status == SubmitStatus.UNKNOWN:
            run.unknown_reason = result.reason
            return
        try:
            async with session.begin_nested():
                from app.modules.dynamic.catalog import get_list_catalog
                from app.modules.dynamic.ports import Ports, UnconfiguredAdapters
                from app.modules.dynamic.service import DynamicReconciliationListener
                from app.modules.integration.emitter import DatabaseEventEmitter

                unavailable = UnconfiguredAdapters()
                ports = Ports(unavailable, DatabaseEventEmitter(session), DatabaseReconcileScheduler(session), unavailable)
                outcome = ReconcileOutcome.succeeded if result.status == SubmitStatus.SUCCESS else ReconcileOutcome.not_executed
                listener = DynamicReconciliationListener(session, await get_list_catalog(session), ports)
                await listener.on_reconciled(code, ReconcileResult(outcome, result.receipt_id))
        except AppError as exc:
            to_manual(task, f"CALLBACK_{exc.code}")
        else:
            resolved(task, outcome.value, "系统")


async def dispatch_queued(timestamp=0):
    total = 0
    for ontology_id in await workspace_ids():
        total += await _dispatch_workspace(ontology_id)
    return total


async def _dispatch_workspace(ontology_id: int):
    sessions = scoped_sessions(ontology_id)
    async with sessions() as session:
        ids = list(await session.scalars(select(WritebackCall.id).where(
            WritebackCall.kind == CallKind.write, WritebackCall.msg == QUEUED)
            .order_by(WritebackCall.id).limit(100)))
    for call_id in ids:
        try:
            await dispatch_one(call_id, ontology_id)
        except Exception:
            logger.exception("Action dispatch could not complete for call %s", call_id)
    from app.modules.integration.action_effects import dispatch_effects
    return len(ids) + await dispatch_effects(ontology_id)
