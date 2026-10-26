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
| POST /change-requests、PATCH /change-requests/{id}、POST/DELETE /change-requests/{id}/targets、
  PUT/GET /change-requests/{id}/items、POST /change-requests/{id}:validate、GET /change-requests/{id}/diffs、
  GET /change-requests/draft-targets | 定义页发起变更 / 加入 / 保存草稿 / 放弃、Releases.vue 校验 |
"""

from collections.abc import Callable
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Body, Depends, Path, Query

from app.common.auth import CurrentUserDep, require_func, resolve_user
from app.common.context import ViewContextDep, allow_in_readonly
from app.common.pagination import MAX_PAGE_SIZE, PageParams
from app.db.session import SessionDep
from app.modules.admin.permissions import active_user_names
from app.modules.static.cr_write import CrWriteService
from app.modules.static.crs import ChangeRequestService
from app.modules.static.knowledge_trial import KnowledgeResolveIn, resolve_knowledge
from app.modules.static.metric_trial import MetricTrialIn, trial_metric
from app.modules.static.metric_versions import MetricRollbackIn
from app.modules.static.metric_versions import rollback as rollback_metric
from app.modules.static.models import CRStatus
from app.modules.static.overview import overview, todos
from app.modules.static.permissions import check_target
from app.modules.static.publish import PublishService
from app.modules.static.schemas import (
    AllocateTargetIn,
    CRCreateIn,
    CRPatchIn,
    ItemsPutIn,
    ItemsPutOut,
    MergeCheckOut,
    PublishIn,
    PublishOut,
    Rec,
    RecPage,
    RejectIn,
    RollbackIn,
    SynonymConflicts,
    TargetIn,
    TargetKind,
    ValidateOut,
)
from app.modules.static.service import StaticService
from app.modules.static.trials import DimensionMatchIn, match_dimension
from app.modules.versioning.repo import ReleaseRepo
from app.modules.versioning.schemas import ReleaseOut

router = APIRouter(tags=["static"])


@router.post("/metrics/{id}:trial", dependencies=[Depends(require_func("metrics", "view"))])
@allow_in_readonly
async def metric_trial(
    id: Annotated[str, Path(min_length=1, max_length=64, pattern=r"^[^\x00]+$")],
    body: MetricTrialIn,
    session: SessionDep,
    ctx: ViewContextDep,
    user: CurrentUserDep,
) -> Any:
    return await trial_metric(session, ctx, user, id, body)


@router.post("/knowledge:resolve", dependencies=[Depends(require_func("glossary", "view"))])
@allow_in_readonly
async def knowledge_resolve(
    body: KnowledgeResolveIn, session: SessionDep, ctx: ViewContextDep, user: CurrentUserDep
) -> Any:
    return await resolve_knowledge(session, ctx, user, body)


@router.post("/dimensions:match", dependencies=[Depends(require_func("dimensions", "view"))])
@allow_in_readonly
async def dimension_match(
    body: DimensionMatchIn, session: SessionDep, ctx: ViewContextDep, user: CurrentUserDep
) -> Any:
    return await match_dimension(session, ctx, user, body)


@router.get("/build/owners", summary="新建构建资源的负责人候选，仅启用用户的编号和姓名")
async def build_owners(
    kind: Literal["object", "metric", "dimension", "action", "event"],
    session: SessionDep,
    user: CurrentUserDep,
) -> list[dict[str, str]]:
    check_target(user, kind)
    return await active_user_names(session)


async def _svc(session: SessionDep, ctx: ViewContextDep, user: CurrentUserDep) -> StaticService:
    """所有静态 GET 都要登录：未知 / 停用的 X-Demo-As → 401（app.common.auth）。"""
    return StaticService(session, ctx, user)


# 功能权限（api-index 权限表）：页面 key·view；
# /object-types/brief 是多页共用的对象下拉，表里记 data-quality·view
FUNC_OF: dict[str, str] = {}


def get(path: str, func: str, **kw: Any) -> Any:
    FUNC_OF[path] = func
    return router.get(path, dependencies=[Depends(require_func(func, "view"))], **kw)


Svc = Annotated[StaticService, Depends(_svc)]
Kw = Annotated[str, Query()]
Focus = Annotated[str | None, Query(alias="focusId")]
DraftId = Annotated[str | None, Query(alias="crId", max_length=64, pattern=r"^[^\x00]+$")]


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
async def list_object_types(svc: Svc, p: P10, kw: Kw = "", focus_id: Focus = None) -> Any:
    return await svc.object_list(kw, p, focus_id)


@get("/object-types/brief", "data-quality", response_model=list[Rec])
async def object_types_brief(svc: Svc) -> Any:
    return await svc.object_brief()


@get("/object-types/{id}", "object-types", response_model=Rec)
async def get_object_type(id: str, svc: Svc) -> Any:
    return await svc.object_detail(id)


@get("/object-types/{id}/properties", "object-types", response_model=RecPage)
async def object_properties(id: str, svc: Svc, p: P10, kw: Kw = "", cr_id: DraftId = None) -> Any:
    return await svc.object_properties(id, kw, p, cr_id)


@get("/object-types/{id}/links", "object-types", response_model=list[Rec])
async def object_links(id: str, svc: Svc) -> Any:
    return await svc.object_links(id)


@get("/object-types/{id}/mappings", "object-types", response_model=RecPage)
async def object_mappings(id: str, svc: Svc, p: P10, cr_id: DraftId = None) -> Any:
    return await svc.object_mappings(id, p, cr_id)


@get("/object-types/{id}/usage", "object-types", response_model=RecPage)
async def object_usage(
    id: str,
    svc: Svc,
    p: P10,
    kind: Literal["metric", "view", "cr"],
    q: Kw = "",
    filter: Kw = "",
    cr_id: DraftId = None,
    affected_only: Annotated[bool, Query(alias="affectedOnly")] = False,
) -> Any:
    return await svc.object_usage(id, kind, q, filter, p, cr_id, affected_only)


# ---------------------------------------------------------------- 总览（OntologyOverview.vue）


@get("/ontology/overview", "ontology-overview")
async def ontology_overview(svc: Svc, user: CurrentUserDep) -> Any:
    return await overview(svc, user)


@get("/ontology/todos", "ontology-overview")
async def ontology_todos(
    svc: Svc, user: CurrentUserDep, p: P8, kind: Literal["全部", "变更", "数据质量"] = "全部"
) -> Any:
    return await todos(svc, kind, p, user)


@get("/ontology/graph", "ontology-overview")
async def ontology_graph(svc: Svc) -> Any:
    """`{nodes: LinkObject[], edges: {from, to, linkId, status}[]}`（api-index §3.4）。"""
    return await svc.graph()


# ---------------------------------------------------------------- 关系类型（LinkTypes.vue）


@get("/link-types", "link-types", response_model=RecPage)
async def list_link_types(
    svc: Svc,
    p: P10,
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


@get("/link-types/catalog", "link-types")
async def link_catalog(svc: Svc) -> Any:
    from app.modules.static.read_pages import catalog

    return await catalog(svc)


@get("/link-types/{id}", "link-types", response_model=Rec)
async def get_link_type(id: str, svc: Svc) -> Any:
    return await svc.link_detail(id)


@get("/link-types/{id}/quality", "link-types", response_model=Rec)
async def link_quality(id: str, svc: Svc) -> Any:
    return await svc.link_quality(id)


@get("/link-types/{id}/usage", "link-types", response_model=RecPage)
async def link_usage(id: str, svc: Svc, p: P10) -> Any:
    return await svc.link_usage(id, p)


@get("/link-types/{id}/history", "link-types", response_model=RecPage)
async def link_history(id: str, svc: Svc, p: P10) -> Any:
    from app.modules.static.read_pages import link_history

    return await link_history(svc, id, p)


# ---------------------------------------------------------------- 指标（Metrics.vue）


@get("/metrics", "metrics", response_model=RecPage)
async def list_metrics(svc: Svc, p: P10, kw: Kw = "", group: Kw = "", focus_id: Focus = None) -> Any:
    return await svc.metric_list(kw, group, p, focus_id)


@get("/metrics/{id}", "metrics", response_model=Rec)
async def get_metric(id: str, svc: Svc) -> Any:
    return await svc.metric_detail(id)


@get("/metrics/{id}/versions", "metrics", response_model=RecPage)
async def metric_versions(id: str, svc: Svc, p: P10) -> Any:
    return await svc.metric_versions(id, p)


@get("/metrics/{id}/refs", "metrics", response_model=RecPage)
async def metric_refs(id: str, svc: Svc, p: P10) -> Any:
    return await svc.metric_refs(id, p)


@router.post("/metrics/{id}:rollback", dependencies=[Depends(require_func("metrics", "manage"))])
async def metric_rollback(
    id: Annotated[str, Path(min_length=1, max_length=64, pattern=r"^[^\x00]+$")],
    body: MetricRollbackIn,
    session: SessionDep,
    user: CurrentUserDep,
) -> Any:
    return await rollback_metric(session, user, id, body)


# ---------------------------------------------------------------- 维度与码表（Dimensions.vue）


@get("/dimensions", "dimensions", response_model=RecPage)
async def list_dimensions(
    svc: Svc,
    p: P10,
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
    cr_id: DraftId = None,
) -> Any:
    return await svc.dim_values(id, level, q, status, p, cr_id)


@get("/dimensions/{id}/mappings", "dimensions", response_model=RecPage)
async def dimension_mappings(
    id: str,
    svc: Svc,
    p: P10,
    conflict: bool = False,
    cr_id: DraftId = None,
) -> Any:
    return await svc.dim_mappings(id, conflict, p, cr_id)


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
    p: P10,
    q: Kw = "",
    type: Kw = "",
    st: Kw = "",
    focus_id: Focus = None,
) -> Any:
    return await svc.knowledge_list(q, type, st, p, focus_id)


@get("/knowledge/synonym-conflicts", "glossary", response_model=SynonymConflicts)
async def synonym_conflicts(svc: Svc, cr_id: DraftId = None) -> Any:
    from app.modules.static.read_pages import synonym_conflicts

    return await synonym_conflicts(svc, cr_id)


@get("/knowledge/{id}", "glossary", response_model=Rec)
async def get_knowledge(id: str, svc: Svc) -> Any:
    return await svc.knowledge_detail(id)


@get("/knowledge/{id}/refs", "glossary", response_model=RecPage)
async def knowledge_refs(id: str, svc: Svc, p: P10) -> Any:
    return await svc.knowledge_refs(id, p)


@get("/knowledge/{id}/history", "glossary", response_model=RecPage)
async def knowledge_history(id: str, svc: Svc, p: P10) -> Any:
    return await svc.knowledge_history(id, p)


# ---------------------------------------------------------------- 整版发布 / 回滚
# 写接口：只读模式（?ver / previewAs）下骨架统一 403 READONLY
_MANAGE = [Depends(require_func("releases", "manage"))]
_CR_VIEW = [Depends(require_func("releases", "view"))]


@router.post("/releases:merge-check", dependencies=_MANAGE, response_model=MergeCheckOut)
async def merge_check(session: SessionDep, _: CurrentUserDep) -> Any:
    """只算合并校验（mock mergeCheck(已通过的单子)），不落库。"""
    return await PublishService(session).merge_check()


@router.post("/releases", dependencies=_MANAGE, response_model=PublishOut, status_code=201)
async def publish(
    session: SessionDep, user: CurrentUserDep, body: Annotated[PublishIn, Body()] = PublishIn()
) -> Any:
    """整版发布：本体管理员。合并校验有阻断 → 409 MERGE_BLOCKED（details.rows）；
    没有已通过的单子 → 409 EMPTY_RELEASE。"""
    service = PublishService(session)
    ver = await service.publish(user, body.note, lock_version=body.lock_version)
    result = PublishOut.model_validate(await ReleaseRepo(session).by_ver_or_404(ver))
    result.skipped = service.skipped
    return result


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
    user: CurrentUserDep,
    ctx: ViewContextDep,
    status: CRStatusQ | None = None,
    full: bool = False,
    workflow: bool = False,
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
    if workflow:
        return await ChangeRequestService(session).workflow(status=status, kw=kw)
    params = (
        None
        if page is None and size is None and focus is None
        else PageParams(page=page or 1, size=size or 10)
    )
    return await ChangeRequestService(session).list(
        status=status,
        full=full,
        kw=kw,
        params=params,
        focus_id=focus,
        actor=(await resolve_user(session, ctx.preview_as)) if ctx.preview_as else user,
    )


@get("/change-requests/draft-targets", "releases")
async def draft_targets(session: SessionDep, _: CurrentUserDep, status: CRStatusQ = "草稿") -> Any:
    """{单号: {kind: [目标编码]}}，同 mock DRAFT_TARGETS（前端 draftOf 用）。必须在 /{id} 之前声明。"""
    return await CrWriteService(session).draft_targets(CRStatus(status))


@get("/change-requests/{id}", "releases")
async def get_change_request(
    id: str,
    session: SessionDep,
    user: CurrentUserDep,
    ctx: ViewContextDep,
    include_diffs: Annotated[bool, Query(alias="includeDiffs")] = True,
) -> Any:
    """一行 + detail（CRDetail 整份）+ events。"""
    actor = await resolve_user(session, ctx.preview_as) if ctx.preview_as else user
    return await ChangeRequestService(session).detail(id, actor, include_diffs=include_diffs)


async def _move(id: str, action: str, session: Any, user: Any, reason: str | None = None) -> Any:
    svc = ChangeRequestService(session)
    await svc.transition(id, action, user, reason)
    return await svc.detail(id, user)


@router.post("/change-requests/{id}:submit", dependencies=_CR_VIEW)
async def submit_change_request(id: str, session: SessionDep, user: CurrentUserDep) -> Any:
    """草稿 → 审核中（不限提交人；有阻断 / 没改动 → 409）。返回更新后的详情。"""
    return await _move(id, "submit", session, user)


@router.post("/change-requests/{id}:withdraw", dependencies=_CR_VIEW)
async def withdraw_change_request(id: str, session: SessionDep, user: CurrentUserDep) -> Any:
    """审核中 → 草稿（owner）。"""
    return await _move(id, "withdraw", session, user)


@router.post("/change-requests/{id}:approve", dependencies=_CR_VIEW)
async def approve_change_request(id: str, session: SessionDep, user: CurrentUserDep) -> Any:
    """审核中 → 已通过（reviewer 且不是 owner；有阻断 → 409）。"""
    return await _move(id, "approve", session, user)


@router.post("/change-requests/{id}:reject", dependencies=_CR_VIEW)
async def reject_change_request(
    id: str, session: SessionDep, user: CurrentUserDep, body: Annotated[RejectIn, Body()] = RejectIn()
) -> Any:
    """审核中 → 已驳回（reviewer 且不是 owner；rejectReason 必填）。"""
    return await _move(id, "reject", session, user, body.reject_reason)


@router.post("/change-requests/{id}:unapprove", dependencies=_CR_VIEW)
async def unapprove_change_request(id: str, session: SessionDep, user: CurrentUserDep) -> Any:
    """已通过 → 草稿（owner 或 reviewer）。"""
    return await _move(id, "unapprove", session, user)


@router.post("/change-requests/{id}:reopen", dependencies=_CR_VIEW)
async def reopen_change_request(id: str, session: SessionDep, user: CurrentUserDep) -> Any:
    """已驳回 → 草稿（owner 或 reviewer）。"""
    return await _move(id, "reopen", session, user)


@router.delete("/change-requests/{id}", dependencies=_CR_VIEW, status_code=204)
async def delete_change_request(id: str, session: SessionDep, user: CurrentUserDep) -> None:
    """删除草稿（owner；硬删 [推断]）。"""
    await ChangeRequestService(session).delete(id, user)


# ---------------------------------------------------------------- 通用对象浏览（static_0004，explorer.py）
from app.modules.static.explorer import ExplorerService, parse_filters  # noqa: E402


@get("/object-types/{id}/meta", "object-types", response_model=Rec)
async def object_explorer_meta(id: str, session: SessionDep, _: CurrentUserDep, ctx: ViewContextDep) -> Any:
    return await ExplorerService(session).meta(id, ctx.ver)


@get("/object-types/{id}/facets", "object-types", response_model=list[Rec])
async def object_facets(
    id: str, session: SessionDep, user: CurrentUserDep, filters: Annotated[str | None, Query()] = None
) -> Any:
    return await ExplorerService(session).facets(id, parse_filters(filters), user)


# ---------------------------------------------------------------- 变更单写接口（handoff 写接口契约）
# 目标写权限在服务层按 kind 校验；返回完整单头/内容的接口还需 releases:view。


@router.post("/change-requests", dependencies=_CR_VIEW, status_code=201)
async def create_change_request(session: SessionDep, user: CurrentUserDep, body: CRCreateIn) -> Any:
    """发起变更：新建草稿单（owner = 当前用户），可同时登记第一个目标。返回详情。"""
    code = await CrWriteService(session).create(user, body)
    return await ChangeRequestService(session).detail(code, user)


@router.patch("/change-requests/{id}", dependencies=_CR_VIEW)
async def patch_change_request(id: str, session: SessionDep, user: CurrentUserDep, body: CRPatchIn) -> Any:
    """改单头（标题 / 原因 / 审核人 / 级别 / 迁移方式），草稿才能改。"""
    await CrWriteService(session).patch(id, body, user)
    return await ChangeRequestService(session).detail(id, user)


@get("/change-requests/{id}/reviewer-options", "releases")
async def change_request_reviewer_options(id: str, session: SessionDep, user: CurrentUserDep) -> Any:
    return await CrWriteService(session).reviewer_options(id, user)


@router.post("/change-requests/{id}/targets", dependencies=_CR_VIEW)
async def add_change_request_target(
    id: str, session: SessionDep, user: CurrentUserDep, body: TargetIn
) -> Any:
    """加入已有变更单（幂等）。"""
    await CrWriteService(session).add_target(id, user, body)
    return await ChangeRequestService(session).detail(id, user)


@router.post("/change-requests/{id}/targets:allocate", dependencies=_CR_VIEW, status_code=201)
async def allocate_change_request_target(
    id: str, session: SessionDep, user: CurrentUserDep, body: AllocateTargetIn
) -> Any:
    target = await CrWriteService(session).allocate_target(id, user, body.kind, body.name)
    return {
        "id": id,
        "target": {
            "kind": target.target_kind,
            "id": target.target_id,
            "isNew": True,
            "name": target.target_name,
        },
    }


@router.delete("/change-requests/{id}/targets/{kind}/{target_id}", status_code=204)
async def discard_change_request_target(
    id: str, kind: TargetKind, target_id: str, session: SessionDep, user: CurrentUserDep
) -> None:
    """放弃本对象草稿：删掉该目标的全部草稿行和登记。"""
    await CrWriteService(session).discard_target(id, kind, target_id, user)


@router.put("/change-requests/{id}/items", response_model=ItemsPutOut)
async def put_change_request_items(
    id: str, session: SessionDep, user: CurrentUserDep, body: ItemsPutIn
) -> Any:
    """保存到草稿：同一 (目标, section) 整段替换。"""
    return await CrWriteService(session).put_items(id, user, body)


@get("/change-requests/{id}/items", "releases")
async def list_change_request_items(
    id: str,
    session: SessionDep,
    _: CurrentUserDep,
    kind: TargetKind | None = None,
    target_id: Annotated[str | None, Query(alias="targetId")] = None,
    section: str | None = None,
) -> Any:
    """读回草稿行（不分页，按保存顺序）。"""
    return await CrWriteService(session).list_items(id, kind, target_id, section)


@router.post("/change-requests/{id}:validate", dependencies=_CR_VIEW, response_model=ValidateOut)
async def validate_change_request(id: str, session: SessionDep, user: CurrentUserDep) -> Any:
    """重新校验（同步）：V1–V17 写 cr_check 和 detail.checks。"""
    return await CrWriteService(session).validate(id, user)


@get("/change-requests/{id}/diffs", "releases")
async def change_request_diffs(id: str, session: SessionDep, _: CurrentUserDep, p: P10) -> Any:
    """改动列表（种子改动 + 草稿行），每页 10（Releases.vue:83）。"""
    return await ChangeRequestService(session).diffs(id, p)
