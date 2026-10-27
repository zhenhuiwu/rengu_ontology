from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.ontology import set_ontology_scope
from app.core import clock
from app.db.dialects import get_adapter
from app.modules.admin.models import AuditLog, AuditResult
from app.modules.integration.models import ExtSystem, ExtSystemStatus
from app.modules.ontology.models import OntologyWorkspace


async def test_shared_audit_and_domain_upsert_use_real_constraint_boundaries(session, conn):
    adapter = get_adapter(session)
    audit = {
        "code": "OPS-UPSERT-GLOBAL",
        "time": clock.now(),
        "created_at": clock.now(),
        "actor": "ops test",
        "action": "read",
        "target": "test only",
        "ip": "127.0.0.1",
        "result": AuditResult.success,
    }
    assert await adapter.upsert(session, AuditLog, [audit], ["code"]) == 1
    assert await adapter.upsert(session, AuditLog, [audit], ["code"]) == 1
    log = await session.scalar(select(AuditLog).where(AuditLog.code == audit["code"]))
    assert log.ontology_id is None
    workspace = OntologyWorkspace(code="ops-upsert-second", name="ops upsert second")
    session.add(workspace)
    await session.flush()
    workspace_id = workspace.id
    system = {
        "code": "OPS-UPSERT",
        "name": "ops upsert",
        "timeout_ms": 1000,
        "status": ExtSystemStatus.ok,
        "env": "test",
    }
    assert await adapter.upsert(session, ExtSystem, [system], ["code"]) == 1
    first_id = await session.scalar(select(ExtSystem.id).where(ExtSystem.code == system["code"]))
    async with AsyncSession(bind=conn, join_transaction_mode="create_savepoint") as other:
        await set_ontology_scope(other, workspace_id)
        assert await adapter.upsert(other, AuditLog, [audit], ["code"]) == 1
        assert await adapter.upsert(other, ExtSystem, [system], ["code"]) == 1
        second_id = await other.scalar(select(ExtSystem.id).where(ExtSystem.code == system["code"]))
        assert second_id != first_id
        await other.commit()
    await set_ontology_scope(session, 1)
    assert await session.scalar(select(ExtSystem.id).where(ExtSystem.code == system["code"])) == first_id
