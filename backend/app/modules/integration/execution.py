"""Durable action dispatch. A committed claim precedes every external POST."""

import logging
import time
import uuid

import httpx
from pydantic import ConfigDict, Field, ValidationError, field_validator
from sqlalchemy import select

from app.common.auth import check_func
from app.contracts.actions import ReconcileOutcome, ReconcileResult, SubmitResult, SubmitStatus
from app.core import clock
from app.core.errors import AppError, Conflict
from app.core.schema import ApiModel
from app.jobs.scope import scoped_sessions, workspace_ids
from app.modules.action.checker import DatabaseSubmissionChecker, structured_params
from app.modules.biz import governance as _governance  # noqa: F401
from app.modules.dynamic.models import ActionRun, ActionStatus
from app.modules.integration.action_edits import business_edits, execution_definition, target_access
from app.modules.integration.adapters import get_adapter, tls_verify
from app.modules.integration.models import NO_AUTH, CallKind, CallResult, ExtSystem, WritebackCall
from app.modules.integration.reconcile import DatabaseReconcileScheduler, lock_run, resolved, to_manual
from app.modules.integration.signing import SigningSecretInvalid, encode_body, resolve_secret, signed_headers
from app.modules.machine.principal import actor_of, resolve_actor

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


async def criteria_user(session, user, run):
    """提交条件属于提交人：已确认的记录由审批人/执行器推进时，按确认人身份复核条件。
    审批人自身的功能、对象与操作权限仍由 preflight 的其余步骤按当前身份校验；确认人失效时阻断。"""
    confirmer = getattr(run, "confirmer", None)
    if not getattr(run, "confirmed_at", None) or not isinstance(confirmer, str) or confirmer in ("", user.id):
        return user
    from app.common.auth import resolve_user

    return await resolve_user(session, confirmer)


async def http_object_values(session, user, run, definition):
    from datetime import date, datetime
    from decimal import Decimal
    from enum import Enum
    from app.common.object_identity import property_column
    from app.modules.functions.runtime import json_value
    from app.modules.integration.action_edits import existing_row, object_binding, object_operation
    from app.modules.qa.object_source import check_object_read

    mapping = definition.get("httpMapping") or {}
    fields = {row["source"][7:] for row in mapping.get("request", []) if isinstance(row.get("source"), str) and row["source"].startswith("object.")}
    if not fields:
        return {}
    if not run.obj_kind or not run.obj_id or run.obj_kind != definition.get("targetKind"):
        raise AppError("HTTP 对象字段需要已授权的动作目标", code="ACTION_HTTP_MAPPING_INVALID", status_code=422)
    check_object_read(user)
    obj, governed = await object_binding(session, run.obj_kind)
    await object_operation(session, user, obj, definition)
    row = await existing_row(session, user, obj, governed, run.obj_id)
    values = {}
    for field in fields:
        props = [p for p in obj.properties if p.api_name == field]
        column = property_column(props[0], governed) if len(props) == 1 else None
        if column is None or any(
            (p.source_column if governed.source_sql else p.backing_column or p.source_column) == column.name
            and (p.is_sensitive or p.visibility == "hidden" or p.transform)
            for p in obj.properties
        ):
            raise AppError("HTTP 映射引用了不可读取的对象属性", code="ACTION_HTTP_MAPPING_INVALID", status_code=422)
        value = row[column.name]
        if isinstance(value, Enum):
            value = value.value
        if isinstance(value, (date, datetime)):
            value = value.isoformat()
        elif isinstance(value, Decimal):
            value = str(value)
        values[field] = value
    return json_value(values)


async def preflight(session, user, run, *, review: dict | None = None):
    check_func(user, "action-center", "manage")
    definition = await execution_definition(session, run)
    from app.modules.integration.action_edits import resolved_params

    run.params = await resolved_params(
        session,
        user,
        definition,
        structured_params(run.params if run.params is not None else run.params_text),
    )
    from app.modules.integration.http_mapping import validate_mapping
    if definition.get("httpEndpointId"):
        from app.modules.integration.http_endpoints import validate_endpoint_params
        validate_endpoint_params(definition.get("httpMapping"), {p["api"] for p in definition["params"]})
    validate_mapping(definition.get("httpMapping"), {p["api"] for p in definition["params"]})
    if definition.get("httpMapping"):
        for row in definition["httpMapping"]["request"]:
            if isinstance(row.get("source"), str) and row["source"].startswith("params.") and row["source"][7:] not in run.params:
                raise AppError("HTTP 请求映射缺少参数值", code="ACTION_HTTP_MAPPING_INVALID", status_code=422)
    await target_access(session, user, run, definition)
    run._http_object_values = await http_object_values(session, user, run, definition)
    checker = DatabaseSubmissionChecker(session, await criteria_user(session, user, run))
    result = await checker.run(
        definition,
        {"objKind": run.obj_kind, "objId": run.obj_id, "params": run.params, "actionRunId": run.code},
    )
    failed = result["failed"]
    if failed:
        raise AppError(
            "动作参数或提交条件未通过",
            code="SUBMISSION_CRITERIA_FAILED",
            status_code=422,
            details={"failed": failed},
        )
    await business_edits(session, user, run, definition)
    from app.modules.integration.action_effects import preflight_effects

    await preflight_effects(session, user, run, definition)
    if review is not None:
        from app.modules.integration.action_edits import declared_writeback

        review.update(
            humanChecks=result.get("humanChecks", []),
            declaredEdits=await declared_writeback(session, definition),
        )
    return definition


def submit_adapter(system, definition):
    allowed = system.action_authorizations
    entry = allowed.get(definition["id"]) if isinstance(allowed, dict) else None
    if allowed is not None:
        details = {"systemId": system.code, "actionId": definition["id"], "field": "actionAuthorizations"}
        if not isinstance(entry, dict):
            raise AppError(
                "此外部系统未授权该动作，请在外部系统“动作授权”中选择已发布动作和允许路径后保存",
                code="ACTION_EXECUTOR_NOT_AUTHORIZED", status_code=403, details=details,
            )
        if entry.get("version") != definition.get("version"):
            raise AppError(
                f"动作执行授权版本已过期（已授权 {entry.get('version')}，当前动作 {definition.get('version')}），请在外部系统“动作授权”中重新勾选当前版本并保存",
                code="ACTION_EXECUTOR_AUTHORIZATION_VERSION_MISMATCH", status_code=409,
                details={**details, "expectedVersion": definition.get("version"), "authorizedVersion": entry.get("version"), "currentVersion": definition.get("version")},
            )
    try:
        adapter = get_adapter(system, "submit")
    except AppError as error:
        if error.code == "INTEGRATION_TLS_CONFIG_INVALID":
            raise
        raise AppError(
            "外部动作执行通道未配置或不可用", code="ACTION_EXECUTOR_UNAVAILABLE", status_code=503
        ) from error
    paths = set(adapter.config.submit_paths) | ({adapter.config.submit_path} if adapter.config.submit_path else set())
    if allowed is None and definition["id"] not in adapter.config.actions:
        raise AppError(
            "动作不在部署执行授权白名单，请联系管理员配置该动作的执行授权", code="ACTION_EXECUTOR_NOT_AUTHORIZED", status_code=403,
            details={"systemId": system.code, "actionId": definition["id"], "field": "actions"},
        )
    if allowed is not None and entry.get("path") not in paths:
        raise AppError(
            "动作授权路径已不在部署白名单，请联系管理员核对路径，并在外部系统“动作授权”中重新选择允许路径",
            code="ACTION_EXECUTOR_AUTHORIZATION_PATH_MISMATCH", status_code=409,
            details={"systemId": system.code, "actionId": definition["id"], "field": "actionAuthorizations.path"},
        )
    if allowed is not None:
        adapter.config.submit_path = entry["path"]
        adapter.path("submit")
    if definition.get("httpEndpointId"):
        from app.modules.integration.http_endpoints import require_selected_endpoint
        endpoint = require_selected_endpoint(system, definition["httpEndpointId"], definition.get("endpoint"), definition.get("httpMapping"), {p["api"] for p in definition.get("params", [])})
        if endpoint.path != adapter.path("submit"):
            raise AppError("所选接口路径与动作授权路径不一致，请在外部系统“动作授权”中核对允许路径", code="ACTION_EXECUTOR_AUTHORIZATION_PATH_MISMATCH", status_code=409, details={"field": "actionAuthorizations.path"})
    # The external system must explicitly implement our stable header contract.
    if not system.idempotent or system.idem_header.lower() != "idempotency-key":
        raise AppError(
            "真实执行要求外部系统支持 Idempotency-Key 去重",
            code="ACTION_EXECUTOR_UNAVAILABLE",
            status_code=503,
        )
    return adapter


def submit_method(system, definition):
    if not definition.get("httpEndpointId"):
        return "POST"
    from app.modules.integration.http_endpoints import require_selected_endpoint
    endpoint = require_selected_endpoint(system, definition["httpEndpointId"], definition.get("endpoint"), definition.get("httpMapping"), {p["api"] for p in definition.get("params", [])})
    return endpoint.method


class DatabaseActionSubmitter:
    def __init__(self, session, user):
        self.session, self.user = session, user

    async def submit(self, action_run_id, idempotency_key):
        # Do not refresh here: the caller has just changed the locked run to executing.
        run = await self.session.scalar(
            select(ActionRun).where(ActionRun.code == action_run_id).with_for_update()
        )
        if run is None or run.idem_key != idempotency_key or run.status != ActionStatus.executing:
            raise Conflict("执行记录或幂等状态不匹配", code="STATE_INVALID")
        definition = await preflight(self.session, self.user, run)
        if not run.sys_code:
            return SubmitResult(SubmitStatus.SUCCESS, code="INTERNAL", elapsed_ms=0)
        system = await self.session.scalar(select(ExtSystem).where(ExtSystem.code == run.sys_code))
        if system is None:
            raise AppError("外部系统不存在", code="ACTION_EXECUTOR_UNAVAILABLE", status_code=503)
        adapter = submit_adapter(system, definition)
        method = submit_method(system, definition)
        target_url = adapter.url(adapter.path('submit'))
        if len(target_url) > 255:
            raise AppError(
                "真实执行地址超过调用账本长度限制", code="ACTION_EXECUTOR_UNAVAILABLE", status_code=503
            )
        old = await self.session.scalar(
            select(WritebackCall).where(
                WritebackCall.action_run_id == run.id, WritebackCall.kind == CallKind.write
            )
        )
        if old is not None:
            raise Conflict("该执行记录已提交，不能再次排队", code="IDEMPOTENCY_CONFLICT")
        run.params = structured_params(run.params if run.params is not None else run.params_text)
        self.session.add(
            WritebackCall(
                code=f"EXEC-{uuid.uuid4().hex}",
                sys_id=system.id,
                action_run_id=run.id,
                kind=CallKind.write,
                at=clock.now(),
                op_name=run.action_name,
                target_label=run.obj_label or run.code,
                method=method,
                path=target_url,
                idem_key=run.idem_key,
                elapsed_ms=0,
                http_status=None,
                result=CallResult.unknown,
                attempt=0,
                actor=actor_of(self.user),
                msg=QUEUED,
            )
        )
        await self.session.flush()
        return SubmitResult(
            SubmitStatus.UNKNOWN,
            reason="已持久排队，尚未调用外部系统；等待执行器处理",
            code="QUEUED",
            elapsed_ms=0,
        )


async def queued_call(session, run_id):
    return await session.scalar(
        select(WritebackCall).where(
            WritebackCall.action_run_id == run_id,
            WritebackCall.kind == CallKind.write,
            WritebackCall.msg == QUEUED,
        )
    )


from app.jobs.scope import workspace_dispatch


@workspace_dispatch
async def dispatch_one(call_id, ontology_id: int):
    sessions = scoped_sessions(ontology_id)
    # Claim in its own committed transaction. A restart never repeats a claimed POST.
    async with sessions() as session, session.begin():
        code = await session.scalar(
            select(ActionRun.code)
            .join(WritebackCall, WritebackCall.action_run_id == ActionRun.id)
            .where(WritebackCall.id == call_id)
        )
        if not code:
            return
        run = await lock_run(session, code)
        call = await session.get(WritebackCall, call_id, with_for_update=True)
        if call.msg != QUEUED or run.status != ActionStatus.unknown:
            return
        try:
            user = await resolve_actor(session, call.actor)
            from app.modules.dynamic.rule_dispatch_guard import validate_rule_dispatch
            deadline = await validate_rule_dispatch(session, user, run)
            definition = await preflight(session, user, run)
            system = await session.get(ExtSystem, call.sys_id)
            adapter = submit_adapter(system, definition)
            verify = tls_verify()
            if call.path != adapter.url(adapter.path('submit')) or call.method != submit_method(system, definition) or call.idem_key != run.idem_key:
                raise Conflict("排队后的执行配置已变更", code="ACTION_DISPATCH_CONFIG_CHANGED")
            try:
                secret = resolve_secret(adapter.config.signing_secret)
            except SigningSecretInvalid as exc:
                raise AppError("外部执行签名密钥不可用", code=str(exc), status_code=503) from None
            if deadline is not None and clock.now() >= deadline:
                raise AppError("规则动作已超过配置有效期，未发送外部请求", code="RULE_ACTION_EXPIRED", status_code=409)
        except AppError as exc:
            call.msg, call.result = exc.code, CallResult.failed
            run.status, run.fail_reason, run.finished_at = ActionStatus.failed, exc.message, clock.now()
            run.unknown_reason = None
            task = await DatabaseReconcileScheduler(session).ensure(run)
            resolved(task, ReconcileOutcome.not_executed.value, "系统")
            from app.modules.dynamic.service import log_alert_action_result
            await log_alert_action_result(session, run, f"关联动作 {run.code} 执行前拒绝：{exc.message}", failed=True)
            return
        call.msg, call.attempt, call.at = CLAIMED, 1, clock.now()
        payload = {
            "ontologyId": ontology_id,
            "actionRunId": run.code,
            "actionType": definition["api"],
            "typeVersion": run.type_version,
            "idemKey": run.idem_key,
            "objKind": run.obj_kind,
            "objId": run.obj_id,
            "params": run.params,
        }
        from app.modules.integration.action_edits import declared_writeback
        from app.modules.integration.http_mapping import request_body

        declared = await declared_writeback(session, definition)
        if declared:  # 自然语言编辑/副作用：声明由外部系统承担，本地不执行（status=external/declared）
            payload["declaredEdits"] = declared
        payload = request_body(definition.get("httpMapping"), payload, run._http_object_values)
        mapping = definition.get("httpMapping")
        key = run.idem_key
    started = time.perf_counter()
    status, response_code, result = None, None, None
    headers = {"Accept": "application/json", "Idempotency-Key": key, "X-Ontology-Id": str(ontology_id)}
    if system.auth_type != NO_AUTH and adapter.config.token:
        headers["Authorization"] = f"Bearer {adapter.config.token.get_secret_value()}"
    # Signed like Webhooks; deliberately no automatic resend here: unknown outcomes go to reconcile_task.
    body = encode_body(payload)
    headers = signed_headers(headers, body, secret)
    try:
        from app.modules.integration.adapters import pin_http_target
        pinned_url, host_header, extensions = await pin_http_target(call.path)
        headers.update(host_header)
        async with httpx.AsyncClient(
            timeout=max(0.1, min(system.timeout_ms / 1000, 60)),
            follow_redirects=False,
            trust_env=False,
            verify=verify,
        ) as client:
            response = await client.request(call.method, pinned_url, content=body, headers=headers, extensions=extensions)
        status = response.status_code
        if not 200 <= status < 300 or len(response.content) > 65_536:
            raise ValueError
        from app.modules.integration.http_mapping import response_body
        result = ExecutionResponse.model_validate(response_body(mapping, response.json()))
        if result.status == SubmitStatus.SUCCESS and not (result.receipt_id and result.receipt_id.strip()):
            raise ValueError
        response_code = result.code
    except (httpx.HTTPError, ValidationError, ValueError):
        result = ExecutionResponse(
            status=SubmitStatus.UNKNOWN, reason="外部调用超时、连接失败或响应无法确认；只对账，不重发业务写入"
        )
    elapsed = round((time.perf_counter() - started) * 1000)
    # Remote outcome is retained even when applying local edits/events fails.
    async with sessions() as session, session.begin():
        run = await lock_run(session, code)
        call = await session.get(WritebackCall, call_id, with_for_update=True)
        call.http_status, call.elapsed_ms = status, elapsed
        call.result = {
            SubmitStatus.SUCCESS: CallResult.succeeded,
            SubmitStatus.FAILED: CallResult.failed,
            SubmitStatus.UNKNOWN: CallResult.unknown,
        }[result.status]
        call.msg = result.reason or result.status.value
        run.elapsed_ms, run.result_code = elapsed, response_code
        if result.receipt_id:
            run.ext_ref = result.receipt_id
        if result.status == SubmitStatus.FAILED:
            run.fail_reason = result.reason or result.code or "外部明确返回失败"
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
                ports = Ports(
                    unavailable,
                    DatabaseEventEmitter(session),
                    DatabaseReconcileScheduler(session),
                    unavailable,
                )
                outcome = (
                    ReconcileOutcome.succeeded
                    if result.status == SubmitStatus.SUCCESS
                    else ReconcileOutcome.not_executed
                )
                listener = DynamicReconciliationListener(session, await get_list_catalog(session), ports)
                await listener.on_reconciled(code, ReconcileResult(outcome, result.receipt_id, {"source": "submit", "reason": result.reason, "code": result.code}))
        except AppError as exc:
            to_manual(task, f"CALLBACK_{exc.code}")
        else:
            resolved(task, outcome.value, "系统")


async def dispatch_queued(timestamp=0):
    total = 0
    for ontology_id in await workspace_ids():
        from app.common.ontology import WorkspaceInactive
        try:
            total += await _dispatch_workspace(ontology_id)
        except WorkspaceInactive:
            continue
    return total


async def _dispatch_workspace(ontology_id: int):
    sessions = scoped_sessions(ontology_id)
    async with sessions() as session:
        ids = list(
            await session.scalars(
                select(WritebackCall.id)
                .where(WritebackCall.kind == CallKind.write, WritebackCall.msg == QUEUED)
                .order_by(WritebackCall.id)
                .limit(100)
            )
        )
    for call_id in ids:
        try:
            await dispatch_one(call_id, ontology_id)
        except Exception:
            logger.exception("Action dispatch could not complete for call %s", call_id)
    from app.modules.integration.action_effects import dispatch_effects

    return len(ids) + await dispatch_effects(ontology_id)
