"""Persist actual token request results without storing headers, query strings or bodies."""

import logging
from time import perf_counter

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.db.session import get_sessionmaker
from app.modules.admin.api_keys import security_now
from app.modules.admin.models import ApiKeyCall


class ApiKeyCallsMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started = perf_counter()

        async def record(message: Message) -> None:
            key_id = scope.get("state", {}).get("api_key_id")
            if message["type"] == "http.response.start" and key_id is not None:
                route = getattr(scope.get("route"), "path", "unknown")
                try:
                    async with get_sessionmaker()() as session, session.begin():
                        session.add(ApiKeyCall(
                            key_id=key_id, api=f"{scope['method']} {route}",
                            ip=(scope.get("client") or ("",))[0], status_code=message["status"],
                            duration_ms=max(0, round((perf_counter() - started) * 1000)),
                            created_at=security_now(),
                        ))
                except Exception:
                    # No credential/header/SQL parameters are logged on telemetry failure.
                    logging.getLogger(__name__).error("API key call record unavailable; key_id=%s", key_id)
            await send(message)

        await self.app(scope, receive, record)
