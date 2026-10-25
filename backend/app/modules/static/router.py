"""静态本体只读接口（挂在 /api/ontology/v1）。分页默认条数 = 页面常量（api-index §4）。

| 接口 | 页面 |
|---|---|
| /object-types[/brief,/{id},/{id}/properties,/links,/mappings,/usage] | ObjectTypes.vue、对象下拉 |
| /link-types[/paths,/{id},/{id}/quality,/{id}/usage] | LinkTypes.vue |
| /metrics[/{id},/{id}/versions,/{id}/refs] | Metrics.vue |
| /dimensions[/{id},/values,/mappings,/usage,/history] | Dimensions.vue |
| /knowledge[/synonym-conflicts,/{id},/{id}/refs] | Glossary.vue |
"""

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query

from app.common.context import ViewContextDep
from app.db.session import SessionDep
from app.modules.static.schemas import Rec, RecPage, SynonymConflicts
from app.modules.static.service import StaticService

router = APIRouter(tags=["static"])


async def _svc(session: SessionDep, ctx: ViewContextDep) -> StaticService:
    return StaticService(session, ctx)


Svc = Annotated[StaticService, Depends(_svc)]
Page = Annotated[int, Query(ge=1)]
Kw = Annotated[str, Query()]
Focus = Annotated[str | None, Query(alias="focusId")]


def Size(n: int) -> Any:  # noqa: N802
    return Query(n, ge=1, le=200)


# ---------------------------------------------------------------- 对象类型（ObjectTypes.vue）


@router.get("/object-types", response_model=RecPage)
async def list_object_types(svc: Svc, kw: Kw = "", page: Page = 1, size: int = Size(22)) -> Any:
    return await svc.object_list(kw, page, size)


@router.get("/object-types/brief", response_model=list[Rec])
async def object_types_brief(svc: Svc) -> Any:
    return await svc.object_brief()


@router.get("/object-types/{id}", response_model=Rec)
async def get_object_type(id: str, svc: Svc) -> Any:
    return await svc.object_detail(id)


@router.get("/object-types/{id}/properties", response_model=RecPage)
async def object_properties(id: str, svc: Svc, kw: Kw = "", page: Page = 1, size: int = Size(12)) -> Any:
    return await svc.object_properties(id, kw, page, size)


@router.get("/object-types/{id}/links", response_model=list[Rec])
async def object_links(id: str, svc: Svc) -> Any:
    return await svc.object_links(id)


@router.get("/object-types/{id}/mappings", response_model=RecPage)
async def object_mappings(id: str, svc: Svc, page: Page = 1, size: int = Size(12)) -> Any:
    return await svc.object_mappings(id, page, size)


@router.get("/object-types/{id}/usage", response_model=RecPage)
async def object_usage(
    id: str,
    svc: Svc,
    kind: Literal["metric", "view", "cr"],
    q: Kw = "",
    filter: Kw = "",
    page: Page = 1,
    size: int = Size(12),
) -> Any:
    return await svc.object_usage(id, kind, q, filter, page, size)


# ---------------------------------------------------------------- 关系类型（LinkTypes.vue）


@router.get("/link-types", response_model=RecPage)
async def list_link_types(
    svc: Svc,
    q: Kw = "",
    src: Kw = "",
    st: Kw = "",
    focus_id: Focus = None,
    page: Page = 1,
    size: int = Size(18),
) -> Any:
    return await svc.link_list(q, src, st, page, size, focus_id)


@router.get("/link-types/paths", response_model=list[Rec])
async def link_paths(
    svc: Svc, from_: Annotated[str | None, Query(alias="from")] = None, to: str | None = None
) -> Any:
    return await svc.link_paths(from_, to)


@router.get("/link-types/{id}", response_model=Rec)
async def get_link_type(id: str, svc: Svc) -> Any:
    return await svc.link_detail(id)


@router.get("/link-types/{id}/quality", response_model=Rec)
async def link_quality(id: str, svc: Svc) -> Any:
    return await svc.link_quality(id)


@router.get("/link-types/{id}/usage", response_model=RecPage)
async def link_usage(id: str, svc: Svc, page: Page = 1, size: int = Size(10)) -> Any:
    return await svc.link_usage(id, page, size)


# ---------------------------------------------------------------- 指标（Metrics.vue）


@router.get("/metrics", response_model=RecPage)
async def list_metrics(svc: Svc, kw: Kw = "", group: Kw = "", page: Page = 1, size: int = Size(18)) -> Any:
    return await svc.metric_list(kw, group, page, size)


@router.get("/metrics/{id}", response_model=Rec)
async def get_metric(id: str, svc: Svc) -> Any:
    return await svc.metric_detail(id)


@router.get("/metrics/{id}/versions", response_model=RecPage)
async def metric_versions(id: str, svc: Svc, page: Page = 1, size: int = Size(10)) -> Any:
    return await svc.metric_versions(id, page, size)


@router.get("/metrics/{id}/refs", response_model=RecPage)
async def metric_refs(id: str, svc: Svc, page: Page = 1, size: int = Size(10)) -> Any:
    return await svc.metric_refs(id, page, size)


# ---------------------------------------------------------------- 维度与码表（Dimensions.vue）


@router.get("/dimensions", response_model=RecPage)
async def list_dimensions(
    svc: Svc,
    q: Kw = "",
    kind: Kw = "all",
    group: Kw = "",
    focus_id: Focus = None,
    page: Page = 1,
    size: int = Size(18),
) -> Any:
    return await svc.dim_list(q, kind, group, page, size, focus_id)


@router.get("/dimensions/{id}", response_model=Rec)
async def get_dimension(id: str, svc: Svc) -> Any:
    return await svc.dim_detail(id)


@router.get("/dimensions/{id}/values", response_model=RecPage)
async def dimension_values(
    id: str,
    svc: Svc,
    level: Kw = "",
    q: Kw = "",
    status: Kw = "",
    page: Page = 1,
    size: int = Size(10),
) -> Any:
    return await svc.dim_values(id, level, q, status, page, size)


@router.get("/dimensions/{id}/mappings", response_model=RecPage)
async def dimension_mappings(
    id: str,
    svc: Svc,
    conflict: bool = False,
    page: Page = 1,
    size: int = Size(8),
) -> Any:
    return await svc.dim_mappings(id, conflict, page, size)


@router.get("/dimensions/{id}/usage", response_model=RecPage)
async def dimension_usage(id: str, svc: Svc, page: Page = 1, size: int = Size(10)) -> Any:
    return await svc.dim_usage(id, page, size)


@router.get("/dimensions/{id}/history", response_model=RecPage)
async def dimension_history(id: str, svc: Svc, page: Page = 1, size: int = Size(10)) -> Any:
    return await svc.dim_history(id, page, size)


# ---------------------------------------------------------------- 业务知识（Glossary.vue）


@router.get("/knowledge", response_model=RecPage)
async def list_knowledge(
    svc: Svc,
    q: Kw = "",
    type: Kw = "",
    st: Kw = "",
    focus_id: Focus = None,
    page: Page = 1,
    size: int = Size(19),
) -> Any:
    return await svc.knowledge_list(q, type, st, page, size, focus_id)


@router.get("/knowledge/synonym-conflicts", response_model=SynonymConflicts)
async def synonym_conflicts(svc: Svc) -> Any:
    return await svc.synonym_conflicts()


@router.get("/knowledge/{id}", response_model=Rec)
async def get_knowledge(id: str, svc: Svc) -> Any:
    return await svc.knowledge_detail(id)


@router.get("/knowledge/{id}/refs", response_model=RecPage)
async def knowledge_refs(id: str, svc: Svc, page: Page = 1, size: int = Size(8)) -> Any:
    return await svc.knowledge_refs(id, page, size)
