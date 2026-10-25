"""静态本体只读接口（挂在 /api/ontology/v1）。分页默认条数 = 页面常量（api-index §4）。

| 接口 | 页面 |
|---|---|
| /object-types[/brief,/{id},/{id}/properties,/links,/mappings,/usage] | ObjectTypes.vue、对象下拉 |
| /link-types[/paths,/{id},/{id}/quality,/{id}/usage] | LinkTypes.vue |
| /metrics[/{id},/{id}/versions,/{id}/refs] | Metrics.vue |
| /dimensions[/{id},/values,/mappings,/usage,/history] | Dimensions.vue |
| /knowledge[/synonym-conflicts,/{id},/{id}/refs] | Glossary.vue |
| POST /releases:merge-check、POST /releases、POST /releases/{ver}:rollback | Releases.vue 发布 / 回滚按钮 |
| GET /change-requests[/{id}]、DELETE /change-requests/{id} | Releases.vue 变更单列表、删除草稿 |
| POST /change-requests/{id}:submit/withdraw/approve/reject/unapprove/reopen | Releases.vue 详情按钮 |
"""

from collections.abc import Callable
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Body, Depends, Query

from app.common.auth import CurrentUserDep, require_func
from app.common.context import ViewContextDep
from app.common.pagination import MAX_PAGE_SIZE, PageParams
from app.db.session import SessionDep
from app.modules.static.crs import ChangeRequestService
from app.modules.static.publish import PublishService
from app.modules.static.schemas import (
    MergeCheckOut,
    PublishIn,
    Rec,
    RecPage,
    RejectIn,
    RollbackIn,
    SynonymConflicts,
)
from app.modules.static.service import StaticService, paginate
from app.modules.versioning.repo import ReleaseRepo
from app.modules.versioning.schemas import ReleaseOut

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


# ---------------------------------------------------------------- 整版发布 / 回滚
# 写接口：只读模式（?ver / previewAs）下骨架统一 403 READONLY
_MANAGE = [Depends(require_func("releases", "manage"))]


@router.post("/releases:merge-check", dependencies=_MANAGE, response_model=MergeCheckOut)
async def merge_check(session: SessionDep, _: CurrentUserDep) -> Any:
    """只算合并校验（mock mergeCheck(已通过的单子)），不落库。"""
    return await PublishService(session).merge_check()


@router.post("/releases", dependencies=_MANAGE, response_model=ReleaseOut, status_code=201)
async def publish(
    session: SessionDep, user: CurrentUserDep, body: Annotated[PublishIn, Body()] = PublishIn()
) -> Any:
    """整版发布：本体管理员。合并校验有阻断 → 409 MERGE_BLOCKED（details.rows）；
    没有已通过的单子 → 409 EMPTY_RELEASE。"""
    ver = await PublishService(session).publish(user, body.note)
    return ReleaseOut.model_validate(await ReleaseRepo(session).by_ver_or_404(ver))


@router.post("/releases/{ver}:rollback", dependencies=_MANAGE, response_model=ReleaseOut, status_code=201)
async def rollback(
    ver: str, session: SessionDep, user: CurrentUserDep, body: Annotated[RollbackIn, Body()] = RollbackIn()
) -> Any:
    """回滚最近一次发布：生成新版本（内容同上一版），不是最新版 → 409 ROLLBACK_NOT_LATEST。"""
    new_ver = await PublishService(session).rollback(user, ver, body.note)
    return ReleaseOut.model_validate(await ReleaseRepo(session).by_ver_or_404(new_ver))


# ---------------------------------------------------------------- 变更单（Releases.vue 列表 / 详情 / 按钮）
CRStatusQ = Literal["草稿", "审核中", "已通过", "已发布", "已驳回"]


@get("/change-requests", "releases")
async def list_change_requests(
    session: SessionDep,
    _: CurrentUserDep,
    status: CRStatusQ | None = None,
    full: bool = False,
    kw: str = "",
    page: Annotated[int | None, Query()] = None,
    size: Annotated[int | None, Query(ge=1, le=MAX_PAGE_SIZE)] = None,
    focus: Focus = None,
) -> Any:
    """mock CHANGE_REQUESTS 原样（单号倒序）。full=true 每行带 detail（CRDetail）和 events（评审追加记录）。

    - `kw`：同 Releases.vue `filtered`——标题包含 kw，或单号包含 kw 的大写。
    - 带了 `page` / `size` / `focusId` 任一个 → 分页，返回骨架 `Page`（`{items,total,page,size}`，
      每页默认 17 = `Releases.vue:59` LPAGE）；`focusId` 在筛选结果里时返回它所在的页
      （同 `Releases.vue:67`），越界页收到最后一页。
    - 都不带 → 返回整个数组（Releases.vue 真接口模式整表载入后本地筛选、分页，保持兼容）[推断]。
    不随 ?ver 变（变更单不进版本快照）[推断]。"""
    rows = await ChangeRequestService(session).list(status=status, full=full)
    if kw:
        up = kw.upper()
        rows = [r for r in rows if kw in r["title"] or up in r["id"]]
    if page is None and size is None and focus is None:
        return rows
    return paginate(rows, PageParams(page=page or 1, size=size or 17), focus)


@get("/change-requests/{id}", "releases")
async def get_change_request(id: str, session: SessionDep, _: CurrentUserDep) -> Any:
    """一行 + detail（CRDetail 整份）+ events。"""
    return await ChangeRequestService(session).detail(id)


async def _move(id: str, action: str, session: Any, user: Any, reason: str | None = None) -> Any:
    svc = ChangeRequestService(session)
    await svc.transition(id, action, user, reason)
    return await svc.detail(id)


@router.post("/change-requests/{id}:submit", dependencies=_MANAGE)
async def submit_change_request(id: str, session: SessionDep, user: CurrentUserDep) -> Any:
    """草稿 → 审核中（不限提交人；有阻断 / 没改动 → 409）。返回更新后的详情。"""
    return await _move(id, "submit", session, user)


@router.post("/change-requests/{id}:withdraw", dependencies=_MANAGE)
async def withdraw_change_request(id: str, session: SessionDep, user: CurrentUserDep) -> Any:
    """审核中 → 草稿（owner）。"""
    return await _move(id, "withdraw", session, user)


@router.post("/change-requests/{id}:approve", dependencies=_MANAGE)
async def approve_change_request(id: str, session: SessionDep, user: CurrentUserDep) -> Any:
    """审核中 → 已通过（reviewer 且不是 owner；有阻断 → 409）。"""
    return await _move(id, "approve", session, user)


@router.post("/change-requests/{id}:reject", dependencies=_MANAGE)
async def reject_change_request(
    id: str, session: SessionDep, user: CurrentUserDep, body: Annotated[RejectIn, Body()] = RejectIn()
) -> Any:
    """审核中 → 已驳回（reviewer 且不是 owner；rejectReason 必填）。"""
    return await _move(id, "reject", session, user, body.reject_reason)


@router.post("/change-requests/{id}:unapprove", dependencies=_MANAGE)
async def unapprove_change_request(id: str, session: SessionDep, user: CurrentUserDep) -> Any:
    """已通过 → 草稿（owner 或 reviewer）。"""
    return await _move(id, "unapprove", session, user)


@router.post("/change-requests/{id}:reopen", dependencies=_MANAGE)
async def reopen_change_request(id: str, session: SessionDep, user: CurrentUserDep) -> Any:
    """已驳回 → 草稿（owner 或 reviewer）。"""
    return await _move(id, "reopen", session, user)


@router.delete("/change-requests/{id}", dependencies=_MANAGE, status_code=204)
async def delete_change_request(id: str, session: SessionDep, user: CurrentUserDep) -> None:
    """删除草稿（owner；硬删 [推断]）。"""
    await ChangeRequestService(session).delete(id, user)
