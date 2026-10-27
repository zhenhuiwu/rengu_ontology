"""业务逻辑。不 commit；跨模块只通过 app/contracts 的 Protocol 调用。"""

import re
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.common.auth import CurrentUser, check_func
from app.common.ontology import set_ontology_scope
from app.common.pagination import PageParams
from app.core.clock import now
from app.core.config import get_settings
from app.core.errors import AppError, DuplicateKey, Forbidden, NotFound
from app.core.schema import DATETIME_SEC_FORMAT
from app.db.session import qa_readonly_engine_kwargs
from app.modules.admin.imports import parse_import_csv as parse_import_csv
from app.modules.admin.imports import row_length_error
from app.modules.admin.models import (
    AuditLog,
    AuditResult,
    SysOrg,
    SysRole,
    SysRoleMember,
    SysUser,
    UserStatus,
)
from app.modules.admin.repo import (
    AuditFilter,
    AuditLogRepository,
    SysUserRepository,
    UserFilter,
    insert_audit,
    lock_code_seq,
)
from app.modules.admin.roles import role_names, sync_primary_role
from app.modules.admin.schemas import ImportRowIn, ImportRowOut, UserCreateIn, UserUpdateIn, department_name


def make_filter(
    start: datetime,
    end: datetime,
    action: str | None,
    result: str | None,
    field: str,
    kw: str | None,
    ontology_id: int | None = None,
) -> AuditFilter:
    """「全部」/ 空串 = 不限（前端下拉的默认值就是「全部」）；kw 先 trim（前端 kw.value.trim()）。"""
    k = (kw or "").strip()
    return AuditFilter(
        start=start,
        end=end,
        action=None if action in (None, "", "全部") else action,
        result=None if result in (None, "", "全部") else AuditResult(result),
        field=field,
        kw=k or None,
        ontology_id=ontology_id,
    )


def actor_of(user: CurrentUser) -> str:
    """mock 的 actor 展示串：「姓名（用户编号）」。"""
    return f"{user.name}（{user.id}）"


class AuditLogService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = AuditLogRepository(session)

    async def page(self, f: AuditFilter, cursor: str | None, size: int) -> tuple[list[AuditLog], str | None]:
        return await self.repo.list_cursor(f, cursor, size)

    async def actions(self) -> list[str]:
        return await self.repo.distinct_actions()

    async def export(self, f: AuditFilter, user: CurrentUser, ip: str) -> str:
        """冻结可下载成员，与导出审计在同一事务中提交。"""
        from app.modules.admin.exports import create_manifest

        fmt = DATETIME_SEC_FORMAT
        tz = ZoneInfo(get_settings().display_tz)
        span = f"{f.start.astimezone(tz).strftime(fmt)} 至 {f.end.astimezone(tz).strftime(fmt)}"
        row = await insert_audit(
            self.session,
            actor=actor_of(user),
            action="导出",
            target=f"审计日志（{span}）",
            ip=ip,
            created_by=user.id,
        )
        job = await create_manifest(self.session, f, user, row)
        return job.code


# ---- 用户（Users.vue，api-index §4.1）----

# Legacy seed/test reference only; live department candidates come from department_options().
ADMIN_DEPTS = [
    "华东事业部 / 运营中心",
    "华东事业部 / 财务数据组",
    "华东事业部 / 供应链",
    "华东事业部 / 会员中心",
    "集团总部 / 数据平台",
    "集团总部 / 信息安全",
    "华南事业部 / 运营中心",
]
# 角色名 = mock/roles.ts ROLES[].name（Users.vue 的 ROLE_OPTS）。校验已改查 sys_role（roles.role_names）；
# 这个常量只留给测试对照 seed。
ROLE_NAMES = [
    "系统管理员",
    "本体建模师",
    "数据分析师",
    "动作审批人",
    "业务查看",
    "门店店长",
    "会员运营",
    "大区经理（华东）",
]
_EMAIL = re.compile(r"^\S+@\S+$")


async def pending_approvals(session: AsyncSession, users: list[SysUser]) -> dict[str, int]:
    from app.modules.dynamic.approvals import pending_approval_counts as action_counts
    from app.modules.static.approvals import pending_approval_counts as cr_counts

    codes = {u.code for u in users if u.status == UserStatus.active}
    if not codes:
        return {}
    names = {u.name for u in users if u.code in codes}
    rows = await session.execute(
        select(SysUser.name, func.min(SysUser.code), func.count())
        .where(SysUser.status == UserStatus.active, SysUser.name.in_(names))
        .group_by(SysUser.name)
    )
    unique_names = {name: code for name, code, count in rows if count == 1}
    from app.modules.ontology.models import OntologyWorkspace

    totals = dict.fromkeys(codes, 0)
    workspace_ids = list(await session.scalars(select(OntologyWorkspace.id).order_by(OntologyWorkspace.id)))
    # A separate read-only pool avoids holding every request connection while waiting for another scope.
    settings = get_settings()
    engine = create_async_engine(
        settings.database_url,
        poolclass=NullPool,
        **qa_readonly_engine_kwargs(settings.qa_statement_timeout_ms),
    )
    try:
        scoped_sessions = async_sessionmaker(engine, expire_on_commit=False)
        # shortcut: one read session per workspace; use a dedicated aggregate if workspace scale requires it.
        for ontology_id in workspace_ids:
            async with scoped_sessions() as scoped:
                await set_ontology_scope(scoped, ontology_id)
                cr = await cr_counts(scoped, codes, unique_names)
                actions = await action_counts(scoped, codes)
                for code in codes:
                    totals[code] += cr.get(code, 0) + actions.get(code, 0)
    finally:
        await engine.dispose()
    return totals


async def active_admin_ids(session: AsyncSession) -> set[int]:
    """Recovery administrators are actual active users with both user and role management grants."""
    rows = await session.execute(
        select(SysUser.id, SysRole.func)
        .join(SysRoleMember, SysRoleMember.user_id == SysUser.id)
        .join(SysRole, SysRole.id == SysRoleMember.role_id)
        .where(SysUser.status == UserStatus.active)
    )
    grants: dict[int, set[str]] = {}
    for user_id, permissions in rows:
        if isinstance(permissions, dict):
            grants.setdefault(user_id, set()).update(
                page for page in ("users", "roles") if permissions.get(page) == "manage"
            )
    return {user_id for user_id, pages in grants.items() if {"users", "roles"} <= pages}


def make_user_filter(dept: str | None, status: str | None, kw: str | None) -> UserFilter:
    """「全部」/ 空 = 不限；kw 不 trim（Users.vue 直接 includes(kw.value)）。"""
    return UserFilter(
        dept=None if not dept or dept == "全部" else dept,
        status=None if not status or status == "全部" else UserStatus(status),
        kw=kw or None,
    )


class ValidationError422(AppError):
    status_code, code, message = 422, "VALIDATION_FAILED", "参数校验失败"


async def department_options(session: AsyncSession) -> list[str]:
    organizations = list(await session.scalars(select(SysOrg.name).order_by(SysOrg.seq, SysOrg.id)))
    existing = list(
        await session.scalars(
            select(SysUser.dept).where(SysUser.dept.is_not(None)).distinct().order_by(SysUser.dept)
        )
    )
    return list(dict.fromkeys(value for value in [*organizations, *existing] if value and value.strip()))


async def _check_role(session: AsyncSession, role: str) -> SysRole:
    selected = await session.scalar(select(SysRole).where(SysRole.name == role))
    if selected is None:
        raise ValidationError422(f"角色不存在：{role}", details={"role": role})
    return selected


class UserService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = SysUserRepository(session)

    async def page(self, f: UserFilter, page: PageParams) -> tuple[list[SysUser], int]:
        rows, total = await self.repo.list_page(self.repo.list_stmt(f), page)
        return list(rows), total

    async def _get(self, code: str) -> SysUser:
        u = await self.session.scalar(
            self.repo.listed()
            .where(SysUser.code == code)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if u is None:
            raise NotFound("用户不存在", details={"id": code})
        return u

    async def _new(
        self, *, name: str, account: str, dept: str, role: str, status: UserStatus, actor: CurrentUser
    ) -> SysUser:
        check_func(actor, "roles", "manage")
        selected = await _check_role(self.session, role)
        dept = department_name(dept)
        n = await self.repo.next_code()
        u = SysUser(
            code=f"U{n:06d}",
            name=name,
            account=account,
            dept=dept,
            role=role,
            status=status,
            joined_on=now().astimezone(ZoneInfo(get_settings().display_tz)).date(),
        )
        u = await self.repo.create(u, actor=actor.id)
        await sync_primary_role(self.session, u.id, None, selected, actor.id)
        await self.session.flush()
        return u

    async def create(self, body: UserCreateIn, user: CurrentUser) -> SysUser:
        account = body.account.strip()
        if await self.repo.existing_accounts([account]):
            raise DuplicateKey("账号已存在", details={"account": account})
        return await self._new(
            name=body.name.strip(),
            account=account,
            dept=body.dept,
            role=body.role,
            status=body.status,
            actor=user,
        )

    async def update(self, code: str, body: UserUpdateIn, user: CurrentUser, *, ip: str = "") -> SysUser:
        selected = await _check_role(self.session, body.role)
        await lock_code_seq(self.session, "sys_user.lifecycle")
        u = await self._get(code)
        previous_status = u.status
        await self._check_status_change(u, body.status, user)
        previous = u.role
        if previous != body.role:
            check_func(user, "roles", "manage")
        values = {"name": body.name.strip(), "dept": body.dept, "role": body.role, "status": body.status}
        u = await self.repo.update(u, values, actor=user.id, lock_version=body.lock_version)
        await sync_primary_role(self.session, u.id, previous, selected, user.id)
        await self.session.flush()
        await self._audit_status(u, previous_status, user, ip)
        return u

    async def preview(self, rows: list[ImportRowIn]) -> list[ImportRowOut]:
        """只校验不写库：必填、邮箱格式、部门文本规范、实际角色、账号已存在、文件内账号重复。"""
        taken = await self.repo.existing_accounts(
            [r.account.strip() for r in rows if r.account.strip() and len(r.account.strip()) <= 128]
        )
        seen: set[str] = set()
        names = set(await role_names(self.session))
        out = []
        for r in rows:
            r = ImportRowIn(
                name=r.name.strip(), account=r.account.strip(), dept=r.dept.strip(), role=r.role.strip()
            )
            err = row_length_error(r)
            if not err:
                try:
                    r.dept = department_name(r.dept)
                except ValueError as error:
                    err = str(error)
            if err:
                pass
            elif not r.name:
                err = "姓名为空"
            elif not _EMAIL.match(r.account):
                err = "账号不是邮箱"
            elif r.role not in names:
                err = "角色不存在"
            elif r.account in taken:
                err = "账号已存在，导入时跳过"
            elif r.account in seen:
                err = "文件内账号重复"
            seen.add(r.account)
            out.append(ImportRowOut(**r.model_dump(), error=err))
        return out

    async def import_rows(self, rows: list[ImportRowIn], user: CurrentUser) -> int:
        """按预览规则逐行校验，有 error 的行跳过（账号已存在也跳过），其余新建为「待激活」。"""
        check_func(user, "roles", "manage")
        n = 0
        for r in await self.preview(rows):
            if r.error:
                continue
            await self._new(
                name=r.name,
                account=r.account,
                dept=r.dept,
                role=r.role,
                status=UserStatus.pending,
                actor=user,
            )
            n += 1
        return n

    async def _check_status_change(
        self, u: SysUser, status: UserStatus, user: CurrentUser, transfer_to: str | None = None
    ) -> None:
        if status == u.status or status == UserStatus.active:
            return
        if u.code == user.id:
            raise Forbidden("不能将当前登录用户改为非启用状态")
        if u.status == UserStatus.active:
            admins = await active_admin_ids(self.session)
            if admins == {u.id}:
                raise AppError(
                    "须保留至少一名启用且可管理用户和角色的管理员", code="STATE_INVALID", status_code=409
                )
        pending = (await pending_approvals(self.session, [u])).get(u.code, 0)
        if pending > 0 and not transfer_to:
            raise ValidationError422(
                "名下有待办审批，需要先完成待办后再更改状态",
                code="TRANSFER_REQUIRED",
                details={"pending": pending},
            )
        if pending > 0:
            raise AppError("请先完成待审批事项后再停用此用户", code="TRANSFER_UNAVAILABLE", status_code=409)
        if transfer_to:
            target = await self.repo.get_listed(transfer_to)
            if target is None or target.code == u.code or target.status != UserStatus.active:
                raise ValidationError422("转交人须是另一个启用中的用户", details={"transferTo": transfer_to})

    async def _audit_status(self, u: SysUser, previous: UserStatus, user: CurrentUser, ip: str) -> None:
        if previous == u.status:
            return
        await insert_audit(
            self.session,
            actor=actor_of(user),
            action="修改用户状态",
            target=f"用户「{u.code}」：{previous.value} → {u.status.value}",
            ip=ip[:64],
            created_by=user.id,
        )

    async def enable(self, code: str, user: CurrentUser, *, ip: str = "") -> SysUser:
        await lock_code_seq(self.session, "sys_user.lifecycle")
        u = await self._get(code)
        previous = u.status
        u = await self.repo.update(u, {"status": UserStatus.active}, actor=user.id)
        await self._audit_status(u, previous, user, ip)
        return u

    async def disable(
        self, code: str, transfer_to: str | None, user: CurrentUser, *, ip: str = ""
    ) -> SysUser:
        """停用。名下有待办审批时 transferTo 必填（api-index【待定】，本分支按必填做），且须是另一个启用用户。

        待办来自真实CR和动作指派；未实现安全转交时拒绝停用，不改审批归属。"""
        await lock_code_seq(self.session, "sys_user.lifecycle")
        u = await self._get(code)
        if u.status == UserStatus.disabled:
            return u
        await self._check_status_change(u, UserStatus.disabled, user, transfer_to)
        previous = u.status
        u = await self.repo.update(u, {"status": UserStatus.disabled}, actor=user.id)
        await self._audit_status(u, previous, user, ip)
        return u
