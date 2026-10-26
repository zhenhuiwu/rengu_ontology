"""data 模块路由（负责人：本体小白）。挂载在 /api/data/v1（见 app/api.py）。

数据连接页 src/pages/data/Connections.vue（api-index §3.1）：/datasources*。
外部系统 /ext-systems*、/writeback-calls* 归 integration 模块（Palantir），这里不碰。
功能权限页面 key：connections（view / manage），require_func 现在是桩（PDE）。
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response

from app.common.auth import CurrentUserDep, require_func
from app.common.pagination import Page, PageParamsDep
from app.db.session import SessionDep
from app.modules.data.models import DsType
from app.modules.data.schemas import (
    DatasourceCreate,
    DatasourceOut,
    DatasourceUpdate,
    DsMeta,
    DsTableOut,
    DsTest,
    DsTestIn,
)
from app.modules.data.service import META, DatasourceService, table_out, to_out

router = APIRouter(tags=["data"])

VIEW = [Depends(require_func("connections", "view"))]
MANAGE = [Depends(require_func("connections", "manage"))]


@router.get("/datasources", response_model=Page[DatasourceOut], summary="数据连接列表", dependencies=VIEW)
async def list_datasources(
    session: SessionDep,
    page: PageParamsDep,
    type: Annotated[DsType | None, Query(description="类型（中文，同 DS_TYPES）；不传 = 全部")] = None,
    kw: Annotated[str | None, Query(description="名称 / 主机包含，或库名包含（不区分大小写）")] = None,
) -> Page[DatasourceOut]:
    rows, total = await DatasourceService(session).search(type_=type, kw=kw or None, page=page)
    return Page[DatasourceOut].of([to_out(r) for r in rows], total, page)


@router.get("/datasources/meta", response_model=DsMeta, summary="类型、默认端口等常量", dependencies=VIEW)
async def datasource_meta() -> DsMeta:
    return META


@router.post(
    "/datasources:test", response_model=DsTest, summary="测试未保存的连接参数（不落库）", dependencies=VIEW
)
async def test_unsaved(body: DsTestIn, session: SessionDep) -> DsTest:
    return await DatasourceService(session).test_unsaved(body)


@router.get("/datasources/{ds_id}", response_model=DatasourceOut, summary="数据连接详情", dependencies=VIEW)
async def get_datasource(ds_id: str, session: SessionDep) -> DatasourceOut:
    return to_out(await DatasourceService(session).get(ds_id))


@router.get(
    "/datasources/{ds_id}/tables", response_model=Page[DsTableOut], summary="库表清单", dependencies=VIEW
)
async def list_tables(
    ds_id: str,
    session: SessionDep,
    page: PageParamsDep,
    kw: Annotated[str | None, Query(description="表名（不区分大小写）或说明包含")] = None,
) -> Page[DsTableOut]:
    rows, total = await DatasourceService(session).list_tables(ds_id, kw or None, page)
    return Page[DsTableOut].of([table_out(t) for t in rows], total, page)


@router.post(
    "/datasources", response_model=DatasourceOut, status_code=201, summary="新建数据连接", dependencies=MANAGE
)
async def create_datasource(
    body: DatasourceCreate, session: SessionDep, user: CurrentUserDep
) -> DatasourceOut:
    svc = DatasourceService(session)
    ds = await svc.create(body, actor=user.id)
    return to_out(await svc.get(ds.code))


@router.put("/datasources/{ds_id}", response_model=DatasourceOut, summary="修改数据连接", dependencies=MANAGE)
async def update_datasource(ds_id: str, body: DatasourceUpdate, session: SessionDep) -> DatasourceOut:
    return to_out(await DatasourceService(session).update(ds_id, body))


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
async def refresh_tables(ds_id: str, session: SessionDep) -> dict[str, int]:
    return {"count": await DatasourceService(session).refresh_tables(ds_id)}
