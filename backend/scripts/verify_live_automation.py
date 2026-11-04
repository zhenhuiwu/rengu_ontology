"""Explicitly scoped live verification; never sweeps queues or writes the source database.

Run only against a newly created verification workspace. Configuration overrides live
in this process, never in the user's .env or running API. HTTP uses loopback only.
"""
import argparse
import asyncio
import hashlib
import json
import os
import re
import secrets
import signal
import socket
import subprocess
import sys
import threading
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

PREFIX = "LIVE-AUTO-1031"
ACTION = "live_auto_receipt"
EVENT = "LiveVerificationRequested"
RULE = "R-LIVE-AUTO-1031"
SYSTEM = "LIVE-GENERIC-RECEIVER"
SUBSCRIBER = "Local verification rule"
records = {}
requests_count = 0


class Receiver(BaseHTTPRequestHandler):
    def respond(self, code, payload):
        data = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        global requests_count
        if self.path != "/receipt":
            return self.respond(404, {})
        length = int(self.headers.get("Content-Length", "0"))
        if not 0 < length < 65536:
            return self.respond(400, {})
        body = self.rfile.read(length)
        payload = json.loads(body)
        key = self.headers.get("Idempotency-Key")
        if (not key or payload.get("idemKey") != key
                or payload.get("ontologyId") != ARGS.workspace
                or payload.get("actionType") != ACTION
                or payload.get("objId") != ARGS.object_id):
            return self.respond(422, {"error": "UNEXPECTED_FIXTURE"})
        requests_count += 1
        digest = hashlib.sha256(body).hexdigest()
        old = records.get(key)
        if old and old[0] != digest:
            return self.respond(409, {"error": "IDEMPOTENCY_CONFLICT"})
        receipt = "LOCAL-" + hashlib.sha256(key.encode()).hexdigest()[:20]
        records[key] = (digest, receipt)
        return self.respond(200, {"status": "SUCCESS", "receiptId": receipt})

    def do_GET(self):
        request = urlsplit(self.path)
        if request.path != "/status":
            return self.respond(404, {})
        key = parse_qs(request.query).get("idemKey", [""])[0]
        row = records.get(key)
        return self.respond(200, {"extStatus": "查到·成功", "extRef": row[1]} if row else {"extStatus": "查无"})

    def log_message(self, *_args):
        pass


def report(step, **values):
    item = {"step": step, **values}
    EVIDENCE.append(item)
    print(json.dumps(item, ensure_ascii=False), flush=True)
    OUTPUT.write_text(json.dumps(EVIDENCE, ensure_ascii=False, indent=2), encoding="utf-8")


async def fixture():
    from sqlalchemy import select
    from app.db.session import get_sessionmaker
    from app.common.ontology import set_ontology_scope
    from app.modules.ontology.models import OntologyWorkspace
    from app.modules.static.models import ObjectType
    from app.modules.action.models import ActionType, ActionTypeVersion, TypeStatus
    from app.modules.action.sections import action_def
    from app.modules.event.models import EventType, EventPayloadField, EventSubscription, EventSource, SubscriberKind, SubscriberStatus
    from app.modules.event.sections import event_def
    from app.modules.dynamic.models import AlertLevel
    from app.modules.dynamic.rules_models import Rule, RuleStatus, RuleOutput
    from app.modules.dynamic.sections import rule_def
    from app.modules.integration.models import ExtSystem, ExtSystemStatus, NO_AUTH
    from app.modules.admin.models import SysUser
    from app.modules.versioning.models import OntologyRelease
    from app.modules.versioning.repo import ReleaseRepo
    from app.modules.versioning.sections import SectionSnapshotProvider

    async with get_sessionmaker()() as session:
        assert session.get_bind().engine.url.database == "rengu_ontology"
        workspace = await session.get(OntologyWorkspace, ARGS.workspace)
        assert workspace and "live" in workspace.code and not workspace.is_default
        await set_ontology_scope(session, ARGS.workspace)
        obj = await session.scalar(select(ObjectType).where(ObjectType.code == ARGS.object_type))
        assert obj and obj.created_by == "U010001" and obj.status.value == "已发布"
        if await session.scalar(select(ActionType.id).where(ActionType.code == ACTION)):
            report("fixture_existing", workspace=ARGS.workspace)
            return
        owner_id = await session.scalar(select(SysUser.id).where(SysUser.code == "U010001"))
        system = ExtSystem(code=SYSTEM, name="本机通用动作真实联调接收端", category="Webhook",
            endpoint="http://127.0.0.1:8766", auth_type=NO_AUTH, idempotent=True,
            idem_header="Idempotency-Key", timeout_ms=3000, max_retry=0,
            status=ExtSystemStatus.ok, env="测试", created_by="U010001",
            action_authorizations={ACTION: {"version": "v1", "path": "/receipt"}},
            http_endpoints=[{"id": "receipt", "name": "本机回执", "purpose": "action", "method": "POST", "path": "/receipt", "httpMapping": None}])
        session.add(system)
        await session.flush()
        action = ActionType(code=ACTION, name="通用验证回执动作", api=ACTION, grp="验证",
            status=TypeStatus.published, version="v1", target=obj.display_name,
            target_api=obj.api_name, target_kind=obj.code, owner="U010001", runners="U010001",
            approval="免审", sys_id=system.id, endpoint="POST /receipt", http_endpoint_id="receipt",
            biz_key_template="", created_by="U010001", params=[], conds=[], edits=[], effects=[], rules=[])
        session.add(action)
        await session.flush()
        await session.refresh(action, ["system"])
        definition = action_def(action)
        definition["policy"] = {"mode": "auto", "callers": ["规则", "人"], "limits": {"dailyCount": 10}}
        session.add(ActionTypeVersion(action_type_id=action.id, ver="v1", release_ver="v2026.10.31", definition=definition,
            change_note="专用本机验证", by_name="U010001", at=datetime.now(UTC), created_by="U010001"))
        event = EventType(code=EVENT, name="通用平台验证事件", api=EVENT, source=EventSource.external,
            object=obj.display_name, object_api=obj.api_name, obj_kind=obj.code, status=TypeStatus.published,
            version="v1", owner="U010001", dedup_key_rule="eventId", dedup_window="永久",
            delivery="至少一次", ordering="不保证", retention_days=7, created_by="U010001",
            payload=[EventPayloadField(seq=1, name="验证标记", api="testFlag", type="布尔", required=True, sample="true", created_by="U010001")],
            subscribers=[EventSubscription(seq=1, kind=SubscriberKind.rule, name=SUBSCRIBER,
                rule_code=RULE, filter_text="  ", then_text="真实本机回执动作", status=SubscriberStatus.enabled, created_by="U010001")])
        session.add(event)
        rule = Rule(code=RULE, name="通用验证自动动作规则", version=1, owner="U010001", status=RuleStatus.published,
            obj_kind=obj.code, level=AlertLevel.low, output=RuleOutput.alert, conds=[], logic="且", expr="event.testFlag == True",
            dedupe="规则 + eventId", cooldown="不重复", trigger={"kind": "事件", "enabled": True, "eventId": EVENT,
                "filter": "", "runAsUserId": "U010001"}, action_type_id=action.id,
            auto_stage={"enabled": True, "actionTypeId": ACTION, "executionMode": "auto", "confirmer": "U010001", "mapping": []},
            created_by="U010001")
        session.add(rule)
        await session.flush()
        latest = await ReleaseRepo(session).latest()
        snapshot = await SectionSnapshotProvider().build_snapshot(session, latest.ver if latest else None, [])
        snapshot.setdefault("actionTypes", {})[ACTION] = definition
        snapshot.setdefault("eventTypes", {})[EVENT] = event_def(event)
        snapshot.setdefault("rules", {})[RULE] = rule_def(rule, ACTION)
        version = "v2026.10.31" if latest is None else "v2026.10." + str(int(latest.ver.rsplit(".", 1)[1]) + 1)
        release = OntologyRelease(ver=version, scope="formal", parent_id=latest.id if latest else None,
            snapshot=snapshot, snapshot_sha256=hashlib.sha256(json.dumps(snapshot, sort_keys=True, ensure_ascii=False).encode()).hexdigest(),
            published_by_id=owner_id, published_at=datetime.now(UTC), note="专用本机通用链路验证", created_by="U010001")
        session.add(release)
        await session.flush()
        await session.commit()
        report("fixture_created", workspace=ARGS.workspace, release=version, action=ACTION, event=EVENT, rule=RULE)


async def main():
    import httpx
    import uvicorn
    from sqlalchemy import select
    from app.main import app
    from app.jobs.scope import scoped_sessions
    from app.modules.event.worker import deliver_event
    from app.modules.event.models import Event, EventDelivery
    from app.modules.dynamic.models import ActionRun
    from app.modules.integration.models import WritebackCall
    from app.modules.integration.execution import dispatch_one, submit_adapter
    from app.modules.integration.adapters import get_adapter
    from app.db.session import dispose_engine

    await fixture()
    if ARGS.fixture_only:
        await dispose_engine()
        return
    if not ARGS.replace_api_pid:
        raise ValueError("Explicit current API PID required for the coordinated restart window")
    await dispose_engine()
    receiver = ThreadingHTTPServer(("127.0.0.1", 8766), Receiver)
    threading.Thread(target=receiver.serve_forever, daemon=True).start()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=8000, lifespan="on", access_log=False, log_level="error"))
    task = None
    stopped = False
    try:
        os.kill(ARGS.replace_api_pid, signal.SIGTERM)
        stopped = True
        for _ in range(100):
            try:
                with socket.create_connection(("127.0.0.1", 8000), timeout=.1):
                    pass
            except OSError:
                break
            await asyncio.sleep(.05)
        else:
            raise RuntimeError("Existing API did not release the approved port")
        task = asyncio.create_task(server.serve())
        for _ in range(100):
            if server.started:
                break
            if task.done():
                raise RuntimeError("Verification API failed to start")
            await asyncio.sleep(.05)
        async with httpx.AsyncClient(base_url="http://127.0.0.1:8000", trust_env=False, timeout=20) as client:
            response = await client.get("/readyz")
            report("verification_api_ready", http=response.status_code, pid=os.getpid(), lifespan="normal", port=8000)
            assert response.status_code == 200
            password_text = (ROOT / ".local" / "admin-login.txt").read_text(encoding="utf-8")
            password = re.search(r"密码[：:]\s*(\S+)", password_text).group(1)
            response = await client.post("/api/auth/v1/login", headers={"Origin": "http://127.0.0.1:8000"}, json={"username": "admin", "password": password})
            report("login", http=response.status_code)
            assert response.status_code == 200
            headers = {"X-Ontology-Id": str(ARGS.workspace), "Origin": "http://127.0.0.1:8000"}
            body = {"objKind": ARGS.object_type, "objId": ARGS.object_id, "params": {}}
            response = await client.post(f"/api/ontology/v1/action-types/{ACTION}:dry-run", headers=headers, json=body)
            report("dry_run", http=response.status_code, result=response.json())
            assert response.status_code == 200 and response.json()["passed"]
            envelope = {"eventId": PREFIX + "-" + RUNSTAMP + "-E1", "eventType": EVENT, "occurredAt": datetime.now(UTC).isoformat(),
                "source": "isolated-live-verification", "objectType": ARGS.object_type, "objectId": ARGS.object_id,
                "payload": {"testFlag": True}}
            ingress_headers = {"X-Ontology-Id": str(ARGS.workspace), "Authorization": "Bearer " + TOKEN}
            for label in ("ingress", "ingress_replay"):
                response = await client.post("/internal/v1/events", headers=ingress_headers, json=envelope)
                report(label, http=response.status_code, result=response.json())
                assert response.status_code == 200
            await deliver_event(ARGS.workspace, envelope["eventId"], SUBSCRIBER)
            async with scoped_sessions(ARGS.workspace)() as session:
                event = await session.scalar(select(Event).where(Event.code == envelope["eventId"]))
                delivery = await session.scalar(select(EventDelivery).where(EventDelivery.event_id == event.id))
                report("delivery", eventId=event.code, outcome=event.outcome.value, deliveryId=delivery.id, state=delivery.state,
                    error=delivery.error, attempts=delivery.attempts, result=delivery.result)
                assert delivery.state == "delivered"
                run = await session.scalar(select(ActionRun).where(ActionRun.action_type_code == ACTION).order_by(ActionRun.id.desc()))
                call = await session.scalar(select(WritebackCall).where(WritebackCall.action_run_id == run.id))
                run_code, call_id, idem_key = run.code, call.id, run.idem_key
                report("automatic_action_queued", runId=run_code, status=run.status.value, callId=call_id, attempt=call.attempt)
            await dispatch_one(call_id, ARGS.workspace)
            await dispatch_one(call_id, ARGS.workspace)
            await deliver_event(ARGS.workspace, envelope["eventId"], SUBSCRIBER)
            async with scoped_sessions(ARGS.workspace)() as session:
                run = await session.scalar(select(ActionRun).where(ActionRun.code == run_code))
                call = await session.get(WritebackCall, call_id)
                report("automatic_action_receipt", runId=run.code, status=run.status.value, receipt=run.ext_ref,
                    reconciliation=run.reconcile_result.value if run.reconcile_result else None,
                    callId=call.id, http=call.http_status, attempt=call.attempt, receiverRequests=requests_count, uniqueReceipts=len(records))
                assert call.http_status == 200 and run.status.value == "已对账" and run.reconcile_result.value == "外部已成功" and run.ext_ref
                assert requests_count == len(records) == 1
                system = await session.get(__import__("app.modules.integration.models", fromlist=["ExtSystem"]).ExtSystem, call.sys_id)
                result = await get_adapter(system).query_by_key(idem_key)
                report("reconcile_query", status=result.ext_status.value, receipt=result.ext_ref)
            response = await client.post(f"/api/ontology/v1/machine/actions/{ACTION}:execute", headers={**headers, "Idempotency-Key": PREFIX + "-" + RUNSTAMP + "-M1"}, json=body)
            report("machine_execute", http=response.status_code, result=response.json())
            assert response.status_code == 201
            machine_run = response.json()["runId"]
            response = await client.post(f"/api/ontology/v1/machine/actions/{ACTION}:execute", headers={**headers, "Idempotency-Key": PREFIX + "-" + RUNSTAMP + "-M1"}, json=body)
            report("machine_replay", http=response.status_code, result=response.json())
            assert response.status_code == 200
            assert response.json()["runId"] == machine_run
            async with scoped_sessions(ARGS.workspace)() as session:
                run = await session.scalar(select(ActionRun).where(ActionRun.code == machine_run))
                call_id = await session.scalar(select(WritebackCall.id).where(WritebackCall.action_run_id == run.id))
            await dispatch_one(call_id, ARGS.workspace)
            await dispatch_one(call_id, ARGS.workspace)
            async with scoped_sessions(ARGS.workspace)() as session:
                run = await session.scalar(select(ActionRun).where(ActionRun.code == machine_run))
                call = await session.get(WritebackCall, call_id)
                report("machine_receipt", runId=run.code, status=run.status.value, receipt=run.ext_ref, http=call.http_status,
                    reconciliation=run.reconcile_result.value if run.reconcile_result else None,
                    receiverRequests=requests_count, uniqueReceipts=len(records))
                assert run.status.value == "已对账" and run.reconcile_result.value == "外部已成功" and requests_count == len(records) == 2
            from app.core.config import get_settings
            from app.core.errors import AppError
            from app.modules.event.simulation import filter_matches
            from app.modules.event.schemas import FieldOut, SubscriberOut
            from app.modules.action.condition_expr import Unavailable
            fields = [FieldOut(name="验证标记", api="testFlag", type="布尔", required=True, desc="", sample="true")]
            for value in ("", "  ", "全部"):
                normalized = SubscriberOut.model_validate({"kind": "规则", "name": SUBSCRIBER, "ruleId": RULE,
                    "filter": value, "then": "验证", "status": "启用", "weekly": 0}).filter
                assert filter_matches(value, fields, {"testFlag": True}) and normalized == "全部"
            try:
                filter_matches("this cannot execute ?", fields, {"testFlag": True})
            except Unavailable:
                report("filter_compatibility", storedBlankSnapshot="delivered", newEmptyAndBlank="全部", invalidNonempty="rejected")
            else:
                raise AssertionError("Invalid nonempty filter accepted")
            from app.modules.integration.models import ExtSystem
            from app.modules.action.models import ActionType
            from app.modules.action.sections import action_def
            async with scoped_sessions(ARGS.workspace)() as session:
                system = await session.scalar(select(ExtSystem).where(ExtSystem.code == SYSTEM))
                definition = action_def(await session.scalar(select(ActionType).where(ActionType.code == ACTION)))
                authorization = system.action_authorizations
                system.action_authorizations = {ACTION: {"version": "v0", "path": "/receipt"}}
                try:
                    submit_adapter(system, definition)
                except AppError as exc:
                    report("stale_authorization", http=exc.status_code, code=exc.code, receiverRequests=requests_count)
                    assert exc.code == "ACTION_EXECUTOR_AUTHORIZATION_VERSION_MISMATCH"
                else:
                    raise AssertionError("Stale authorization accepted")
                system.action_authorizations = authorization
                endpoints = system.http_endpoints
                system.http_endpoints = []
                try:
                    submit_adapter(system, definition)
                except AppError as exc:
                    report("missing_endpoint", http=exc.status_code, code=exc.code, receiverRequests=requests_count)
                    assert exc.code == "ACTION_HTTP_ENDPOINT_NOT_FOUND"
                else:
                    raise AssertionError("Missing endpoint accepted")
                system.http_endpoints = endpoints
                await session.rollback()
            missing = {**envelope, "eventId": PREFIX + "-" + RUNSTAMP + "-MISSING", "objectId": "LIVE-NOT-SYNCED-1031"}
            response = await client.post("/internal/v1/events", headers=ingress_headers, json=missing)
            assert response.status_code == 200
            await deliver_event(ARGS.workspace, missing["eventId"], SUBSCRIBER)
            async with scoped_sessions(ARGS.workspace)() as session:
                event = await session.scalar(select(Event).where(Event.code == missing["eventId"]))
                delivery = await session.scalar(select(EventDelivery).where(EventDelivery.event_id == event.id))
                report("missing_mirror_row", http=response.status_code, eventId=event.code, deliveryId=delivery.id,
                    state=delivery.state, error=delivery.error, attempts=delivery.attempts,
                    nextAtSet=delivery.next_at is not None, boundedWaitMinutes=15, receiverRequests=requests_count)
                assert delivery.state == "pending" and delivery.error == "EVENT_OBJECT_NOT_SYNCED" and delivery.attempts == 0
            from pydantic import SecretStr
            settings = get_settings()
            original_routes = settings.event_delivery_routes
            settings.event_delivery_routes = SecretStr(json.dumps({f"{ARGS.workspace}/{EVENT}/{SUBSCRIBER}":
                {"rule_code": RULE, "user": "U010002", "rule_version": 1}}))
            try:
                denied = {**envelope, "eventId": PREFIX + "-" + RUNSTAMP + "-DENIED"}
                response = await client.post("/internal/v1/events", headers=ingress_headers, json=denied)
                assert response.status_code == 200
                await deliver_event(ARGS.workspace, denied["eventId"], SUBSCRIBER)
                await deliver_event(ARGS.workspace, denied["eventId"], SUBSCRIBER)
                async with scoped_sessions(ARGS.workspace)() as session:
                    event = await session.scalar(select(Event).where(Event.code == denied["eventId"]))
                    delivery = await session.scalar(select(EventDelivery).where(EventDelivery.event_id == event.id))
                    report("permission_terminal", http=response.status_code, eventId=event.code, deliveryId=delivery.id,
                        state=delivery.state, error=delivery.error, attempts=delivery.attempts, nextAtSet=delivery.next_at is not None,
                        receiverRequests=requests_count)
                    assert delivery.state == "failed" and delivery.error in ("NO_PERMISSION", "EVENT_OBJECT_FORBIDDEN", "UNAUTHORIZED")
                    assert delivery.next_at is None and delivery.attempts == 0
            finally:
                settings.event_delivery_routes = original_routes
    finally:
        server.should_exit = True
        if task is not None:
            await task
        receiver.shutdown()
        receiver.server_close()
        await dispose_engine()
        if not stopped:
            raise RuntimeError("Approved original API process was not stopped; no replacement launched")
        with (ROOT / ".local" / "automation-api-restored.log").open("ab") as log:
            restored = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8000",
                "--env-file", str(ROOT / ".env")], cwd=ROOT, env=BASE_ENV, stdout=log, stderr=log, creationflags=subprocess.CREATE_NO_WINDOW)
        async with httpx.AsyncClient(trust_env=False, timeout=2) as client:
            for _ in range(100):
                try:
                    response = await client.get("http://127.0.0.1:8000/readyz")
                    report("original_api_restored", http=response.status_code, launcherPid=restored.pid, overridesRemoved=True)
                    break
                except httpx.HTTPError:
                    await asyncio.sleep(.1)
            else:
                report("original_api_restore_failed", launcherPid=restored.pid)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=int, required=True)
    parser.add_argument("--object-type", required=True)
    parser.add_argument("--object-id", required=True)
    parser.add_argument("--fixture-only", action="store_true")
    parser.add_argument("--replace-api-pid", type=int)
    ARGS = parser.parse_args()
    assert ARGS.workspace > 1
    BASE_ENV = dict(os.environ)
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env", override=False)
    TOKEN = secrets.token_urlsafe(32)
    RUNSTAMP = datetime.now(UTC).strftime("%H%M%S%f")
    os.environ["RENGU_INTERNAL_EVENT_TOKENS"] = json.dumps({str(ARGS.workspace): TOKEN})
    os.environ["RENGU_INTEGRATION_REAL_CONNECT"] = "true"
    os.environ["RENGU_INTEGRATION_ADAPTERS"] = json.dumps({f"{ARGS.workspace}/{SYSTEM}": {
        "origin": "http://127.0.0.1:8766", "submit_path": "/receipt", "query_path": "/status"}})
    EVIDENCE = []
    OUTPUT = ROOT / ".local" / f"automation-live-evidence-{RUNSTAMP}.json"
    try:
        asyncio.run(main())
    except Exception as exc:
        cause = getattr(getattr(exc, "orig", None), "__cause__", None)
        report("verification_failed", errorType=type(exc).__name__, code=getattr(exc, "code", None),
            constraint=getattr(cause, "constraint_name", None), column=getattr(cause, "column_name", None),
            message=str(exc).split("\n", 1)[0][:180] if type(exc).__name__ not in ("IntegrityError", "DBAPIError") else None)
        raise SystemExit(1) from None
