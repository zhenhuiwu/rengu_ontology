"""Admin browser authentication; session tokens never enter JSON or database plaintext."""

import asyncio
import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, Field, SecretStr
from sqlalchemy import case, delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import AppError, Unauthorized
from app.db.session import SessionDep, get_sessionmaker
from app.modules.admin.models import AuthCredential, AuthLoginThrottle, AuthSession, SysUser, UserStatus

router = APIRouter(tags=["登录"])
COOKIE_NAME = "rengu_session"
PASSWORD_ROUNDS = 600_000


def security_now() -> datetime:
    # Security expiry must advance even when the business/demo clock is frozen.
    return datetime.now(UTC)  # noqa: TID251


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), PASSWORD_ROUNDS)
    return f"pbkdf2_sha256${PASSWORD_ROUNDS}${salt}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, rounds, salt, expected = encoded.split("$")
        if algorithm != "pbkdf2_sha256" or int(rounds) != PASSWORD_ROUNDS:
            return False
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), int(rounds))
        return hmac.compare_digest(actual.hex(), expected)
    except (ValueError, TypeError):
        return False


_DUMMY_HASH = f"pbkdf2_sha256${PASSWORD_ROUNDS}${'00' * 16}${'00' * 32}"


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def fixture_identity_enabled() -> bool:
    s = get_settings()
    url = make_url(s.database_url)
    return (
        s.env == "test"
        and s.test_identity_enabled
        and url.host in ("localhost", "127.0.0.1", "::1")
        and (url.database or "").startswith("rengu_test")
    )


def require_same_origin(request: Request) -> None:
    """Strict Origin on browser writes, including login; bearer API keys use their own auth."""
    origin = request.headers.get("origin")
    s = get_settings()
    if not origin or origin == "null":
        raise AppError("请求来源校验失败，请刷新登录页重试", code="CSRF_ORIGIN_REJECTED", status_code=403)
    if s.auth_allow_any_origin:
        # Dev/intranet escape hatch: still require a browser-supplied http(s) Origin,
        # but do not pin it to a preconfigured IP list.
        parsed = urlsplit(origin)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise AppError("请求来源无效", code="CSRF_ORIGIN_REJECTED", status_code=403)
        return
    allowed = {str(request.base_url).rstrip("/"), *s.auth_allowed_origins}
    # The checked-in Vite proxy runs on loopback :5280 and changes Host to :8000.
    if s.env == "dev" and request.url.hostname in ("127.0.0.1", "localhost", "::1"):
        allowed.update({"http://127.0.0.1:5280", "http://localhost:5280"})
    if origin not in allowed:
        raise AppError("请求来源校验失败，请刷新登录页重试", code="CSRF_ORIGIN_REJECTED", status_code=403)
    parsed = urlsplit(origin)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise AppError("请求来源无效", code="CSRF_ORIGIN_REJECTED", status_code=403)


def cookie_secure(request: Request) -> bool:
    s = get_settings()
    return s.auth_cookie_secure or request.url.scheme == "https"


async def session_user(session: AsyncSession, request: Request) -> SysUser:
    token = request.cookies.get(COOKIE_NAME)
    if not token or len(token) > 128:
        raise Unauthorized()
    user = await session.scalar(
        select(SysUser)
        .join(AuthSession, AuthSession.user_id == SysUser.id)
        .where(
            AuthSession.token_hash == token_hash(token),
            AuthSession.expires_at > security_now(),
            SysUser.code == "U010001",
            SysUser.account == "admin",
            SysUser.status == UserStatus.active,
        )
        .execution_options(populate_existing=True)
    )
    if user is None:
        raise Unauthorized("登录已失效，请重新登录")
    return user


def session_payload(user: SysUser) -> dict:
    return {"authenticated": True, "user": {"code": user.code, "name": user.name, "account": user.account}}


async def create_session(session: AsyncSession, user: SysUser, request: Request, response: Response) -> None:
    now = security_now()
    token = secrets.token_urlsafe(32)
    ttl = timedelta(hours=get_settings().auth_session_hours)
    session.add(
        AuthSession(token_hash=token_hash(token), user_id=user.id, created_at=now, expires_at=now + ttl)
    )
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=int(ttl.total_seconds()),
        httponly=True,
        secure=cookie_secure(request),
        samesite="lax",
        path="/",
    )
    response.headers["Cache-Control"] = "no-store"


async def consume_login_attempt(request: Request) -> None:
    """Reserve attempts in their own transaction so failed requests cannot roll the limit back."""
    now = security_now()
    cutoff = now - timedelta(minutes=15)
    host = request.client.host if request.client else "unknown"
    blocked = False
    async with get_sessionmaker()() as limiter, limiter.begin():
        await limiter.execute(delete(AuthLoginThrottle).where(AuthLoginThrottle.window_start < cutoff))
        for name, limit in (("global", 100), ("ip:" + host, 10)):
            stmt = insert(AuthLoginThrottle).values(bucket=token_hash(name), window_start=now, attempts=1)
            stmt = stmt.on_conflict_do_update(
                index_elements=[AuthLoginThrottle.bucket],
                set_={
                    "window_start": case(
                        (AuthLoginThrottle.window_start <= cutoff, now), else_=AuthLoginThrottle.window_start
                    ),
                    "attempts": case(
                        (AuthLoginThrottle.window_start <= cutoff, 1), else_=AuthLoginThrottle.attempts + 1
                    ),
                },
            ).returning(AuthLoginThrottle.attempts)
            blocked |= (await limiter.scalar(stmt)) > limit
    if blocked:
        raise AppError("登录尝试过于频繁，请15分钟后重试", code="LOGIN_RATE_LIMITED", status_code=429)


class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=128)
    password: SecretStr = Field(min_length=1, max_length=1024)


@router.post("/login")
async def login(body: LoginIn, request: Request, response: Response, session: SessionDep) -> dict:
    require_same_origin(request)
    await consume_login_attempt(request)
    user = await session.scalar(
        select(SysUser).where(SysUser.code == "U010001", SysUser.account == "admin").with_for_update()
    )
    credential = await session.get(AuthCredential, user.id) if user else None
    valid = await asyncio.to_thread(
        verify_password,
        body.password.get_secret_value(),
        credential.password_hash if credential else _DUMMY_HASH,
    )
    if body.username != "admin" or not valid or user is None or user.status != UserStatus.active:
        raise Unauthorized("账号或密码错误，或账号已停用")
    now = security_now()
    await session.execute(delete(AuthSession).where(AuthSession.expires_at <= now))
    previous = request.cookies.get(COOKIE_NAME)
    if previous and len(previous) <= 128:
        await session.execute(delete(AuthSession).where(AuthSession.token_hash == token_hash(previous)))
    user.last_login_at = now
    await create_session(session, user, request, response)
    return session_payload(user)


class PasswordIn(BaseModel):
    current_password: SecretStr = Field(alias="currentPassword", min_length=1, max_length=1024)
    new_password: SecretStr = Field(alias="newPassword", min_length=8, max_length=128)


@router.post("/password")
async def change_password(
    body: PasswordIn, request: Request, response: Response, session: SessionDep
) -> dict:
    require_same_origin(request)
    user = await session_user(session, request)
    await consume_login_attempt(request)
    await session.execute(select(SysUser.id).where(SysUser.id == user.id).with_for_update())
    # Recheck after obtaining the password-rotation lock; a competing rotation may revoke this session.
    user = await session_user(session, request)
    credential = await session.get(AuthCredential, user.id, populate_existing=True)
    current = body.current_password.get_secret_value()
    new = body.new_password.get_secret_value()
    valid = await asyncio.to_thread(
        verify_password, current, credential.password_hash if credential else _DUMMY_HASH
    )
    if not valid or credential is None:
        raise Unauthorized("当前密码错误")
    if new == current:
        raise AppError(
            "新密码需与当前密码不同",
            code="VALIDATION_FAILED",
            status_code=422,
        )
    credential.password_hash = await asyncio.to_thread(hash_password, new)
    await session.execute(delete(AuthSession).where(AuthSession.user_id == user.id))
    await create_session(session, user, request, response)
    return session_payload(user)


@router.get("/session")
async def current_session(request: Request, response: Response, session: SessionDep) -> dict:
    user = await session_user(session, request)
    response.headers["Cache-Control"] = "no-store"
    return session_payload(user)


@router.post("/logout")
async def logout(request: Request, response: Response, session: SessionDep) -> dict:
    require_same_origin(request)
    token = request.cookies.get(COOKIE_NAME)
    if token and len(token) <= 128:
        await session.execute(delete(AuthSession).where(AuthSession.token_hash == token_hash(token)))
    response.delete_cookie(
        COOKIE_NAME, path="/", httponly=True, secure=cookie_secure(request), samesite="lax"
    )
    response.headers["Cache-Control"] = "no-store"
    return {"authenticated": False}
