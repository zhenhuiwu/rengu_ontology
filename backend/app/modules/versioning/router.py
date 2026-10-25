"""版本接口（只读）。发布本身由静态模块的「发布」接口在事务里调用 create_version，不在这里暴露。

前端对应：VersionPicker（版本列表）、Releases 页「当前版本 / 待发布版本」、`?ver=` 历史查看。
"""

from typing import Annotated

from fastapi import APIRouter, Depends

from app.common.auth import CurrentUserDep
from app.common.pagination import Page, PageParamsDep
from app.core import clock
from app.db.session import SessionDep
from app.modules.versioning.repo import ReleaseRepo
from app.modules.versioning.schemas import CurrentVersionOut, ReleaseDetailOut, ReleaseOut
from app.modules.versioning.service import VersioningService, get_version_service, next_ver

router = APIRouter(prefix="/releases", tags=["本体版本"])
ServiceDep = Annotated[VersioningService, Depends(get_version_service)]


@router.get("", response_model=Page[ReleaseOut], summary="版本列表（新→旧，第一条是当前版本）")
async def list_releases(session: SessionDep, page: PageParamsDep, _: CurrentUserDep) -> Page[ReleaseOut]:
    rows, total = await ReleaseRepo(session).page(page)
    return Page[ReleaseOut](
        items=[ReleaseOut.model_validate(r) for r in rows], total=total, page=page.page, size=page.size
    )


@router.get("/current", response_model=CurrentVersionOut, summary="当前版本与下一个版本号")
async def current_release(session: SessionDep, _: CurrentUserDep) -> CurrentVersionOut:
    latest = await ReleaseRepo(session).latest()
    cur = latest.ver if latest else None
    return CurrentVersionOut(
        current=cur,
        next=_next_from(cur) if cur else next_ver(None, clock.now()),
        release=ReleaseOut.model_validate(latest) if latest else None,
    )


def _next_from(ver: str) -> str:
    """待发布版本号按当前版本同月 +1 显示（前端 NEXT_VERSION）；真正的号在发布时按当时月份确定。"""
    head, n = ver.rsplit(".", 1)
    return f"{head}.{int(n) + 1}"


@router.get("/{ver}", response_model=ReleaseDetailOut, summary="单个版本详情")
async def get_release(ver: str, session: SessionDep, _: CurrentUserDep) -> ReleaseDetailOut:
    r = await ReleaseRepo(session).get_or_404(ver)
    return ReleaseDetailOut.model_validate(r)


@router.get("/{ver}/snapshot", summary="版本快照（?ver= 只读查看用）")
async def get_release_snapshot(ver: str, session: SessionDep, svc: ServiceDep, _: CurrentUserDep) -> dict:
    await ReleaseRepo(session).get_or_404(ver)
    return await svc.get_snapshot(session, ver) or {}
