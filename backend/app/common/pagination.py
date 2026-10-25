"""分页（初版形状，EXTENSION POINT (PDE)：PDE 可调整默认值、上限和 cursor 编码）。

PDE 定的规则：
- 页面上有「‹ 1/N ›」或页码的列表 → 页码分页：`?page=1&size=20` → `{items, total, page, size}`
  （前端 mock paginate() 只返回 items、total；多出的 page、size 不影响前端）
- 只有**页面本来就是游标翻页**的（审计日志等）才用游标分页：`?cursor=&size=` → `{items, nextCursor}`，
  cursor 是不透明字符串，nextCursor=null 表示到底
细节（默认值、上限、cursor 编码）由 PDE 定，这里保持最小。

页码越界按前端 mock 收拢（src/mock/paginate.ts:12-13）：
`page = min(max(1, page), max(1, ceil(total / size)))`。超过最后一页 → 返回最后一页的数据，响应里的
`page` 也是收拢后的页码；page ≤ 0 → 第 1 页（不报 422）；
total = 0 → page = 1、items = []。所以必须**先数 total、再算 offset**：
用 `BaseRepository.list_page`（先 count 再取这一页，两条语句）+ `Page.of(...)`；自己拼 SQL 的用
`page.clamp(total).offset`。
"""

from collections.abc import Sequence
from typing import Annotated, Any, Generic, Self, TypeVar

from fastapi import Depends, Query
from pydantic import Field, field_validator

from app.core.schema import ApiModel

T = TypeVar("T")

MAX_PAGE_SIZE = 200


class PageParams(ApiModel):
    page: int = 1
    size: int = Field(20, ge=1, le=MAX_PAGE_SIZE)

    @field_validator("page")
    @classmethod
    def _page_at_least_1(cls, v: int) -> int:
        """page < 1（0、负数）当第 1 页，不报 422（前端 paginate.ts:13 `Math.max(1, q.page)`）。"""
        return max(1, v)

    @property
    def offset(self) -> int:
        """按**请求的** page 算，**不收拢**——不要直接拿它切片！

        自己切片 / 拼 SQL 的模块**必须先 `p = page.clamp(total)`**，再用 `p.offset`、`p.size`，响应里的
        `page` 也用 `p.page`（或直接 `Page.of(rows, total, page)`，它内部会 clamp）。直接用未收拢的
        offset：越界页返回空列表、响应 page 和数据对不上，和前端 paginate.ts:13 不一致。
        `BaseRepository.list_page` 已经先 count 再 clamp，用它就不用自己算。
        """
        return (self.page - 1) * self.size

    def clamp(self, total: int) -> "PageParams":
        """按 total 收拢页码（前端 paginate.ts:13）：越界 → 最后一页；total=0 → 第 1 页。"""
        return PageParams(page=min(self.page, page_count(total, self.size)), size=self.size)


def page_count(total: int, size: int) -> int:
    """总页数，至少 1（前端 paginate.ts:12 `Math.max(1, Math.ceil(total / size))`）。"""
    return max(1, -(-total // size))


def _page_params(
    page: Annotated[int, Query()] = 1,
    size: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = 20,
) -> PageParams:
    # page 不设下限：0、负数在 PageParams 里收成 1。size 照旧校验（前端写死的值，不是用户输入）。
    return PageParams(page=page, size=size)


PageParamsDep = Annotated[PageParams, Depends(_page_params)]


class Page(ApiModel, Generic[T]):
    items: list[T]
    total: int
    page: int
    size: int

    @classmethod
    def of(cls, items: Sequence[Any], total: int, params: PageParams, **extra: Any) -> Self:
        """组装响应：page 用收拢后的页码（和 list_page 取的那一页一致）。extra 给子类多出的字段。"""
        p = params.clamp(total)
        return cls(items=list(items), total=total, page=p.page, size=p.size, **extra)


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
