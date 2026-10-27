"""Copy only authorized retail source rows and prepare connection/identity prerequisites, never definitions."""
import asyncio
import hashlib
import json
import os
import sys
from datetime import date
from pathlib import Path

import asyncpg
from cryptography.fernet import Fernet
from dotenv import dotenv_values
from sqlalchemy.engine import make_url

BACKEND = Path(__file__).resolve().parents[1]
DATABASE = "rengu_test_use_qa_20261027"
WORKSPACE = "wf-retail-20261027"
DS = "WF-RETAIL-SOURCE"
TEMP = Path(os.environ["TEMP"]) / "rengu-ontology"


def local_settings(target):
    key = TEMP / "retail-workflow-ds.key"
    if not key.exists():
        key.write_bytes(Fernet.generate_key())
    os.environ["RENGU_DS_SECRET_KEY"] = key.read_text().strip()
    os.environ["WF_RETAIL_READONLY_URL"] = target.render_as_string(hide_password=False)
    os.environ["RENGU_OBJECT_READ_ENABLED"] = "1"
    os.environ["RENGU_OBJECT_READ_BINDINGS"] = str(TEMP / "retail-workflow-bindings.json")


async def prepare():
    raw = make_url(dotenv_values(BACKEND / ".env").get("RENGU_DATABASE_URL") or "")
    assert raw.host in ("localhost", "127.0.0.1") and raw.port == 5433 and raw.database == "rengu_ontology"
    target = raw.set(database=DATABASE)
    local_settings(target)
    source = await asyncpg.connect(raw.set(drivername="postgresql").render_as_string(hide_password=False))
    dest = await asyncpg.connect(target.set(drivername="postgresql").render_as_string(hide_password=False))
    try:
        scope = await dest.fetchval("SELECT id FROM ontology_workspace WHERE code=$1", WORKSPACE)
        assert scope and scope != 1
        original = {t: await dest.fetchval(f'SELECT count(*) FROM "{t}" WHERE ontology_id=1') for t in ("store", "biz_order")}
        manifest_path = TEMP / "retail-workflow-source.json"
        if not manifest_path.exists():
            async with source.transaction(isolation="repeatable_read", readonly=True), dest.transaction():
                stores = [dict(r) for r in await source.fetch("SELECT * FROM store WHERE ontology_id=1 ORDER BY id")]
                orders = [dict(r) for r in await source.fetch("SELECT * FROM biz_order WHERE ontology_id=1 ORDER BY id")]
                mapping = {}
                for table, rows in (("store", stores), ("biz_order", orders)):
                    assert await dest.fetchval(f'SELECT count(*) FROM "{table}" WHERE ontology_id=$1', scope) == 0
                    ids = await dest.fetch("SELECT nextval(pg_get_serial_sequence($1,'id')) AS id FROM generate_series(1,$2)", table, len(rows))
                    columns = list(rows[0])
                    copied = []
                    for row, number in zip(rows, ids, strict=True):
                        before = row["id"]
                        row = dict(row, id=number["id"], ontology_id=scope)
                        if table == "store":
                            mapping[before] = row["id"]
                        else:
                            row["store_id"] = mapping[row["store_id"]]
                            row["member_phone"], row["waybill"] = None, None
                        copied.append(tuple(row[c] for c in columns))
                    await dest.copy_records_to_table(table, records=copied, columns=columns)
                for table, rows in (("store", stores), ("biz_order", orders)):
                    assert await dest.fetchval(f'SELECT count(*) FROM "{table}" WHERE ontology_id=$1', scope) == len(rows)
                fields = {"store": ["code", "store_no", "name"], "biz_order": ["code", "created_at_biz", "amount", "status", "channel", "discount"]}
                checks = {}
                for table, rows in (("store", stores), ("biz_order", orders)):
                    def digest(values):
                        payload = sorted(json.dumps({k: r[k] for k in fields[table]}, default=str, sort_keys=True) for r in values)
                        return hashlib.sha256(json.dumps(payload).encode()).hexdigest()
                    actual = await dest.fetch(f'SELECT * FROM "{table}" WHERE ontology_id=$1', scope)
                    assert digest(rows) == digest(actual)
                    checks[table] = {"count": len(rows), "business_sha256": digest(rows)}
                assert await dest.fetchval("SELECT count(*) FROM biz_order o JOIN store s ON s.id=o.store_id WHERE o.ontology_id=$1 AND s.ontology_id=$1", scope) == len(orders)
                manifest = {"source": "rengu_ontology/ontology1", "target": DATABASE, "ontologyId": scope, "tables": checks,
                            "foreignKeys": "store.id remapped; biz_order.store_id points to scope2 copies", "personalFields": "member_phone/waybill cleared only in copies", "oldScope1": original}
            manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        assert original == {t: await dest.fetchval(f'SELECT count(*) FROM "{t}" WHERE ontology_id=1') for t in original}
    finally:
        await source.close()
        await dest.close()

    os.environ["RENGU_DATABASE_URL"] = target.render_as_string(hide_password=False)
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

    from app.common.ontology import set_ontology_scope
    from app.core import clock
    from app.modules.admin.models import SysRole, SysRoleMember, SysUser
    from app.modules.data.models import Datasource, DsStatus, DsSyncMode, DsType
    from app.modules.data.secrets import encrypt_password
    from app.modules.ontology.models import OntologyMember, OntologyRoleBinding

    engine = create_async_engine(target)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session, session.begin():
            await set_ontology_scope(session, scope)
            owner = await session.scalar(select(SysUser).where(SysUser.code == "U010001"))
            reviewer = await session.scalar(select(SysUser).where(SysUser.code == "U010002"))
            assert owner and reviewer
            role = await session.scalar(select(SysRole).where(SysRole.code == "WF-RETAIL-ROLE"))
            if role is None:
                role = SysRole(code="WF-RETAIL-ROLE", name="WF零售验收专属角色", changed_at=clock.now(), changed_by=owner.code,
                    func={key: "manage" for key in ("object-types", "link-types", "metrics", "releases", "connections", "event-types", "action-types", "rules", "object-search", "object-detail", "qa-workbench", "qa-pages", "alerts", "action-center")},
                    objs=[{"api": name, "objType": label, "view": True, "export": True, "actions": ["wf_record_review"]} for name, label in (("store", "门店"), ("order", "订单"))])
                session.add(role)
                await session.flush()
            for user in (owner, reviewer):
                if not await session.scalar(select(SysRoleMember.id).where(SysRoleMember.role_id == role.id, SysRoleMember.user_id == user.id)):
                    session.add(SysRoleMember(role_id=role.id, user_id=user.id, seq=0))
                if not await session.scalar(select(OntologyMember.id).where(OntologyMember.ontology_id == scope, OntologyMember.user_id == user.id)):
                    session.add(OntologyMember(ontology_id=scope, user_id=user.id, role="editor"))
            if not await session.scalar(select(OntologyRoleBinding.id).where(OntologyRoleBinding.ontology_id == scope, OntologyRoleBinding.role_id == role.id)):
                session.add(OntologyRoleBinding(ontology_id=scope, role_id=role.id))
            if not await session.scalar(select(Datasource.id).where(Datasource.code == DS)):
                session.add(Datasource(code=DS, name="WF真实零售只读副本", type=DsType.postgresql, host=target.host, port=5433, database=DATABASE,
                    username=target.username, password_enc=encrypt_password(target.password), ssl=False, status=DsStatus.untested, owner=owner.name,
                    sync_mode=DsSyncMode.manual, table_count=2, created_on=date.today(), note="仅本地隔离副本；尚未通过UI测试连接，不代表已建本体定义"))
    finally:
        await engine.dispose()
    fields = {"store": ["id", "code", "store_no", "name", "city", "region", "big_region", "format", "opened_at", "status", "is_test"],
              "order": ["id", "code", "store_id", "created_at_biz", "shipped_at", "amount", "discount", "status", "channel", "warehouse", "is_test"]}
    bindings = [{"ontologyId": scope, "objectType": name, "datasourceId": DS, "schema": "public", "table": "biz_order" if name == "order" else "store",
                 "columns": columns, "urlEnv": "WF_RETAIL_READONLY_URL"} for name, columns in fields.items()]
    (TEMP / "retail-workflow-bindings.json").write_text(json.dumps(bindings), encoding="utf-8")
    print(json.dumps({"scope": scope, "datasource": DS, "reviewer": "U010002", "source": json.loads(manifest_path.read_text(encoding='utf-8'))}, ensure_ascii=False))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.path.insert(0, str(BACKEND))
    os.chdir(BACKEND)
    asyncio.run(prepare())
