"""data 模块路由（负责人：本体小白）。挂载在 /api/data/v1（见 app/api.py）。

数据连接页 src/pages/data/Connections.vue（api-index §3.1）：/datasources*。
外部系统 /ext-systems*、/writeback-calls* 归 integration 模块（Palantir），这里不碰。
功能权限页面 key：connections（view / manage），基于数据库角色聚合权限。
"""

from typing import Annotated

from fastapi import APIRouter, Body, Depends, Query, Response
from pydantic import Field, field_validator
from sqlalchemy import and_, func, or_, select

from app.common.auth import CurrentUserDep, require_func
from app.common.pagination import Page, PageParamsDep
from app.core.errors import AppError
from app.core.schema import ApiModel
from app.db.session import SessionDep
from app.modules.data.models import DatasourceRun, DatasourceTable, DsType
from app.modules.data.schemas import (
    DatasourceCreate,
    DatasourceOut,
    DatasourceUpdate,
    DsMeta,
    DsRun,
    DsTableOut,
    DsTest,
    DsTestIn,
)
from app.modules.data.service import META, DatasourceService, table_out, to_out
from app.modules.static.models import ObjectType

router = APIRouter(tags=["data"])

VIEW = [Depends(require_func("connections", "view"))]
MANAGE = [Depends(require_func("connections", "manage"))]


class RefreshTablesIn(ApiModel):
    schemas: list[Annotated[str, Field(min_length=1, max_length=64)]] = Field(min_length=1, max_length=32)

    @field_validator("schemas")
    @classmethod
    def valid_schemas(cls, values: list[str]) -> list[str]:
        from app.modules.data.connector import SYSTEM_SCHEMAS

        if len(values) != len(set(values)) or any(
            name != name.strip() or any(ord(char) < 32 or ord(char) == 127 or 0xD800 <= ord(char) <= 0xDFFF for char in name)
            or name.lower() in SYSTEM_SCHEMAS or name.lower().startswith("pg_") for name in values
        ):
            raise ValueError("schema 名称无效、重复或属于系统 schema")
        return values


@router.get("/datasources", response_model=Page[DatasourceOut], summary="数据连接列表", dependencies=VIEW)
async def list_datasources(
    session: SessionDep,
    page: PageParamsDep,
    type: Annotated[DsType | None, Query(description="类型（中文，同 DS_TYPES）；不传 = 全部")] = None,
    kw: Annotated[str | None, Query(description="名称 / 主机包含，或库名包含（不区分大小写）")] = None,
) -> Page[DatasourceOut]:
    rows, total = await DatasourceService(session).search(type_=type, kw=kw or None, page=page)
    sources = {key for row in rows for key in (row.code, row.name)}
    mapped = (
        await session.execute(
            select(ObjectType.datasource_name, ObjectType.display_name, ObjectType.source_table)
            .where(ObjectType.datasource_name.in_(sources))
            .order_by(ObjectType.code)
        )
    ).all()
    scoped = [and_(DatasourceTable.datasource_id == row.id, DatasourceTable.schema_name == row.database.strip())
              for row in rows if row.type == DsType.dm8 and row.database.strip()]
    counts = {}
    if scoped:
        counted = await session.execute(
            select(DatasourceTable.datasource_id, func.count())
            .where(or_(*scoped))
            .group_by(DatasourceTable.datasource_id)
        )
        counts = {source_id: total for source_id, total in counted.all()}
    items = [
        to_out(row, []).model_copy(
            update={"object_types": [name for source, name, table in mapped if source in (row.code, row.name)
                    and (row.type != DsType.dm8 or not row.database.strip() or (table or '').startswith(row.database.strip() + '.'))],
                    "table_count": counts.get(row.id, 0) if row.type == DsType.dm8 and row.database.strip() else row.table_count}
        )
        for row in rows
    ]
    return Page[DatasourceOut].of(items, total, page)


@router.get("/datasources/meta", response_model=DsMeta, summary="类型、默认端口等常量", dependencies=VIEW)
async def datasource_meta() -> DsMeta:
    return META


@router.post(
    "/datasources:test", response_model=DsTest, summary="测试未保存的连接参数（不落库）", dependencies=MANAGE
)
async def test_unsaved(body: DsTestIn, session: SessionDep) -> DsTest:
    return await DatasourceService(session).test_unsaved(body)


@router.get("/datasources/{ds_id}", response_model=DatasourceOut, summary="数据连接详情", dependencies=VIEW)
async def get_datasource(ds_id: str, session: SessionDep) -> DatasourceOut:
    return await _out(session, await DatasourceService(session).get(ds_id))


@router.get(
    "/datasources/{ds_id}/tables", response_model=Page[DsTableOut], summary="库表清单", dependencies=VIEW
)
async def list_tables(
    ds_id: str,
    session: SessionDep,
    page: PageParamsDep,
    kw: Annotated[str | None, Query(description="表名（不区分大小写）或说明包含")] = None,
) -> Page[DsTableOut]:
    svc = DatasourceService(session)
    ds = await svc.get(ds_id)
    from app.modules.data.mappings import mapped_objects

    mapped = await mapped_objects(session, ds)
    rows, total = await svc.tables.page_for(
        ds.id, kw, page, [o.source_table for o in mapped if o.source_table],
        schema=ds.database.strip() if ds.type == DsType.dm8 and ds.database.strip() else None,
    )
    items = []
    for t in rows:
        names = [
            o.display_name
            for o in mapped
            if o.source_table in (f"{t.schema_name}.{t.name}", t.name if t.schema_name == "public" else None)
        ]
        items.append(table_out(t).model_copy(update={"object_type": "、".join(names) or None}))
    return Page[DsTableOut].of(items, total, page)


@router.post(
    "/datasources", response_model=DatasourceOut, status_code=201, summary="新建数据连接", dependencies=MANAGE
)
async def create_datasource(
    body: DatasourceCreate, session: SessionDep, user: CurrentUserDep
) -> DatasourceOut:
    if body.type.value == "达梦 DM8" and body.mode.value != "手动" and user.ontology_role != "owner":
        raise AppError("只有工作空间所有者可启用镜像定时同步", code="FORBIDDEN", status_code=403)
    svc = DatasourceService(session)
    ds = await svc.create(body, actor=user.id)
    return await _out(session, await svc.get(ds.code))


@router.put("/datasources/{ds_id}", response_model=DatasourceOut, summary="修改数据连接", dependencies=MANAGE)
async def update_datasource(ds_id: str, body: DatasourceUpdate, session: SessionDep, user: CurrentUserDep) -> DatasourceOut:
    if body.type == DsType.dm8 and body.mode.value != "手动" and user.ontology_role != "owner":
        raise AppError("只有工作空间所有者可启用镜像定时同步", code="FORBIDDEN", status_code=403)
    return await _out(session, await DatasourceService(session).update(ds_id, body, actor=user.id))


@router.delete("/datasources/{ds_id}", status_code=204, summary="删除数据连接", dependencies=MANAGE)
async def delete_datasource(ds_id: str, session: SessionDep) -> Response:
    await DatasourceService(session).delete(ds_id)
    return Response(status_code=204)


@router.post(
    "/datasources/{ds_id}:test", response_model=DsTest, summary="测试已保存的连接", dependencies=MANAGE
)
async def test_saved(ds_id: str, session: SessionDep) -> DsTest:
    return await DatasourceService(session).test_saved(ds_id)


@router.post(
    "/datasources/{ds_id}/tables:refresh",
    summary="连上源库内省，刷新库表清单（需开启真实连接；测试连接成功时也会自动刷新）",
    dependencies=MANAGE,
)
async def refresh_tables(ds_id: str, session: SessionDep, body: Annotated[RefreshTablesIn | None, Body()] = None) -> dict[str, int]:
    return {"count": await DatasourceService(session).refresh_tables(ds_id, schemas=body.schemas if body else None)}


@router.get("/datasources/{ds_id}/schemas", dependencies=MANAGE)
async def list_schemas(ds_id: str, session: SessionDep) -> dict[str, list[str]]:
    return {"items": await DatasourceService(session).list_schemas(ds_id)}


from app.modules.data.ontology_import import router as _import_router  # noqa: E402
from app.modules.data.quality_router import router as _quality_router  # noqa: E402

router.include_router(_quality_router)
router.include_router(_import_router)


@router.get("/datasources/{ds_id}/mirror/runs", dependencies=VIEW)
async def datasource_mirror_runs(ds_id: str, session: SessionDep, page: PageParamsDep):
    from app.modules.data.models import ExternalMirrorRun

    await DatasourceService(session).get(ds_id)
    q = select(ExternalMirrorRun).where(ExternalMirrorRun.datasource_code == ds_id)
    total = await session.scalar(select(func.count()).select_from(ExternalMirrorRun).where(ExternalMirrorRun.datasource_code == ds_id))
    rows = list(await session.scalars(q.order_by(ExternalMirrorRun.id.desc()).offset(page.offset).limit(page.size)))
    return {"items": [{"id": r.id, "objectId": r.object_code, "trigger": r.trigger, "status": r.status,
                       "startedAt": r.started_at, "finishedAt": r.finished_at, "readCount": r.read_count,
                       "writeCount": r.write_count, "complete": r.complete, "error": r.error} for r in rows],
            "total": total, "page": page.page, "size": page.size}


@router.get("/datasources/{ds_id}/runs", response_model=Page[DsRun], dependencies=VIEW)
async def datasource_runs(ds_id: str, session: SessionDep, page: PageParamsDep):
    ds = await DatasourceService(session).get(ds_id)
    query = select(DatasourceRun).where(DatasourceRun.datasource_id == ds.id)
    total = await session.scalar(select(func.count()).select_from(query.subquery()))
    p = page.clamp(total)
    rows = list(
        await session.scalars(
            query.order_by(DatasourceRun.run_at.desc(), DatasourceRun.id.desc())
            .offset(p.offset)
            .limit(p.size)
        )
    )
    return Page[DsRun].of(
        [DsRun(time=r.run_at, result=r.result, rows=r.rows, secs=r.secs, msg=r.msg,
               origin="demo" if r.is_seed else "unverified") for r in rows],
        total,
        page,
    )


async def _out(session, ds) -> DatasourceOut:
    from app.modules.data.mappings import mapped_objects

    runs = list(
        await session.scalars(
            select(DatasourceRun)
            .where(DatasourceRun.datasource_id == ds.id)
            .order_by(DatasourceRun.seq, DatasourceRun.id)
            .limit(10)
        )
    )
    mapped = await mapped_objects(session, ds)
    schema = ds.database.strip() if ds.type == DsType.dm8 else ""
    count = await session.scalar(select(func.count()).select_from(DatasourceTable).where(
        DatasourceTable.datasource_id == ds.id, DatasourceTable.schema_name == schema
    )) if schema else ds.table_count
    return to_out(ds, runs).model_copy(update={
        "object_types": [o.display_name for o in mapped if not schema or (o.source_table or "").startswith(schema + ".")],
        "table_count": count,
    })
