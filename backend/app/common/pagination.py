"""分页（初版形状，EXTENSION POINT (PDE)：PDE 可调整默认值、上限和 cursor 编码）。

PDE 定的规则：
- 页面上有「‹ 1/N ›」或页码的列表 → 页码分页：`?page=1&size=20` → `{items, total, page, size}`
  （前端 mock paginate() 只返回 items、total；多出的 page、size 不影响前端）
- 只有**页面本来就是游标翻页**的（审计日志等）才用游标分页：`?cursor=&size=` → `{items, nextCursor}`，
  cursor 是不透明字符串，nextCursor=null 表示到底
细节（默认值、上限、cursor 编码）由 PDE 定，这里保持最小。
"""

from typing import Annotated, Generic, TypeVar

from fastapi import Depends, Query
from pydantic import Field

from app.core.schema import ApiModel

T = TypeVar("T")

MAX_PAGE_SIZE = 200


class PageParams(ApiModel):
    page: int = Field(1, ge=1)
    size: int = Field(20, ge=1, le=MAX_PAGE_SIZE)

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.size


def _page_params(
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = 20,
) -> PageParams:
    return PageParams(page=page, size=size)


PageParamsDep = Annotated[PageParams, Depends(_page_params)]


class Page(ApiModel, Generic[T]):
    items: list[T]
    total: int
    page: int
    size: int


class CursorPage(ApiModel, Generic[T]):
    items: list[T]
    next_cursor: str | None = None


class CursorParams(ApiModel):
    cursor: str | None = None
    size: int = Field(20, ge=1, le=MAX_PAGE_SIZE)


def _cursor_params(
    cursor: Annotated[str | None, Query()] = None,
    size: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = 20,
) -> CursorParams:
    return CursorParams(cursor=cursor or None, size=size)


CursorParamsDep = Annotated[CursorParams, Depends(_cursor_params)]
