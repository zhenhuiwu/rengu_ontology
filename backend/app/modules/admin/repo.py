"""数据访问（继承 app.common.repository.BaseRepository）。

审计日志：
- `insert_audit(session, ...)`：只追加写一条。app/common/audit.py（PDE）落地时可以直接调它
  （那个文件现在仍是只打日志的桩，本分支不改）。
- `AuditLogRepository.list_cursor(...)`：按 (time desc, id desc) keyset 取一页，多取一条判断有没有下一页。
"""

import base64
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import BigInteger as BigIntegerType
from sqlalchemy import Select, func, or_, select, text, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.repository import BaseRepository
from app.core.clock import now
from app.modules.admin.models import AuditLog, AuditResult, SysUser, UserStatus


def _new_code(at: datetime) -> str:
    # mock 的 id 是 AL<毫秒>；新写入同毫秒可能多条，再加 4 位随机十六进制避免撞唯一键
    return f"AL{int(at.timestamp() * 1000)}{secrets.token_hex(2)}"


async def lock_code_seq(session: AsyncSession, key: str) -> None:
    """编号「最大值 + 1」串行化：按表取事务级 advisory lock，提交 / 回滚时自动释放。

    两个请求同时新建时，后来的一个等前一个提交后再取最大值，不会撞号（不用计数表，不需要迁移）。
    key 用表名（sys_role / sys_role_scope / sys_user / datasource）。
    """
    await session.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": f"rengu:code:{key}"})


async def insert_audit(
    session: AsyncSession,
    *,
    actor: str,
    action: str,
    target: str,
    ip: str,
    result: AuditResult | str = AuditResult.success,
    time: datetime | None = None,
    created_by: str | None = None,
) -> AuditLog:
    """写一条审计（与业务写入同一事务，不 commit）。result 可传枚举或中文「成功 / 失败 / 拒绝」。"""
    at = time or now()
    # Machine caller (API key / service account) recorded by authenticate_key; humans → principal_kind "user".
    caller = session.info.get("api_caller") or {}
    row = AuditLog(
        code=_new_code(at),
        time=at,
        actor=actor,
        action=action,
        target=target,
        ip=ip,
        result=result if isinstance(result, AuditResult) else AuditResult(result),
        created_by=created_by,
        ontology_id=session.info.get("ontology_id"),
        principal_kind=caller.get("principal_kind", "user"),
        service_account_code=caller.get("service_account_code"),
        api_key_prefix=caller.get("api_key_prefix"),
    )
    session.add(row)
    await session.flush()
    return row


# ---- 游标：不透明字符串，前端不解析（src/mock/paginate.ts）。"k" + base64url("<微秒时间戳>:<id>") ----


@dataclass(frozen=True)
class AuditCursor:
    time_us: int
    id: int

    def encode(self) -> str:
        raw = f"{self.time_us}:{self.id}".encode()
        return "k" + base64.urlsafe_b64encode(raw).decode().rstrip("=")

    @property
    def time(self) -> datetime:
        return datetime.fromtimestamp(self.time_us / 1_000_000, tz=UTC)

    @classmethod
    def of(cls, row: AuditLog) -> "AuditCursor":
        t = row.time
        return cls(int(t.timestamp()) * 1_000_000 + t.microsecond, row.id)

    @classmethod
    def decode(cls, cursor: str | None) -> "AuditCursor | None":
        """解不开的游标当没传（从第一页开始），和前端 paginateCursor 的 decode 一样不报错。"""
        if not cursor or cursor[0] != "k":
            return None
        try:
            body = cursor[1:]
            raw = base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)).decode()
            t, i = raw.split(":")
            return cls(int(t), int(i))
        except (ValueError, UnicodeDecodeError):
            return None


@dataclass(frozen=True)
class AuditFilter:
    start: datetime
    end: datetime
    action: str | None = None
    result: AuditResult | None = None
    field: str = "actor"
    kw: str | None = None
    ontology_id: int | None = None


class AuditLogRepository(BaseRepository[AuditLog]):
    model = AuditLog
    not_found_message = "审计记录不存在"

    def _filtered(self, f: AuditFilter):
        stmt = select(AuditLog).where(AuditLog.time >= f.start, AuditLog.time <= f.end)
        if f.ontology_id is not None:
            stmt = stmt.where(AuditLog.ontology_id == f.ontology_id)
        if f.action:
            stmt = stmt.where(AuditLog.action == f.action)
        if f.result is not None:
            stmt = stmt.where(AuditLog.result == f.result)
        if f.kw:
            col = AuditLog.actor if f.field == "actor" else AuditLog.target
            # 前端 r[field].includes(k)：区分大小写的子串，不当通配符 → strpos 而不是 LIKE
            stmt = stmt.where(func.strpos(col, f.kw) > 0)
        return stmt

    async def list_cursor(
        self, f: AuditFilter, cursor: str | None, size: int
    ) -> tuple[list[AuditLog], str | None]:
        stmt = self._filtered(f)
        c = AuditCursor.decode(cursor)
        if c is not None:
            stmt = stmt.where(tuple_(AuditLog.time, AuditLog.id) < tuple_(c.time, c.id))
        stmt = stmt.order_by(AuditLog.time.desc(), AuditLog.id.desc()).limit(size + 1)
        rows = list((await self.session.scalars(stmt)).all())
        more = len(rows) > size
        rows = rows[:size]
        return rows, (AuditCursor.of(rows[-1]).encode() if more and rows else None)

    async def distinct_actions(self) -> list[str]:
        """去重的动作，按首次出现（时间倒序）排列——和前端 `new Set(AUDIT_LOGS.map(r => r.action))` 同序。"""
        stmt = (
            select(AuditLog.action)
            .group_by(AuditLog.action)
            .order_by(func.max(AuditLog.time).desc(), func.max(AuditLog.id).desc())
        )
        return list((await self.session.scalars(stmt)).all())


# ---- 用户（Users.vue，api-index §4.1）----


@dataclass(frozen=True)
class UserFilter:
    dept: str | None = None
    status: UserStatus | None = None
    kw: str | None = None


class SysUserRepository(BaseRepository[SysUser]):
    model = SysUser
    not_found_message = "用户不存在"

    def listed(self) -> Select[tuple[SysUser]]:
        """用户列表里的人：有 role 的（ADMIN_USERS + 新建 / 导入）；SYS_USERS 占位人 role 为 null，不列出。"""
        return self.base_query().where(SysUser.role.is_not(None))

    def list_stmt(self, f: UserFilter) -> Select[tuple[SysUser]]:
        """顺序 = mock ADMIN_USERS 的数组顺序 = code 升序（U010231、U010238…），新用户编号更大排在后面。"""
        stmt = self.listed()
        if f.dept:
            stmt = stmt.where(SysUser.dept == f.dept)
        if f.status:
            stmt = stmt.where(SysUser.status == f.status)
        if f.kw:
            stmt = stmt.where(
                or_(
                    func.strpos(SysUser.name, f.kw) > 0,
                    func.strpos(func.coalesce(SysUser.account, ""), f.kw) > 0,
                    func.strpos(SysUser.code, f.kw) > 0,
                )
            )
        return stmt.order_by(SysUser.code)

    async def get_listed(self, code: str) -> SysUser | None:
        return await self.session.scalar(self.listed().where(SysUser.code == code))

    async def existing_accounts(self, accounts: list[str]) -> set[str]:
        if not accounts:
            return set()
        rows = await self.session.scalars(select(SysUser.account).where(SysUser.account.in_(accounts)))
        return {a for a in rows if a}

    async def next_code(self) -> int:
        """下一个用户编号的数字部分：现有 U+数字 编号的最大值 + 1（先加锁，并发不撞号）。"""
        await lock_code_seq(self.session, "sys_user")
        mx = await self.session.scalar(
            select(func.max(func.substring(SysUser.code, 2).cast(BigIntegerType))).where(
                SysUser.code.op("~")(r"^U[0-9]+$")
            )
        )
        return int(mx or 0) + 1
