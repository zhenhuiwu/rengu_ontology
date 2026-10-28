"""Pydantic 输出模型（继承 app.core.schema.ApiModel）。

[推断] 记录本身按前端 mock 的 TS interface 原样输出（shapes.py 生成，键名已是 camelCase、可选键缺省），
这里只约束分页外壳：`{items,total,page,size}` +
页面要的附加键（diff / summary / levels / groups / conflictCount）。
"""

from typing import Any, Literal

from pydantic import ConfigDict, Field, field_validator

from app.common.pagination import Page
from app.core.schema import ApiModel
from app.modules.versioning.schemas import ReleaseOut

Rec = dict[str, Any]


class RecPage(Page[Rec]):
    """分页结果 = 骨架 app.common.pagination.Page（{items,total,page,size}）；附加键原样透传。"""

    model_config = ConfigDict(extra="allow")


class SynonymConflict(ApiModel):
    word: str
    ids: list[str]


class SynonymConflicts(ApiModel):
    """mock/knowledge.ts synonymConflicts（Map → 数组）+ CONFLICT_RULE。"""

    conflicts: list[SynonymConflict]
    rule: dict[str, str]


class PublishIn(ApiModel):
    """lockVersion 为 /releases/current 返回的当前发布头令牌；省略兼容旧客户端。"""

    note: str | None = None
    lock_version: int | None = Field(None, ge=0)


class PublishOut(ReleaseOut):
    skipped: list[str] = Field(default_factory=list)


class RollbackIn(ApiModel):
    note: str | None = None


class MergeCheckOut(ApiModel):
    rows: list[dict[str, Any]]
    blocked: bool


class RejectIn(ApiModel):
    """POST /change-requests/{id}:reject：驳回原因必填（前端 5 个预设之一，后台不限定取值 [推断]）。"""

    reject_reason: str | None = None


# ---------------------------------------------------------------- 变更单写接口（handoff 写接口契约）

TargetKind = Literal[
    "object", "link", "metric", "dimension", "knowledge", "action", "event", "rule", "function", "quality"
]
ItemOp = Literal["add", "update", "delete"]


class TargetIn(ApiModel):
    """变更目标（通用 kind + 业务编码）。name 只用于改动列表显示，可省。"""

    kind: TargetKind
    id: str = Field(min_length=1, max_length=64)
    is_new: bool = False
    name: str | None = Field(None, max_length=128)


class AllocateTargetIn(ApiModel):
    kind: Literal["link", "knowledge"]
    name: str | None = Field(None, max_length=128)

    @field_validator("name")
    @classmethod
    def pg_text(cls, value: str | None) -> str | None:
        if value is not None:
            if "\x00" in value:
                raise ValueError("名称不能包含 NUL 字符")
            try:
                value.encode("utf-8")
            except UnicodeEncodeError as error:
                raise ValueError("名称包含无效 Unicode 字符") from error
        return value


class CRCreateIn(ApiModel):
    title: str = Field(min_length=1, max_length=40)
    reason: str = ""
    target: TargetIn | None = None
    level: Literal["重大", "一般", "修订"] = "一般"
    reviewer: str | None = Field(None, max_length=64)


class CRPatchIn(ApiModel):
    title: str | None = Field(None, min_length=1, max_length=40)
    reason: str | None = None
    reviewer: str | None = Field(None, max_length=64)
    level: Literal["重大", "一般", "修订"] | None = None
    migration: str | None = None
    lock_version: int | None = None


class ItemIn(ApiModel):
    key: str = Field(min_length=1, max_length=128)
    op: ItemOp
    before: Any = None
    after: Any = None
    type: str | None = Field(None, max_length=32)
    field: str | None = Field(None, max_length=128)
    before_text: str | None = None
    after_text: str | None = None

    @field_validator("before", "after")
    @classmethod
    def jsonb_text(cls, value: Any) -> Any:
        pending = [value]
        while pending:
            item = pending.pop()
            if isinstance(item, str):
                if "\x00" in item:
                    raise ValueError("变更内容不能包含空字符")
                try:
                    item.encode("utf-8")
                except UnicodeEncodeError:
                    raise ValueError("变更内容含无效 Unicode 字符") from None
            elif isinstance(item, dict):
                pending.extend(item.keys())
                pending.extend(item.values())
            elif isinstance(item, list):
                pending.extend(item)
        return value


class ItemsPutIn(ApiModel):
    target: TargetIn
    section: str = Field(min_length=1, max_length=32)
    rows: list[ItemIn]
    lock_version: int | None = None


class ApplyTargetIn(ApiModel):
    kind: TargetKind
    id: str | None = Field(None, min_length=1, max_length=64)
    is_new: bool = False
    name: str | None = Field(None, max_length=128)


class ApplySectionIn(ApiModel):
    section: str = Field(min_length=1, max_length=32)
    rows: list[ItemIn] = Field(max_length=1000)


class ApplyModelIn(ApiModel):
    target: ApplyTargetIn
    sections: list[ApplySectionIn] = Field(min_length=1, max_length=32)
    title: str | None = Field(None, min_length=1, max_length=40)
    note: str | None = None
    lock_version: int = Field(ge=0)


class ItemsPutOut(ApiModel):
    id: str
    changes: int
    lock_version: int
    items: list[dict[str, Any]]


class ValidateOut(ApiModel):
    id: str
    checked_at: str
    checks: list[dict[str, Any]]
    summary: str
    blocked: bool
