"""请求 ID：读取或生成 X-Request-ID，放进 contextvar，日志和错误体都会带上。"""

import json
import logging
import uuid
from contextvars import ContextVar

from starlette.types import ASGIApp, Message, Receive, Scope, Send

REQUEST_ID_HEADER = "X-Request-ID"
_request_id: ContextVar[str] = ContextVar("request_id", default="-")


def get_request_id() -> str:
    return _request_id.get()


class RequestIdMiddleware:
    """纯 ASGI 中间件（不用 BaseHTTPMiddleware，避免影响流式响应）。"""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        incoming = dict(scope["headers"]).get(REQUEST_ID_HEADER.lower().encode())
        rid = incoming.decode()[:64] if incoming else uuid.uuid4().hex
        token = _request_id.set(rid)

        started = False

        async def send_wrapper(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
                headers = list(message.get("headers", []))
                headers.append((REQUEST_ID_HEADER.encode(), rid.encode()))
                message["headers"] = headers
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception:
            # 未处理异常在这里兜底（还在 contextvar 有效范围内），保证 500 带 requestId 和响应头。
            logging.getLogger("app.errors").exception("unhandled error")
            if started:
                raise
            body = json.dumps(
                {"code": "INTERNAL_ERROR", "message": "服务器内部错误", "details": None, "requestId": rid},
                ensure_ascii=False,
            ).encode()
            await send_wrapper(
                {
                    "type": "http.response.start",
                    "status": 500,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"content-length", str(len(body)).encode()),
                    ],
                }
            )
            await send_wrapper({"type": "http.response.body", "body": body})
        finally:
            _request_id.reset(token)
