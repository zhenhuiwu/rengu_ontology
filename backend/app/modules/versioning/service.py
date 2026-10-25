"""VersionService 的实现（契约见 app/contracts/versioning.py）。"""

import hashlib
import json
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.outbox import RELEASE_PUBLISHED, emit
from app.contracts.versioning import ReleaseInfo, Snapshot, SnapshotProvider
from app.core import clock
from app.core.config import get_settings
from app.core.errors import AppError, EmptyRelease
from app.db.dialects import get_adapter
from app.modules.admin.models import SysUser
from app.modules.versioning.models import OntologyRelease, ReleaseKind
from app.modules.versioning.repo import ReleaseRepo
from app.modules.versioning.sections import restore_sections

PUBLISH_LOCK = "ontology.publish"


def snapshot_digest(snapshot: Snapshot) -> str:
    canonical = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def next_ver(current: str | None, now: datetime) -> str:
    """vYYYY.MM.N：同月递增 N，跨月从 1 开始（按业务时区算月份）。"""
    local = now.astimezone(ZoneInfo(get_settings().display_tz))
    prefix = f"v{local:%Y.%m}."
    if current and current.startswith(prefix):
        return f"{prefix}{int(current.rsplit('.', 1)[1]) + 1}"
    return f"{prefix}1"


def _info(r: OntologyRelease) -> ReleaseInfo:
    return ReleaseInfo(
        ver=r.ver,
        parent_ver=r.parent_ver,
        published_at=r.published_at,
        published_by=r.published_by,
        note=r.note,
        change_order_ids=tuple(r.change_order_ids or ()),
        snapshot_sha256=r.snapshot_sha256,
        rollback_of=r.rollback_of,
        published_by_code=r.published_by_code,
    )


class VersioningService:
    def __init__(self, provider: SnapshotProvider | None = None) -> None:
        if provider is None:
            from app.modules.versioning.sections import SectionSnapshotProvider

            provider = SectionSnapshotProvider()
        self.provider: SnapshotProvider = provider

    async def create_version(
        self,
        session: AsyncSession,
        change_order_ids: list[str],
        *,
        rollback_of: str | None = None,
        published_by: str,
        note: str,
        now: datetime | None = None,
    ) -> str:
        """唯一入口。发布与回滚都生成新版本（只追加，不改任何旧行），同事务、不 commit。

        - 普通发布（rollback_of=None）：依次调用各模块登记的 section builder 组装快照；
        - 回滚（rollback_of=被回滚的版本号，必须是当前最新版）：**不调用** builder，
          快照原样复制它的上一版（restoredFrom），change_order_ids 是那张回滚变更单。
        """
        if not change_order_ids:
            raise EmptyRelease()
        await get_adapter(session).xact_lock(session, PUBLISH_LOCK)
        repo = ReleaseRepo(session)
        latest = await repo.latest()
        base_ver = latest.ver if latest else None
        cr_ids = list(change_order_ids)
        if rollback_of is None:
            snapshot = await self.provider.build_snapshot(session, base_ver, cr_ids)
            summary: dict = {"changeOrders": len(cr_ids)}
        else:
            # 前端规则：「只能回滚最近一次发布」
            if latest is None or latest.ver != rollback_of or latest.parent is None:
                raise AppError(
                    "只能回滚最近一次发布",
                    code="ROLLBACK_NOT_LATEST",
                    status_code=409,
                    details={"current": base_ver, "rollbackOf": rollback_of},
                )
            target = latest.parent
            snapshot = dict(target.snapshot)
            summary = {"changeOrders": len(cr_ids), "rollbackOf": rollback_of, "restoredFrom": target.ver}
        release = await self._append(
            session,
            base_ver,
            snapshot,
            cr_ids,
            summary,
            published_by=published_by,
            note=note,
            now=now,
            latest=latest,
            rollback=rollback_of is not None,
        )
        if rollback_of is not None:
            # 先写新版本行，再让各模块把定义表恢复成被恢复的那一版：钩子拿到这次回滚版本的 id
            # （Palantir 把快照里没有的类型的 retired_release_id 写成它，action-event-api.md:325）。同一事务。
            await restore_sections(session, snapshot, release.id)
        return release.ver

    async def _append(
        self,
        session: AsyncSession,
        base_ver: str | None,
        snapshot: Snapshot,
        change_order_ids: list[str],
        summary: dict,
        *,
        published_by: str,
        note: str,
        now: datetime | None,
        latest: OntologyRelease | None,
        rollback: bool = False,
    ) -> OntologyRelease:
        now = now or clock.now()
        ver = next_ver(base_ver, now)
        publisher = await session.scalar(select(SysUser).where(SysUser.code == published_by))
        if publisher is None:
            raise AppError(
                "发布人不存在", code="USER_NOT_FOUND", status_code=422, details={"userId": published_by}
            )
        release = await ReleaseRepo(session).add(
            OntologyRelease(
                ver=ver,
                parent=latest,  # parent_id = 上一版的 id
                kind=ReleaseKind.rollback if rollback else ReleaseKind.publish,
                rollback_target=latest if rollback else None,  # rollback_of_id
                snapshot=snapshot,
                snapshot_sha256=snapshot_digest(snapshot),
                change_order_ids=change_order_ids,
                change_summary=summary,
                note=note,
                publisher=publisher,
                published_at=now,
            )
        )
        # 同一事务写 outbox：其它模块只订阅 release.published（PDE 定）
        await emit(
            session,
            RELEASE_PUBLISHED,
            {
                "ver": ver,
                "parentVer": base_ver,
                "kind": "rollback" if rollback else "release",
                "rollbackOf": base_ver if rollback else None,
                "changeOrderIds": list(change_order_ids),
            },
            event_id=f"ontology_release:{release.ver}:{RELEASE_PUBLISHED}",
        )
        return release

    async def current_version(self, session: AsyncSession) -> str | None:
        latest = await ReleaseRepo(session).latest()
        return latest.ver if latest else None

    async def get_release(self, session: AsyncSession, ver: str) -> ReleaseInfo | None:
        r = await ReleaseRepo(session).by_ver(ver)
        return _info(r) if r else None

    async def get_snapshot(self, session: AsyncSession, ver: str | None = None) -> Snapshot | None:
        repo = ReleaseRepo(session)
        r = await (repo.by_ver(ver) if ver else repo.latest())
        return dict(r.snapshot) if r else None


def get_version_service() -> VersioningService:
    # 快照 = 各模块登记的 section builder 拼成（app/modules/versioning/sections.py）
    return VersioningService()
