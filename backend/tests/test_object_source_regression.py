"""Real PostgreSQL object-reader boundaries; all writes are confined to the named test database."""

import csv
import io
import json
import secrets
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import asyncpg
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, event, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.ontology import set_ontology_scope
from app.core.errors import AppError
from app.modules.admin.models import SysRole, SysRoleScope, SysUser
from app.modules.data.models import Datasource, DsStatus, DsSyncMode, DsType
from app.modules.ontology.models import OntologyMember, OntologyRoleBinding, OntologyWorkspace
from app.modules.qa.object_source import resolve_source
from app.modules.static.models import (
    Cardinality,
    ConflictStrategy,
    DefStatus,
    Lifecycle,
    LinkType,
    ObjectStatus,
    ObjectType,
    Property,
)
from tests.helpers.workspace import TEST_DB
from tests.qa_access_support import seed_qa_access
from tests.use_objects_support import enable_fixture_export

B = "/api/qa/v1"
DB = TEST_DB


@pytest.fixture(scope="session")
async def real_source(engine):
    assert engine.url.database == DB
    token = uuid4().hex[:10]
    schema, reader, bypass = f"obj_source_{token}", f"obj_read_{token}", f"obj_bypass_{token}"
    password = secrets.token_urlsafe(24)
    connection = await asyncpg.connect(
        engine.url.set(drivername="postgresql").render_as_string(hide_password=False)
    )
    created = []
    try:
        for name, flag in ((reader, "NOBYPASSRLS"), (bypass, "BYPASSRLS")):
            ddl = await connection.fetchval(
                "SELECT format('CREATE ROLE %I LOGIN PASSWORD %L NOSUPERUSER "
                "NOCREATEDB NOCREATEROLE NOINHERIT ' || $3::text, $1::text, $2::text)",
                name,
                password,
                flag,
            )
            try:
                await connection.execute(ddl)
            except Exception as error:
                raise RuntimeError(f"Isolated role setup failed: {type(error).__name__}") from None
            created.append(name)
        await connection.execute(f'CREATE SCHEMA "{schema}"')
        await connection.execute(f'''CREATE TABLE "{schema}".assets (
            ontology_id bigint NOT NULL, asset_id text NOT NULL, name text NOT NULL,
            amount numeric(12,2) NOT NULL, segment text NOT NULL, secret text NOT NULL,
            hidden_text text NOT NULL, PRIMARY KEY (ontology_id,asset_id))''')
        await connection.executemany(
            f'INSERT INTO "{schema}".assets VALUES ($1,$2,$3,$4,$5,$6,$7)',
            [
                (
                    1,
                    f"A{i:02}",
                    "=1+1" if i == 0 else f"设备{i:02}",
                    Decimal(i) + Decimal("1.25"),
                    "even" if i % 2 == 0 else "odd",
                    "secret-value",
                    "hidden-value",
                )
                for i in range(25)
            ]
            + [(2, "OTHER", "other-workspace", Decimal(9999), "even", "other-secret", "other-hidden")],
        )
        await connection.execute(f'ALTER TABLE "{schema}".assets ENABLE ROW LEVEL SECURITY')
        await connection.execute(f'ALTER TABLE "{schema}".assets FORCE ROW LEVEL SECURITY')
        await connection.execute(f'''CREATE POLICY scope ON "{schema}".assets
            USING (ontology_id = NULLIF(current_setting('rengu.ontology_id',true),'')::bigint)''')
        for name in created:
            await connection.execute(f'GRANT USAGE ON SCHEMA "{schema}" TO "{name}"')
            await connection.execute(f'GRANT SELECT ON "{schema}".assets TO "{name}"')
        yield SimpleNamespace(
            schema=schema,
            reader=reader,
            url=engine.url.set(username=reader, password=password),
            bypass_url=engine.url.set(username=bypass, password=password),
            admin_url=engine.url,
        )
    finally:
        await connection.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        for name in created:
            await connection.execute(f'DROP OWNED BY "{name}"')
            await connection.execute(f'DROP ROLE "{name}"')
        await connection.close()


def properties():
    return [
        Property(
            api_name=api,
            display_name=name,
            data_type=typ,
            prop_group="测试",
            source_column=column,
            description="",
            is_pk=api == "asset_id",
            is_title=api == "name",
            is_sensitive=api == "secret",
            visibility="hidden" if api == "hidden_text" else "normal",
            searchable=api in ("asset_id", "name"),
            filterable=api in ("segment", "amount"),
            sortable=api == "amount",
            list_visible=api == "amount",
        )
        for api, name, typ, column in (
            ("asset_id", "设备编号", "string", "asset_id"),
            ("name", "设备名称", "string", "name"),
            ("amount", "金额", "decimal", "amount"),
            ("segment", "分组", "enum", "segment"),
            ("secret", "秘密", "string", "secret"),
            ("hidden_text", "隐藏", "string", "hidden_text"),
        )
    ]


@pytest.fixture
async def source_case(users, client, real_source, tmp_path, monkeypatch):
    await seed_qa_access(users)
    ds = Datasource(
        code="DS-OBJ-TEST",
        name="对象测试源",
        type=DsType.postgresql,
        host=real_source.url.host,
        port=real_source.url.port,
        database=DB,
        username=real_source.reader,
        ssl=False,
        status=DsStatus.ok,
        owner="测试",
        sync_mode=DsSyncMode.manual,
        created_on=date(2026, 10, 27),
    )
    users.add(ds)
    objects = []
    for code, api in (("test_asset", "TestAsset"), ("test_target", "TestTarget")):
        obj = ObjectType(
            code=code,
            api_name=api,
            display_name=code,
            obj_group="测试",
            conflict_strategy=ConflictStrategy.user_edit_wins,
            lifecycle=Lifecycle.active,
            status=ObjectStatus.published,
            datasource_name=ds.code,
            source_table=f"{real_source.schema}.assets",
            pk_property="asset_id",
            properties=properties(),
        )
        users.add(obj)
        objects.append(obj)
    await users.flush()
    users.add(
        LinkType(
            code="asset_group",
            api_name="AssetGroup",
            display_name="同组设备",
            from_code="test_asset",
            to_code="test_target",
            from_object_id=objects[0].id,
            to_object_id=objects[1].id,
            cardinality=Cardinality("1 : N"),
            key_expr="segment=segment",
            from_prop="segment",
            to_prop="segment",
            join_sql="",
            reverse_name="分组来源",
            nullable=True,
            description="",
            status=DefStatus.published,
            owner="测试",
            since_release="v2026.10.3",
        )
    )
    role = await users.scalar(select(SysRole).where(SysRole.code == "R-01"))
    role.objs = [
        *role.objs,
        {"objType": "test_asset", "api": "TestAsset", "view": True, "export": False, "actions": []},
        {"objType": "test_target", "api": "TestTarget", "view": False, "export": False, "actions": []},
    ]
    await users.flush()
    manifest = [
        {
            "ontologyId": 1,
            "objectType": obj.code,
            "datasourceId": ds.code,
            "schema": real_source.schema,
            "table": "assets",
            "columns": ["asset_id", "name", "amount", "segment", "secret", "hidden_text"],
            "urlEnv": "RENGU_OBJECT_READ_URL_TEST_ONLY",
        }
        for obj in objects
    ]
    file = tmp_path / "bindings.json"
    file.write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setenv("RENGU_OBJECT_READ_ENABLED", "1")
    monkeypatch.setenv("RENGU_OBJECT_READ_BINDINGS", str(file))
    monkeypatch.setenv(
        "RENGU_OBJECT_READ_URL_TEST_ONLY", real_source.url.render_as_string(hide_password=False)
    )
    return SimpleNamespace(
        session=users,
        client=client,
        obj=objects[0],
        target=objects[1],
        role=role,
        manifest=manifest,
        file=file,
        real=real_source,
    )


async def search(case, **body):
    response = await case.client.post(f"{B}/object-types/test_asset/search", json=body)
    assert response.status_code == 200, response.text
    return response.json()


async def test_real_binding_pagination_filters_facets_sort_and_aggregate(source_case):
    case = source_case
    first = await search(case, sort="amount")
    second = await search(case, sort="amount", page=2)
    assert first["total"] == 25 and first["size"] == 10 and len(second["items"]) == 10
    assert not {x["id"] for x in first["items"]} & {x["id"] for x in second["items"]}
    assert first["items"][0]["values"]["amount"] == 1.25
    assert "secret" not in first["boundProps"] and "hidden_text" not in first["items"][0]["values"]
    even = await search(case, filters={"segment": ["even"]}, sort="-amount")
    assert even["total"] == 13 and even["items"][0]["values"]["amount"] == 25.25
    assert sum(x["count"] for x in next(f for f in even["facets"] if f["key"] == "segment")["values"]) == 25
    assert (await search(case, filters={"amount": {"min": 20, "max": 30}}))["total"] == 6
    for question, expected in (
        ("对象数量", "25"),
        ("amount合计", "331.25"),
        ("amount平均值", "13.25"),
        ("amount最小值", "1.25"),
        ("amount最大值", "25.25"),
    ):
        result = await case.client.post(
            f"{B}/scoped-ask", json={"scope": {"kind": "test_asset", "query": {}}, "question": question}
        )
        assert result.status_code == 200 and expected in result.json()["text"], result.text
    selected = await case.client.post(
        f"{B}/scoped-ask",
        json={"scope": {"kind": "test_asset", "ids": ["A00", "A01", "A01"]}, "question": "对象数量"},
    )
    assert "2 个" in selected.json()["text"]


async def test_catalog_uses_bounded_sql_pages(source_case, conn):
    queries = []

    def record(_conn, _cursor, statement, _params, _context, _many):
        queries.append(" ".join(statement.lower().split()))

    event.listen(conn.sync_connection, "before_cursor_execute", record)
    try:
        first = await source_case.client.get(f"{B}/object-types")
        second = await source_case.client.get(f"{B}/object-types", params={"page": 2})
    finally:
        event.remove(conn.sync_connection, "before_cursor_execute", record)
    assert first.status_code == second.status_code == 200
    a, b = first.json(), second.json()
    assert a["size"] == b["size"] == 10 and a["total"] == b["total"] > 10
    assert len(a["items"]) == 10 and b["page"] == 2
    assert not {x["id"] for x in a["items"]} & {x["id"] for x in b["items"]}
    assert any("from object_type" in q and "count(" in q for q in queries)
    assert any("from object_type" in q and "limit " in q and "offset " in q for q in queries)


async def test_unregistered_source_permission_basis_denies_all_reads(source_case):
    case = source_case
    scope = SysRoleScope(
        code="OBJ-TEST-SCOPE",
        role_id=case.role.id,
        seq=0,
        obj_type="TestAsset",
        prop="segment",
        op="∈",
        values=["even"],
    )
    case.session.add(scope)
    await case.session.flush()
    result = await search(case)
    # filterable is not an approved permission basis. Never infer an authorization mapping.
    assert result["total"] == 0 and result["items"] == []
    assert next(f for f in result["facets"] if f["key"] == "segment")["values"] == []
    assert (await case.client.get(f"{B}/object-types/test_asset/objects/A00")).status_code == 404
    answer = await case.client.post(
        f"{B}/scoped-ask", json={"scope": {"kind": "test_asset"}, "question": "amount合计"}
    )
    assert answer.status_code == 200 and "共 0 个" in answer.json()["text"]
    assert "暂无数据" in answer.json()["text"]
    await enable_fixture_export(case.session)
    assert (await case.client.post(f"{B}/object-types/test_asset/export", json={})).status_code == 403
    scope.prop = "UnknownObject.secret"
    await case.session.flush()
    refused = await search(case)
    assert refused["total"] == 0 and refused["items"] == []


async def test_recommendations_are_executable_and_relations_apply_both_acls(source_case):
    case = source_case
    meta = (await case.client.get(f"{B}/object-types/test_asset/meta")).json()
    assert meta["scopeQuestions"] == ["对象数量", "金额合计", "金额平均值"]
    for question in meta["scopeQuestions"]:
        assert (
            await case.client.post(
                f"{B}/scoped-ask", json={"scope": {"kind": "test_asset"}, "question": question}
            )
        ).status_code == 200
    url = f"{B}/object-types/test_asset/objects/A00/related"
    denied = (await case.client.get(url)).json()[0]
    assert denied["total"] == 0 and denied["items"] == []
    case.role.objs = [{**p, "view": True} if p.get("api") == "TestTarget" else p for p in case.role.objs]
    for prop in case.obj.properties + case.target.properties:
        if prop.api_name == "segment":
            prop.filterable = False
    await case.session.flush()
    first = (await case.client.get(url)).json()[0]
    second = (await case.client.get(url, params={"link": "asset_group", "page": 2})).json()[0]
    assert first["total"] == 13 and len(first["items"]) == 10 and len(second["items"]) == 3
    assert not {r["id"] for r in first["items"]} & {r["id"] for r in second["items"]}


async def test_export_grant_formula_and_legacy_projection(source_case):
    case = source_case
    for path in ("object-types/test_asset", "objects/test_asset"):
        assert (await case.client.post(f"{B}/{path}/export", json={})).status_code == 403
    await enable_fixture_export(case.session)
    for path in ("object-types/test_asset", "objects/test_asset"):
        response = await case.client.post(f"{B}/{path}/export", json={"ids": ["A00"]})
        assert response.status_code == 200, response.text
        rows = list(csv.reader(io.StringIO(response.content.decode("utf-8-sig"))))
        assert rows[1][0] == "'=1+1" and "秘密" not in rows[0] and "隐藏" not in rows[0]
        assert (
            len(
                list(
                    csv.reader(
                        io.StringIO(
                            (await case.client.post(f"{B}/{path}/export", json={"ids": []})).content.decode(
                                "utf-8-sig"
                            )
                        )
                    )
                )
            )
            == 1
        )
    old = (await case.client.get(f"{B}/objects/test_asset/A00")).json()
    assert old["code"] == "A00" and "secret-value" not in json.dumps(old)
    assert (
        await case.client.post(f"{B}/objects/test_asset/search", json={"facets": {"retail_alias": ["x"]}})
    ).status_code == 422


@pytest.mark.parametrize("flag", ["hidden", "sensitive"])
async def test_hidden_sensitive_primary_blocks_new_and_old_routes(source_case, flag):
    case = source_case
    pk = next(p for p in case.obj.properties if p.api_name == "asset_id")
    if flag == "hidden":
        pk.visibility = "hidden"
    else:
        pk.is_sensitive = True
    await case.session.flush()
    for path in ("object-types/test_asset/search", "objects/test_asset/search"):
        response = await case.client.post(f"{B}/{path}", json={})
        assert response.status_code == 503 and response.json()["code"] == "OBJECT_IDENTIFIER_UNAVAILABLE"
    for suffix in ("", "/related", "/timeline", "/actions"):
        assert (await case.client.get(f"{B}/objects/test_asset/A00{suffix}")).status_code == 503


async def test_real_source_readonly_role_guc_and_bypass_rejection(source_case, monkeypatch):
    case = source_case
    source = await resolve_source(case.session, case.obj)
    async with source.read() as conn:
        row = (
            await conn.execute(
                text(
                    "SELECT current_setting('transaction_read_only'), current_setting('rengu.ontology_id'), "
                    "rolsuper, rolbypassrls FROM pg_roles WHERE rolname=current_user"
                )
            )
        ).one()
        assert row == ("on", "1", False, False)
        assert await conn.scalar(text(f'SELECT count(*) FROM "{case.real.schema}".assets')) == 25
    with pytest.raises(AppError):
        async with source.read() as conn:
            await conn.execute(
                text(
                    f'INSERT INTO "{case.real.schema}".assets '
                    "VALUES (1,'BLOCKED','blocked',1,'even','s','h')"
                )
            )
    async with source.read() as conn:
        assert await conn.scalar(text(f'SELECT count(*) FROM "{case.real.schema}".assets')) == 25
    monkeypatch.setenv(
        "RENGU_OBJECT_READ_URL_TEST_ONLY", case.real.bypass_url.render_as_string(hide_password=False)
    )
    response = await case.client.post(f"{B}/object-types/test_asset/search", json={})
    assert response.status_code == 503 and response.json()["code"] == "OBJECT_DATA_UNAVAILABLE"
    monkeypatch.setenv(
        "RENGU_OBJECT_READ_URL_TEST_ONLY", case.real.admin_url.render_as_string(hide_password=False)
    )
    assert (await case.client.post(f"{B}/object-types/test_asset/search", json={})).status_code == 503


async def test_source_binding_mismatch_is_refused_before_business_read(source_case, monkeypatch):
    case = source_case
    connected = []

    def forbidden(*args, **kwargs):
        connected.append(True)
        raise AssertionError("A mismatched source must not open a connection")

    monkeypatch.setattr("app.modules.qa.object_source.create_async_engine", forbidden)
    monkeypatch.setenv(
        "RENGU_OBJECT_READ_URL_TEST_ONLY",
        case.real.url.set(database="rengu_test_not_allowed").render_as_string(hide_password=False),
    )
    response = await case.client.post(f"{B}/object-types/test_asset/search", json={})
    assert response.status_code == 503 and "不一致" in response.json()["message"]
    assert "password" not in response.text and "postgresql" not in response.text
    assert connected == []
    monkeypatch.setenv(
        "RENGU_OBJECT_READ_URL_TEST_ONLY",
        case.real.url.update_query_dict({"options": "-c role=postgres"}).render_as_string(
            hide_password=False
        ),
    )
    assert (await case.client.post(f"{B}/object-types/test_asset/search", json={})).status_code == 503
    assert connected == []
    monkeypatch.setenv("RENGU_OBJECT_READ_URL_TEST_ONLY", case.real.url.render_as_string(hide_password=False))
    case.manifest[0]["table"] = "assets; DROP TABLE assets"
    case.file.write_text(json.dumps(case.manifest), encoding="utf-8")
    assert (await case.client.post(f"{B}/object-types/test_asset/search", json={})).status_code == 503
    assert connected == []
    case.manifest[0]["table"] = "assets"
    case.manifest[0]["columns"] = ["name"]
    case.file.write_text(json.dumps(case.manifest), encoding="utf-8")
    result = await search(case)
    assert result["bound"] is False and result["total"] == 0


async def test_history_uses_its_published_mapping_and_public_projection(source_case):
    from app.modules.static.sections import object_def
    from app.modules.static.shapes import prop as property_shape
    from app.modules.versioning.models import OntologyRelease

    case = source_case
    release = await case.session.scalar(select(OntologyRelease).where(OntologyRelease.ver == "v2026.10.2"))
    snapshot = dict(release.snapshot)
    snapshot["objectTypes"] = {**snapshot["objectTypes"], "test_asset": object_def(case.obj)}
    snapshot["properties"] = {
        **snapshot["properties"],
        "test_asset": {p.api_name: property_shape(p) for p in case.obj.properties},
    }
    history = OntologyRelease(
        ver="v2026.10.90",
        snapshot=snapshot,
        snapshot_sha256="0" * 64,
        published_by_id=release.published_by_id,
        published_at=release.published_at,
        change_order_ids=[],
        change_summary={},
        note="对象只读适配测试专属历史定义",
    )
    case.session.add(history)
    next(p for p in case.obj.properties if p.api_name == "asset_id").source_column = "unmapped_current_key"
    await case.session.flush()
    assert (await search(case))["bound"] is False
    for path in ("object-types/test_asset/objects/A00", "objects/test_asset/A00"):
        response = await case.client.get(f"{B}/{path}", params={"ver": history.ver})
        assert response.status_code == 200 and response.json()["id"] == "A00", response.text
        assert "secret-value" not in response.text and response.json()["dataVersion"] == "current"


async def test_source_statement_timeout_is_enforced(source_case, monkeypatch):
    from app.core.config import get_settings

    case = source_case
    monkeypatch.setattr(get_settings(), "qa_statement_timeout_ms", 100)
    connection = await asyncpg.connect(
        case.real.admin_url.set(drivername="postgresql").render_as_string(hide_password=False)
    )
    transaction = connection.transaction()
    await transaction.start()
    try:
        await connection.execute(f'LOCK TABLE "{case.real.schema}".assets IN ACCESS EXCLUSIVE MODE')
        response = await case.client.post(f"{B}/object-types/test_asset/search", json={})
        assert response.status_code == 503 and response.json()["code"] == "OBJECT_DATA_UNAVAILABLE"
    finally:
        await transaction.rollback()
        await connection.close()


async def test_full_aggregate_exceeds_id_expansion_limit_and_export_never_truncates(source_case):
    case = source_case
    connection = await asyncpg.connect(
        case.real.admin_url.set(drivername="postgresql").render_as_string(hide_password=False)
    )
    try:
        # One server-side statement makes the physical cardinality explicit before API assertions.
        inserted = await connection.execute(f'''INSERT INTO "{case.real.schema}".assets
            SELECT 1, 'BIG_' || lpad(i::text,5,'0'), '批量测试', 1, 'big', 's', 'h'
            FROM generate_series(0,10000) AS n(i)''')
        assert inserted == "INSERT 0 10001"
        assert (
            await connection.fetchval(f'SELECT count(*) FROM "{case.real.schema}".assets WHERE ontology_id=1')
            == 10026
        )
        response = await case.client.post(
            f"{B}/scoped-ask", json={"scope": {"kind": "test_asset"}, "question": "对象数量"}
        )
        assert response.status_code == 200 and "10026" in response.json()["text"], response.text
        summed = await case.client.post(
            f"{B}/scoped-ask", json={"scope": {"kind": "test_asset"}, "question": "amount合计"}
        )
        assert summed.status_code == 200 and "10332.25" in summed.json()["text"]
        await enable_fixture_export(case.session)
        exported = await case.client.post(f"{B}/object-types/test_asset/export", json={})
        assert exported.status_code == 422 and "10000" in exported.json()["message"]
        oversized = await case.client.post(
            f"{B}/object-types/test_asset/export", json={"ids": ["A00"] * 5001}
        )
        assert oversized.status_code == 422
    finally:
        await connection.execute(f"DELETE FROM \"{case.real.schema}\".assets WHERE asset_id LIKE 'BIG_%'")
        await connection.close()


@pytest.mark.parametrize("flag", ["hidden", "sensitive"])
async def test_title_falls_back_only_to_public_primary_and_recommendations_skip_transforms(source_case, flag):
    case = source_case
    title = next(p for p in case.obj.properties if p.api_name == "name")
    if flag == "hidden":
        title.visibility = "hidden"
    else:
        title.is_sensitive = True
    next(p for p in case.obj.properties if p.api_name == "amount").transform = "amount * 1000"
    await case.session.flush()
    result = await search(case)
    assert (
        result["items"][0]["title"] == "A00"
        and "name" not in result["boundProps"]
        and "amount" not in result["boundProps"]
    )
    meta = (await case.client.get(f"{B}/object-types/test_asset/meta")).json()
    assert meta["scopeQuestions"] == ["对象数量"]
    assert (
        await case.client.post(
            f"{B}/scoped-ask", json={"scope": {"kind": "test_asset"}, "question": "amount合计"}
        )
    ).status_code == 422


async def test_detail_only_permission_without_build_or_search_and_revoked_binding(source_case):
    case = source_case
    case.role.func = {
        key: value
        for key, value in case.role.func.items()
        if key not in ("object-search", "object-types", "link-types")
    }
    case.role.func = {**case.role.func, "object-detail": "view"}
    await case.session.flush()
    assert (await case.client.get(f"{B}/object-types")).status_code == 200
    assert (await case.client.get(f"{B}/object-types/test_asset/meta")).status_code == 200
    assert (await case.client.get(f"{B}/object-types/test_asset/objects/A00")).status_code == 200
    assert (await case.client.get(f"{B}/object-types/test_asset/objects/A00/related")).status_code == 200
    assert (await case.client.post(f"{B}/object-types/test_asset/search", json={})).status_code == 403
    single = {
        "scope": {"kind": "test_asset", "single": {"kind": "test_asset", "id": "A00"}},
        "question": "对象数量",
    }
    assert (await case.client.post(f"{B}/scoped-ask", json=single)).status_code == 200
    assert (
        await case.client.post(
            f"{B}/scoped-ask", json={"scope": {"kind": "test_asset"}, "question": "对象数量"}
        )
    ).status_code == 403
    await case.session.execute(
        delete(OntologyRoleBinding).where(
            OntologyRoleBinding.ontology_id == 1, OntologyRoleBinding.role_id == case.role.id
        )
    )
    await case.session.flush()
    assert (await case.client.get(f"{B}/object-types/test_asset/objects/A00")).status_code == 404


async def test_invalid_unicode_keyword_is_validation_error(source_case):
    response = await source_case.client.post(
        f"{B}/object-types/test_asset/search",
        content=b'{"kw":"\\ud800"}',
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 422, response.text


async def test_same_code_other_workspace_never_borrows_retail(source_case, conn):
    from app.db.session import get_session
    from app.main import app

    case = source_case
    workspace = OntologyWorkspace(
        code="objects-other", name="另一本体", description="", domain="tests", is_default=False
    )
    case.session.add(workspace)
    await case.session.flush()
    uid = await case.session.scalar(select(SysUser.id).where(SysUser.code == "U010001"))
    case.session.add_all(
        [
            OntologyMember(ontology_id=workspace.id, user_id=uid, role="editor"),
            OntologyRoleBinding(ontology_id=workspace.id, role_id=case.role.id),
        ]
    )
    await case.session.flush()
    async with AsyncSession(
        bind=conn, join_transaction_mode="create_savepoint", expire_on_commit=False
    ) as other:
        await set_ontology_scope(other, workspace.id)
        other.add(
            ObjectType(
                code="order",
                api_name="SalesOrder",
                display_name="另一本体订单",
                obj_group="测试",
                conflict_strategy=ConflictStrategy.user_edit_wins,
                lifecycle=Lifecycle.active,
                status=ObjectStatus.published,
                pk_property="order_id",
                properties=[
                    Property(
                        api_name="order_id",
                        display_name="编号",
                        data_type="string",
                        prop_group="标识",
                        source_column="order_id",
                        description="",
                        is_pk=True,
                        visibility="normal",
                    )
                ],
            )
        )
        await other.commit()
    await set_ontology_scope(case.session, 1)
    previous = app.dependency_overrides[get_session]

    async def fresh():
        async with AsyncSession(
            bind=conn, join_transaction_mode="create_savepoint", expire_on_commit=False
        ) as request:
            yield request

    app.dependency_overrides[get_session] = fresh
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            result = await client.post(
                f"{B}/object-types/order/search", headers={"X-Ontology-Id": str(workspace.id)}, json={}
            )
            assert result.status_code == 200, result.text
            assert result.json()["bound"] is False and result.json()["items"] == []
    finally:
        app.dependency_overrides[get_session] = previous
