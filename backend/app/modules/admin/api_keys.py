"""Real API key management. Only explicitly implemented metadata GET routes accept tokens."""

import hashlib
import ipaddress
import secrets
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import Field, field_validator
from sqlalchemy import case, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.auth import CurrentUser, CurrentUserDep, check_func, require_func, resolve_user
from app.common.pagination import PageParams
from app.core.config import api_prefix, get_settings
from app.core.errors import DuplicateKey, Forbidden, NotFound, Unauthorized
from app.core.schema import ApiModel
from app.db.session import SessionDep
from app.modules.admin.models import ApiKey, ApiKeyCall, SysUser
from app.modules.admin.repo import insert_audit

# Security expiry must keep advancing even when the demonstration business clock is frozen.
def security_now() -> datetime:
    return datetime.now(UTC)


METADATA_ROUTES = {
    "/object-types": "object-types",
    "/object-types/brief": "data-quality",
    "/object-types/{id}": "object-types",
    "/object-types/{id}/properties": "object-types",
    "/metrics": "metrics",
    "/dimensions": "dimensions",
    "/dimensions/{id}": "dimensions",
    "/knowledge": "glossary",
    "/knowledge/{id}": "glossary",
}
SCOPE = "元数据读取"
STATUSES = ("有效", "即将过期", "已过期", "已吊销")
router = APIRouter(tags=["admin-api-keys"], dependencies=[Depends(require_func("api-keys", "view"))])


class KeyBindingIn(ApiModel):
    kind: Literal["用户"]
    user_id: str = Field(min_length=1, max_length=64)


class KeyCreateIn(ApiModel):
    name: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{2,39}$")
    env: Literal["生产", "测试"]
    scopes: list[Literal["元数据读取"]] = Field(min_length=1, max_length=1)
    binding: KeyBindingIn
    life_days: Literal[0, 30, 90, 180, 365] = 90
    note: str = Field(default="", max_length=512)
    ip_allow: list[str] = Field(default_factory=list, max_length=50)

    @field_validator("ip_allow")
    @classmethod
    def valid_networks(cls, values: list[str]) -> list[str]:
        return list(dict.fromkeys(str(ipaddress.ip_network(value, strict=False)) for value in values))


def metadata_permissions(user: CurrentUser) -> dict[str, str]:
    return {page: "view" for page in set(METADATA_ROUTES.values()) if user.func.get(page) in ("view", "manage")}


def fmt(value: datetime | None) -> str | None:
    return value.astimezone(ZoneInfo(get_settings().display_tz)).strftime("%Y-%m-%d %H:%M:%S") if value else None


def status_expr(at: datetime):
    return case(
        (ApiKey.revoked_at.is_not(None), "已吊销"),
        (ApiKey.expires_at <= at, "已过期"),
        (ApiKey.expires_at <= at + timedelta(days=30), "即将过期"),
        else_="有效",
    )


async def get_key(session: AsyncSession, code: str, *, lock: bool = False) -> ApiKey:
    query = select(ApiKey).where(ApiKey.code == code)
    key = await session.scalar(query.with_for_update() if lock else query)
    if key is None:
        raise NotFound("密钥不存在")
    return key


async def key_rows(session: AsyncSession, keys: list[ApiKey], at: datetime) -> list[dict]:
    if not keys:
        return []
    ids = [key.id for key in keys]
    recent = dict((await session.execute(
        select(ApiKeyCall.key_id, func.count()).where(
            ApiKeyCall.key_id.in_(ids), ApiKeyCall.created_at >= at - timedelta(days=7)
        ).group_by(ApiKeyCall.key_id)
    )).all())
    latest = (select(ApiKeyCall.key_id, func.max(ApiKeyCall.id).label("last_id"))
              .where(ApiKeyCall.key_id.in_(ids)).group_by(ApiKeyCall.key_id).subquery())
    calls = {call.key_id: call for call in await session.scalars(
        select(ApiKeyCall).join(latest, ApiKeyCall.id == latest.c.last_id)
    )}
    users = {user.id: user for user in await session.scalars(select(SysUser).where(SysUser.id.in_([key.user_id for key in keys])))}
    result = []
    for key in keys:
        bound = users[key.user_id]
        last = calls.get(key.id)
        state = "已吊销" if key.revoked_at else "已过期" if key.expires_at and key.expires_at <= at else "即将过期" if key.expires_at and key.expires_at <= at + timedelta(days=30) else "有效"
        result.append({
            "id": key.code, "name": key.name, "env": key.env, "prefix": key.prefix, "last4": key.last4,
            "ontologyIds": key.ontology_ids,
            "scopes": key.scopes, "binding": {"kind": "用户", "userId": bound.code, "userName": bound.name, "scope": "仅元数据；创建时授权与当前真实角色权限的交集"},
            "creator": key.creator, "createdAt": fmt(key.created_at), "expiresAt": fmt(key.expires_at),
            "lastUsed": fmt(last.created_at) if last else None, "lastIp": last.ip if last else None,
            "status": state, "calls7d": recent.get(key.id, 0), "ipAllow": key.ip_allow, "note": key.note,
            "revokedAt": fmt(key.revoked_at), "revokedBy": key.revoked_by,
        })
    return result


@router.get("/api-keys/meta")
async def key_meta(user: CurrentUserDep) -> dict:
    return {"scopes": [SCOPE] if metadata_permissions(user) else [],
            "binding": {"kind": "用户", "userId": user.id, "userName": user.name, "scope": "仅元数据；创建时授权与当前真实角色权限的交集"},
            "allowedPages": sorted(metadata_permissions(user)), "canManage": user.func.get("api-keys") == "manage"}


@router.get("/api-keys")
async def list_keys(
    session: SessionDep,
    page: int = 1,
    size: Annotated[int, Query(ge=1, le=200)] = 10,
    status: Literal["全部", "有效", "即将过期", "已过期", "已吊销"] = "全部",
    scope: Literal["全部", "元数据读取"] = "全部",
    kw: Annotated[str, Query(max_length=128)] = "",
) -> dict:
    at = security_now()
    conditions = []
    if scope != "全部":
        conditions.append(func.jsonb_exists(ApiKey.scopes, scope))
    if kw.strip():
        pattern = "%" + kw.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        conditions.append(or_(*(column.ilike(pattern, escape="\\") for column in (ApiKey.name, ApiKey.prefix, ApiKey.creator, ApiKey.code))))
    counts = {name: count for name, count in await session.execute(
        select(status_expr(at).label("state"), func.count()).where(*conditions).group_by("state")
    )}
    if status != "全部":
        conditions.append(status_expr(at) == status)
    total = int(await session.scalar(select(func.count()).select_from(ApiKey).where(*conditions)) or 0)
    params = PageParams(page=page, size=size).clamp(total)
    keys = list(await session.scalars(select(ApiKey).where(*conditions).order_by(ApiKey.id.desc()).offset(params.offset).limit(params.size)))
    return {"items": await key_rows(session, keys, at), "total": total, "page": params.page, "size": params.size,
            "counts": {state: counts.get(state, 0) for state in STATUSES}}


@router.get("/api-keys/{id}/calls")
async def list_calls(id: str, session: SessionDep, page: int = 1, size: Annotated[int, Query(ge=1, le=200)] = 10) -> dict:
    key = await get_key(session, id)
    total = int(await session.scalar(select(func.count()).select_from(ApiKeyCall).where(ApiKeyCall.key_id == key.id)) or 0)
    params = PageParams(page=page, size=size).clamp(total)
    calls = await session.scalars(select(ApiKeyCall).where(ApiKeyCall.key_id == key.id).order_by(ApiKeyCall.id.desc()).offset(params.offset).limit(size))
    return {"items": [{"id": call.id, "time": fmt(call.created_at), "api": call.api, "ip": call.ip, "code": call.status_code, "ms": call.duration_ms} for call in calls],
            "total": total, "page": params.page, "size": params.size}


@router.get("/api-keys/{id}")
async def key_detail(id: str, session: SessionDep) -> dict:
    return (await key_rows(session, [await get_key(session, id)], security_now()))[0]


@router.post("/api-keys", status_code=201)
async def create_key(body: KeyCreateIn, session: SessionDep, user: CurrentUserDep, request: Request, response: Response) -> dict:
    check_func(user, "api-keys", "manage")
    if body.binding.user_id != user.id:
        raise Forbidden("密钥只能绑定当前真实身份")
    permissions = metadata_permissions(user)
    if not permissions:
        raise Forbidden("当前身份没有可授予的元数据读取权限")
    bound = await session.scalar(select(SysUser).where(SysUser.code == user.id))
    from app.modules.ontology.models import OntologyMember
    raw_ontology = request.headers.get("x-ontology-id", "1")
    if len(raw_ontology) > 19 or not raw_ontology.isascii() or not raw_ontology.isdecimal() or not 0 < int(raw_ontology) < 2**63:
        raise Forbidden("请选择有效的本体工作空间")
    ontology_id = int(raw_ontology)
    membership = await session.scalar(select(OntologyMember.id).where(
        OntologyMember.ontology_id == ontology_id, OntologyMember.user_id == bound.id))
    if membership is None:
        raise Forbidden("没有此本体工作空间的访问权限")
    at = security_now()
    token = ("rg_live_" if body.env == "生产" else "rg_test_") + secrets.token_urlsafe(32)
    key = ApiKey(code="AK-" + secrets.token_hex(12), name=body.name, env=body.env, user_id=bound.id,
                 token_hash=hashlib.sha256(token.encode()).hexdigest(), prefix=token[:12], last4=token[-4:],
                 scopes=list(body.scopes), ontology_ids=[ontology_id], func_snapshot=permissions, ip_allow=body.ip_allow, note=body.note,
                 creator=f"{user.name}（{user.id}）", created_by=user.id, created_at=at, updated_at=at,
                 expires_at=at + timedelta(days=body.life_days) if body.life_days else None)
    session.add(key)
    try:
        await session.flush()
    except IntegrityError as exc:
        raise DuplicateKey("已有同名未吊销密钥，请更换名称或先吊销") from exc
    await insert_audit(session, actor=key.creator, action="新建 API 密钥",
                       target=f"{key.code} · {key.name} · {SCOPE} · 绑定 {user.id}",
                       ip=request.client.host if request.client else "", created_by=user.id, time=at)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return {**(await key_rows(session, [key], at))[0], "secret": token}


@router.post("/api-keys/{id}:revoke")
async def revoke_key(id: str, session: SessionDep, user: CurrentUserDep, request: Request) -> dict:
    check_func(user, "api-keys", "manage")
    key = await get_key(session, id, lock=True)
    if key.revoked_at is None:
        key.revoked_at = security_now()
        key.revoked_by = f"{user.name}（{user.id}）"
        await insert_audit(session, actor=key.revoked_by, action="吊销 API 密钥",
                           target=f"{key.code} · {key.name} · 绑定用户ID {key.user_id}",
                           ip=request.client.host if request.client else "", created_by=user.id, time=key.revoked_at)
        await session.flush()
    return (await key_rows(session, [key], security_now()))[0]


def token_page(method: str, route: str) -> str | None:
    if method != "GET":
        return None
    prefix = api_prefix("ontology")
    return METADATA_ROUTES.get(route[len(prefix):]) if route.startswith(prefix + "/") else None


async def authenticate_key(session: AsyncSession, token: str, request: Request, tenant_id: str | None) -> CurrentUser:
    if len(token) > 128 or not token.startswith(("rg_live_", "rg_test_")):
        raise Unauthorized("无效 API 密钥")
    key = await session.scalar(select(ApiKey).where(ApiKey.token_hash == hashlib.sha256(token.encode()).hexdigest()))
    if key is None:
        raise Unauthorized("无效 API 密钥")
    request.state.api_key_id = key.id
    if key.revoked_at is not None or key.expires_at is not None and key.expires_at <= security_now():
        raise Unauthorized("API 密钥已吊销或已过期")
    if request.headers.get("x-demo-as") or request.query_params.get("previewAs"):
        raise Forbidden("API 密钥不能覆盖或预览其他身份")
    if "full" in request.query_params:
        raise Forbidden("API 密钥不能读取包含运行记录的完整定义")
    route = getattr(request.scope.get("route"), "path", "")
    page = token_page(request.method, route)
    if SCOPE not in key.scopes or page is None:
        raise Forbidden("API 密钥没有此接口的权限")
    if key.ip_allow:
        try:
            address = ipaddress.ip_address(request.client.host if request.client else "")
        except ValueError as exc:
            raise Forbidden("无法验证调用方 IP") from exc
        if not any(address in ipaddress.ip_network(network) for network in key.ip_allow):
            raise Forbidden("调用方不在 IP 白名单")
    bound = await session.get(SysUser, key.user_id)
    if bound is None:
        raise Unauthorized("密钥绑定身份不存在")
    user = await resolve_user(session, bound.code, tenant_id)
    permissions = {name: "view" for name in key.func_snapshot if user.func.get(name) in ("view", "manage")}
    check_func(replace(user, func=permissions), page)
    # The whitelist is metadata only: no business rows or administrator capabilities travel with a token.
    return replace(user, func=permissions, row_filters={}, is_ontology_admin=False,
                   ontology_ids=tuple(value for value in key.ontology_ids if type(value) is int and value > 0))
