"""PDE lists use database pages; directories stay complete and mapping order stays stable."""

from datetime import timedelta

from sqlalchemy import func, select

from app.core import clock
from app.modules.data.models import Datasource, DatasourceRun, DatasourceTable, DsRunResult
from app.modules.integration.models import ExtSystem, ExtSystemStatus
from app.modules.integration.schemas import ExtSystemIn
from app.modules.integration.service import ExtSystemService
from app.modules.static.models import ConflictStrategy, Lifecycle, ObjectType
from app.seeds.loader import run_seeds
from tests.test_datasources import ds  # noqa: F401
from tests.test_integration_code_lock import BODY

P = "/api/data/v1"


async def test_datasource_pages_mapping_order_and_bounded_runs(client, ds):  # noqa: F811
    source = await ds.scalar(select(Datasource).where(Datasource.code == "DS-001"))
    for index in range(23):
        ds.add(
            DatasourceTable(
                datasource_id=source.id,
                schema_name="public",
                name=f"paging_table_{index:02}",
                comment="分页",
                meta_updated_at=clock.now(),
                row_count=index,
                col_count=1,
                seq=1000 + index,
            )
        )
    ds.add(
        ObjectType(
            code="paging_mapped_object",
            api_name="PagingObject",
            display_name="分页映射对象",
            obj_group="测试",
            conflict_strategy=ConflictStrategy.user_edit_wins,
            lifecycle=Lifecycle.active,
            datasource_name=source.code,
            source_table="public.paging_table_22",
            properties=[],
        )
    )
    for index in range(31):
        ds.add(
            DatasourceRun(
                datasource_id=source.id,
                seq=1000 + index,
                run_at=clock.now() - timedelta(minutes=index),
                result=DsRunResult.success,
                rows=index,
                secs=1,
                msg=f"运行{index}",
            )
        )
    await ds.flush()
    first = (await client.get(P + "/datasources", params={"page": 1, "size": 10})).json()
    second = (await client.get(P + "/datasources", params={"page": 2, "size": 10})).json()
    assert first["total"] == second["total"] == 16 and len(first["items"]) == 10 and len(second["items"]) == 6
    assert not {r["id"] for r in first["items"]} & {r["id"] for r in second["items"]}
    assert all(r["runs"] == [] for r in first["items"])
    tables = (
        await client.get(
            P + f"/datasources/{source.code}/tables", params={"page": 1, "size": 10, "kw": "paging_table"}
        )
    ).json()
    assert tables["total"] == 23 and len(tables["items"]) == 10
    assert (
        tables["items"][0]["name"] == "paging_table_22" and tables["items"][0]["objectType"] == "分页映射对象"
    )
    next_tables = (
        await client.get(
            P + f"/datasources/{source.code}/tables", params={"page": 2, "size": 10, "kw": "paging_table"}
        )
    ).json()
    assert len(next_tables["items"]) == 10
    assert not {r["name"] for r in tables["items"]} & {r["name"] for r in next_tables["items"]}
    detail = (await client.get(P + f"/datasources/{source.code}")).json()
    assert len(detail["runs"]) == 10
    page = (await client.get(P + f"/datasources/{source.code}/runs", params={"page": 2, "size": 10})).json()
    total = await ds.scalar(
        select(func.count()).select_from(DatasourceRun).where(DatasourceRun.datasource_id == source.id)
    )
    assert page["total"] == total and page["page"] == 2 and len(page["items"]) == 10


async def test_external_system_pages_literal_filters_and_writeback_catalog(client, ds):  # noqa: F811
    await run_seeds(ds, ["integration"])
    svc = ExtSystemService(ds)
    for index in range(15):
        await svc.create(ExtSystemIn.model_validate({**BODY, "name": f"分页系统{index}"}), actor="U010001")
    await svc.create(ExtSystemIn.model_validate({**BODY, "name": "literal%_system"}), actor="U010001")
    first_response = await client.get(P + "/ext-systems")
    assert first_response.status_code == 200, first_response.text
    first = first_response.json()
    second = (await client.get(P + "/ext-systems", params={"page": 2, "size": 10})).json()
    assert first["size"] == 10 and len(first["items"]) == 10 and len(second["items"]) == 10
    assert not {r["id"] for r in first["items"]} & {r["id"] for r in second["items"]}
    filtered = (await client.get(P + "/ext-systems", params={"kw": "%_"})).json()
    assert filtered["total"] == 1 and filtered["items"][0]["name"] == "literal%_system"
    catalog = (await client.get(P + "/ext-systems", params={"writeback": 1})).json()
    expected = await ds.scalar(
        select(func.count())
        .select_from(ExtSystem)
        .where(ExtSystem.env == "生产", ExtSystem.status != ExtSystemStatus.disabled)
    )
    assert catalog["total"] == expected and len(catalog["items"]) == expected and expected > 10


async def test_role_state_bulk_baseline_and_members_sql_pages(client, ds):  # noqa: F811
    from app.modules.admin.models import SysRole, SysRoleMember, SysUser

    response = await client.get("/api/admin/v1/roles/state")
    assert response.status_code == 200, response.text
    state = response.json()
    assert len(state["users"]) == await ds.scalar(
        select(func.count()).select_from(SysUser).where(SysUser.role.is_not(None))
    )
    assert sum(map(len, state["members"].values())) == await ds.scalar(
        select(func.count()).select_from(SysRoleMember)
    )
    role = max(state["roles"], key=lambda r: r["memberCount"])
    assert len(state["members"][role["id"]]) == role["memberCount"] > 10
    first = (
        await client.get(f"/api/admin/v1/roles/{role['id']}/members", params={"page": 1, "size": 10})
    ).json()
    second = (
        await client.get(f"/api/admin/v1/roles/{role['id']}/members", params={"page": 2, "size": 10})
    ).json()
    assert first["total"] == second["total"] == role["memberCount"] and len(first["items"]) == 10
    assert not {row["id"] for row in first["items"]} & {row["id"] for row in second["items"]}
    assert len(state["members"][role["id"]]) == role["memberCount"], (
        "display pages never truncate edit baseline"
    )
    restricted = await ds.scalar(select(SysRole).where(SysRole.code == "R-02"))
    restricted.func = {**restricted.func, "roles": "view", "users": "none"}
    await ds.flush()
    assert (await client.get("/api/admin/v1/roles", headers={"X-Demo-As": "U010004"})).status_code == 200
    denied = await client.get("/api/admin/v1/roles/state", headers={"X-Demo-As": "U010004"})
    assert denied.status_code == 403 and denied.json()["details"]["page"] == "users"
