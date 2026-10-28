"""业务逻辑。不 commit；跨模块只通过 app/contracts 的 Protocol 调用。

（ext-systems 详情里的「关联动作类型」直接查 action_type：action 和 integration 同属 Palantir专家，
动作类型本来就外键到 ext_system。）
"""

import time
import uuid
from collections.abc import Sequence

from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.ontology import require_ontology_id
from app.common.pagination import PageParams
from app.core import clock
from app.core.enums import from_api
from app.core.errors import AppError, Conflict, DuplicateKey, NotFound
from app.db.dialects import get_adapter
from app.modules.action.models import ActionType
from app.modules.dynamic.models import ActionRun
from app.modules.integration.adapters import (
    local_demo_config,
    local_demo_matches,
    local_demo_ready,
    local_demo_selection,
    local_demo_selections,
)
from app.modules.integration.models import CallKind, CallResult, ExtSystem, ExtSystemStatus, WritebackCall
from app.modules.integration.repo import ExtSystemRepo, WritebackCallRepo
from app.modules.integration.schemas import ExtSystemIn

PROD = "生产"
CODE_LOCK = "integration.ext_system.code"


def is_writeback_target(s: ExtSystem) -> bool:
    """前端 writebackSystems()：生产环境且未停用。"""
    if getattr(s, "local_only", False):
        return local_demo_matches(s)
    return s.env == PROD and s.status != ExtSystemStatus.disabled


class ExtSystemService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = ExtSystemRepo(session)

    def search_query(self, *, writeback: bool, kw: str | None):
        stmt = select(ExtSystem)
        if writeback:
            permitted = and_(ExtSystem.local_only.is_(False), ExtSystem.env == PROD)
            for target in local_demo_selections() if local_demo_config() else []:
                if local_demo_ready(target):
                    permitted = or_(
                        permitted,
                        and_(
                            ExtSystem.local_only.is_(True),
                            ExtSystem.ontology_id == target.ontology_id,
                            ExtSystem.code == target.system_code,
                            ExtSystem.env == "测试",
                            ExtSystem.category == "Webhook",
                            ExtSystem.endpoint == target.origin,
                        ),
                    )
            stmt = stmt.where(permitted, ExtSystem.status != ExtSystemStatus.disabled)
        if kw:
            term = "%" + kw.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            stmt = stmt.where(
                or_(
                    ExtSystem.name.like(term, escape="\\"),
                    ExtSystem.code.like(term, escape="\\"),
                    ExtSystem.vendor.like(term, escape="\\"),
                )
            )
        return stmt.order_by(ExtSystem.id)

    async def search(self, *, writeback: bool, kw: str | None) -> list[ExtSystem]:
        return list(await self.session.scalars(self.search_query(writeback=writeback, kw=kw)))

    async def page(self, *, writeback: bool, kw: str | None, page: PageParams):
        return await self.repo.list_page(self.search_query(writeback=writeback, kw=kw), page)

    async def action_counts(self, ids: list[int] | None = None) -> dict[int, int]:
        """每个系统被多少个（未下线的）动作类型写回：列表悬停文字「N 个动作 / 无动作写回」。"""
        stmt = (
            select(ActionType.sys_id, func.count())
            .where(ActionType.sys_id.is_not(None), ActionType.retired_release_id.is_(None))
            .group_by(ActionType.sys_id)
        )
        if ids is not None:
            stmt = stmt.where(ActionType.sys_id.in_(ids))
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
        并发：先拿事务级咨询锁（`xact_lock`，PG 上是 pg_advisory_xact_lock，提交 / 回滚时释放），
        两个请求同时新建时后一个等前一个提交后再取号，不会撞号（原来会撞 code 唯一约束报 500）。
        """
        await get_adapter(self.session).xact_lock(self.session, CODE_LOCK)
        stmt = select(ExtSystem.code).where(ExtSystem.code.like("EXT-N%"))
        codes = (await self.session.scalars(stmt)).all()
        nums = [int(c[5:]) for c in codes if c[5:].isdigit()]
        return f"EXT-N{(max(nums, default=0) + 1):02d}"

    async def create(self, body: ExtSystemIn, actor: str) -> ExtSystem:
        await self._check_name(body.name)
        code = await self._next_code()
        oid = require_ontology_id(self.session)
        obj = ExtSystem(code=code, ontology_id=oid, **_values(body))
        target = local_demo_selection(oid, code)
        if target and (oid, code) == (target.ontology_id, target.system_code):
            obj.local_only = True
            if not local_demo_matches(obj):
                raise AppError(
                    "指定本机演示系统的环境、地址或适配器配置不匹配",
                    code="LOCAL_DEMO_INVALID",
                    status_code=422,
                )
        return await self.repo.create(obj, actor=actor)

    async def update(self, code: str, body: ExtSystemIn, actor: str) -> ExtSystem:
        obj = await self.repo.get_by_code_or_404(code)
        await self._check_name(body.name, exclude_id=obj.id)
        values = _values(body)
        if obj.local_only and any(
            values[field] != getattr(obj, field) for field in ("env", "category", "endpoint")
        ):
            raise AppError(
                "本机演示系统不能修改环境、类型或地址", code="LOCAL_DEMO_IMMUTABLE", status_code=409
            )
        for field, column in (("auth", "auth_type"), ("auth_detail", "auth_detail")):
            if field not in body.model_fields_set:
                values.pop(column)
        if body.usage is None:
            values.pop("usage")  # 前端表单没有 usage（不做写回的集成的用途说明），编辑时保留原值
        return await self.repo.update(obj, values, actor=actor)

    async def test_call(self, code: str, user_label: str) -> dict:
        """Only an explicitly configured read-only probe can create a test-call result."""
        from app.modules.integration.adapters import get_adapter as configured_adapter

        s = await self.repo.get_by_code_or_404(code)
        if s.status == ExtSystemStatus.disabled:
            raise Conflict("该系统已停用，先在“编辑”里改为正常再测试", code="EXT_SYSTEM_DISABLED")
        try:
            adapter = configured_adapter(s, "probe")
        except AppError:
            raise AppError(
                "未配置真实连通性测试执行器，未发送请求，也未生成调用记录",
                code="EXT_SYSTEM_TEST_UNAVAILABLE",
                status_code=503,
                details={"systemId": s.code, "reason": "executor_not_configured"},
            ) from None
        started = time.perf_counter()
        result, msg = CallResult.succeeded, "只读连通性探测收到成功状态；不代表业务写回已执行"
        try:
            await adapter.request("probe")
        except AppError as exc:
            result, msg = CallResult.failed, f"{exc.message}（{exc.code}）"
        call = WritebackCall(
            code=f"PROBE-{uuid.uuid4().hex}",
            sys_id=s.id,
            kind=CallKind.test,
            at=clock.now(),
            op_name="连通性探测",
            target_label=s.name,
            method="GET",
            path=adapter.path("probe"),
            elapsed_ms=round((time.perf_counter() - started) * 1000),
            http_status=adapter.last_http,
            result=result,
            attempt=1,
            actor=user_label,
            msg=msg,
        )
        self.session.add(call)
        await self.session.flush()
        return call_dict(call, s.code, None)


_CALLS = (
    select(WritebackCall, ExtSystem.code, ActionRun.code)
    .join(ExtSystem, WritebackCall.sys_id == ExtSystem.id)
    .outerjoin(ActionRun, WritebackCall.action_run_id == ActionRun.id)
)
# 排序：测试调用排最前（新的在上，前端 tests.unshift），其余按时间倒序、同一时间按编号倒序（同 extCalls()）
_ORDER = (
    case((WritebackCall.kind == CallKind.test, 0), else_=1),
    case((WritebackCall.kind == CallKind.test, WritebackCall.id), else_=None).desc(),
    WritebackCall.at.desc(),
    WritebackCall.code.desc(),
)


def call_dict(c: WritebackCall, sys_code: str, run_code: str | None) -> dict:
    """ORM 行 → 前端 ExtCall。"""
    return {
        "id": c.code,
        "sys_id": sys_code,
        "time": f"{c.at.astimezone(clock.local_now().tzinfo):%Y-%m-%d %H:%M:%S}",
        "action": c.op_name,
        "target": c.target_label,
        "method": c.method,
        "path": c.path,
        "idem_key": c.idem_key,
        "ms": c.elapsed_ms,
        "http": c.http_status,
        "result": c.result.value,
        "attempt": c.attempt,
        "actor": c.actor,
        "msg": c.msg,
        "run_id": run_code,
    }


class WritebackCallService:
    """外部调用记录（外部系统页「最近调用」、调用详情抽屉、人工核对）。"""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = WritebackCallRepo(session)

    async def search(self, *, sys_code: str | None, result: str | None, page: PageParams) -> dict:
        """result：成功 / 失败 / 结果未知；全部 / 不传 = 不过滤。counts 按系统统计，不受 result 影响。"""
        where = []
        if sys_code:
            sys = await ExtSystemRepo(self.session).get_by_code_or_404(sys_code)
            where.append(WritebackCall.sys_id == sys.id)
        counts = {"全部": 0, **{r.value: 0 for r in CallResult}}
        stmt = select(WritebackCall.result, func.count()).where(*where).group_by(WritebackCall.result)
        for r, n in (await self.session.execute(stmt)).all():
            counts[r.value] = n
            counts["全部"] += n
        if result and result != "全部":
            where.append(WritebackCall.result == from_api(CallResult, result))
            total = counts.get(result, 0)
        else:
            total = counts["全部"]
        p = page.clamp(total)
        stmt = _CALLS.where(*where).order_by(*_ORDER).offset(p.offset).limit(p.size)
        rows = (await self.session.execute(stmt)).all()
        return {
            "items": [call_dict(c, sc, rc) for c, sc, rc in rows],
            "total": total,
            "page": p.page,
            "size": p.size,
            "counts": counts,
        }

    async def get(self, code: str) -> dict:
        row = (await self.session.execute(_CALLS.where(WritebackCall.code == code))).first()
        if row is None:
            raise NotFound("调用记录不存在", details={"id": code})
        return call_dict(*row)

    async def mark_checked(self, code: str, user_name: str) -> dict:
        """人工核对 · 确认对方已处理（文档 §3.1）：只改这条调用的 result / msg，不碰执行记录。

        只允许「结果未知」且不带执行记录的调用（前端只对这种显示按钮；带 runId 的去动作中心对账），
        其余 → 409 CALL_NOT_CHECKABLE。文案同前端：「人工核对：对方已处理（王敏 16:30 标记）」。
        """
        await self.get(code)  # 404
        c = await self.repo.get_by_code_or_404(code)
        if c.result != CallResult.unknown or c.action_run_id is not None:
            raise Conflict("只有结果未知、且没有执行记录的调用可以人工核对", code="CALL_NOT_CHECKABLE")
        t = clock.local_now()
        await self.repo.update(
            c,
            {"result": CallResult.succeeded, "msg": f"人工核对：对方已处理（{user_name} {t:%H:%M} 标记）"},
            actor=user_name,
        )
        return await self.get(code)


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
