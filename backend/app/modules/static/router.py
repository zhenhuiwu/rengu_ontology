"""静态本体只读接口（挂在 /api/ontology/v1）。分页默认条数 = 页面常量（api-index §4）。

| 接口 | 页面 |
|---|---|
| /object-types[/brief,/{id},/{id}/properties,/links,/mappings,/usage] | ObjectTypes.vue、对象下拉 |
| /link-types[/paths,/{id},/{id}/quality,/{id}/usage] | LinkTypes.vue |
| /metrics[/{id},/{id}/versions,/{id}/refs] | Metrics.vue |
| /dimensions[/{id},/values,/mappings,/usage,/history] | Dimensions.vue |
| /knowledge[/synonym-conflicts,/{id},/{id}/refs] | Glossary.vue |
"""

from collections.abc import Callable
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query

from app.common.auth import CurrentUserDep, require_func
from app.common.context import ViewContextDep
from app.common.pagination import MAX_PAGE_SIZE, PageParams
from app.db.session import SessionDep
from app.modules.static.schemas import Rec, RecPage, SynonymConflicts
from app.modules.static.service import StaticService

router = APIRouter(tags=["static"])


async def _svc(session: SessionDep, ctx: ViewContextDep, _: CurrentUserDep) -> StaticService:
    """所有静态 GET 都要登录：未知 / 停用的 X-Demo-As → 401（app.common.auth）。"""
    return StaticService(session, ctx)


# 功能权限（api-index 权限表）：页面 key·view；
# /object-types/brief 是多页共用的对象下拉，表里记 data-quality·view
FUNC_OF: dict[str, str] = {}


def get(path: str, func: str, **kw: Any) -> Any:
    FUNC_OF[path] = func
    return router.get(path, dependencies=[Depends(require_func(func, "view"))], **kw)


Svc = Annotated[StaticService, Depends(_svc)]
Kw = Annotated[str, Query()]
Focus = Annotated[str | None, Query(alias="focusId")]


def page_dep(default: int) -> Callable[..., PageParams]:
    """骨架 PageParams，默认条数按页面常量（api-index §4）。page 不设下限：0、负数由 PageParams 收成 1，
    越界页由 service.paginate 里的 clamp 收到最后一页（前端 paginate.ts:13）；size 照旧校验 1..200。"""

    def _dep(
        page: Annotated[int, Query()] = 1,
        size: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = default,
    ) -> PageParams:
        return PageParams(page=page, size=size)

    return _dep


P8 = Annotated[PageParams, Depends(page_dep(8))]
P10 = Annotated[PageParams, Depends(page_dep(10))]
P12 = Annotated[PageParams, Depends(page_dep(12))]
P18 = Annotated[PageParams, Depends(page_dep(18))]
P19 = Annotated[PageParams, Depends(page_dep(19))]
P22 = Annotated[PageParams, Depends(page_dep(22))]


# ---------------------------------------------------------------- 对象类型（ObjectTypes.vue）


@get("/object-types", "object-types", response_model=RecPage)
async def list_object_types(svc: Svc, p: P22, kw: Kw = "") -> Any:
    return await svc.object_list(kw, p)


@get("/object-types/brief", "data-quality", response_model=list[Rec])
async def object_types_brief(svc: Svc) -> Any:
    return await svc.object_brief()


@get("/object-types/{id}", "object-types", response_model=Rec)
async def get_object_type(id: str, svc: Svc) -> Any:
    return await svc.object_detail(id)


@get("/object-types/{id}/properties", "object-types", response_model=RecPage)
async def object_properties(id: str, svc: Svc, p: P12, kw: Kw = "") -> Any:
    return await svc.object_properties(id, kw, p)


@get("/object-types/{id}/links", "object-types", response_model=list[Rec])
async def object_links(id: str, svc: Svc) -> Any:
    return await svc.object_links(id)


@get("/object-types/{id}/mappings", "object-types", response_model=RecPage)
async def object_mappings(id: str, svc: Svc, p: P12) -> Any:
    return await svc.object_mappings(id, p)


@get("/object-types/{id}/usage", "object-types", response_model=RecPage)
async def object_usage(
    id: str,
    svc: Svc,
    p: P12,
    kind: Literal["metric", "view", "cr"],
    q: Kw = "",
    filter: Kw = "",
) -> Any:
    return await svc.object_usage(id, kind, q, filter, p)


# ---------------------------------------------------------------- 关系类型（LinkTypes.vue）


@get("/link-types", "link-types", response_model=RecPage)
async def list_link_types(
    svc: Svc,
    p: P18,
    q: Kw = "",
    src: Kw = "",
    st: Kw = "",
    focus_id: Focus = None,
) -> Any:
    return await svc.link_list(q, src, st, p, focus_id)


@get("/link-types/paths", "link-types", response_model=list[Rec])
async def link_paths(
    svc: Svc, from_: Annotated[str | None, Query(alias="from")] = None, to: str | None = None
) -> Any:
    return await svc.link_paths(from_, to)


@get("/link-types/{id}", "link-types", response_model=Rec)
async def get_link_type(id: str, svc: Svc) -> Any:
    return await svc.link_detail(id)


@get("/link-types/{id}/quality", "link-types", response_model=Rec)
async def link_quality(id: str, svc: Svc) -> Any:
    return await svc.link_quality(id)


@get("/link-types/{id}/usage", "link-types", response_model=RecPage)
async def link_usage(id: str, svc: Svc, p: P10) -> Any:
    return await svc.link_usage(id, p)


# ---------------------------------------------------------------- 指标（Metrics.vue）


@get("/metrics", "metrics", response_model=RecPage)
async def list_metrics(svc: Svc, p: P18, kw: Kw = "", group: Kw = "") -> Any:
    return await svc.metric_list(kw, group, p)


@get("/metrics/{id}", "metrics", response_model=Rec)
async def get_metric(id: str, svc: Svc) -> Any:
    return await svc.metric_detail(id)


@get("/metrics/{id}/versions", "metrics", response_model=RecPage)
async def metric_versions(id: str, svc: Svc, p: P10) -> Any:
    return await svc.metric_versions(id, p)


@get("/metrics/{id}/refs", "metrics", response_model=RecPage)
async def metric_refs(id: str, svc: Svc, p: P10) -> Any:
    return await svc.metric_refs(id, p)


# ---------------------------------------------------------------- 维度与码表（Dimensions.vue）


@get("/dimensions", "dimensions", response_model=RecPage)
async def list_dimensions(
    svc: Svc,
    p: P18,
    q: Kw = "",
    kind: Kw = "all",
    group: Kw = "",
    focus_id: Focus = None,
) -> Any:
    return await svc.dim_list(q, kind, group, p, focus_id)


@get("/dimensions/{id}", "dimensions", response_model=Rec)
async def get_dimension(id: str, svc: Svc, full: bool = False) -> Any:
    """full=1：完整 DimItem（含 values / mappings / usage / history，草稿行原样），
    给前端整表载入用（Dimensions.vue 按 DIM_ITEMS 自己算取值 / 映射 / 使用情况视图）[推断]。"""
    return await svc.dim_detail(id, full)


@get("/dimensions/{id}/values", "dimensions", response_model=RecPage)
async def dimension_values(
    id: str,
    svc: Svc,
    p: P10,
    level: Kw = "",
    q: Kw = "",
    status: Kw = "",
) -> Any:
    return await svc.dim_values(id, level, q, status, p)


@get("/dimensions/{id}/mappings", "dimensions", response_model=RecPage)
async def dimension_mappings(
    id: str,
    svc: Svc,
    p: P8,
    conflict: bool = False,
) -> Any:
    return await svc.dim_mappings(id, conflict, p)


@get("/dimensions/{id}/usage", "dimensions", response_model=RecPage)
async def dimension_usage(id: str, svc: Svc, p: P10) -> Any:
    return await svc.dim_usage(id, p)


@get("/dimensions/{id}/history", "dimensions", response_model=RecPage)
async def dimension_history(id: str, svc: Svc, p: P10) -> Any:
    return await svc.dim_history(id, p)


# ---------------------------------------------------------------- 业务知识（Glossary.vue）


@get("/knowledge", "glossary", response_model=RecPage)
async def list_knowledge(
    svc: Svc,
    p: P19,
    q: Kw = "",
    type: Kw = "",
    st: Kw = "",
    focus_id: Focus = None,
) -> Any:
    return await svc.knowledge_list(q, type, st, p, focus_id)


@get("/knowledge/synonym-conflicts", "glossary", response_model=SynonymConflicts)
async def synonym_conflicts(svc: Svc) -> Any:
    return await svc.synonym_conflicts()


@get("/knowledge/{id}", "glossary", response_model=Rec)
async def get_knowledge(id: str, svc: Svc) -> Any:
    return await svc.knowledge_detail(id)


@get("/knowledge/{id}/refs", "glossary", response_model=RecPage)
async def knowledge_refs(id: str, svc: Svc, p: P8) -> Any:
    return await svc.knowledge_refs(id, p)
