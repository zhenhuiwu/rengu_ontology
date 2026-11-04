import json
from typing import Literal

from pydantic import ConfigDict, Field, model_validator

from app.core.schema import ApiModel

Operation = Literal["read", "filter", "aggregate", "export", "execute", "external", "invoke"]


class ClosedModel(ApiModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True, alias_generator=None)


class Vocabulary(ClosedModel):
    code: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    name: str = Field(min_length=1, max_length=64)


class Grant(ClosedModel):
    role: str = Field(min_length=1, max_length=64)
    purposes: list[str] = Field(default_factory=list, max_length=64)
    classes: list[str] = Field(default_factory=list, max_length=64)
    level: str | None = None
    operations: list[Operation] = Field(default_factory=list, max_length=7)
    unmask: bool = False


class Downgrade(ClosedModel):
    from_level: str | None = None
    to_level: str | None = None
    remove_classes: list[str] = Field(default_factory=list, max_length=64)


class ResourcePolicy(ClosedModel):
    resource: str = Field(min_length=1, max_length=768)
    classes: list[str] = Field(default_factory=list, max_length=64)
    level: str | None = None
    purposes: list[str] = Field(default_factory=list, max_length=64)
    mask: Literal["none", "null", "redact"] = "none"
    dependencies: list[str] = Field(default_factory=list, max_length=128)
    dependencies_complete: bool = False
    downgrade: Downgrade | None = None
    reason: str = Field(default="", max_length=512)

    @model_validator(mode="after")
    def explicit_resources(self):
        for value in [self.resource, *self.dependencies]:
            try:
                parts = json.loads(value)
            except (ValueError, TypeError):
                raise ValueError("资源须来自当前血缘目录") from None
            if (
                not isinstance(parts, list)
                or len(parts) < 2
                or any(not isinstance(p, str) or not p for p in parts)
            ):
                raise ValueError("资源标识无效")
        if (self.dependencies_complete or self.downgrade) and not self.reason.strip():
            raise ValueError("确认依赖完整或降级时须填写依据")
        return self


class PolicyDefinition(ClosedModel):
    classes: list[Vocabulary] = Field(default_factory=list, max_length=64)
    levels: list[Vocabulary] = Field(default_factory=list, max_length=16)
    purposes: list[Vocabulary] = Field(default_factory=list, max_length=64)
    grants: list[Grant] = Field(default_factory=list, max_length=256)
    resources: list[ResourcePolicy] = Field(default_factory=list, max_length=1000)
    downgrades: list[Downgrade] = Field(default_factory=list, max_length=64)

    @model_validator(mode="after")
    def declared_vocabulary(self):
        for rows in (self.classes, self.levels, self.purposes):
            if len({r.code for r in rows}) != len(rows):
                raise ValueError("分类、等级、用途编码不能重复")
        classes, levels, purposes = (
            {r.code for r in rows} for rows in (self.classes, self.levels, self.purposes)
        )
        if len({r.resource for r in self.resources}) != len(self.resources):
            raise ValueError("资源策略不能重复")
        for row in [*self.grants, *self.resources]:
            if not set(row.classes) <= classes or (row.level is not None and row.level not in levels):
                raise ValueError("策略引用了未声明分类或等级")
            if not set(row.purposes) <= purposes:
                raise ValueError("策略引用了未声明用途")
        for rule in self.downgrades:
            if not set(rule.remove_classes) <= classes or any(
                v is not None and v not in levels for v in (rule.from_level, rule.to_level)
            ):
                raise ValueError("降级规则引用了未声明分类或等级")
        for rule in self.resources:
            if rule.downgrade is not None and rule.downgrade not in self.downgrades:
                raise ValueError("资源降级须匹配管理员显式允许的降级规则")
        return self


class PolicySave(ClosedModel):
    revision: int = Field(ge=0)
    definition: PolicyDefinition
