"""Pydantic 输入输出（字段名、枚举值对齐前端 mock：dynamic.ts Alert、actions.ts ActionRecord、
actionEvent.ts ActionRun；见 dynamic-api.md §1.3）。"""

from typing import Annotated, Any, Literal

from pydantic import Field, StringConstraints, model_validator

from app.common.pagination import Page
from app.core.schema import ApiModel, DateTimeMin, DateTimeSec

NonBlank = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]

# ---------------- 告警 ----------------


class AlertLogOut(ApiModel):
    time: DateTimeSec
    text: str
    who: str
    abnormal: bool | None = None


class DimOut(ApiModel):
    """通用维度值：维度所属对象类型 + 对象编号 + 显示名。"""

    obj_kind: str
    id: str
    label: str
    kind_label: str | None = None


class AlertOut(ApiModel):
    id: str
    level: str
    rule_id: str
    obj_kind: str
    obj_kind_label: str
    obj_id: str
    obj_label: str
    dims: list[DimOut] = Field(default_factory=list)
    value: str
    threshold: str
    triggered_at: DateTimeSec
    cutoff: DateTimeMin
    status: str
    assignee: str | None = None
    assignee_id: str | None = None
    can_close: bool = False
    can_claim: bool = False
    assigned_to_me: bool = False
    # 关联动作（Alerts.vue Row.actionRef / actionStatus；actionId 为补充，方便跳到动作中心）
    action_ref: str | None = None
    action_status: str | None = None
    action_id: str | None = None


class AlertCounts(ApiModel):
    open: int
    doing: int
    closed: int
    all: int


class AlertPage(Page[AlertOut]):
    counts: AlertCounts


class AlertCan(ApiModel):
    """当前用户能否操作；不能时 reasons[操作] = 悬停原因。"""

    claim: bool
    transfer: bool
    close: bool
    start_action: bool
    reasons: dict[str, str] = Field(default_factory=dict)


class AlertDetail(AlertOut):
    rule: dict[str, Any] | None = None
    # 发起动作时的审批人：取规则绑定的动作类型的审批人配置（无动作或未配置为 null）
    approver: str | None = None
    recent7d: int = Field(alias="recent7d")
    locked: bool
    log: list[AlertLogOut]
    can: AlertCan
    action_params: list[dict[str, Any]] = Field(default_factory=list)


class AlertActionIn(ApiModel):
    params: dict[str, Any] = Field(default_factory=dict)


class LaunchValidateIn(ApiModel):
    obj_kind: str = Field(min_length=1, max_length=64)
    obj_id: str = Field(min_length=1, max_length=64)
    params: dict[str, Any] = Field(default_factory=dict)


class IdsIn(ApiModel):
    ids: list[str]


class TransferIn(ApiModel):
    to: NonBlank


class CloseIn(ApiModel):
    as_: Literal["已解决", "已忽略"] = Field(alias="as")
    reason: NonBlank


class BatchCloseIn(CloseIn):
    ids: list[str]


class BatchClaimOut(ApiModel):
    claimed: int
    ids: list[str]


class BatchCloseOut(ApiModel):
    closed: int
    ids: list[str]


# ---------------- 动作 ----------------


class ActionLogOut(ApiModel):
    time: DateTimeSec
    text: str
    desc: str | None = None
    status: str | None = None


class ActionRunOut(ApiModel):
    """统一响应类型 ActionRun（ActionRecord ∪ actionEvent.ActionRun）。

    source：动作中心记录（in_center）为对象 {type, ruleId, …}；动作类型页执行记录为来源原文字符串 [推断]。
    """

    id: str
    type_id: str
    action: str
    type_version: str | None = None
    source: dict[str, Any] | str
    obj_kind: str | None = None
    obj_id: str | None = None
    obj_label: str | None = None
    initiator: str
    confirmer: str | None = None
    approver: str | None = None
    status: str
    submitted_at: DateTimeSec
    at: DateTimeSec
    idem_key: str
    sys_id: str | None = None
    system: str | None = None
    ext_ref: str | None = None
    params: str | None = None
    summary: str | None = None
    ms: int | None = None
    code: str | None = None
    retries: int | None = None
    fail_reason: str | None = None
    unknown_reason: str | None = None
    reconcile: str | None = None
    retry_of: str | None = None
    retried_as: str | None = None
    alert_id: str | None = None
    rule_id: str | None = None

    dispatch_phase: Literal["queued", "claimed"] | None = None
    params_unavailable: str | None = None


class ActionCan(ApiModel):
    confirm: bool
    withdraw: bool
    approve: bool
    resubmit: bool
    reconcile: bool
    reasons: dict[str, str] = Field(default_factory=dict)


class ActionRunDetail(ActionRunOut):
    log: list[ActionLogOut]
    can: ActionCan


class ActionTabCounts(ApiModel):
    pending: int
    records: int
    unknown: int


class ActionRunPage(Page[ActionRunOut]):
    counts: ActionTabCounts
    # 当前筛选（不含 tab / status）下 待确认、结果未知 的条数（对象类型页 L63–64）
    status_counts: dict[str, int]


class StartActionOut(ApiModel):
    alert: AlertDetail
    action: ActionRunDetail


class SourceIn(ApiModel):
    type: Literal["规则", "告警", "问数", "手动", "推演"]
    rule_id: str | None = None
    rule_version: str | None = None
    alert_id: str | None = None
    confirmer: str | None = None
    session: str | None = None
    session_title: str | None = None
    scenario_id: str | None = None
    scenario_name: str | None = None


class StageIn(ApiModel):
    """POST /action-runs:stage。action（中文动作名）和 typeId 二选一。"""

    action: str | None = None
    type_id: str | None = None
    source: SourceIn
    obj_kind: str | None = None
    obj_id: str
    obj_label: str
    params: str | None = None
    approver: str | None = None
    idempotency_key: str | None = Field(
        default=None, min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_-]+$"
    )

    @model_validator(mode="after")
    def _need_action(self) -> "StageIn":
        if not self.action and not self.type_id:
            raise ValueError("action 和 typeId 至少填一个")
        return self


class WithdrawIn(ApiModel):
    reason: NonBlank


class ReconcileIn(ApiModel):
    result: Literal["外部已成功", "外部未执行", "仍无法确认"]
    note: NonBlank


class ResubmitOut(ApiModel):
    id: str
    idem_key: str
    action: ActionRunDetail
    original: ActionRunDetail
