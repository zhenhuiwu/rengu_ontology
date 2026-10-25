"""统一错误体：{"code", "message", "details", "requestId"}（trace 字段就是 requestId，500 也带）。

- code：大写下划线英文错误码，前端按 code 分支；
- message：中文，可以直接放进悬停提示或确认卡的阻断原因；
- details：可选，结构化补充信息（例如阻断的校验项列表）；
  参数校验失败统一 422 VALIDATION_FAILED，details.fields = [{field, message}]；
- 通用 409：DUPLICATE_KEY（唯一约束）、EMPTY_INPUT（输入为空）、STATE_INVALID（状态不允许，app.core.state）、
  EMPTY_RELEASE（发布没有变更单）、VERSION_CONFLICT（乐观锁）；
- 历史版本 / 预览下的写操作 403 READONLY，details.mode = "history" | "preview"；
- 动作提交：422 SUBMISSION_CRITERIA_FAILED（details.failed = [{name, fail}]）、
  409 IDEMPOTENCY_CONFLICT（同一幂等键、不同请求体）；导出：403 EXPORT_FORBIDDEN。
  不设 ACTION_BLOCKED：提交前校验不通过一律用 SUBMISSION_CRITERIA_FAILED。

业务代码只抛 AppError（或其子类），不要直接抛 HTTPException。
"""

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.request_id import get_request_id


class AppError(Exception):
    status_code: int = 400
    code: str = "BAD_REQUEST"
    message: str = "请求有误"

    def __init__(
        self,
        message: str | None = None,
        *,
        code: str | None = None,
        status_code: int | None = None,
        details: Any = None,
    ) -> None:
        self.message = message or self.message
        self.code = code or self.code
        self.status_code = status_code or self.status_code
        self.details = details
        super().__init__(self.message)


class NotFound(AppError):
    status_code, code, message = 404, "NOT_FOUND", "记录不存在"


class Conflict(AppError):
    status_code, code, message = 409, "CONFLICT", "数据冲突"


class DuplicateKey(Conflict):
    """违反唯一约束 / 业务上「已存在」（BaseRepository 把 IntegrityError 转成它）。"""

    code, message = "DUPLICATE_KEY", "记录已存在"


class EmptyInput(Conflict):
    """必须至少有一项的输入为空（如发布没有变更单的通用形式、批量操作没有选中项）。"""

    code, message = "EMPTY_INPUT", "没有可处理的内容"


class EmptyRelease(Conflict):
    code, message = "EMPTY_RELEASE", "发布至少要包含一张变更单"


class VersionConflict(AppError):
    status_code, code, message = 409, "VERSION_CONFLICT", "数据已被他人修改，请刷新后重试"


class Forbidden(AppError):
    status_code, code, message = 403, "NO_PERMISSION", "不在你的权限范围内"


class ExportForbidden(Forbidden):
    """无导出权限（或导出范围超出行级权限）。"""

    code, message = "EXPORT_FORBIDDEN", "没有导出权限"


class IdempotencyConflict(Conflict):
    """同一幂等键已用于内容不同的请求（内容相同的重放直接返回原结果，不抛错）。"""

    code, message = "IDEMPOTENCY_CONFLICT", "幂等键已被另一请求使用"


class SubmissionCriteriaFailed(AppError):
    """动作提交前校验（submission criteria）不通过：422，details.failed = [{name, fail}]。

    name = 校验项名称，fail = 不通过原因（前端确认卡逐条显示）。
    """

    status_code, code, message = 422, "SUBMISSION_CRITERIA_FAILED", "提交条件未满足"

    def __init__(self, failed: list[dict[str, str]], message: str | None = None) -> None:
        items = [{"name": str(f["name"]), "fail": str(f["fail"])} for f in failed]
        super().__init__(message, details={"failed": items})


class Unauthorized(AppError):
    status_code, code, message = 401, "UNAUTHORIZED", "请先登录"


class Readonly(AppError):
    """历史版本 / 预览下写入：403 READONLY，details.mode = "history" | "preview"（两者都带时按 preview）。"""

    status_code, code = 403, "READONLY"


class ReadonlyHistory(Readonly):
    def __init__(self, ver: str) -> None:
        super().__init__(f"正在查看历史版本 {ver}，只读", details={"mode": "history", "ver": ver})


class ReadonlyPreview(Readonly):
    def __init__(self, user: str, ver: str | None = None) -> None:
        details = {"mode": "preview", "previewAs": user} | ({"ver": ver} if ver else {})
        super().__init__(f"正在以 {user} 的身份预览，只读", details=details)


def error_body(code: str, message: str, details: Any = None) -> dict[str, Any]:
    return {"code": code, "message": message, "details": details, "requestId": get_request_id()}


def validation_fields(errors: Any) -> list[dict[str, str]]:
    """PDE api-index：details.fields = [{field, message}]；field 去掉 body/query/path 前缀，用点连接。"""
    out = []
    for e in errors:
        loc = [str(x) for x in e.get("loc", ())]
        if loc and loc[0] in ("body", "query", "path", "header", "cookie"):
            loc = loc[1:]
        out.append({"field": ".".join(loc) or "-", "message": str(e.get("msg", ""))})
    return out


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(error_body(exc.code, exc.message, exc.details), status_code=exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            error_body("VALIDATION_FAILED", "参数校验失败", {"fields": validation_fields(exc.errors())}),
            status_code=422,
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = {404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED", 401: "UNAUTHORIZED", 403: "NO_PERMISSION"}
        return JSONResponse(
            error_body(code.get(exc.status_code, "HTTP_ERROR"), str(exc.detail)), status_code=exc.status_code
        )

    # 未捕获异常（500）不在这里处理：Starlette 的 ServerErrorMiddleware 在最外层，拿不到请求 ID。
    # 由 app.core.request_id.RequestIdMiddleware 自己兜底，保证 500 也带 requestId 和 X-Request-ID 头。
