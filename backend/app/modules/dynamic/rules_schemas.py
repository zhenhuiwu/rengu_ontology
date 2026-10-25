"""规则 / 情景推演 / 生命周期接口的入参（dynamic-api.md §4、§6.3–6.5）。

出参是前端 mock 形状的 dict（RuleDef / Hit / Scenario / Lifecycle…，字段名已是 camelCase），
统一用 `Page[dict]` 分页；字段清单见 docs/backend/dynamic-rules-impl-notes.md。
"""

from typing import Any, Literal

from pydantic import Field

from app.core.schema import ApiModel

NonBlank = Field(min_length=1)


class MappingIn(ApiModel):
    param: str
    from_: str = Field("", alias="from")


class AutoStageIn(ApiModel):
    enabled: bool
    action_type_id: str | None = None
    confirmer: str | None = None
    mapping: list[MappingIn] = []


class RuleDraftIn(ApiModel):
    """PUT /rules/{id}/draft：保存到草稿（R7）。前端只改自动暂存配置和阈值。"""

    cr: str | None = None
    auto_stage: AutoStageIn
    thresholds: list[str] = []


class ReplayIn(ApiModel):
    range: Literal["7d", "30d"] = "7d"
    version: Literal["current", "draft"] = "current"


class ScopeIn(ApiModel):
    area: str
    category: str
    weeks: int = Field(8, ge=1, le=52)


class DriversIn(ApiModel):
    price_pct: int | float = 0
    replenish_pct: int | float = 0
    safety_days: int | float = 7
    promo: str = "无"
    promo_depth: int | float = 0
    promo_weeks: int | float = 0


class ScenarioCreateIn(ApiModel):
    from_: str | None = Field(None, alias="from")


class ScenarioPatchIn(ApiModel):
    name: str


class ScenarioRunIn(ApiModel):
    name: str | None = None
    scope: ScopeIn
    drivers: DriversIn


class ScenarioStageIn(ApiModel):
    keys: list[str]


class LifecycleDraftIn(ApiModel):
    """PUT /object-types/{id}/lifecycle/draft：整份草稿改动（L3–L6，前端 LcEdits 形状）。"""

    cr: str | None = None
    add_states: list[dict[str, Any]] = []
    add_transitions: list[dict[str, Any]] = []
    edit_transitions: dict[str, dict[str, Any]] = {}
    remove_transitions: list[str] = []
