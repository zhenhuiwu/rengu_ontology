"""对外 Schema（字段名 = 前端 mock：ACTION_TYPES / paramsOf / condsOf / writebackOf / versionsOf）。"""

from typing import Generic, Literal, TypeVar

from pydantic import Field

from app.core.schema import ApiModel, DateTimeMin
from app.modules.action.models import CriterionKind, EditOp, EffectKind, TypeStatus

T = TypeVar("T")


class ActionTypeOut(ApiModel):
    id: str
    name: str
    api: str
    group: str
    status: TypeStatus
    version: str
    target: str
    target_api: str
    target_kind: str | None = None


class ActionTypeListOut(ApiModel):
    items: list[ActionTypeOut]
    groups: list[str]
    total: int
    page: int
    size: int


class ActionTypeDetailOut(ActionTypeOut):
    owner: str
    desc: str
    runners: str
    approval: str
    sys_id: str | None = None
    system: str
    endpoint: str
    idem_key: str
    timeout: str
    retry: str
    idempotent: bool
    used_by: list[str]


class ParamOut(ApiModel):
    name: str
    api: str
    type: str
    required: bool
    def_: str = Field("", alias="def")  # mock 字段名就是 def（Python 关键字）
    rule: str = ""


class CriterionOut(ApiModel):
    kind: CriterionKind
    name: str
    expr: str
    fail: str


class ItemsOut(ApiModel, Generic[T]):
    items: list[T]


class EditOut(ApiModel):
    op: EditOp
    obj: str
    field: str


class EffectOut(ApiModel):
    kind: EffectKind
    target: str
    when: str
    note: str


class WritebackConfigOut(ApiModel):
    sys_id: str | None = None
    endpoint: str
    idem_key: str


class FlowItemOut(ApiModel):
    time: str
    text: str
    desc: str
    status: Literal["done", "warn", "error", "unknown"] | None = None


class WritebackOut(ApiModel):
    config: WritebackConfigOut
    edits: list[EditOut]
    effects: list[EffectOut]
    flow: list[FlowItemOut]


class TypeVersionOut(ApiModel):
    ver: str
    release: str
    status: Literal["生效中", "已替代", "草稿"]
    by: str
    at: DateTimeMin
    change: str


class RunDailyOut(ApiModel):
    """mock runDaily() 一项（palantir-r2）。date = "MM-DD"。"""

    date: str
    ok: int
    failed: int
    unknown: int
