"""动作类型执行策略（第 2 期第一部分）：只给建议 / 人工确认 / 在上限内自动执行。

- 存放：不加迁移。策略随动作定义版本走，写在 `action_type_version.definition["policy"]`
  （变更单 `policy` 段写回，发布时并进该版完整定义和发布快照）；没有策略 = 默认 = 现有行为。
- 调用方类型由暂存来源推出：手动 / 推演 / 告警处理 → 人；问数 → AI；规则 / 告警 → 规则。
  `服务账号`：API 密钥的 principal 为服务账号时（`CurrentUser.is_service_account`），走机器接口
  `/machine/actions/{code}:execute`（scope actions:execute）。
- 自动执行额度：按业务时区自然日，从动作时间线里“按执行策略自动执行”条目计数，
  同一本体 + 动作类型用事务级互斥锁串行；超限 / 不在允许对象 / 金额超限 / 需审批 → 照常待确认。
"""

import math
from datetime import datetime, time
from typing import Annotated, Any, Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from sqlalchemy import func, select, tuple_

from app.core import clock
from app.core.config import get_settings
from app.core.errors import AppError

MODES = ("suggest", "confirm", "auto")
CALLERS = ("人", "规则", "AI", "服务账号")
CALLER_OF_SOURCE = {
    "手动": "人",
    "推演": "人",
    "告警处理": "人",
    "问数": "AI",
    "规则": "规则",
    "告警": "规则",
}
AUTO_TEXT = "按执行策略自动执行"
OVERFLOW_TEXT = "未自动执行，转人工确认"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class AmountLimit(_Strict):
    param: Annotated[str, Field(min_length=1, max_length=64)]
    max: float

    @field_validator("max")
    @classmethod
    def _finite(cls, value: float) -> float:
        if not math.isfinite(value) or value < 0:
            raise ValueError("上限须为非负有限数")
        return value


class Limits(_Strict):
    daily_count: Annotated[int, Field(alias="dailyCount", ge=1, le=10_000)]
    per_caller_daily_count: Annotated[int | None, Field(alias="perCallerDailyCount", ge=1, le=10_000)] = None
    amount: AmountLimit | None = None
    objects: Annotated[
        list[Annotated[str, Field(min_length=1, max_length=64)]] | None, Field(max_length=500)
    ] = None


class Policy(_Strict):
    mode: Literal["suggest", "confirm", "auto"]
    callers: Annotated[list[Literal["人", "规则", "AI", "服务账号"]] | None, Field(min_length=1)] = None
    limits: Limits | None = None
    overflow: Literal["stage"] = "stage"

    @field_validator("callers")
    @classmethod
    def _unique(cls, value):
        if value is not None and len(set(value)) != len(value):
            raise ValueError("调用方类型不能重复")
        return value

    def dump(self) -> dict[str, Any]:
        return self.model_dump(by_alias=True, exclude_none=True)


DEFAULT = Policy(mode="confirm")


def parse(value: Any) -> Policy:
    """变更单写回用：非法 → ValueError（钩子转 409）。"""
    if not isinstance(value, dict):
        raise ValueError("执行策略须为对象")
    try:
        policy = Policy.model_validate(value)
    except ValidationError as exc:
        errors = (f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors())
        raise ValueError("; ".join(errors)) from None
    if policy.mode == "auto" and policy.limits is None:
        raise ValueError("自动执行必须设置每日上限 limits.dailyCount")
    if policy.mode != "auto" and policy.limits is not None:
        raise ValueError("只有自动执行才能设置上限")
    return policy


def stored(value: Any) -> Policy:
    """读定义里已发布的策略；缺省或历史脏数据一律按默认（人工确认）处理，不放宽。"""
    if value is None:
        return DEFAULT
    try:
        return parse(value)
    except ValueError:
        return DEFAULT


async def version_policies(session, pairs: list[tuple[int, str]]) -> dict[int, dict | None]:
    """{action_type_id: 该版定义里的 policy 原值}；没有版本行或没有 policy → 不在结果里。"""
    from app.modules.action.models import ActionTypeVersion

    if not pairs:
        return {}
    rows = await session.execute(
        select(ActionTypeVersion.action_type_id, ActionTypeVersion.definition).where(
            tuple_(ActionTypeVersion.action_type_id, ActionTypeVersion.ver).in_(pairs)
        )
    )
    return {tid: d["policy"] for tid, d in rows.all() if isinstance(d, dict) and d.get("policy") is not None}


async def load(session, code: str, version: str | None) -> Policy:
    from app.modules.action.models import ActionType

    tid = await session.scalar(select(ActionType.id).where(ActionType.code == code))
    if tid is None or not version:
        return DEFAULT
    return stored((await version_policies(session, [(tid, version)])).get(tid))


def caller_of(source_type: str, user=None) -> str:
    """调用方类型：服务账号密钥 → 服务账号（不论入口）；否则按执行来源。"""
    if user is not None and getattr(user, "is_service_account", False):
        return "服务账号"
    return CALLER_OF_SOURCE.get(source_type, "人")


def gate(policy: Policy, caller: str) -> None:
    """暂存前：调用方不在允许范围 → 403；只给建议 → 409（请用试运行）。"""
    if policy.callers is not None and caller not in policy.callers:
        raise AppError(
            f"该动作的执行策略不允许「{caller}」发起",
            code="CALLER_NOT_ALLOWED",
            status_code=403,
            details={"caller": caller, "callers": list(policy.callers)},
        )
    if policy.mode == "suggest":
        raise AppError(
            "该动作只给建议，不生成执行记录；请用试运行查看结果",
            code="ACTION_POLICY_SUGGEST_ONLY",
            status_code=409,
            details={"mode": "suggest"},
        )


def day_start() -> datetime:
    tz = ZoneInfo(get_settings().display_tz)
    return datetime.combine(clock.today(), time.min, tzinfo=tz)


def caller_label(caller: str, caller_id: str) -> str:
    return f"调用方 {caller}:{caller_id}"


async def auto_decision(session, run, policy: Policy, caller: str, caller_id: str, approval_free: bool):
    """→ (是否自动执行, 原因, 说明)。持锁到事务结束，计数含本条。"""
    from app.db.dialects import get_adapter
    from app.modules.action.checker import structured_params
    from app.modules.dynamic.models import ActionRun, ActionRunLog

    limits = policy.limits
    if policy.mode != "auto" or limits is None:
        return False, None, None
    if not approval_free:
        return False, "动作需审批，不能自动执行", None
    if limits.objects is not None and run.obj_id not in limits.objects:
        return False, f"对象 {run.obj_id} 不在允许范围", None
    if limits.amount is not None:
        try:
            params = structured_params(run.params_text)
        except Exception:
            params = {}
        value = params.get(limits.amount.param)
        if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
            return False, f"参数 {limits.amount.param} 不是数值，不能判断上限", None
        if value > limits.amount.max:
            return False, f"参数 {limits.amount.param}={value:g} 超过上限 {limits.amount.max:g}", None
    await get_adapter(session).xact_lock(session, f"action-policy:{run.action_type_code}")
    base = (
        select(func.count(ActionRunLog.id))
        .join(ActionRun, ActionRun.id == ActionRunLog.action_run_id)
        .where(
            ActionRun.action_type_code == run.action_type_code,
            ActionRunLog.text == AUTO_TEXT,
            ActionRunLog.at >= day_start(),
        )
    )
    used = int(await session.scalar(base) or 0)
    if used >= limits.daily_count:
        return False, f"今日自动执行已达上限 {limits.daily_count}", None
    note = f"{caller_label(caller, caller_id)} · 今日 {used + 1}/{limits.daily_count}"
    if limits.per_caller_daily_count is not None:
        mine = int(
            await session.scalar(
                base.where(
                    ActionRunLog.actor.startswith(caller_label(caller, caller_id) + " ·", autoescape=True)
                )
            )
            or 0
        )
        if mine >= limits.per_caller_daily_count:
            return False, f"该调用方今日自动执行已达上限 {limits.per_caller_daily_count}", None
        note += f" · 本调用方 {mine + 1}/{limits.per_caller_daily_count}"
    return True, None, note
