"""规则 / 情景推演 / 生命周期接口的入参（dynamic-api.md §4、§6.3–6.5）。

出参是前端 mock 形状的 dict（RuleDef / Hit / Scenario / Lifecycle…，字段名已是 camelCase），
统一用 `Page[dict]` 分页；字段清单见 docs/backend/dynamic-rules-impl-notes.md。
"""

import json
from typing import Any, Literal

from pydantic import Field, model_validator

from app.core.errors import AppError
from app.core.schema import ApiModel
from app.modules.static.schemas import ItemIn

NonBlank = Field(min_length=1)


class JsonInput(ApiModel):
    @model_validator(mode="after")
    def json_text(self):
        json.dumps(self.model_dump(mode="json"), allow_nan=False)
        ItemIn.jsonb_text(self.model_dump())
        return self


class MappingIn(ApiModel):
    param: str = Field(max_length=64)
    from_: str = Field("", alias="from")


class AutoStageIn(ApiModel):
    enabled: bool
    execution_mode: Literal["confirm", "auto"] = "confirm"
    validity_seconds: int = Field(default=0, ge=0, le=86400, strict=True)
    action_type_id: str | None = Field(None, max_length=64)
    confirmer: str | None = Field(None, max_length=64)
    mapping: list[MappingIn] = []


class RuleConditionMetadata(ApiModel):
    """Published condition fields only; unknown snapshot fields never reach machine clients."""

    metric: str | None = None
    metric_ver: int | None = None
    field: str | None = None
    expression: str | None = None
    op: str
    value: str
    period: str | None = None
    transform: str | None = None


class RuleTriggerMetadata(ApiModel):
    kind: str
    enabled: bool = False
    cron: str | None = None
    timezone: str | None = None
    catchup_minutes: int | None = None
    event_id: str | None = None
    filter: str | None = None
    text: str | None = None


class RuleMappingMetadata(ApiModel):
    param: str
    from_: str = Field("", alias="from")


class RuleAutoStageMetadata(ApiModel):
    enabled: bool
    execution_mode: Literal["confirm", "auto"] = "confirm"
    validity_seconds: int = Field(default=0, ge=0, le=86400, strict=True)
    action_type_id: str | None = None
    mapping: list[RuleMappingMetadata] = []


class RuleDefinitionMetadata(ApiModel):
    """Allowlisted configuration, without runtime values, draft data or connector credentials."""

    id: str
    name: str
    version: int = Field(ge=1, strict=True)
    status: str
    owner: str
    core: bool
    obj_kind: str
    level: str
    output: str
    desc: str
    conds: list[RuleConditionMetadata]
    logic: str
    expr: str
    dedupe: str
    cooldown: str
    trigger: RuleTriggerMetadata
    action_type_id: str | None = None
    auto_stage: RuleAutoStageMetadata


class PublishedRuleMetadata(ApiModel):
    release: str
    definition: RuleDefinitionMetadata


class RuleDraftIn(JsonInput):
    """PUT /rules/{id}/draft：保存到草稿（R7）。前端只改自动暂存配置和阈值。"""

    cr: str | None = None
    auto_stage: AutoStageIn
    thresholds: list[str] = []
    lock_version: int | None = None
    conds: list[dict[str, Any]] | None = Field(None, max_length=40)
    logic: Literal["且", "或"] | None = None
    expr: str | None = Field(None, max_length=4096)
    trigger: dict[str, Any] | None = None
    obj_kind: str | None = Field(None, max_length=32)
    dedupe: str | None = Field(None, max_length=200)
    cooldown: str | None = Field(None, max_length=64)

    @model_validator(mode="before")
    @classmethod
    def require_auto_stage(cls, data):
        if isinstance(data, dict) and "autoStage" not in data and "auto_stage" not in data:
            message = "缺少必填字段autoStage；请显式提交自动暂存配置，停用时也须提供enabled=false"
            raise AppError(
                message, code="VALIDATION_FAILED", status_code=422,
                details={"paths": ["autoStage"], "fields": [{"field": "autoStage", "message": message}]},
            )
        return data


class RuleApplyIn(RuleDraftIn):
    release_lock_version: int = Field(ge=0)


class RuleCreateIn(JsonInput):
    """POST /rules：新建规则（草稿，登记到变更单 cr）。

    不传 cr 时只建规则，随后 POST /rules/{id}/draft 登记。"""

    cr: str | None = Field(None, max_length=32)
    name: str | None = Field(None, max_length=200)
    obj_kind: str | None = Field(None, max_length=32)


class RuleOpenDraftIn(ApiModel):
    """POST /rules/{id}/draft：把规则登记到变更单 cr（开草稿，幂等）。"""

    cr: str = Field(..., min_length=1, max_length=32)


class ReplayIn(ApiModel):
    range: Literal["7d", "30d"] = "7d"
    version: Literal["current", "draft"] = "current"
    mode: Literal["snapshot", "events"] = "snapshot"
    page: int = Field(1, ge=1)
    size: int = Field(10, ge=1, le=100)
    anchor: str | None = Field(None, max_length=200, pattern=r"^[0-9]{10}\.[0-9a-f]{64}\.[0-9a-f]{64}$")


class ScenarioCreateIn(ApiModel):
    from_: str | None = Field(None, alias="from")


class ScenarioPatchIn(ApiModel):
    name: str


class ScenarioRunIn(ApiModel):
    name: str | None = None
    # 按方案模板校验（scenario_templates.validate）：键 = 模板字段 key
    scope: dict[str, Any]
    drivers: dict[str, Any]


class ScenarioStageIn(ApiModel):
    keys: list[str]


class LifecycleDraftIn(JsonInput):
    """PUT /object-types/{id}/lifecycle/draft：整份草稿改动（L3–L6，前端 LcEdits 形状）。"""

    cr: str | None = None
    add_states: list[dict[str, Any]] = []
    add_transitions: list[dict[str, Any]] = []
    edit_transitions: dict[str, dict[str, Any]] = {}
    remove_transitions: list[str] = []
    lock_version: int | None = None
