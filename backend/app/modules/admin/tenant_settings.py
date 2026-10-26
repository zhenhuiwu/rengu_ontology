"""租户基础信息扩展；不实现 /settings 的模型、通知或安全策略配置。"""

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.common.auth import CurrentUser, require_func
from app.core.errors import AppError, NotFound, VersionConflict
from app.core.schema import ApiModel, DateTimeMin
from app.db.session import SessionDep
from app.modules.admin.models import SysOrg, SysTenant
from app.modules.admin.repo import insert_audit

router = APIRouter(prefix="/settings/tenant", tags=["admin"])
ViewUser = Annotated[CurrentUser, Depends(require_func("settings", "view"))]
ManageUser = Annotated[CurrentUser, Depends(require_func("settings", "manage"))]


class OrgOption(ApiModel):
    value: str
    label: str


class TenantSettingsOut(ApiModel):
    tenant_id: str
    name: str
    short_name: str
    industry: str
    default_org: str | None
    org_options: list[OrgOption]
    lock_version: int
    updated_at: DateTimeMin


class TenantSettingsIn(ApiModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=40, strict=True)
    short_name: str = Field(min_length=1, max_length=12, strict=True)
    default_org: str = Field(min_length=1, max_length=64, strict=True)
    lock_version: int = Field(ge=1, strict=True)

    @field_validator("name", "short_name", "default_org", mode="before")
    @classmethod
    def valid_text(cls, value: object) -> str:
        if not isinstance(value, str) or "\0" in value:
            raise ValueError("须为不含 NUL 的字符串")
        try:
            value.encode("utf-8")
        except UnicodeEncodeError:
            raise ValueError("包含无效 Unicode 字符") from None
        return value.strip()


async def _tenant(session: AsyncSession, *, lock: bool = False) -> SysTenant:
    stmt = select(SysTenant).order_by(SysTenant.id).limit(1).execution_options(populate_existing=True)
    if lock:
        stmt = stmt.with_for_update()
    tenant = await session.scalar(stmt)
    if tenant is None:
        raise NotFound("租户信息尚未配置")
    return tenant


async def _orgs(session: AsyncSession, *, lock: bool = False) -> list[SysOrg]:
    stmt = select(SysOrg).order_by(SysOrg.seq, SysOrg.code).execution_options(populate_existing=True)
    return list(await session.scalars(stmt.with_for_update() if lock else stmt))


def _out(tenant: SysTenant, orgs: list[SysOrg]) -> TenantSettingsOut:
    return TenantSettingsOut(
        tenant_id=tenant.code,
        name=tenant.name,
        short_name=tenant.short_name,
        industry=tenant.industry,
        default_org=next((o.code for o in orgs if o.is_default), None),
        org_options=[OrgOption(value=o.code, label=o.name) for o in orgs],
        lock_version=tenant.lock_version,
        updated_at=tenant.updated_at,
    )


async def update_tenant(
    session: AsyncSession, body: TenantSettingsIn, user: CurrentUser, ip: str
) -> TenantSettingsOut:
    # 父行锁覆盖默认组织更新及审计；等待后重读版本，旧编辑者不能覆盖先提交者。
    tenant = await _tenant(session, lock=True)
    if tenant.lock_version != body.lock_version:
        raise VersionConflict(details={"expected": body.lock_version, "actual": tenant.lock_version})
    orgs = await _orgs(session, lock=True)
    selected = next((o for o in orgs if o.code == body.default_org), None)
    if selected is None:
        raise AppError(
            "默认组织不存在",
            code="VALIDATION_FAILED",
            status_code=422,
            details={"fields": [{"field": "defaultOrg", "message": "请选择已有组织编号"}]},
        )
    tenant.name, tenant.short_name = body.name, body.short_name
    for org in orgs:
        org.is_default = org.code == selected.code
    # defaultOrg-only 也必须更新父行一次，交给 mapper 统一推进 lock_version。
    flag_modified(tenant, "name")
    await session.flush()
    await insert_audit(
        session,
        actor=f"{user.name}（{user.id}）",
        action="修改租户基础信息",
        target=(
            f"租户「{tenant.code}」：{tenant.name} / {tenant.short_name}；"
            f"默认组织 {selected.code}（{selected.name}）"
        ),
        ip=ip,
        created_by=user.id,
    )
    return _out(tenant, orgs)


@router.get("", response_model=TenantSettingsOut, summary="租户基础信息（非全量系统设置）")
async def get_tenant_settings(session: SessionDep, _user: ViewUser) -> TenantSettingsOut:
    return _out(await _tenant(session), await _orgs(session))


@router.put("", response_model=TenantSettingsOut, summary="保存租户名称、简称及默认组织")
async def put_tenant_settings(
    body: TenantSettingsIn, session: SessionDep, user: ManageUser, request: Request
) -> TenantSettingsOut:
    ip = request.headers.get("X-Forwarded-For", "").split(",", 1)[0].strip()
    ip = (ip or (request.client.host if request.client else ""))[:64]
    return await update_tenant(session, body, user, ip)
