"""Authorized fault injection into workspace 15 only; no source writes or queue sweep."""
import asyncio
import hashlib
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv
load_dotenv(ROOT / ".env", override=False)

from sqlalchemy import delete, select
from app.db import models as _models
from app.db.session import dispose_engine
from app.jobs.scope import scoped_sessions
from app.common.auth import resolve_user
from app.common.sql_guard import ensure_governed_tables, governed_tables
from app.common.object_identity import identity_rows
from app.modules.event.delivery import prepare
from app.modules.event.worker import deliver_event
from app.modules.event.ingress import receive
from app.modules.event.ingress_schema import IngressIn
from app.modules.event.models import Event, EventDelivery, EventPayloadField, EventSource, EventSubscription, EventType, SubscriberKind, SubscriberStatus
from app.modules.action.models import TypeStatus
from app.modules.data.models import ExternalMirrorRow, ExternalMirrorRun
from app.modules.dynamic.models import AlertLevel
from app.modules.dynamic.rules_models import Rule, RuleOutput, RuleStatus
from app.modules.integration.models import WritebackCall
from app.modules.static.models import ObjectType
from app.modules.ontology.models import OntologyWorkspace
from app.modules.versioning.repo import ReleaseRepo
from app.modules.versioning.models import OntologyRelease
from app.modules.versioning.sections import load_sections, SectionSnapshotProvider
from app.modules.admin.models import SysUser

OID = 15
EVENT_TYPE = "LiveIdentityPreflightRequested"
RULE = "R-LIVE-IDENTITY-1031"
SUB = "Controlled identity preflight"
EVIDENCE = []
OUTPUT = ROOT / ".local" / "automation-live-boundaries.json"


def report(step, **data):
    EVIDENCE.append({"step": step, **data})
    OUTPUT.write_text(json.dumps(EVIDENCE, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(EVIDENCE[-1], ensure_ascii=False), flush=True)


async def main():
    load_sections()
    sessions = scoped_sessions(OID)
    inserted_id = None
    original_ids = None
    try:
        async with sessions() as session:
            assert session.get_bind().engine.url.database == "rengu_ontology"
            workspace = await session.get(OntologyWorkspace, OID)
            assert workspace.code == "dm-live-20261031"
            delivery = await session.get(EventDelivery, 20)
            event = await session.get(Event, delivery.event_id)
            assert event.code == "LIVE-AUTO-1031-063954851910-MISSING" and delivery.error == "EVENT_OBJECT_NOT_SYNCED" and delivery.state == "pending"
            original_created = delivery.created_at
            delivery.created_at = datetime.now(UTC) - timedelta(minutes=16)
            await session.commit()
            assert not await prepare(session, delivery.id)
            await session.commit()
            report("expired_missing_mirror_preflight", deliveryId=delivery.id, state=delivery.state,
                error=delivery.error, attempts=delivery.attempts, nextAtSet=delivery.next_at is not None,
                deadlineInjection="only this new test delivery created_at moved 16 minutes earlier")
            assert delivery.state == "failed" and delivery.next_at is None and delivery.attempts == 0
            # Restore the timestamp after proving expiry; preserve the terminal result and appended preflight evidence.
            delivery.created_at = original_created
            await session.commit()

        async with sessions() as session:
            assert await session.scalar(select(ExternalMirrorRun.id).where(
                ExternalMirrorRun.object_code == "LiveProject", ExternalMirrorRun.status.in_(("queued", "running")))) is None
            original_ids = set(await session.scalars(select(ExternalMirrorRow.id).where(ExternalMirrorRow.object_code == "LiveProject")))
            assert len(original_ids) == 9
            obj = await session.scalar(select(ObjectType).where(ObjectType.code == "LiveProject"))
            primary = next(p for p in obj.properties if p.api_name == obj.pk_property)
            row = await session.scalar(select(ExternalMirrorRow).where(ExternalMirrorRow.object_code == "LiveProject", ExternalMirrorRow.is_current.is_(True)).order_by(ExternalMirrorRow.id))
            identity = str(row.payload[primary.source_column])
            calls_before = set(await session.scalars(select(WritebackCall.id)))
            event_type = await session.scalar(select(EventType).where(EventType.code == EVENT_TYPE))
            if event_type is None:
                event_type = EventType(code=EVENT_TYPE, name="对象身份预检验证事件", api=EVENT_TYPE,
                    source=EventSource.external, object=obj.display_name, object_api=obj.api_name, obj_kind=obj.code,
                    status=TypeStatus.published, version="v1", owner="U010001", dedup_key_rule="eventId", dedup_window="永久",
                    ordering="不保证", retention_days=7, created_by="U010001",
                    payload=[EventPayloadField(seq=1, name="验证标记", api="testFlag", type="布尔", required=True, sample="true", created_by="U010001")],
                    subscribers=[EventSubscription(seq=1, kind=SubscriberKind.rule, name=SUB, rule_code=RULE,
                        filter_text="全部", then_text="对象预检", status=SubscriberStatus.enabled, created_by="U010001")])
                session.add(event_type)
                session.add(Rule(code=RULE, name="对象身份预检验证规则", version=1, owner="U010001",
                    status=RuleStatus.published, obj_kind=obj.code, level=AlertLevel.low, output=RuleOutput.alert,
                    conds=[], expr="event.testFlag == True", dedupe="规则 + eventId", cooldown="不重复",
                    trigger={"kind": "事件", "enabled": True, "eventId": EVENT_TYPE, "filter": "全部", "runAsUserId": "U010001"},
                    auto_stage={"enabled": False}, created_by="U010001"))
                await session.flush()
                latest = await ReleaseRepo(session).latest()
                snapshot = await SectionSnapshotProvider().build_snapshot(session, latest.ver, [])
                version = "v2026.10." + str(int(latest.ver.rsplit(".", 1)[1]) + 1)
                owner = await session.scalar(select(SysUser.id).where(SysUser.code == "U010001"))
                session.add(OntologyRelease(ver=version, scope="formal", parent_id=latest.id, snapshot=snapshot,
                    snapshot_sha256=hashlib.sha256(json.dumps(snapshot, ensure_ascii=False, sort_keys=True).encode()).hexdigest(),
                    published_by_id=owner, published_at=datetime.now(UTC), note="仅新本体的身份预检故障注入验证", created_by="U010001"))
                await session.commit()
                report("identity_fixture_published", workspace=OID, eventType=EVENT_TYPE, rule=RULE, release=version)
            injected = ExternalMirrorRow(object_code="LiveProject", source_key=json.dumps("LOCAL-IDENTITY-CORRUPTION-1031"),
                payload=dict(row.payload), binding_signature=row.binding_signature, last_seen_scan=row.last_seen_scan, is_current=True)
            session.add(injected)
            await session.flush()
            inserted_id = injected.id
            await session.commit()
            report("controlled_mirror_collision_inserted", objectType="LiveProject", physicalRowsBefore=9,
                insertedMirrorRowId=inserted_id, sourceWrites=0)

        async with sessions() as session:
            actor = await resolve_user(session, "U010001")
            await ensure_governed_tables(session)
            governed = next(g for g in governed_tables(OID, session).values() if g.object_type == "LiveProject")
            candidates = list(await session.execute(identity_rows(governed, unique_only=False)))
            assert len([candidate for candidate in candidates if candidate.identity == identity]) == 2
            stamp = datetime.now(UTC).strftime("%H%M%S%f")
            event_code = "LIVE-IDENTITY-1031-" + stamp
            received = await receive(session, IngressIn(event_id=event_code, event_type=EVENT_TYPE,
                occurred_at=datetime.now(UTC), source="controlled-local-mirror-corruption", object_type="LiveProject",
                object_id=identity, payload={"testFlag": True}))
            await session.commit()
            report("identity_event_service_ingress", eventId=received["eventId"], realActor=actor.id,
                duplicateIdentityCandidates=2, httpRequest=False)
        await deliver_event(OID, event_code, SUB)
        await deliver_event(OID, event_code, SUB)
        async with sessions() as session:
            event = await session.scalar(select(Event).where(Event.code == event_code))
            delivery = await session.scalar(select(EventDelivery).where(EventDelivery.event_id == event.id))
            assert set(await session.scalars(select(WritebackCall.id))) == calls_before
            report("identity_collision_terminal", eventId=event.code, deliveryId=delivery.id, state=delivery.state,
                error=delivery.error, attempts=delivery.attempts, nextAtSet=delivery.next_at is not None, newWritebackCalls=0)
            assert delivery.state == "failed" and delivery.error == "EVENT_OBJECT_AMBIGUOUS" and delivery.next_at is None and delivery.attempts == 0
    finally:
        if inserted_id is not None:
            async with sessions() as session:
                result = await session.execute(delete(ExternalMirrorRow).where(ExternalMirrorRow.id == inserted_id,
                    ExternalMirrorRow.object_code == "LiveProject", ExternalMirrorRow.source_key == json.dumps("LOCAL-IDENTITY-CORRUPTION-1031")))
                await session.commit()
                restored_ids = set(await session.scalars(select(ExternalMirrorRow.id).where(ExternalMirrorRow.object_code == "LiveProject")))
                report("controlled_mirror_cleanup", deletedExactly=result.rowcount, physicalRowsAfter=len(restored_ids),
                    originalPrimaryKeySetRestored=restored_ids == original_ids, sourceWrites=0)
                assert result.rowcount == 1 and restored_ids == original_ids
        await dispose_engine()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as exc:
        cause = getattr(getattr(exc, "orig", None), "__cause__", None)
        report("boundary_verification_failed", errorType=type(exc).__name__, code=getattr(exc, "code", None),
            constraint=getattr(cause, "constraint_name", None), column=getattr(cause, "column_name", None))
        raise SystemExit(1) from None
