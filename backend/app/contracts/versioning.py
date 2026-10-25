"""契约 1：本体整版发布与版本化。

规则（团队约定）：
1. 静态模块发布时调用 `VersionService.create_version(session, change_order_ids, ...)`；
   版本层在**调用方同一个事务**里写入整版快照，返回新版本号（如 "v2026.10.4"）。
   调用方负责同一事务内把变更单置为「已发布」并 commit；任何一步失败整体回滚，版本号不会被占用。
2. 版本只追加、不可修改；“当前版本”= 最新一条，没有可移动的指针。
3. `?ver=` 只读地读取那一版的快照（`get_snapshot(ver)`）。
4. 整版回滚 = 调用 `create_version_from(session, target_ver, rollback_cr_id, ...)`：
   生成一个**新版本**，快照原样复制自 target_ver，并记录一张回滚变更单（审计用）和 rollback_of；
   仍然只追加，绝不移动指针、不改旧版本。和前端 Releases 页一致：
   「回滚会生成一个新版本，内容等于上一版本；只能回滚最近一次发布」——
   所以 target_ver 必须是当前版本的上一版（parent_ver），否则 ROLLBACK_NOT_LATEST。
5. 快照内容由静态模块提供（SnapshotProvider），版本层不理解快照里的业务结构，只负责存储、
   校验和（sha256）、版本号和并发控制（全局发布锁）。
6. 函数（Functions）**不走**本契约：单独存 fn_version 表（按函数各自递增版本，dynamic 模块负责）。
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from sqlalchemy.ext.asyncio import AsyncSession

Snapshot = dict[str, Any]


@dataclass(frozen=True)
class ReleaseInfo:
    ver: str
    parent_ver: str | None
    published_at: datetime
    published_by: str
    note: str
    change_order_ids: tuple[str, ...]
    snapshot_sha256: str
    rollback_of: str | None = None


@runtime_checkable
class SnapshotProvider(Protocol):
    """静态本体负责人实现：在 base_ver 快照基础上应用这些变更单，返回新的完整快照。"""

    async def build_snapshot(
        self, session: AsyncSession, base_ver: str | None, change_order_ids: list[str]
    ) -> Snapshot: ...


@runtime_checkable
class VersionService(Protocol):
    """本体小白实现（app/modules/versioning/service.py）。"""

    async def create_version(
        self,
        session: AsyncSession,
        change_order_ids: list[str],
        *,
        published_by: str,
        note: str,
    ) -> str:
        """同事务写快照，返回新版本号。不 commit。"""
        ...

    async def create_version_from(
        self,
        session: AsyncSession,
        target_ver: str,
        rollback_cr_id: str,
        *,
        published_by: str,
        note: str,
    ) -> str:
        """整版回滚：新版本内容 = target_ver 的快照。同事务、不 commit，返回新版本号。"""
        ...

    async def current_version(self, session: AsyncSession) -> str | None: ...

    async def get_release(self, session: AsyncSession, ver: str) -> ReleaseInfo | None: ...

    async def get_snapshot(self, session: AsyncSession, ver: str | None = None) -> Snapshot | None:
        """ver=None 表示当前版本。"""
        ...
