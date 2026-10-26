"""独立测试库真实 PG 内省 → 预览 → 静态 CR → 发布/回滚，源表不写入业务记录。"""

import uuid

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import func, select, text

from app.modules.admin.models import AuditLog
from app.modules.data.models import Datasource, DsType
from app.modules.data.schemas import DatasourceCreate
from app.modules.data.service import DatasourceService
from app.modules.static.models import ChangeRequest, CrItem, CRStatus, LinkType, ObjectType
from app.modules.versioning.models import OntologyRelease
from app.seeds.loader import run_seeds
from tests.conftest import TEST_URL
from tests.function_access_support import grant_fixture_roles

P = "/api/data/v1/datasources"
S = "/api/ontology/v1"
ADMIN = {"X-Demo-As": "U010001"}


@pytest.fixture
async def source(engine):
    schema = "import_" + uuid.uuid4().hex[:12]
    async with engine.begin() as conn:
        await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
        await conn.execute(
            text(f'CREATE TABLE "{schema}".customers (id bigint PRIMARY KEY, name text NOT NULL)')
        )
        await conn.execute(
            text(
                f'CREATE TABLE "{schema}".orders (id bigint PRIMARY KEY, customer_id bigint '
                f'REFERENCES "{schema}".customers(id), amount numeric(12,2))'
            )
        )
        await conn.execute(text(f'CREATE TABLE "{schema}".no_pk (name text)'))
    yield schema
    async with engine.begin() as conn:
        await conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))


@pytest.fixture
async def imp(session, source, monkeypatch):
    from app.modules.static.sections import BUILDERS
    from app.modules.versioning.sections import isolated_sections

    monkeypatch.setenv("RENGU_DS_REAL_CONNECT", "1")
    monkeypatch.setenv("RENGU_DS_SECRET_KEY", Fernet.generate_key().decode())
    with isolated_sections(*BUILDERS):
        await run_seeds(session, ["admin", "versioning", "static"])
        await grant_fixture_roles(session)
        ds = await DatasourceService(session).create(
            DatasourceCreate(
                name="真实导入测试库",
                type=DsType.postgresql,
                host=TEST_URL.host,
                port=TEST_URL.port,
                database=TEST_URL.database,
                user=TEST_URL.username,
                password=TEST_URL.password,
                ssl=False,
            ),
            actor="U010001",
        )
        await session.commit()
        yield session, ds.code, source


async def preview(client, imp, tables=None):
    _, code, schema = imp
    r = await client.post(
        f"{P}/{code}/import-preview",
        json={"schema": schema, "tables": tables or ["customers", "orders"]},
        headers=ADMIN,
    )
    assert r.status_code == 200, r.text
    return r.json()


def body(p, **changes):
    return {
        "schema": p["schema"],
        "tables": [t["name"] for t in p["tables"]],
        "fingerprint": p["fingerprint"],
        "title": "导入真实源表",
        "reason": "真实数据库结构生成",
        "objectTypes": [
            {
                "table": o["table"],
                "id": "new_" + o["id"],
                "api": "New" + o["api"],
                "name": o["name"],
                "owner": "U010004",
            }
            for o in p["proposal"]["objectTypes"]
        ],
        **changes,
    }


async def apply(client, imp, data):
    return await client.post(f"{P}/{imp[1]}/import-to-cr", json=data, headers=ADMIN)


async def counts(session):
    return tuple(
        [
            await session.scalar(select(func.count()).select_from(model))
            for model in (ChangeRequest, CrItem, AuditLog, ObjectType)
        ]
    )


async def publish(client, session, cr_id):
    for cr in await session.scalars(select(ChangeRequest).where(ChangeRequest.status == CRStatus.approved)):
        cr.status = CRStatus.draft
    cr = await session.scalar(select(ChangeRequest).where(ChangeRequest.code == cr_id))
    cr.status = CRStatus.approved
    await session.commit()
    r = await client.post(S + "/releases", json={}, headers=ADMIN)
    assert r.status_code == 201, r.text
    return r.json()


async def test_real_preview_has_columns_pk_fk_and_no_mutation(client, imp):
    before = await counts(imp[0])
    p = await preview(client, imp)
    assert p["generator"] == "规则" and p["datasourceName"] == "真实导入测试库"
    orders = next(t for t in p["tables"] if t["name"] == "orders")
    assert orders["pk"] == ["id"] and orders["fks"][0]["refTable"] == "customers"
    assert next(c for c in orders["columns"] if c["name"] == "amount")["type"] == "NUMERIC(12, 2)"
    assert p["proposal"]["linkTypes"][0]["from"] == "orders"
    assert await counts(imp[0]) == before


async def test_metadata_diff_reads_source_without_mutating_definition(client, imp, engine):
    session, _, schema = imp
    p = await preview(client, imp)
    applied = await apply(client, imp, body(p, includeLinks=False))
    assert applied.status_code == 201, applied.text
    await publish(client, session, applied.json()["crId"])
    before = await counts(session)
    async with engine.begin() as conn:
        await conn.execute(text(f'ALTER TABLE "{schema}".orders ADD COLUMN added_column text'))
        await conn.execute(text(f'ALTER TABLE "{schema}".orders DROP COLUMN amount'))
    endpoint = "/api/data/v1/object-types/new_orders/metadata-diff"
    r = await client.post(endpoint, headers=ADMIN)
    assert r.status_code == 200, r.text
    assert [c["name"] for c in r.json()["added"]] == ["added_column"]
    assert r.json()["deleted"] == ["amount"]
    assert await counts(session) == before
    obj = await session.scalar(select(ObjectType).where(ObjectType.code == "new_orders"))
    assert "amount" in {p.api_name for p in obj.properties}
    for query in ("?ver=v0.1.0", "?previewAs=U010001"):
        denied = await client.post(endpoint + query, headers=ADMIN)
        assert denied.status_code == 403 and denied.json()["code"] == "READONLY"
    async with engine.begin() as conn:
        await conn.execute(text(f'DROP TABLE "{schema}".orders'))
    failed = await client.post(endpoint, headers=ADMIN)
    assert failed.status_code == 404 and failed.json()["code"] == "TABLES_NOT_FOUND"
    assert await counts(session) == before


async def test_import_publish_true_definitions_mapping_and_delete_guard(client, imp):
    session, ds_id, schema = imp
    p = await preview(client, imp)
    links = [p["proposal"]["linkTypes"][0]["id"]]
    r = await apply(client, imp, body(p, linkIds=links))
    assert r.status_code == 201, r.text
    assert len(r.json()["targets"]) == 3
    cr_id = r.json()["crId"]
    assert await session.scalar(select(ObjectType.id).where(ObjectType.code == "new_orders")) is None
    assert (await client.get(f"{P}/{ds_id}/mappings", headers=ADMIN)).json() == []
    denied = await client.delete(f"{P}/{ds_id}", headers=ADMIN)
    assert denied.status_code == 409 and denied.json()["code"] == "DS_IN_USE"
    release = await publish(client, session, cr_id)
    obj = await session.scalar(select(ObjectType).where(ObjectType.code == "new_orders"))
    assert (
        obj.datasource_name == ds_id
        and obj.source_table == schema + ".orders"
        and obj.api_name == "NewOrders"
    )
    assert obj.owner == "刘婷"
    props = {p.api_name: p for p in obj.properties}
    assert props["id"].is_pk and props["amount"].data_type == "decimal"
    assert props["customer_id"].source_column == "customer_id"
    link = await session.scalar(select(LinkType).where(LinkType.code == links[0]))
    assert link.from_code == "new_orders" and link.to_code == "new_customers"
    actual = (await client.get(f"{P}/{ds_id}/mappings", headers=ADMIN)).json()
    assert {m["objectId"] for m in actual} == {"new_orders", "new_customers"}
    assert all(m["schema"] == schema and m["datasourceName"] == "真实导入测试库" for m in actual)
    out = (await client.get(f"{P}/{ds_id}", headers=ADMIN)).json()
    assert sorted(out["objectTypes"]) == ["Customers", "Orders"]
    assert (await client.delete(f"{P}/{ds_id}", headers=ADMIN)).status_code == 409
    from app.modules.versioning.service import VersioningService

    snap = await VersioningService().get_snapshot(session, release["ver"])
    assert snap["objectTypes"]["new_orders"]["source"] == ds_id


@pytest.mark.parametrize(
    "case",
    ["fingerprint", "id_collision", "api_collision", "owner", "unknown_link", "duplicate", "missing_pk"],
)
async def test_import_invalid_is_atomic(client, imp, case):
    p = await preview(client, imp, ["no_pk"] if case == "missing_pk" else None)
    data = body(p)
    if case == "fingerprint":
        data["fingerprint"] = "0" * 64
    elif case == "id_collision":
        data["objectTypes"][0]["id"] = "store"
    elif case == "api_collision":
        obj = await imp[0].scalar(select(ObjectType).where(ObjectType.code == "store"))
        data["objectTypes"][0]["api"] = obj.api_name
    elif case == "owner":
        data["objectTypes"][0]["owner"] = "missing"
    elif case == "unknown_link":
        data["linkIds"] = ["invented"]
    elif case == "duplicate":
        data["objectTypes"][1]["id"] = data["objectTypes"][0]["id"]
    before = await counts(imp[0])
    r = await apply(client, imp, data)
    assert r.status_code == 409, r.text
    assert await counts(imp[0]) == before


async def test_schema_change_recheck_and_existing_cr_version(client, imp, engine):
    session, _ds_id, schema = imp
    p = await preview(client, imp)
    async with engine.begin() as conn:
        await conn.execute(text(f'ALTER TABLE "{schema}".orders ADD COLUMN extra text'))
    assert (await apply(client, imp, body(p))).json()["code"] == "SCHEMA_CHANGED"
    p = await preview(client, imp)
    created = await client.post(
        S + "/change-requests",
        json={"title": "已有单", "target": {"kind": "object", "id": "unrelated_new", "isNew": True}},
        headers=ADMIN,
    )
    assert created.status_code == 201, created.text
    cr_id = created.json()["id"]
    cr = await session.scalar(select(ChangeRequest).where(ChangeRequest.code == cr_id))
    before = await counts(session)
    failed = await apply(client, imp, body(p, crId=cr_id, lockVersion=cr.lock_version + 20))
    assert failed.status_code == 409 and failed.json()["code"] == "VERSION_CONFLICT"
    assert await counts(session) == before
    cr = await session.scalar(select(ChangeRequest).where(ChangeRequest.code == cr_id))
    done = await apply(client, imp, body(p, crId=cr_id, lockVersion=cr.lock_version, linkIds=[]))
    assert done.status_code == 201, done.text
    assert done.json()["crId"] == cr_id and len(done.json()["targets"]) == 3


async def test_mapping_changes_only_after_publish(client, imp):
    session, ds_id, schema = imp
    p = await preview(client, imp, ["customers"])
    new = await apply(client, imp, body(p))
    await publish(client, session, new.json()["crId"])
    ds = await session.scalar(select(Datasource).where(Datasource.code == ds_id))
    second = await DatasourceService(session).create(
        DatasourceCreate(
            name="第二真实连接",
            type=DsType.postgresql,
            host=ds.host,
            port=ds.port,
            database=ds.database,
            user=ds.username,
            password=TEST_URL.password,
            ssl=False,
        ),
        actor="U010001",
    )
    await session.commit()
    r = await client.put(
        f"{P}/{second.code}/tables/{schema}/customers/mapping",
        json={"objectId": "new_customers", "title": "改映射"},
        headers=ADMIN,
    )
    assert r.status_code == 201, r.text
    obj = await session.scalar(select(ObjectType).where(ObjectType.code == "new_customers"))
    assert obj.datasource_name == ds_id
    before = await counts(session)
    assert (await client.delete(f"{P}/{second.code}", headers=ADMIN)).status_code == 409
    assert await counts(session) == before
    await publish(client, session, r.json()["crId"])
    await session.refresh(obj)
    assert obj.datasource_name == second.code
    assert (await client.get(f"{P}/{ds_id}/mappings", headers=ADMIN)).json() == []
    assert (await client.delete(f"{P}/{ds_id}", headers=ADMIN)).status_code == 204


@pytest.mark.parametrize("mapped_source,valid", [("DS-999999", False), ("DS-legacy-label", True)])
async def test_generic_cr_cannot_publish_missing_connection_but_legacy_labels_work(
    client, imp, mapped_source, valid
):
    session, _, _ = imp
    p = await preview(client, imp, ["customers"])
    imported = await apply(client, imp, body(p))
    assert imported.status_code == 201
    cr_id = imported.json()["crId"]
    cr = await session.scalar(select(ChangeRequest).where(ChangeRequest.code == cr_id))
    item = await session.scalar(select(CrItem).where(CrItem.cr_id == cr.id, CrItem.item_key == "source"))
    item.after = mapped_source
    for other in await session.scalars(
        select(ChangeRequest).where(ChangeRequest.status == CRStatus.approved)
    ):
        other.status = CRStatus.draft
    cr.status = CRStatus.approved
    await session.commit()
    releases = await session.scalar(select(func.count()).select_from(OntologyRelease))
    r = await client.post(S + "/releases", json={}, headers=ADMIN)
    if valid:
        assert r.status_code == 201, r.text
        obj = await session.scalar(select(ObjectType).where(ObjectType.code == "new_customers"))
        assert obj.datasource_name == mapped_source
    else:
        assert r.status_code == 409 and r.json()["code"] == "DS_NOT_FOUND", r.text
        assert await session.scalar(select(ObjectType.id).where(ObjectType.code == "new_customers")) is None
        assert await session.scalar(select(func.count()).select_from(OntologyRelease)) == releases


async def test_late_conflict_rolls_back_new_cr_and_partial_targets(client, imp):
    session = imp[0]
    p = await preview(client, imp)
    held = await client.post(
        S + "/change-requests",
        json={"title": "占用第二目标", "target": {"kind": "object", "id": "new_orders", "isNew": True}},
        headers=ADMIN,
    )
    assert held.status_code == 201
    before = await counts(session)
    failed = await apply(client, imp, body(p))
    assert failed.status_code == 409 and failed.json()["code"] == "TARGET_IN_OTHER_CR", failed.text
    assert await counts(session) == before


async def test_invalid_owner_on_generic_cr_publish_rolls_back(client, imp):
    session = imp[0]
    p = await preview(client, imp, ["customers"])
    imported = await apply(client, imp, body(p))
    cr_id = imported.json()["crId"]
    cr = await session.scalar(select(ChangeRequest).where(ChangeRequest.code == cr_id))
    owner = await session.scalar(select(CrItem).where(CrItem.cr_id == cr.id, CrItem.item_key == "owner"))
    assert owner.after == "U010004"
    owner.after = "不存在的负责人"
    for other in await session.scalars(
        select(ChangeRequest).where(ChangeRequest.status == CRStatus.approved)
    ):
        other.status = CRStatus.draft
    cr.status = CRStatus.approved
    await session.commit()
    before = await counts(session)
    r = await client.post(S + "/releases", json={}, headers=ADMIN)
    assert r.status_code == 409 and r.json()["code"] == "WRITEBACK_FAILED", r.text
    assert await counts(session) == before


async def test_connection_name_change_preserves_stable_mapping_and_blocks_legacy_name(client, imp):
    session, code, _ = imp
    p = await preview(client, imp, ["customers"])
    created = await apply(client, imp, body(p))
    await publish(client, session, created.json()["crId"])
    ds = await session.scalar(select(Datasource).where(Datasource.code == code))
    obj = await session.scalar(select(ObjectType).where(ObjectType.code == "new_customers"))
    obj.datasource_name = ds.name
    await session.commit()
    data = {
        "name": "真实新库名",
        "type": ds.type.value,
        "host": ds.host,
        "port": ds.port,
        "database": ds.database,
        "user": ds.username,
        "password": None,
        "ssl": False,
    }
    failed = await client.put(f"{P}/{code}", json=data, headers=ADMIN)
    assert failed.status_code == 409 and failed.json()["code"] == "DS_IN_USE"
    obj = await session.scalar(select(ObjectType).where(ObjectType.code == "new_customers"))
    obj.datasource_name = code
    await session.commit()
    saved = await client.put(f"{P}/{code}", json=data, headers=ADMIN)
    assert saved.status_code == 200, saved.text
    actual = (await client.get(f"{P}/{code}/mappings", headers=ADMIN)).json()
    assert actual[0]["datasourceName"] == "真实新库名" and actual[0]["objectId"] == "new_customers"


@pytest.mark.parametrize(
    "case", ["readonly", "no_object_access", "no_link_access", "nul", "sql", "unknown_table"]
)
async def test_boundary_and_permissions(client, imp, case):
    p = await preview(client, imp)
    data = body(p)
    headers = dict(ADMIN)
    params = {}
    if case == "readonly":
        params = {"ver": "v2026.10.1"}
    elif case in ("no_object_access", "no_link_access"):
        from app.modules.admin.models import SysRole

        role = await imp[0].scalar(select(SysRole).where(SysRole.code == "R-01"))
        role.func = {**role.func, ("object-types" if case == "no_object_access" else "link-types"): "view"}
        if case == "no_link_access":
            data["linkIds"] = [p["proposal"]["linkTypes"][0]["id"]]
        await imp[0].commit()
    elif case == "nul":
        data["objectTypes"][0]["name"] = "坏\x00名称"
    elif case == "sql":
        data["sql"] = "select * from private"
    elif case == "unknown_table":
        data["tables"] = ["not_a_table"]
    before = await counts(imp[0])
    r = await client.post(f"{P}/{imp[1]}/import-to-cr", json=data, headers=headers, params=params)
    assert r.status_code == (
        403
        if case in ("readonly", "no_object_access", "no_link_access")
        else 404
        if case == "unknown_table"
        else 422
    ), r.text
    assert await counts(imp[0]) == before
