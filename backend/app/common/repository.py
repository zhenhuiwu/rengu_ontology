"""通用数据访问基类（骨架负责）。模块的 repo.py 继承它，只写本模块特有的查询。

提供：get / get_or_404 / list_page / create / update（带乐观锁）/ soft_delete。
- 软删除：模型带 deleted_at 时，查询默认排除已删除行；
- 乐观锁：模型带 lock_version 时，update 需传入客户端看到的 lock_version，不一致抛 VERSION_CONFLICT；
- 不 commit：事务由请求依赖统一提交。
"""

from collections.abc import Sequence
from typing import Any, Generic, TypeVar

from sqlalchemy import Select, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.exc import StaleDataError

from app.common.pagination import PageParams
from app.core import clock
from app.core.errors import Conflict, NotFound, VersionConflict
from app.db.base import Base

M = TypeVar("M", bound=Base)


class BaseRepository(Generic[M]):
    model: type[M]
    not_found_message = "记录不存在"

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ---- 查询 ----
    def base_query(self) -> Select[tuple[M]]:
        stmt = select(self.model)
        if hasattr(self.model, "deleted_at"):
            stmt = stmt.where(self.model.deleted_at.is_(None))  # type: ignore[attr-defined]
        return stmt

    async def get(self, id_: Any) -> M | None:
        obj = await self.session.get(self.model, id_)
        if obj is not None and getattr(obj, "deleted_at", None) is not None:
            return None
        return obj

    async def get_or_404(self, id_: Any) -> M:
        obj = await self.get(id_)
        if obj is None:
            raise NotFound(self.not_found_message, details={"id": id_})
        return obj

    async def list_page(self, stmt: Select[tuple[M]], page: PageParams) -> tuple[Sequence[M], int]:
        total = await self.session.scalar(select(func.count()).select_from(stmt.order_by(None).subquery()))
        rows = (await self.session.scalars(stmt.offset(page.offset).limit(page.size))).all()
        return rows, int(total or 0)

    # ---- 写入 ----
    async def create(self, obj: M, *, actor: str | None = None) -> M:
        if hasattr(obj, "created_by"):
            obj.created_by = obj.updated_by = actor  # type: ignore[attr-defined]
        self.session.add(obj)
        try:
            await self.session.flush()
        except IntegrityError as e:
            raise Conflict("记录已存在或违反唯一约束", code="DUPLICATE_KEY") from e
        return obj

    async def update(
        self, obj: M, values: dict[str, Any], *, actor: str | None = None, lock_version: int | None = None
    ) -> M:
        if lock_version is not None and getattr(obj, "lock_version", lock_version) != lock_version:
            raise VersionConflict(details={"expected": lock_version, "actual": obj.lock_version})  # type: ignore[attr-defined]
        for k, v in values.items():
            setattr(obj, k, v)
        if hasattr(obj, "updated_by"):
            obj.updated_by = actor  # type: ignore[attr-defined]
        try:
            await self.session.flush()
        except StaleDataError as e:
            raise VersionConflict() from e
        return obj

    async def soft_delete(self, obj: M, *, actor: str | None = None) -> None:
        if not hasattr(obj, "deleted_at"):
            raise TypeError(f"{self.model.__name__} 不支持软删除")
        obj.deleted_at = clock.now()  # type: ignore[attr-defined]
        obj.deleted_by = actor  # type: ignore[attr-defined]
        await self.session.flush()
