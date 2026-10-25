"""VersionService 的实现（契约见 app/contracts/versioning.py）。"""

import hashlib
import json
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from app.contracts.versioning import ReleaseInfo, Snapshot, SnapshotProvider
from app.core import clock
from app.core.config import get_settings
from app.core.errors import AppError, NotFound
from app.db.dialects import get_adapter
from app.modules.versioning.models import OntRelease, ReleaseKind
from app.modules.versioning.repo import ReleaseRepo

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


def _info(r: OntRelease) -> ReleaseInfo:
    return ReleaseInfo(
        ver=r.ver,
        parent_ver=r.parent_ver,
        published_at=r.published_at,
        published_by=r.published_by,
        note=r.note,
        change_order_ids=tuple(r.change_order_ids or ()),
        snapshot_sha256=r.snapshot_sha256,
        rollback_of=r.rollback_of,
    )


class EmptySnapshotProvider:
    """占位：静态模块实现 SnapshotProvider 之前，沿用上一版快照并记录变更单号。"""

    async def build_snapshot(
        self, session: AsyncSession, base_ver: str | None, change_order_ids: list[str]
    ) -> Snapshot:
        base = await VersioningService(self).get_snapshot(session, base_ver) if base_ver else None
        return dict(base or {})


class VersioningService:
    def __init__(self, provider: SnapshotProvider | None = None) -> None:
        self.provider: SnapshotProvider = provider or EmptySnapshotProvider()

    async def create_version(
        self,
        session: AsyncSession,
        change_order_ids: list[str],
        *,
        published_by: str,
        note: str,
        now: datetime | None = None,
    ) -> str:
        # [推断] 前端发布按钮只在有已通过的变更单时可用，这里同样要求至少一张
        if not change_order_ids:
            raise AppError("发布至少要包含一张变更单", code="EMPTY_RELEASE")
        await get_adapter(session).xact_lock(session, PUBLISH_LOCK)
        latest = await ReleaseRepo(session).latest()
        base_ver = latest.ver if latest else None
        snapshot = await self.provider.build_snapshot(session, base_ver, list(change_order_ids))
        return await self._append(
            session,
            base_ver,
            snapshot,
            list(change_order_ids),
            {"changeOrders": len(change_order_ids)},
            published_by=published_by,
            note=note,
            now=now,
        )

    async def create_version_from(
        self,
        session: AsyncSession,
        target_ver: str,
        rollback_cr_id: str,
        *,
        published_by: str,
        note: str,
        now: datetime | None = None,
    ) -> str:
        """整版回滚：生成新版本，快照原样复制 target_ver。只追加，不改任何旧行。"""
        await get_adapter(session).xact_lock(session, PUBLISH_LOCK)
        repo = ReleaseRepo(session)
        latest = await repo.latest()
        target = await repo.get(target_ver)
        if target is None:
            raise NotFound("版本不存在", details={"ver": target_ver})
        # 前端规则：「只能回滚最近一次发布」，回滚到的就是当前版本的上一版
        if latest is None or latest.parent_ver != target_ver:
            raise AppError(
                "只能回滚最近一次发布",
                code="ROLLBACK_NOT_LATEST",
                status_code=409,
                details={"current": latest and latest.ver, "allowedTarget": latest and latest.parent_ver},
            )
        return await self._append(
            session,
            latest.ver,
            dict(target.snapshot),
            [rollback_cr_id],
            {"changeOrders": 1, "rollbackOf": latest.ver, "restoredFrom": target_ver},
            published_by=published_by,
            note=note,
            now=now,
            rollback_of=latest.ver,
        )

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
        rollback_of: str | None = None,
    ) -> str:
        now = now or clock.now()
        ver = next_ver(base_ver, now)
        await ReleaseRepo(session).add(
            OntRelease(
                ver=ver,
                parent_ver=base_ver,
                kind=ReleaseKind.rollback if rollback_of else ReleaseKind.publish,
                rollback_of=rollback_of,
                snapshot=snapshot,
                snapshot_sha256=snapshot_digest(snapshot),
                change_order_ids=change_order_ids,
                change_summary=summary,
                note=note,
                published_by=published_by,
                published_at=now,
            )
        )
        return ver

    async def current_version(self, session: AsyncSession) -> str | None:
        latest = await ReleaseRepo(session).latest()
        return latest.ver if latest else None

    async def get_release(self, session: AsyncSession, ver: str) -> ReleaseInfo | None:
        r = await ReleaseRepo(session).get(ver)
        return _info(r) if r else None

    async def get_snapshot(self, session: AsyncSession, ver: str | None = None) -> Snapshot | None:
        repo = ReleaseRepo(session)
        r = await (repo.get(ver) if ver else repo.latest())
        return dict(r.snapshot) if r else None


def get_version_service() -> VersioningService:
    # 静态模块实现 SnapshotProvider 后，在这里注入：VersioningService(StaticSnapshotProvider())
    return VersioningService()
