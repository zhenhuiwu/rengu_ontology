"""组织（org）行级规则：每个本体按对象类型配置「组织列」，角色范围用 prop = ORG_PROP 引用它。

通用规则，不含任何行业表名 / 字段名：
- 配置存 platform_setting（code = SETTING_CODE），value = {"<ontology_id>": {"<对象类型编号>": "<字段>"}}；
  字段必须是该对象类型受控表（sql_guard.governed_tables）的白名单列，且该表不是继承表。
- 角色范围 {prop: "@org", op: "="/"∈", values: [...]} → 执行时换成配置的字段做 = / IN。
  对象类型没配组织列（或配置失效）→ 该范围 DENY_ALL（宁严勿宽，绝不当作不限制）。
- 没有组织列的表不能用这条规则：`GET /row-scope/org-columns` 的 gaps 列出这些对象类型。
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from pydantic import Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.common.auth import CurrentUser, require_func
from app.common.ontology import require_ontology_id
from app.common.sql_guard import ORG_PROP as ORG_PROP  # 单一定义在基础层，此处再导出
from app.core.errors import AppError
from app.core.schema import ApiModel
from app.db.session import SessionDep
from app.modules.admin.models import PlatformSetting
from app.modules.admin.repo import insert_audit

ORG_LABEL = "所属组织"
SETTING_CODE = "row-scope:org-columns"

router = APIRouter(prefix="/row-scope/org-columns", tags=["admin"])
ViewUser = Annotated[CurrentUser, Depends(require_func("roles", "view"))]
ManageUser = Annotated[CurrentUser, Depends(require_func("roles", "manage"))]


class OrgColumnInvalid(AppError):
    status_code, code, message = 422, "ORG_COLUMN_INVALID", "组织列配置无效"


def _scope_tables(ontology_id: int) -> dict[str, Any]:
    from app.common.sql_guard import governed_tables

    return {g.object_type: g for g in governed_tables(ontology_id).values() if not g.inherit}


async def _setting(session: AsyncSession) -> PlatformSetting | None:
    return await session.scalar(select(PlatformSetting).where(PlatformSetting.code == SETTING_CODE))


async def load_org_fields(session: AsyncSession) -> dict[str, str]:
    """当前本体：对象类型编号 → 组织字段（只返回仍有效的配置）。"""
    ontology_id = session.info.get("ontology_id")
    if ontology_id is None:
        return {}
    row = await _setting(session)
    raw = (row.value or {}).get(str(ontology_id), {}) if row is not None else {}
    tables = _scope_tables(ontology_id)
    return {
        code: field
        for code, field in raw.items()
        if isinstance(field, str) and code in tables and field in tables[code].columns
    }


class OrgColumnsIn(ApiModel):
    columns: dict[str, str] = Field(description="对象类型编号 → 组织字段（受控表的白名单列）；整体替换")


class OrgColumnItem(ApiModel):
    object_type_code: str
    field: str | None = Field(description="已配置的组织字段；null = 未配置（gap）")
    candidates: list[str] = Field(description="可选字段（受控表白名单列）")


class OrgColumnsOut(ApiModel):
    items: list[OrgColumnItem]
    gaps: list[str] = Field(description="没配组织列的受控对象类型：这些对象类型上的 @org 范围一律拒绝")


async def org_columns_report(session: AsyncSession) -> OrgColumnsOut:
    tables = _scope_tables(require_ontology_id(session))
    configured = await load_org_fields(session)
    items = [
        OrgColumnItem(
            object_type_code=code,
            field=configured.get(code),
            candidates=sorted(k for k in g.columns if "." not in k),
        )
        for code, g in sorted(tables.items())
    ]
    return OrgColumnsOut(items=items, gaps=[i.object_type_code for i in items if i.field is None])


async def save_org_columns(
    session: AsyncSession, columns: dict[str, str], user: CurrentUser, ip: str
) -> OrgColumnsOut:
    ontology_id = require_ontology_id(session)
    tables = _scope_tables(ontology_id)
    bad = {
        code: field
        for code, field in columns.items()
        if code not in tables or field not in tables[code].columns
    }
    if bad:
        raise OrgColumnInvalid("对象类型不受控或字段不在受控表白名单里", details={"invalid": bad})
    row = await _setting(session)
    if row is None:
        row = PlatformSetting(code=SETTING_CODE, value={}, created_by=user.id)
        session.add(row)
    value = dict(row.value or {})
    value[str(ontology_id)] = dict(sorted(columns.items()))
    row.value = value
    flag_modified(row, "value")
    await session.flush()
    await insert_audit(
        session,
        actor=f"{user.name}（{user.id}）",
        action="修改组织列",
        target="；".join(f"{c}={f}" for c, f in sorted(columns.items())) or "清空",
        ip=ip[:64],
        created_by=user.id,
    )
    return await org_columns_report(session)


@router.get("", response_model=OrgColumnsOut, summary="当前本体的组织列配置与缺口")
async def get_org_columns(_user: ViewUser, session: SessionDep) -> OrgColumnsOut:
    return await org_columns_report(session)


@router.put("", response_model=OrgColumnsOut, summary="整体替换当前本体的组织列配置")
async def put_org_columns(
    body: OrgColumnsIn, user: ManageUser, session: SessionDep, request: Request
) -> OrgColumnsOut:
    ip = request.client.host if request.client else ""
    return await save_org_columns(session, body.columns, user, ip)
