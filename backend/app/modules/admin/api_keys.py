"""Real API key management (admin_0025: scopes, allowlists, service accounts, quota).

Tokens are accepted only on (a) explicitly listed metadata GET routes (scope meta:read, unchanged
behaviour) or (b) routes that declare `require_api_scope(...)`. Every other route rejects tokens.
Caller context (principal kind / service account / key prefix) is written to request.state and
session.info["api_caller"]; insert_audit copies it into audit_log automatically.
"""

import hashlib
import ipaddress
import secrets
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import Field, field_validator, model_validator
from sqlalchemy import case, func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.auth import (
    CurrentUser,
    CurrentUserDep,
    check_func,
    full_route_path,
    require_func,
    resolve_user,
)
from app.common.ontology import require_ontology_id
from app.common.pagination import PageParams
from app.core.config import api_prefix, get_settings
from app.core.errors import AppError, DuplicateKey, Forbidden, NotFound, Unauthorized
from app.core.schema import ApiModel
from app.db.session import SessionDep
from app.modules.admin.models import ApiKey, ApiKeyCall, ApiKeyUsage, ServiceAccount, SysRole, SysUser
from app.modules.admin.repo import insert_audit, lock_code_seq


# Security expiry must keep advancing even when the demonstration business clock is frozen.
def security_now() -> datetime:
    return datetime.now(UTC)  # noqa: TID251 - security expiry uses the wall clock


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
# Scope codes are the external contract (Palantir integration); labels are display only.
SCOPES: dict[str, str] = {
    "meta:read": "元数据读取",
    "objects:read": "对象读取",
    "functions:invoke": "函数调用",
    "actions:dry-run": "动作试运行",
    "actions:execute": "动作执行",
    "events:ingest": "事件写入",
    "dynamic:read": "动态读取",
}
LEGACY_SCOPES = {"元数据读取": "meta:read"}
SCOPE = "元数据读取"  # legacy label, still accepted on input
ScopeCode = Literal[
    "meta:read",
    "objects:read",
    "functions:invoke",
    "actions:dry-run",
    "actions:execute",
    "events:ingest",
    "dynamic:read",
    "元数据读取",
]
# Function pages a scope can carry, and the highest level it can carry (always ∩ current grants).
SCOPE_PAGES: dict[str, dict[str, str]] = {
    "meta:read": {page: "view" for page in set(METADATA_ROUTES.values())},
    "objects:read": {"object-search": "view", "object-detail": "view"},
    "functions:invoke": {"functions": "view"},
    "actions:dry-run": {"action-types": "view", "action-center": "view"},
    "actions:execute": {"action-types": "view", "action-center": "manage"},
    "events:ingest": {"event-types": "view"},
    # Read-only dynamic layer: alerts, rules / rule hits, lifecycle (object-types view), action center.
    # Never carries manage, so no write route is reachable with this scope alone.
    "dynamic:read": {"alerts": "view", "rules": "view", "object-types": "view", "action-center": "view"},
}
STATUSES = ("有效", "即将过期", "已过期", "已吊销")
RANK = {"none": 0, "view": 1, "manage": 2}
TypeCode = Annotated[str, Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.:\-]+$")]
router = APIRouter(tags=["admin-api-keys"], dependencies=[Depends(require_func("api-keys", "view"))])


class RateLimited(AppError):
    status_code, code, message = 429, "RATE_LIMITED", "API 密钥调用次数超出限制"


def normalize_scopes(values) -> list[str]:
    if not isinstance(values, list):
        return []
    out = [LEGACY_SCOPES.get(v, v) for v in values if isinstance(v, str)]
    return [v for v in dict.fromkeys(out) if v in SCOPES]


class KeyBindingIn(ApiModel):
    kind: Literal["用户", "服务账号"]
    user_id: str | None = Field(default=None, min_length=1, max_length=64)
    service_account_id: str | None = Field(default=None, min_length=1, max_length=64)

    @model_validator(mode="after")
    def one_principal(self):
        if self.kind == "用户" and (not self.user_id or self.service_account_id):
            raise ValueError("用户密钥需要 userId")
        if self.kind == "服务账号" and (not self.service_account_id or self.user_id):
            raise ValueError("服务账号密钥需要 serviceAccountId")
        return self


class KeyPolicyFields(ApiModel):
    # null = 该 scope 下全部类型；列表 = 只允许这些类型编号（空列表 = 全部拒绝）
    action_types: list[TypeCode] | None = Field(default=None, max_length=200)
    event_types: list[TypeCode] | None = Field(default=None, max_length=200)
    rate_per_minute: int | None = Field(default=None, ge=1, le=100_000)
    quota_per_day: int | None = Field(default=None, ge=1, le=100_000_000)

    @field_validator("action_types", "event_types")
    @classmethod
    def unique_codes(cls, values: list[str] | None) -> list[str] | None:
        return None if values is None else list(dict.fromkeys(values))


class KeyCreateIn(KeyPolicyFields):
    name: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{2,39}$")
    env: Literal["生产", "测试"]
    scopes: list[ScopeCode] = Field(min_length=1, max_length=len(SCOPES))
    binding: KeyBindingIn
    life_days: Literal[0, 30, 90, 180, 365] = 90
    note: str = Field(default="", max_length=512)
    ip_allow: list[str] = Field(default_factory=list, max_length=50)

    @field_validator("ip_allow")
    @classmethod
    def valid_networks(cls, values: list[str]) -> list[str]:
        return list(dict.fromkeys(str(ipaddress.ip_network(value, strict=False)) for value in values))

    @field_validator("scopes")
    @classmethod
    def canonical_scopes(cls, values: list[str]) -> list[str]:
        return normalize_scopes(values)

    @model_validator(mode="after")
    def allowlists_need_scope(self):
        check_allowlist_scopes(self.scopes, self.action_types, self.event_types)
        return self


class KeyPolicyIn(KeyPolicyFields):
    """PATCH：只改传入的字段；scopes 只能收窄（不能新增）。"""

    scopes: list[ScopeCode] | None = Field(default=None, min_length=1, max_length=len(SCOPES))

    @field_validator("scopes")
    @classmethod
    def canonical_scopes(cls, values: list[str] | None) -> list[str] | None:
        return None if values is None else normalize_scopes(values)


def check_allowlist_scopes(scopes: list[str], action_types, event_types) -> None:
    if action_types is not None and not {"actions:dry-run", "actions:execute"} & set(scopes):
        raise ValueError("动作类型白名单需要 actions:dry-run 或 actions:execute")
    if event_types is not None and "events:ingest" not in scopes:
        raise ValueError("事件类型白名单需要 events:ingest")


class ServiceAccountIn(ApiModel):
    name: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{2,63}$")
    description: str = Field(default="", max_length=512)
    role_id: str = Field(min_length=1, max_length=64, description="服务角色编号（sys_role.code）")


class ServiceAccountPatch(ApiModel):
    description: str | None = Field(default=None, max_length=512)
    role_id: str | None = Field(default=None, min_length=1, max_length=64)


def metadata_permissions(user: CurrentUser) -> dict[str, str]:
    return {
        page: "view" for page in set(METADATA_ROUTES.values()) if user.func.get(page) in ("view", "manage")
    }


def scope_permissions(scopes: list[str], func_grants: dict) -> dict[str, str]:
    """Pages the scopes can carry, clamped to the principal's current grants (never above)."""
    out: dict[str, str] = {}
    for scope in scopes:
        for page, cap in SCOPE_PAGES.get(scope, {}).items():
            have = func_grants.get(page) if isinstance(func_grants, dict) else None
            level = min(RANK.get(cap, 0), RANK.get(have, 0) if isinstance(have, str) else 0)
            if level and level > RANK.get(out.get(page, "none"), 0):
                out[page] = "manage" if level == 2 else "view"
    return out


def clamp_permissions(snapshot: dict, current: dict) -> dict[str, str]:
    """Runtime grants = creation snapshot ∩ current role grants. Legacy snapshots are view-only."""
    out = {}
    for page, cap in (snapshot or {}).items():
        cap_rank = RANK.get(cap, 1) if isinstance(cap, str) else 1
        have = current.get(page)
        level = min(cap_rank, RANK.get(have, 0) if isinstance(have, str) else 0)
        if level:
            out[page] = "manage" if level == 2 else "view"
    return out


def fmt(value: datetime | None) -> str | None:
    return (
        value.astimezone(ZoneInfo(get_settings().display_tz)).strftime("%Y-%m-%d %H:%M:%S") if value else None
    )


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


async def get_service_account(session: AsyncSession, code: str, *, lock: bool = False) -> ServiceAccount:
    query = select(ServiceAccount).where(ServiceAccount.code == code)
    account = await session.scalar(query.with_for_update() if lock else query)
    if account is None:
        raise NotFound("服务账号不存在")
    return account


def window_buckets(at: datetime) -> dict[str, datetime]:
    local = at.astimezone(ZoneInfo(get_settings().display_tz))
    return {
        "minute": at.replace(second=0, microsecond=0),
        "day": local.replace(hour=0, minute=0, second=0, microsecond=0),
    }


async def key_usage(session: AsyncSession, ids: list[int], at: datetime) -> dict[int, dict[str, int]]:
    buckets = window_buckets(at)
    out: dict[int, dict[str, int]] = {i: {"minute": 0, "day": 0} for i in ids}
    if not ids:
        return out
    rows = await session.execute(
        select(ApiKeyUsage.key_id, ApiKeyUsage.period, ApiKeyUsage.calls).where(
            ApiKeyUsage.key_id.in_(ids),
            or_(*((ApiKeyUsage.period == p) & (ApiKeyUsage.bucket == b) for p, b in buckets.items())),
        )
    )
    for key_id, period, calls in rows:
        out[key_id][period] = calls
    return out


async def key_rows(session: AsyncSession, keys: list[ApiKey], at: datetime) -> list[dict]:
    if not keys:
        return []
    ids = [key.id for key in keys]
    recent = dict(
        (
            await session.execute(
                select(ApiKeyCall.key_id, func.count())
                .where(ApiKeyCall.key_id.in_(ids), ApiKeyCall.created_at >= at - timedelta(days=7))
                .group_by(ApiKeyCall.key_id)
            )
        ).all()
    )
    latest = (
        select(ApiKeyCall.key_id, func.max(ApiKeyCall.id).label("last_id"))
        .where(ApiKeyCall.key_id.in_(ids))
        .group_by(ApiKeyCall.key_id)
        .subquery()
    )
    calls = {
        call.key_id: call
        for call in await session.scalars(select(ApiKeyCall).join(latest, ApiKeyCall.id == latest.c.last_id))
    }
    user_ids = [key.user_id for key in keys if key.user_id is not None]
    users = {user.id: user for user in await session.scalars(select(SysUser).where(SysUser.id.in_(user_ids)))}
    account_ids = [key.service_account_id for key in keys if key.service_account_id is not None]
    accounts = {
        account.id: (account, role)
        for account, role in await session.execute(
            select(ServiceAccount, SysRole)
            .join(SysRole, SysRole.id == ServiceAccount.role_id)
            .where(ServiceAccount.id.in_(account_ids))
        )
    }
    usage = await key_usage(session, ids, at)
    result = []
    for key in keys:
        last = calls.get(key.id)
        state = (
            "已吊销"
            if key.revoked_at
            else "已过期"
            if key.expires_at and key.expires_at <= at
            else "即将过期"
            if key.expires_at and key.expires_at <= at + timedelta(days=30)
            else "有效"
        )
        scopes = normalize_scopes(key.scopes)
        if key.principal_kind == "service":
            account, role = accounts[key.service_account_id]
            binding = {
                "kind": "服务账号",
                "serviceAccountId": account.code,
                "serviceAccountName": account.name,
                "roleId": role.code,
                "roleName": role.name,
                "scope": "服务角色当前权限与密钥授权范围的交集",
            }
        else:
            bound = users[key.user_id]
            binding = {
                "kind": "用户",
                "userId": bound.code,
                "userName": bound.name,
                "scope": "创建时授权与当前真实角色权限的交集"
                if scopes != ["meta:read"]
                else "仅元数据；创建时授权与当前真实角色权限的交集",
            }
        result.append(
            {
                "id": key.code,
                "name": key.name,
                "env": key.env,
                "prefix": key.prefix,
                "last4": key.last4,
                "ontologyIds": key.ontology_ids,
                "principalKind": key.principal_kind,
                "scopes": scopes,
                "binding": binding,
                "actionTypes": key.action_types,
                "eventTypes": key.event_types,
                "ratePerMinute": key.rate_per_minute,
                "quotaPerDay": key.quota_per_day,
                "usage": usage[key.id],
                "creator": key.creator,
                "createdAt": fmt(key.created_at),
                "expiresAt": fmt(key.expires_at),
                "lastUsed": fmt(last.created_at) if last else None,
                "lastIp": last.ip if last else None,
                "status": state,
                "calls7d": recent.get(key.id, 0),
                "ipAllow": key.ip_allow,
                "note": key.note,
                "revokedAt": fmt(key.revoked_at),
                "revokedBy": key.revoked_by,
            }
        )
    return result


def client_host(request: Request) -> str:
    return request.client.host if request.client else ""


def request_ontology(request: Request) -> int:
    raw = request.headers.get("x-ontology-id", "1")
    if len(raw) > 19 or not raw.isascii() or not raw.isdecimal() or not 0 < int(raw) < 2**63:
        raise Forbidden("请选择有效的本体工作空间")
    return int(raw)


async def role_bound(session: AsyncSession, role_id: int, ontology_id: int) -> bool:
    from app.modules.ontology.models import OntologyRoleBinding

    return (
        await session.scalar(
            select(OntologyRoleBinding.id).where(
                OntologyRoleBinding.role_id == role_id, OntologyRoleBinding.ontology_id == ontology_id
            )
        )
        is not None
    )


# ---- endpoints: keys ----


@router.get("/api-keys/meta")
async def key_meta(user: CurrentUserDep, session: SessionDep) -> dict:
    grantable = [scope for scope in SCOPES if scope_permissions([scope], user.func)]
    can_manage_sa = user.func.get("api-keys") == "manage" and user.func.get("roles") == "manage"
    accounts = []
    if can_manage_sa:
        accounts = [
            {"id": account.code, "name": account.name, "roleId": role.code, "roleName": role.name}
            for account, role in await session.execute(
                select(ServiceAccount, SysRole)
                .join(SysRole, SysRole.id == ServiceAccount.role_id)
                .where(ServiceAccount.revoked_at.is_(None))
                .order_by(ServiceAccount.code)
            )
        ]
    return {
        "scopes": grantable,
        "scopeOptions": [
            {"value": code, "label": label, "pages": sorted(SCOPE_PAGES[code])}
            for code, label in SCOPES.items()
        ],
        "binding": {
            "kind": "用户",
            "userId": user.id,
            "userName": user.name,
            "scope": "仅元数据；创建时授权与当前真实角色权限的交集",
        },
        "allowedPages": sorted(metadata_permissions(user)),
        "canManage": user.func.get("api-keys") == "manage",
        "canManageServiceAccounts": can_manage_sa,
        "serviceAccounts": accounts,
    }


@router.get("/api-keys")
async def list_keys(
    session: SessionDep,
    page: int = 1,
    size: Annotated[int, Query(ge=1, le=200)] = 10,
    status: Literal["全部", "有效", "即将过期", "已过期", "已吊销"] = "全部",
    scope: Literal[
        "全部",
        "meta:read",
        "objects:read",
        "functions:invoke",
        "actions:dry-run",
        "actions:execute",
        "events:ingest",
        "dynamic:read",
        "元数据读取",
    ] = "全部",
    principal: Literal["全部", "user", "service"] = "全部",
    kw: Annotated[str, Query(max_length=128)] = "",
) -> dict:
    at = security_now()
    conditions = []
    if scope != "全部":
        conditions.append(func.jsonb_exists(ApiKey.scopes, LEGACY_SCOPES.get(scope, scope)))
    if principal != "全部":
        conditions.append(ApiKey.principal_kind == principal)
    if kw.strip():
        pattern = "%" + kw.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        conditions.append(
            or_(
                *(
                    column.ilike(pattern, escape="\\")
                    for column in (ApiKey.name, ApiKey.prefix, ApiKey.creator, ApiKey.code)
                )
            )
        )
    counts = {
        name: count
        for name, count in await session.execute(
            select(status_expr(at).label("state"), func.count()).where(*conditions).group_by("state")
        )
    }
    if status != "全部":
        conditions.append(status_expr(at) == status)
    total = int(await session.scalar(select(func.count()).select_from(ApiKey).where(*conditions)) or 0)
    params = PageParams(page=page, size=size).clamp(total)
    keys = list(
        await session.scalars(
            select(ApiKey)
            .where(*conditions)
            .order_by(ApiKey.id.desc())
            .offset(params.offset)
            .limit(params.size)
        )
    )
    return {
        "items": await key_rows(session, keys, at),
        "total": total,
        "page": params.page,
        "size": params.size,
        "counts": {state: counts.get(state, 0) for state in STATUSES},
    }


@router.get("/api-keys/{id}/calls")
async def list_calls(
    id: str, session: SessionDep, page: int = 1, size: Annotated[int, Query(ge=1, le=200)] = 10
) -> dict:
    key = await get_key(session, id)
    total = int(
        await session.scalar(select(func.count()).select_from(ApiKeyCall).where(ApiKeyCall.key_id == key.id))
        or 0
    )
    params = PageParams(page=page, size=size).clamp(total)
    calls = await session.scalars(
        select(ApiKeyCall)
        .where(ApiKeyCall.key_id == key.id)
        .order_by(ApiKeyCall.id.desc())
        .offset(params.offset)
        .limit(size)
    )
    return {
        "items": [
            {
                "id": call.id,
                "time": fmt(call.created_at),
                "api": call.api,
                "ip": call.ip,
                "code": call.status_code,
                "ms": call.duration_ms,
            }
            for call in calls
        ],
        "total": total,
        "page": params.page,
        "size": params.size,
    }


@router.get("/api-keys/{id}")
async def key_detail(id: str, session: SessionDep) -> dict:
    return (await key_rows(session, [await get_key(session, id)], security_now()))[0]


@router.post("/api-keys", status_code=201)
async def create_key(
    body: KeyCreateIn, session: SessionDep, user: CurrentUserDep, request: Request, response: Response
) -> dict:
    check_func(user, "api-keys", "manage")
    ontology_id = request_ontology(request)
    at = security_now()
    account = None
    if body.binding.kind == "用户":
        if body.binding.user_id != user.id:
            raise Forbidden("密钥只能绑定当前真实身份")
        bound = await session.scalar(select(SysUser).where(SysUser.code == user.id))
        from app.modules.ontology.models import OntologyMember

        membership = await session.scalar(
            select(OntologyMember.id).where(
                OntologyMember.ontology_id == ontology_id, OntologyMember.user_id == bound.id
            )
        )
        if membership is None:
            raise Forbidden("没有此本体工作空间的访问权限")
        grants = user.func
        principal = f"绑定 {user.id}"
    else:
        # A service key carries a shared role's grants: only role administrators may mint one.
        check_func(user, "roles", "manage")
        account = await get_service_account(session, body.binding.service_account_id or "")
        if account.revoked_at is not None:
            raise Forbidden("服务账号已吊销")
        role = await session.get(SysRole, account.role_id)
        if not await role_bound(session, role.id, ontology_id):
            raise Forbidden("服务角色未绑定此本体工作空间")
        grants = role.func if isinstance(role.func, dict) else {}
        principal = f"服务账号 {account.code}"
    for scope in body.scopes:
        if not scope_permissions([scope], grants):
            raise Forbidden(f"当前身份没有可授予的{SCOPES[scope]}权限", details={"scope": scope})
    permissions = scope_permissions(body.scopes, grants)
    token = ("rg_live_" if body.env == "生产" else "rg_test_") + secrets.token_urlsafe(32)
    key = ApiKey(
        code="AK-" + secrets.token_hex(12),
        name=body.name,
        env=body.env,
        principal_kind="service" if account else "user",
        user_id=None if account else bound.id,
        service_account_id=account.id if account else None,
        token_hash=hashlib.sha256(token.encode()).hexdigest(),
        prefix=token[:12],
        last4=token[-4:],
        scopes=list(body.scopes),
        ontology_ids=[ontology_id],
        func_snapshot=permissions,
        ip_allow=body.ip_allow,
        note=body.note,
        action_types=body.action_types,
        event_types=body.event_types,
        rate_per_minute=body.rate_per_minute,
        quota_per_day=body.quota_per_day,
        creator=f"{user.name}（{user.id}）",
        created_by=user.id,
        created_at=at,
        updated_at=at,
        expires_at=at + timedelta(days=body.life_days) if body.life_days else None,
    )
    session.add(key)
    try:
        await session.flush()
    except IntegrityError as exc:
        raise DuplicateKey("已有同名未吊销密钥，请更换名称或先吊销") from exc
    await insert_audit(
        session,
        actor=key.creator,
        action="新建 API 密钥",
        target=f"{key.code} · {key.name} · {'、'.join(key.scopes)} · {principal}",
        ip=client_host(request),
        created_by=user.id,
        time=at,
    )
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return {**(await key_rows(session, [key], at))[0], "secret": token}


@router.patch("/api-keys/{id}")
async def update_key_policy(
    id: str, body: KeyPolicyIn, session: SessionDep, user: CurrentUserDep, request: Request
) -> dict:
    """Change allowlists / quota, or narrow scopes. Widening needs a new key (new grant snapshot)."""
    check_func(user, "api-keys", "manage")
    key = await get_key(session, id, lock=True)
    if key.revoked_at is not None:
        raise Forbidden("密钥已吊销")
    if key.principal_kind == "service":
        check_func(user, "roles", "manage")
    fields = body.model_fields_set
    scopes = normalize_scopes(key.scopes)
    if "scopes" in fields and body.scopes is not None:
        extra = set(body.scopes) - set(scopes)
        if extra:
            raise Forbidden("只能收窄密钥授权范围，新增范围请新建密钥", details={"scopes": sorted(extra)})
        scopes = body.scopes
        key.scopes = scopes
        key.func_snapshot = clamp_permissions(
            key.func_snapshot, scope_permissions(scopes, {page: "manage" for page in key.func_snapshot})
        )
    for name in ("action_types", "event_types", "rate_per_minute", "quota_per_day"):
        if name in fields:
            setattr(key, name, getattr(body, name))
    try:
        check_allowlist_scopes(scopes, key.action_types, key.event_types)
    except ValueError as exc:
        raise AppError(str(exc), code="VALIDATION_FAILED", status_code=422) from exc
    key.updated_at = security_now()
    await session.flush()
    await insert_audit(
        session,
        actor=f"{user.name}（{user.id}）",
        action="修改 API 密钥策略",
        target=f"{key.code} · {key.name} · {'、'.join(sorted(fields))}",
        ip=client_host(request),
        created_by=user.id,
        time=key.updated_at,
    )
    return (await key_rows(session, [key], security_now()))[0]


@router.post("/api-keys/{id}:revoke")
async def revoke_key(id: str, session: SessionDep, user: CurrentUserDep, request: Request) -> dict:
    check_func(user, "api-keys", "manage")
    key = await get_key(session, id, lock=True)
    if key.revoked_at is None:
        key.revoked_at = security_now()
        key.revoked_by = f"{user.name}（{user.id}）"
        bound = f"绑定用户ID {key.user_id}" if key.user_id else f"服务账号ID {key.service_account_id}"
        await insert_audit(
            session,
            actor=key.revoked_by,
            action="吊销 API 密钥",
            target=f"{key.code} · {key.name} · {bound}",
            ip=client_host(request),
            created_by=user.id,
            time=key.revoked_at,
        )
        await session.flush()
    return (await key_rows(session, [key], security_now()))[0]


# ---- endpoints: service accounts ----


async def account_rows(session: AsyncSession, accounts: list[ServiceAccount]) -> list[dict]:
    if not accounts:
        return []
    ids = [a.id for a in accounts]
    roles = {
        r.id: r
        for r in await session.scalars(select(SysRole).where(SysRole.id.in_({a.role_id for a in accounts})))
    }
    active = dict(
        (
            await session.execute(
                select(ApiKey.service_account_id, func.count())
                .where(ApiKey.service_account_id.in_(ids), ApiKey.revoked_at.is_(None))
                .group_by(ApiKey.service_account_id)
            )
        ).all()
    )
    return [
        {
            "id": a.code,
            "name": a.name,
            "description": a.description,
            "roleId": roles[a.role_id].code,
            "roleName": roles[a.role_id].name,
            "status": "已吊销" if a.revoked_at else "启用",
            "activeKeys": active.get(a.id, 0),
            "creator": a.creator,
            "createdAt": fmt(a.created_at),
            "revokedAt": fmt(a.revoked_at),
            "revokedBy": a.revoked_by,
        }
        for a in accounts
    ]


async def role_by_code(session: AsyncSession, code: str) -> SysRole:
    role = await session.scalar(select(SysRole).where(SysRole.code == code))
    if role is None:
        raise NotFound("服务角色不存在", details={"roleId": code})
    return role


@router.get("/service-accounts")
async def list_service_accounts(session: SessionDep) -> dict:
    accounts = list(await session.scalars(select(ServiceAccount).order_by(ServiceAccount.code)))
    return {"items": await account_rows(session, accounts)}


@router.post("/service-accounts", status_code=201)
async def create_service_account(
    body: ServiceAccountIn, session: SessionDep, user: CurrentUserDep, request: Request
) -> dict:
    check_func(user, "api-keys", "manage")
    check_func(user, "roles", "manage")
    role = await role_by_code(session, body.role_id)
    await lock_code_seq(session, "service_account")
    codes = (
        await session.scalars(select(ServiceAccount.code).where(ServiceAccount.code.op("~")(r"^SA-[0-9]+$")))
    ).all()
    code = f"SA-{max((int(c[3:]) for c in codes), default=0) + 1:04d}"
    if await session.scalar(select(ServiceAccount.id).where(ServiceAccount.name == body.name)) is not None:
        raise DuplicateKey("服务账号名称已存在", details={"name": body.name})
    at = security_now()
    account = ServiceAccount(
        code=code,
        name=body.name,
        description=body.description,
        role_id=role.id,
        creator=f"{user.name}（{user.id}）",
        created_by=user.id,
        created_at=at,
        updated_at=at,
    )
    session.add(account)
    await session.flush()
    await insert_audit(
        session,
        actor=account.creator,
        action="新建服务账号",
        target=f"{code} · {body.name} · 服务角色「{role.name}」",
        ip=client_host(request),
        created_by=user.id,
        time=at,
    )
    return (await account_rows(session, [account]))[0]


@router.patch("/service-accounts/{id}")
async def update_service_account(
    id: str, body: ServiceAccountPatch, session: SessionDep, user: CurrentUserDep, request: Request
) -> dict:
    check_func(user, "api-keys", "manage")
    check_func(user, "roles", "manage")
    account = await get_service_account(session, id, lock=True)
    if account.revoked_at is not None:
        raise Forbidden("服务账号已吊销")
    changes = []
    if body.description is not None:
        account.description = body.description
        changes.append("说明")
    if body.role_id is not None:
        role = await role_by_code(session, body.role_id)
        account.role_id = role.id
        changes.append(f"服务角色「{role.name}」")
    account.updated_at = security_now()
    await session.flush()
    await insert_audit(
        session,
        actor=f"{user.name}（{user.id}）",
        action="修改服务账号",
        target=f"{account.code} · {account.name} · {'、'.join(changes) or '无变化'}",
        ip=client_host(request),
        created_by=user.id,
        time=account.updated_at,
    )
    return (await account_rows(session, [account]))[0]


@router.post("/service-accounts/{id}:revoke")
async def revoke_service_account(
    id: str, session: SessionDep, user: CurrentUserDep, request: Request
) -> dict:
    """One-click revoke: the account and every live key bound to it stop working immediately."""
    check_func(user, "api-keys", "manage")
    account = await get_service_account(session, id, lock=True)
    if account.revoked_at is None:
        at = security_now()
        actor = f"{user.name}（{user.id}）"
        account.revoked_at, account.revoked_by, account.updated_at = at, actor, at
        keys = list(
            await session.scalars(
                select(ApiKey)
                .where(ApiKey.service_account_id == account.id, ApiKey.revoked_at.is_(None))
                .with_for_update()
            )
        )
        for key in keys:
            key.revoked_at, key.revoked_by = at, actor
        await session.flush()
        await insert_audit(
            session,
            actor=actor,
            action="吊销服务账号",
            target=f"{account.code} · {account.name} · 同时吊销密钥 {len(keys)} 个",
            ip=client_host(request),
            created_by=user.id,
            time=at,
        )
    return (await account_rows(session, [account]))[0]


# ---- token authentication ----


@dataclass(frozen=True)
class ApiKeyContext:
    key_id: int
    code: str
    prefix: str
    principal_kind: str
    scopes: tuple[str, ...]
    action_types: tuple[str, ...] | None
    event_types: tuple[str, ...] | None
    service_account_id: int | None = None
    service_account_code: str | None = None
    role_id: int | None = None
    user_id: int | None = None


def token_page(method: str, route: str) -> str | None:
    if method != "GET":
        return None
    prefix = api_prefix("ontology")
    return METADATA_ROUTES.get(route[len(prefix) :]) if route.startswith(prefix + "/") else None


def route_api_scopes(route) -> set[str]:
    """Scopes declared on the matched route via require_api_scope (route, router or nested deps)."""
    found: set[str] = set()
    stack = [getattr(route, "dependant", None)]
    while stack:
        dependant = stack.pop()
        if dependant is None:
            continue
        scope = getattr(dependant.call, "api_scope", None)
        if isinstance(scope, str):
            found.add(scope)
        stack.extend(dependant.dependencies)
    return found


async def consume_quota(session: AsyncSession, key: ApiKey, at: datetime) -> dict[str, dict[str, int | None]]:
    """Atomic per-window counter in the request transaction (rolled back requests are not counted).

    Returns the window state after this call: {period: {"limit", "used"}}; unlimited windows are not counted.
    """
    state: dict[str, dict[str, int | None]] = {}
    for period, limit in (("minute", key.rate_per_minute), ("day", key.quota_per_day)):
        state[period] = {"limit": limit, "used": None}
        if limit is None:
            continue
        bucket = window_buckets(at)[period]
        statement = pg_insert(ApiKeyUsage).values(key_id=key.id, period=period, bucket=bucket, calls=1)
        calls = await session.scalar(
            statement.on_conflict_do_update(
                index_elements=["key_id", "period", "bucket"], set_={"calls": ApiKeyUsage.calls + 1}
            ).returning(ApiKeyUsage.calls)
        )
        if calls > limit:
            raise RateLimited(
                "API 密钥每分钟调用次数超出限制" if period == "minute" else "API 密钥当日调用次数超出配额",
                details={"period": period, "limit": limit},
            )
        state[period]["used"] = calls
    return state


async def build_service_account_principal(
    session: AsyncSession,
    service_account_or_code: ServiceAccount | str,
    *,
    tenant_id: str | None = None,
    bind_ontology: bool = True,
) -> CurrentUser:
    """服务账号身份（请求外可用：后台执行 / 队列 / 事件发射按 `api:{编号}` 重建主体）。

    - 按编号加载（传入 ServiceAccount 时也按其编号重新读取，取当前吊销状态）；不存在或已吊销 → Unauthorized。
    - 角色取绑定角色：roles=(role.code,)，func=role.func；没有 runAs 用户，不借用任何真人身份。
    - bind_ontology=True（默认）：要求会话已绑定本体范围（set_ontology_scope）且角色绑定了该空间，
      否则 Unauthorized；返回带 ontology_id / ontology_role="service" / 角色行级范围的身份。
      bind_ontology=False：只给账号 + 角色（API 密钥鉴权在绑定空间前调用，行级范围随后按密钥计算）。
    """
    from app.modules.admin.permissions import role_row_access

    code = (
        service_account_or_code if isinstance(service_account_or_code, str) else service_account_or_code.code
    )
    account = await session.scalar(select(ServiceAccount).where(ServiceAccount.code == code))
    if account is None or account.revoked_at is not None:
        raise Unauthorized("服务账号已吊销或不存在", details={"principal": f"api:{code}"})
    role = await session.get(SysRole, account.role_id)
    if role is None:
        raise Unauthorized("服务账号绑定角色不存在", details={"principal": f"api:{code}"})
    user = CurrentUser(
        id=account.code,
        name=account.name,
        func=role.func if isinstance(role.func, dict) else {},
        roles=(role.code,),
        is_service_account=True,
        tenant_id=tenant_id,
        service_account_id=account.id,
    )
    if not bind_ontology:
        return user
    ontology_id = session.info.get("ontology_id")
    if ontology_id is None or not await role_bound(session, role.id, ontology_id):
        raise Unauthorized("服务账号角色未绑定此本体工作空间", details={"principal": f"api:{code}"})
    return replace(
        user,
        ontology_id=ontology_id,
        ontology_role="service",
        row_filters=await role_row_access(session, [role]),
    )


async def authenticate_key(
    session: AsyncSession, token: str, request: Request, tenant_id: str | None
) -> CurrentUser:
    if len(token) > 128 or not token.startswith(("rg_live_", "rg_test_")):
        raise Unauthorized("无效 API 密钥")
    key = await session.scalar(
        select(ApiKey).where(ApiKey.token_hash == hashlib.sha256(token.encode()).hexdigest())
    )
    if key is None:
        raise Unauthorized("无效 API 密钥")
    request.state.api_key_id = key.id
    if key.revoked_at is not None or (key.expires_at is not None and key.expires_at <= security_now()):
        raise Unauthorized("API 密钥已吊销或已过期")
    if request.headers.get("x-demo-as") or request.query_params.get("previewAs"):
        raise Forbidden("API 密钥不能覆盖或预览其他身份")
    if "full" in request.query_params:
        raise Forbidden("API 密钥不能读取包含运行记录的完整定义")
    scopes = normalize_scopes(key.scopes)
    route = request.scope.get("route")
    declared = route_api_scopes(route)
    page = None
    if declared:
        if not declared <= set(scopes):
            raise Forbidden("API 密钥没有此接口的权限", details={"required": sorted(declared - set(scopes))})
    else:
        page = token_page(request.method, full_route_path(request))
        if "meta:read" not in scopes or page is None:
            raise Forbidden("API 密钥没有此接口的权限")
    if key.ip_allow:
        try:
            address = ipaddress.ip_address(request.client.host if request.client else "")
        except ValueError as exc:
            raise Forbidden("无法验证调用方 IP") from exc
        if not any(address in ipaddress.ip_network(network) for network in key.ip_allow):
            raise Forbidden("调用方不在 IP 白名单")
    account = None
    if key.principal_kind == "service":
        account = await session.get(ServiceAccount, key.service_account_id)
        if account is None or account.revoked_at is not None:
            raise Unauthorized("服务账号已吊销")
        user = await build_service_account_principal(
            session, account, tenant_id=tenant_id, bind_ontology=False
        )
        current = user.func
    else:
        bound = await session.get(SysUser, key.user_id) if key.user_id is not None else None
        if bound is None:
            raise Unauthorized("密钥绑定身份不存在")
        user = await resolve_user(session, bound.code, tenant_id)
        current = user.func
    permissions = clamp_permissions(key.func_snapshot, current)
    if page is not None:
        check_func(replace(user, func=permissions), page)
    quota = await consume_quota(session, key, security_now())
    context = ApiKeyContext(
        key_id=key.id,
        code=key.code,
        prefix=key.prefix,
        principal_kind=key.principal_kind,
        scopes=tuple(scopes),
        action_types=None if key.action_types is None else tuple(key.action_types),
        event_types=None if key.event_types is None else tuple(key.event_types),
        service_account_id=account.id if account else None,
        service_account_code=account.code if account else None,
        role_id=account.role_id if account else None,
        user_id=key.user_id,
    )
    request.state.api_key = context
    caller = {
        "principal_kind": key.principal_kind,
        "service_account_code": context.service_account_code,
        "api_key_prefix": key.prefix,
    }
    request.state.api_caller = caller
    session.info["api_caller"] = caller
    # Only scope-carried pages travel with a token; never ontology-admin capabilities.
    return replace(
        user,
        func=permissions,
        row_filters={},
        is_ontology_admin=False,
        ontology_ids=tuple(value for value in key.ontology_ids if type(value) is int and value > 0),
        api_key_prefix=key.prefix,
        api_scopes=context.scopes,
        action_allowlist=context.action_types,
        event_allowlist=context.event_types,
        quota=quota,
    )


# 私有别名（按 PDE 要求保留；批次 2 为 authenticate_key 内联构造，没有更早的私有函数）。
_service_account_user = build_service_account_principal


async def key_workspace_role(
    session: AsyncSession, request: Request, user: CurrentUser, ontology_id: int
) -> str | None:
    """Workspace access for a token caller: user keys need membership, service keys a role binding."""
    from app.modules.ontology.models import OntologyMember

    context: ApiKeyContext | None = getattr(request.state, "api_key", None)
    if context is not None and context.principal_kind == "service":
        return "service" if await role_bound(session, context.role_id, ontology_id) else None
    return await session.scalar(
        select(OntologyMember.role)
        .join(SysUser, SysUser.id == OntologyMember.user_id)
        .where(OntologyMember.ontology_id == ontology_id, SysUser.code == user.id)
    )


async def key_row_filters(session: AsyncSession, request: Request, user: CurrentUser) -> dict:
    """Business rows travel only with objects:read; then the principal's real row scopes apply."""
    from app.modules.admin.permissions import object_row_access, role_row_access
    from app.modules.ontology.models import OntologyRoleBinding

    context: ApiKeyContext | None = getattr(request.state, "api_key", None)
    if context is None or "objects:read" not in context.scopes:
        return {}
    if context.principal_kind == "service":
        roles = list(
            await session.execute(
                select(SysRole.id, SysRole.objs, SysRole.default_deny)
                .join(OntologyRoleBinding, OntologyRoleBinding.role_id == SysRole.id)
                .where(
                    SysRole.id == context.role_id,
                    OntologyRoleBinding.ontology_id == require_ontology_id(session),
                )
            )
        )
        return await role_row_access(session, roles)
    return await object_row_access(session, context.user_id)


# ---- dependency for machine-callable routes (Palantir integration) ----


def _path_value(spec: str | None, request: Request) -> str | None:
    if spec and spec.startswith("{") and spec.endswith("}"):
        value = request.path_params.get(spec[1:-1])
        return None if value is None else str(value)
    return spec


def enforce_api_allowlist(
    request: Request, *, action_type: str | None = None, event_type: str | None = None
) -> None:
    """Per-key allowlist check for type codes known only after parsing the body. No-op for humans."""
    context: ApiKeyContext | None = getattr(request.state, "api_key", None)
    if context is None:
        return
    if (
        action_type is not None
        and context.action_types is not None
        and action_type not in context.action_types
    ):
        raise Forbidden("API 密钥不允许调用此动作类型", details={"actionType": action_type})
    if event_type is not None and context.event_types is not None and event_type not in context.event_types:
        raise Forbidden("API 密钥不允许写入此事件类型", details={"eventType": event_type})


def require_api_scope(
    scope: str, *, action_type: str | None = None, event_type: str | None = None, key_only: bool = False
):
    """FastAPI dependency for machine-callable routes, e.g.

    `dependencies=[Depends(require_api_scope("actions:dry-run", action_type="{type_id}"))]`.

    - Bearer token: authenticates the key (ontology binding, scope, allowlist, quota) and records the
      caller context for audit. `action_type` / `event_type` may be a literal code or "{path_param}".
    - No token: human session identity passes through unchanged unless key_only=True (then 401).
    Declaring the dependency is also what lets tokens reach the route at all.
    """
    if scope not in SCOPES:
        raise ValueError(f"unknown API scope: {scope}")
    from app.common.auth import get_current_user

    async def _dep(request: Request, user: Annotated[CurrentUser, Depends(get_current_user)]) -> CurrentUser:
        context: ApiKeyContext | None = getattr(request.state, "api_key", None)
        if context is None:
            if key_only:
                raise Unauthorized("此接口需要 API 密钥")
            return user
        if scope not in context.scopes:
            raise Forbidden("API 密钥没有此接口的权限", details={"required": scope})
        enforce_api_allowlist(
            request,
            action_type=_path_value(action_type, request),
            event_type=_path_value(event_type, request),
        )
        return user

    _dep.api_scope = scope  # type: ignore[attr-defined]
    return _dep
