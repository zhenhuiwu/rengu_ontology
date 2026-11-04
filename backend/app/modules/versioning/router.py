"""版本接口（只读）。发布本身由静态模块的「发布」接口在事务里调用 create_version，不在这里暴露。

前端对应：VersionPicker（版本列表）、Releases 页「当前版本 / 待发布版本」、`?ver=` 历史查看。
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.common.auth import CurrentUserDep, resolve_user
from app.common.pagination import MAX_PAGE_SIZE, PageParams
from app.core import clock
from app.db.session import SessionDep
from app.modules.versioning.repo import ReleaseRepo
from app.modules.versioning.schemas import CurrentVersionOut, ReleaseDetailOut, ReleaseListOut, ReleaseOut
from app.modules.versioning.service import VersioningService, get_version_service, next_ver

router = APIRouter(prefix="/releases", tags=["本体版本"])
ServiceDep = Annotated[VersioningService, Depends(get_version_service)]


@router.get("", response_model=ReleaseListOut, response_model_exclude_unset=True, summary="正式发布列表")
async def list_releases(
    session: SessionDep,
    _: CurrentUserDep,
    page: int | None = None,
    size: Annotated[int | None, Query(ge=1, le=MAX_PAGE_SIZE)] = None,
    kw: Annotated[str | None, Query(max_length=200, pattern=r"^[^\x00]*$")] = None,
) -> ReleaseListOut:
    """Explicit paging/search reads formal releases only; omitted parameters retain the full picker catalog."""
    repo = ReleaseRepo(session)
    if page is not None or size is not None or kw is not None:
        params = PageParams(page=page if page is not None else 1, size=size if size is not None else 20)
        rows, total = await repo.formal_page(params, kw or "")
        actual = params.clamp(total)
        return ReleaseListOut(
            items=[ReleaseOut.model_validate(r) for r in rows], current=await repo.latest_formal_ver(),
            total=total, page=actual.page, size=actual.size,
        )
    rows = await repo.formal()
    items = [ReleaseOut.model_validate(r) for r in rows]
    return ReleaseListOut(
        items=items,
        current=items[0].ver if items else None,
    )


@router.get("/current", response_model=CurrentVersionOut, summary="当前版本与下一个版本号")
async def current_release(session: SessionDep, _: CurrentUserDep) -> CurrentVersionOut:
    latest = await ReleaseRepo(session).latest()
    formal = await ReleaseRepo(session).formal()
    cur = latest.ver if latest else None
    return CurrentVersionOut(
        current=cur,
        next=_next_from(cur) if cur else next_ver(None, clock.now()),
        release=ReleaseOut.model_validate(latest) if latest else None,
        lock_version=latest.id if latest else 0,
        formal_current=formal[0].ver if formal else None,
        formal_release=ReleaseOut.model_validate(formal[0]) if formal else None,
    )


def _next_from(ver: str) -> str:
    """待发布版本号按当前版本同月 +1 显示（前端 NEXT_VERSION）；真正的号在发布时按当时月份确定。"""
    head, n = ver.rsplit(".", 1)
    return f"{head}.{int(n) + 1}"


@router.get("/{ver}", response_model=ReleaseDetailOut, summary="单个版本详情")
async def get_release(ver: str, session: SessionDep, _: CurrentUserDep) -> ReleaseDetailOut:
    r = await ReleaseRepo(session).by_ver_or_404(ver)
    return ReleaseDetailOut.model_validate(r)


@router.get("/{ver}/snapshot", summary="版本快照（?ver= 只读查看用）")
async def get_release_snapshot(ver: str, session: SessionDep, svc: ServiceDep, user: CurrentUserDep, preview_as: Annotated[str | None, Query(alias="previewAs")] = None) -> dict:
    await ReleaseRepo(session).by_ver_or_404(ver)
    snapshot = await svc.get_snapshot(session, ver) or {}
    if user.func.get("functions") not in ("view", "manage"):
        snapshot.pop("functions", None)
    viewer = await resolve_user(session, preview_as) if preview_as else user
    if any(
        principal.ontology_role != "owner"
        or principal.func.get("connections") != "manage"
        or principal.func.get("object-types") != "manage"
        for principal in (user, viewer)
    ):
        snapshot.pop("objectBindings", None)
    return snapshot
