"""Retain dynamic action events in the real event outbox; no subscriber delivery claim."""

import hashlib

from sqlalchemy import select

from app.contracts.actions import EVENT_ACTION_EXECUTED, EVENT_ACTION_OUTCOME_UNKNOWN
from app.core import clock
from app.core.errors import AppError, NotFound
from app.modules.dynamic.models import ActionRun
from app.modules.event.ingress import receive
from app.modules.event.ingress_schema import IngressIn
from app.modules.event.models import Event
from app.modules.integration.models import CallKind, CallResult, ExtSystem, WritebackCall


class DatabaseEventEmitter:
    def __init__(self, session):
        self.session = session

    async def emit(self, event_id: str, event_type: str, payload: dict[str, object]) -> None:
        if event_type not in (EVENT_ACTION_EXECUTED, EVENT_ACTION_OUTCOME_UNKNOWN):
            raise AppError("未配置该内部事件的载荷映射", code="EVENT_DELIVERY_UNAVAILABLE", status_code=503)
        run = await self.session.scalar(select(ActionRun).where(ActionRun.code == payload.get("actionRunId")))
        if run is None:
            raise NotFound("事件来源动作不存在")
        from app.modules.integration.action_edits import business_edits, execution_definition

        definition = await execution_definition(self.session, run)
        values = {"runId": run.code, "actionType": definition["api"]}
        if event_type == EVENT_ACTION_EXECUTED:
            values.update(
                targetId=run.obj_id or "",
                initiator=run.initiator,
                confirmer=run.confirmer or "",
                writeback=run.result_code or "",
            )
        else:
            system = await self.session.scalar(select(ExtSystem).where(ExtSystem.code == run.sys_code))
            if system is None or run.elapsed_ms is None:
                raise AppError(
                    "动作结果未知事件缺少实际系统或等待时长",
                    code="EVENT_CONTEXT_UNAVAILABLE",
                    status_code=503,
                )
            values.update(system=system.code, idemKey=run.idem_key, waitedMs=run.elapsed_ms)
        identity = event_id if run.ontology_id == 1 else f"{run.ontology_id}:{event_id}"
        code = f"dynamic:{hashlib.sha256(identity.encode()).hexdigest()[:56]}"
        previous = await self.session.scalar(select(Event).where(Event.code == code))
        from app.modules.machine.principal import resolve_actor
        call = await self.session.scalar(select(WritebackCall).where(
            WritebackCall.action_run_id == run.id, WritebackCall.kind == CallKind.write))
        actor = call.actor if call else (run.approver or run.confirmer or run.created_by)
        user = await resolve_actor(self.session, actor or "")
        if event_type == EVENT_ACTION_EXECUTED and not previous and (
                definition["writeback"]["edits"] or definition["writeback"]["effects"]):
            if run.sys_code:
                evidence = (call is not None and call.attempt > 0 and call.idem_key == run.idem_key
                            and call.result == CallResult.succeeded and call.http_status is not None
                            and 200 <= call.http_status < 300)
                if not evidence and call is not None and call.attempt > 0:
                    evidence = await self.session.scalar(select(WritebackCall.id).where(
                        WritebackCall.action_run_id == run.id, WritebackCall.kind == CallKind.query,
                        WritebackCall.idem_key == run.idem_key,
                        WritebackCall.result == CallResult.succeeded,
                        WritebackCall.msg == "查到·成功").limit(1))
                if not evidence:
                    raise AppError("缺少真实外部成功证据，人工标注不能触发业务属性修改",
                                   code="ACTION_EDITS_EVIDENCE_REQUIRED", status_code=409)
            await business_edits(self.session, user, run, definition, apply=True)
        await receive(
            self.session,
            IngressIn(
                event_id=code,
                event_type=event_type,
                occurred_at=previous.occurred_at if previous else clock.now(),
                source="dynamic",
                object_type="ActionRun",
                object_id=run.code,
                payload=values,
                dedup_key=run.code,
            ),
        )
        if not previous:
            from app.modules.integration.action_effects import retain_effects
            await retain_effects(self.session, user, run, definition, event_type)
        if event_type == EVENT_ACTION_OUTCOME_UNKNOWN:
            from app.modules.integration.reconcile import DatabaseReconcileScheduler, lock_run

            await DatabaseReconcileScheduler(self.session).ensure(await lock_run(self.session, run.code))
