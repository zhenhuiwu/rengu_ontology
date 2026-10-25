"""业务逻辑。不 commit；跨模块只通过 app/contracts 的 Protocol 调用。

（ext-systems 详情里的「关联动作类型」直接查 action_type：action 和 integration 同属 Palantir专家，
动作类型本来就外键到 ext_system。）
"""

from collections.abc import Sequence
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import clock
from app.core.errors import Conflict, DuplicateKey
from app.modules.action.models import ActionType
from app.modules.integration.models import ExtSystem, ExtSystemStatus
from app.modules.integration.repo import ExtSystemRepo
from app.modules.integration.schemas import ExtSystemIn

PROD = "生产"
_MSG_502 = "对方返回 502（网关错误），系统处于降级状态，请联系负责人"


def is_writeback_target(s: ExtSystem) -> bool:
    """前端 writebackSystems()：生产环境且未停用。"""
    return s.env == PROD and s.status != ExtSystemStatus.disabled


class ExtSystemService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = ExtSystemRepo(session)

    async def search(self, *, writeback: bool, kw: str | None) -> list[ExtSystem]:
        """kw 匹配名称 / 编号 / 厂商（前端外部系统页没有搜索框，匹配哪些字段是【推断】）。"""
        rows = list(await self.repo.all_ordered())
        if writeback:
            rows = [s for s in rows if is_writeback_target(s)]
        if kw:
            rows = [s for s in rows if kw in s.name or kw in s.code or kw in s.vendor]
        return rows

    async def action_counts(self) -> dict[int, int]:
        """每个系统被多少个（未下线的）动作类型写回：列表悬停文字「N 个动作 / 无动作写回」。"""
        stmt = (
            select(ActionType.sys_id, func.count())
            .where(ActionType.sys_id.is_not(None), ActionType.retired_release_id.is_(None))
            .group_by(ActionType.sys_id)
        )
        return {sid: n for sid, n in (await self.session.execute(stmt)).all()}

    async def get(self, code: str) -> tuple[ExtSystem, Sequence[ActionType]]:
        s = await self.repo.get_by_code_or_404(code)
        stmt = (
            select(ActionType)
            .where(ActionType.sys_id == s.id, ActionType.retired_release_id.is_(None))
            .order_by(ActionType.id)
        )
        return s, (await self.session.scalars(stmt)).all()

    # ---- 写入（POST / PUT /ext-systems，直接生效，不走变更单：文档 §3.1） ----

    async def _check_name(self, name: str, exclude_id: int | None = None) -> None:
        stmt = select(ExtSystem.id).where(ExtSystem.name == name)
        if exclude_id is not None:
            stmt = stmt.where(ExtSystem.id != exclude_id)
        if await self.session.scalar(stmt) is not None:
            msg = "系统名称已存在"
            raise DuplicateKey(msg, details={"fields": [{"field": "name", "message": msg}]})

    async def _next_code(self) -> str:
        """前端 saveEdit：EXT-N + 两位序号（EXT-N01…）。

        取已有 EXT-N 最大序号 + 1（前端用个数 + 1，删过就会撞号）。
        """
        stmt = select(ExtSystem.code).where(ExtSystem.code.like("EXT-N%"))
        codes = (await self.session.scalars(stmt)).all()
        nums = [int(c[5:]) for c in codes if c[5:].isdigit()]
        return f"EXT-N{(max(nums, default=0) + 1):02d}"

    async def create(self, body: ExtSystemIn, actor: str) -> ExtSystem:
        await self._check_name(body.name)
        obj = ExtSystem(code=await self._next_code(), **_values(body))
        return await self.repo.create(obj, actor=actor)

    async def update(self, code: str, body: ExtSystemIn, actor: str) -> ExtSystem:
        obj = await self.repo.get_by_code_or_404(code)
        await self._check_name(body.name, exclude_id=obj.id)
        values = _values(body)
        if body.usage is None:
            values.pop("usage")  # 前端表单没有 usage（不做写回的集成的用途说明），编辑时保留原值
        return await self.repo.update(obj, values, actor=actor)

    async def test_call(self, code: str, user_label: str) -> dict:
        """测试调用（文档 §3.1）：dev / test 走 mock 适配器，不连真实系统。

        停用 → 409 EXT_SYSTEM_DISABLED；降级 → 502 失败；其余 200 成功。数值照抄前端 testCall()。
        【推断】writeback_call 表还没建（integration_runs 阶段），测试结果只返回、不落库；
        编号 = 系统编号去掉 EXT- + -T + 6 位随机（dev 时钟固定，不能用时间做编号）。
        """
        s = await self.repo.get_by_code_or_404(code)
        if s.status == ExtSystemStatus.disabled:
            raise Conflict("该系统已停用，先在“编辑”里改为正常再测试", code="EXT_SYSTEM_DISABLED")
        degraded = s.status == ExtSystemStatus.degraded
        t = clock.local_now()
        return {
            "id": f"{s.code[4:]}-T{uuid4().hex[:6].upper()}",
            "sys_id": s.code,
            "time": f"{t:%Y-%m-%d %H:%M:%S}",
            "action": "测试调用（连通性）",
            "target": "—",
            "method": "GET",
            "path": "/health",
            "idem_key": None,
            "ms": 3120 if degraded else 140 + (s.timeout_ms % 97),
            "http": 502 if degraded else 200,
            "result": "失败" if degraded else "成功",
            "attempt": 1,
            "actor": user_label,
            "msg": _MSG_502 if degraded else "OK · 鉴权通过，心跳正常",
        }


def retry_text(idempotent: bool, max_retry: int) -> str:
    """同前端 ExternalSystems.vue retryText()。"""
    if idempotent and max_retry:
        return f"外部明确失败时最多 {max_retry} 次；超时不重试，标记结果未知"
    return "不重试：外部明确失败转人工；超时标记结果未知，人工对账"


def _values(b: ExtSystemIn) -> dict:
    max_retry = b.max_retry if b.idempotent else 0
    return {
        "name": b.name,
        "vendor": b.vendor,
        "category": b.category,
        "endpoint": b.endpoint,
        "auth_type": b.auth,
        "auth_detail": b.auth_detail,
        "idempotent": b.idempotent,
        "idem_header": b.idem_key if b.idempotent else "-",
        "timeout_ms": b.timeout_ms,
        "max_retry": max_retry,
        "retry_text": b.retry or retry_text(b.idempotent, max_retry),
        "rate_limit": b.rate_limit,
        "owner": b.owner,
        "status": b.status,
        "env": b.env,
        "usage": b.usage,
    }
