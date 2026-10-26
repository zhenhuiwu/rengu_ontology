"""变更目标对应定义页面；整单操作必须覆盖全部目标，包含旧快照里的 touches。"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.auth import CurrentUser, check_func
from app.core.errors import Forbidden
from app.modules.static.models import ChangeRequest, CrItem, CrTarget

TARGET_PAGE = {
    "object": "object-types",
    "link": "link-types",
    "metric": "metrics",
    "dimension": "dimensions",
    "knowledge": "glossary",
    "action": "action-types",
    "event": "event-types",
    "rule": "rules",
    "function": "functions",
    "quality": "data-quality",
    # 已有种子 detail.touches 的细分资源名，不是额外的写入目标种类。
    "property": "object-types",
    "relation": "link-types",
    "hierarchy": "dimensions",
    "objectset": "object-types",
    "lifecycle": "object-types",
}


def cr_owner_id(cr: ChangeRequest) -> str | None:
    return cr.created_by if not cr.is_seed else None


def is_cr_owner(cr: ChangeRequest, user: CurrentUser) -> bool:
    owner_id = cr_owner_id(cr)
    return owner_id == user.id if owner_id else cr.owner == user.name


def check_target(user: CurrentUser, kind: str, level: str = "manage") -> None:
    page = TARGET_PAGE.get(kind)
    if page is None:
        raise Forbidden("无法确认变更目标的页面权限", details={"kind": kind})
    check_func(user, page, level)


async def cr_target_kinds(session: AsyncSession, cr: ChangeRequest) -> set[str]:
    kinds = set(await session.scalars(select(CrTarget.target_kind).where(CrTarget.cr_id == cr.id)))
    kinds.update(await session.scalars(select(CrItem.target_kind).where(CrItem.cr_id == cr.id)))
    if cr.detail is not None and not isinstance(cr.detail, dict):
        raise Forbidden("无法确认变更单目标的页面权限")
    touches = (cr.detail or {}).get("touches") or []
    if not isinstance(touches, list):
        raise Forbidden("无法确认变更单目标的页面权限")
    for touch in touches:
        if not isinstance(touch, str) or ":" not in touch:
            raise Forbidden("无法确认变更单目标的页面权限")
        kinds.add(touch.split(":", 1)[0])
    return kinds


async def check_cr(session: AsyncSession, cr: ChangeRequest, user: CurrentUser) -> None:
    kinds = await cr_target_kinds(session, cr)
    if not kinds:
        check_func(user, "releases", "manage")
    for kind in sorted(kinds):
        check_target(user, kind)
