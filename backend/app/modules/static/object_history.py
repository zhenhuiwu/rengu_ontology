"""对象涉及的实际变更单摘要，不合成修订或评审时间线。"""

from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.static.crs import ChangeRequestService
from app.modules.static.models import ChangeRequest, CrItem, CrTarget, LinkType
from app.modules.versioning.models import OntologyRelease
from app.modules.versioning.repo import ReleaseRepo


async def object_change_history(
    session: AsyncSession,
    oid: str,
    name: str,
    ver: str | None = None,
    *,
    selected_ids: list[int] | None = None,
) -> list[dict]:
    q = (
        select(ChangeRequest, OntologyRelease)
        .outerjoin(OntologyRelease, OntologyRelease.id == ChangeRequest.release_id)
        .order_by(ChangeRequest.status_at.desc(), ChangeRequest.code.desc())
    )
    if selected_ids is not None:
        if not selected_ids:
            return []
        q = q.where(ChangeRequest.id.in_(selected_ids))
    if ver:
        head = await ReleaseRepo(session).by_ver_or_404(ver)
        q = q.where(OntologyRelease.published_at <= head.published_at)
        links = (head.snapshot or {}).get("links") or {}
        link_ids = {code for code, row in links.items() if oid in (row.get("from"), row.get("to"))}
    else:
        link_ids = set(
            await session.scalars(
                select(LinkType.code).where((LinkType.from_code == oid) | (LinkType.to_code == oid))
            )
        )
    records = list(await session.execute(q))
    cr_ids = [cr.id for cr, _ in records]
    if not cr_ids:
        return []
    parts: dict[int, set[str]] = defaultdict(set)
    for target in await session.scalars(select(CrTarget).where(CrTarget.cr_id.in_(cr_ids))):
        if target.target_kind == "object" and target.target_id == oid:
            parts[target.cr_id].add("对象类型")
        elif target.target_kind == "link" and target.target_id in link_ids:
            parts[target.cr_id].add("关系")
    for item in await session.scalars(select(CrItem).where(CrItem.cr_id.in_(cr_ids))):
        if item.target_kind == "object" and item.target_id == oid:
            parts[item.cr_id].add(
                "属性"
                if item.section == "properties"
                else "生命周期"
                if item.section.startswith("lc_")
                else "对象类型"
            )
        elif (
            item.target_kind == "link"
            and item.section in {"def", "config"}
            and item.item_key in {"from", "to"}
            and oid in (item.before, item.after)
        ):
            parts[item.cr_id].add("关系")
    out = []
    for cr, release in records:
        detail = cr.detail or {}
        for touch in detail.get("touches") or []:
            if not isinstance(touch, str):
                continue
            if touch == f"object:{oid}":
                parts[cr.id].add("对象类型")
            elif touch.startswith(f"property:{oid}."):
                parts[cr.id].add("属性")
            elif touch.startswith(f"relation:{oid}."):
                parts[cr.id].add("关系")
        if cr.is_seed:
            for diff in detail.get("diffs") or []:
                target = diff.get("target", "")
                if target == name or target.startswith(name + "."):
                    parts[cr.id].add(diff.get("type") or "对象类型")
        if parts[cr.id]:
            out.append(
                {
                    "cr": ChangeRequestService.row(cr, release.ver if release else None),
                    "parts": sorted(parts[cr.id]),
                }
            )
    return out
