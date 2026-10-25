"""版本与预览上下文（EXTENSION POINT (PDE)）。

前端约定：
- `?ver=v2026.10.1`：看历史版本，整页只读；
- `?previewAs=<用户>`：以某用户身份只读预览（按被预览用户的有效权限返回数据）。

业务模块用法：

    async def list_x(ctx: ViewContextDep): ...          # 读接口：按 ctx.ver 取那一版的快照

**全局只读守卫**（app.api 挂在每个分组路由上，业务模块不用自己加）：
除 GET/HEAD/OPTIONS 外的所有接口，只要带 `?ver=` 或 `?previewAs=` 一律 403
（code READONLY，details.mode = history | preview）。
例外由**各模块在自己的路由上**用 `@allow_in_readonly` 标记：不落库的 POST（dry-run、试算、校验），
以及前端明确允许在只读下做的写操作（例如 qa W16「申请权限」，会落库但前端允许）：

    @router.post("/rules/{id}:dry-run")
    @allow_in_readonly                                  # 两种只读模式都放行
    async def dry_run(...): ...

    @router.post("/permission-requests", openapi_extra=readonly_extra("history", "preview"))  # qa W16

两个参数都带时按 preview（details.mode = "preview"）。

桩实现：只解析参数并拒绝写；`ver` 是否存在、预览用户的权限解析由 PDE 实现。
"""

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Annotated, Any

from fastapi import Depends, Query, Request

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
    """显式写法（全局守卫已经覆盖，一般不用再加）。"""
    check_writable(ctx.ver, ctx.preview_as)
    return ctx


WritableDep = Depends(require_writable)


SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
READONLY_MODES = frozenset({"history", "preview"})
_ALLOW_ATTR = "__allow_in_readonly__"
_EXTRA_KEY = "x-allow-in-readonly"


def readonly_mode(ver: str | None, preview_as: str | None) -> str | None:
    """只读模式：两个参数都带时按 preview（预览优先，PDE 定）。"""
    if preview_as:
        return "preview"
    if ver:
        return "history"
    return None


def _modes(modes: Iterable[str]) -> frozenset[str]:
    m = frozenset(modes)
    if not m or not m <= READONLY_MODES:
        raise ValueError(f"只读放行模式只能是 {sorted(READONLY_MODES)} 的子集：{sorted(m)}")
    return m


def allow_in_readonly(fn: Any = None, *, modes: Iterable[str] = READONLY_MODES) -> Any:
    """路由级标记：这个非 GET 接口在只读模式下也允许调用，modes 指定哪几种（默认两种都放行）。

    用于 dry-run 等不落库的 POST，或前端明确允许的写（如 qa W16 申请权限）。两种写法等价：

        @router.post("/rules/{id}:dry-run")
        @allow_in_readonly                                   # history + preview
        async def dry_run(...): ...

        @router.post("/permission-requests", openapi_extra=readonly_extra("history", "preview"))
    """

    allowed = _modes(modes)

    def mark(f: Any) -> Any:
        setattr(f, _ALLOW_ATTR, allowed)
        return f

    return mark(fn) if fn is not None else mark


def readonly_extra(*modes: str) -> dict[str, list[str]]:
    """路由参数写法：`openapi_extra=readonly_extra("history")`；OpenAPI 里也能看到放行模式。"""
    return {_EXTRA_KEY: sorted(_modes(modes or READONLY_MODES))}


def allowed_readonly_modes(endpoint: Any, route: Any = None) -> frozenset[str]:
    marked = getattr(endpoint, _ALLOW_ATTR, None)
    if marked:
        return frozenset(marked)
    extra = getattr(route, "openapi_extra", None) or {}
    return frozenset(extra.get(_EXTRA_KEY) or ())


def check_writable(ver: str | None, preview_as: str | None, allowed: frozenset[str] = frozenset()) -> None:
    mode = readonly_mode(ver, preview_as)
    if mode is None or mode in allowed:
        return
    if mode == "preview":
        raise ReadonlyPreview(preview_as or "", ver)
    raise ReadonlyHistory(ver or "")


async def readonly_guard(request: Request) -> None:
    """全局依赖：非安全方法 + ?ver / ?previewAs → 403 READONLY，除非路由对该模式放行。"""
    if request.method in SAFE_METHODS:
        return
    allowed = allowed_readonly_modes(request.scope.get("endpoint"), request.scope.get("route"))
    q = request.query_params
    check_writable(q.get("ver") or None, q.get("previewAs") or None, allowed)


ReadonlyGuardDep = Depends(readonly_guard)
