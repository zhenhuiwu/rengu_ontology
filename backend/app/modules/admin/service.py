"""业务逻辑。不 commit；跨模块只通过 app/contracts 的 Protocol 调用。"""

import csv
import io
import re
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from app.common.auth import CurrentUser
from app.common.pagination import PageParams
from app.core.clock import now
from app.core.config import get_settings
from app.core.errors import AppError, DuplicateKey, NotFound
from app.core.schema import DATETIME_SEC_FORMAT
from app.modules.admin.models import AuditLog, AuditResult, SysUser, UserStatus
from app.modules.admin.repo import (
    AuditFilter,
    AuditLogRepository,
    SysUserRepository,
    UserFilter,
    insert_audit,
)
from app.modules.admin.roles import role_names
from app.modules.admin.schemas import ImportRowIn, ImportRowOut, UserCreateIn, UserUpdateIn


def make_filter(
    start: datetime,
    end: datetime,
    action: str | None,
    result: str | None,
    field: str,
    kw: str | None,
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
        """桩：不真正生成文件，只返回 jobId，并按 api-index §4.3「导出本身也写一条审计」。"""
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
        return f"EXP-{row.code}"


# ---- 用户（Users.vue，api-index §4.1）----

# 部门下拉 = mock/admin.ts DEPTS（ADMIN_DEPTS）原样、原顺序；部门表由 PDE 补
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
IMPORT_HEADER = {"姓名": "name", "账号": "account", "部门": "dept", "角色": "role"}


def pending_approvals(u: SysUser) -> int:
    """名下待办审批数，与 mock/admin.ts pendingApprovals 一致：启用 ? (编号数字 × 13) % 5 : 0。

    [推断] 动作审批、口径评审接上后改成真实计数。"""
    if u.status != UserStatus.active:
        return 0
    digits = u.code[1:]
    return (int(digits) * 13) % 5 if digits.isdigit() else 0


def make_user_filter(dept: str | None, status: str | None, kw: str | None) -> UserFilter:
    """「全部」/ 空 = 不限；kw 不 trim（Users.vue 直接 includes(kw.value)）。"""
    return UserFilter(
        dept=None if not dept or dept == "全部" else dept,
        status=None if not status or status == "全部" else UserStatus(status),
        kw=kw or None,
    )


class ValidationError422(AppError):
    status_code, code, message = 422, "VALIDATION_FAILED", "参数校验失败"


async def _check_role(session: AsyncSession, role: str) -> None:
    names = await role_names(session)
    if role not in names:
        raise ValidationError422(f"角色不存在：{role}", details={"role": role, "allowed": names})


def parse_import_csv(text: str) -> list[ImportRowIn]:
    """模板 CSV（UTF-8，可带 BOM）：首行表头 姓名,账号,部门,角色（顺序不限，缺列当空）。"""
    reader = csv.reader(io.StringIO(text.lstrip("\ufeff")))
    header = next(reader, None)
    if not header:
        return []
    keys = [IMPORT_HEADER.get(h.strip()) for h in header]
    out = []
    for line in reader:
        if not any(c.strip() for c in line):
            continue
        vals = {k: (line[i].strip() if i < len(line) else "") for i, k in enumerate(keys) if k}
        out.append(ImportRowIn(**vals))
    return out


class UserService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = SysUserRepository(session)

    async def page(self, f: UserFilter, page: PageParams) -> tuple[list[SysUser], int]:
        rows, total = await self.repo.list_page(self.repo.list_stmt(f), page)
        return list(rows), total

    async def _get(self, code: str) -> SysUser:
        u = await self.repo.get_listed(code)
        if u is None:
            raise NotFound("用户不存在", details={"id": code})
        return u

    async def _new(
        self, *, name: str, account: str, dept: str, role: str, status: UserStatus, actor: str
    ) -> SysUser:
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
        return await self.repo.create(u, actor=actor)

    async def create(self, body: UserCreateIn, user: CurrentUser) -> SysUser:
        await _check_role(self.session, body.role)
        account = body.account.strip()
        if await self.repo.existing_accounts([account]):
            raise DuplicateKey("账号已存在", details={"account": account})
        return await self._new(
            name=body.name.strip(),
            account=account,
            dept=body.dept,
            role=body.role,
            status=body.status,
            actor=user.id,
        )

    async def update(self, code: str, body: UserUpdateIn, user: CurrentUser) -> SysUser:
        await _check_role(self.session, body.role)
        u = await self._get(code)
        values = {"name": body.name.strip(), "dept": body.dept, "role": body.role, "status": body.status}
        return await self.repo.update(u, values, actor=user.id)

    async def preview(self, rows: list[ImportRowIn]) -> list[ImportRowOut]:
        """只校验不写库：必填、邮箱格式、部门 / 角色在下拉里、账号已存在、文件内账号重复。"""
        taken = await self.repo.existing_accounts([r.account.strip() for r in rows if r.account.strip()])
        seen: set[str] = set()
        names = set(await role_names(self.session))
        out = []
        for r in rows:
            r = ImportRowIn(
                name=r.name.strip(), account=r.account.strip(), dept=r.dept.strip(), role=r.role.strip()
            )
            err = None
            if not r.name:
                err = "姓名为空"
            elif not _EMAIL.match(r.account):
                err = "账号不是邮箱"
            elif r.dept not in ADMIN_DEPTS:
                err = "部门不存在"
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
                actor=user.id,
            )
            n += 1
        return n

    async def enable(self, code: str, user: CurrentUser) -> SysUser:
        u = await self._get(code)
        return await self.repo.update(u, {"status": UserStatus.active}, actor=user.id)

    async def disable(self, code: str, transfer_to: str | None, user: CurrentUser) -> SysUser:
        """停用。名下有待办审批时 transferTo 必填（api-index【待定】，本分支按必填做），且须是另一个启用用户。

        转交本身是桩：审批数据在动作 / 变更单模块，接上后在这里调契约转交。"""
        u = await self._get(code)
        if u.status == UserStatus.disabled:
            return u
        if pending_approvals(u) > 0 and not transfer_to:
            raise ValidationError422(
                "名下有待办审批，需要选择转交人",
                code="TRANSFER_REQUIRED",
                details={"pending": pending_approvals(u)},
            )
        if transfer_to:
            t = await self.repo.get_listed(transfer_to)
            if t is None or t.code == u.code or t.status != UserStatus.active:
                raise ValidationError422("转交人须是另一个启用中的用户", details={"transferTo": transfer_to})
        return await self.repo.update(u, {"status": UserStatus.disabled}, actor=user.id)
