"""统一错误体：{"code", "message", "details", "requestId"}。

- code：大写下划线英文错误码，前端按 code 分支；
- message：中文，可以直接放进悬停提示或确认卡的阻断原因；
- details：可选，结构化补充信息（例如阻断的校验项列表）。

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


class VersionConflict(AppError):
    status_code, code, message = 409, "VERSION_CONFLICT", "数据已被他人修改，请刷新后重试"


class Forbidden(AppError):
    status_code, code, message = 403, "NO_PERMISSION", "不在你的权限范围内"


class Unauthorized(AppError):
    status_code, code, message = 401, "UNAUTHORIZED", "请先登录"


class ReadonlyHistory(AppError):
    status_code, code = 409, "READONLY_HISTORY"

    def __init__(self, ver: str) -> None:
        super().__init__(f"正在查看历史版本 {ver}，只读", details={"ver": ver})


class ReadonlyPreview(AppError):
    status_code, code = 409, "READONLY_PREVIEW"

    def __init__(self, user: str) -> None:
        super().__init__(f"正在以 {user} 的身份预览，只读", details={"previewAs": user})


def error_body(code: str, message: str, details: Any = None) -> dict[str, Any]:
    return {"code": code, "message": message, "details": details, "requestId": get_request_id()}


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(error_body(exc.code, exc.message, exc.details), status_code=exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        details = [
            {"loc": list(e.get("loc", [])), "msg": e.get("msg"), "type": e.get("type")} for e in exc.errors()
        ]
        return JSONResponse(error_body("VALIDATION_ERROR", "参数校验失败", details), status_code=422)

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = {404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED", 401: "UNAUTHORIZED", 403: "NO_PERMISSION"}
        return JSONResponse(
            error_body(code.get(exc.status_code, "HTTP_ERROR"), str(exc.detail)), status_code=exc.status_code
        )

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception) -> JSONResponse:
        import logging

        logging.getLogger("app.errors").exception("unhandled error")
        return JSONResponse(error_body("INTERNAL_ERROR", "服务器内部错误"), status_code=500)
