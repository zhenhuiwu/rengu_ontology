"""版本与预览上下文（EXTENSION POINT (PDE)）。

前端约定：
- `?ver=v2026.10.1`：看历史版本，整页只读；
- `?previewAs=<用户>`：以某用户身份只读预览（按被预览用户的有效权限返回数据）。

业务模块用法：

    async def list_x(ctx: ViewContextDep): ...          # 读接口：按 ctx.ver 取那一版的快照
    @router.post(..., dependencies=[WritableDep])       # 写接口：带 ver / previewAs 一律拒绝

桩实现：只解析参数并在写接口上拒绝；`ver` 是否存在、预览用户的权限解析由 PDE 实现。
"""

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Query

from app.core.errors import ReadonlyHistory, ReadonlyPreview


@dataclass(frozen=True)
class ViewContext:
    ver: str | None = None  # None = 当前版本
    preview_as: str | None = None

    @property
    def read_only(self) -> bool:
        return self.ver is not None or self.preview_as is not None


async def get_view_context(
    ver: Annotated[str | None, Query(description="历史本体版本，只读")] = None,
    preview_as: Annotated[str | None, Query(alias="previewAs", description="以某用户只读预览")] = None,
) -> ViewContext:
    # EXTENSION POINT (PDE): 校验 ver 存在（可调用 versioning 的 get_release），预览需要权限
    return ViewContext(ver=ver or None, preview_as=preview_as or None)


ViewContextDep = Annotated[ViewContext, Depends(get_view_context)]


async def require_writable(ctx: ViewContextDep) -> ViewContext:
    if ctx.ver:
        raise ReadonlyHistory(ctx.ver)
    if ctx.preview_as:
        raise ReadonlyPreview(ctx.preview_as)
    return ctx


WritableDep = Depends(require_writable)
