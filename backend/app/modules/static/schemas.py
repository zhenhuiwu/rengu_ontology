"""Pydantic 输出模型（继承 app.core.schema.ApiModel）。

[推断] 记录本身按前端 mock 的 TS interface 原样输出（shapes.py 生成，键名已是 camelCase、可选键缺省），
这里只约束分页外壳：`{items,total,page,size}` +
页面要的附加键（diff / summary / levels / groups / conflictCount）。
"""

from typing import Any, Literal

from pydantic import ConfigDict, Field

from app.common.pagination import Page
from app.core.schema import ApiModel

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
    """POST /releases：mock 发布不填说明 → note 可省 [推断]；lockVersion 先收下不校验（发布串行由版本层的
    事务锁保证）[推断]。"""

    note: str | None = None
    lock_version: int | None = None


class RollbackIn(ApiModel):
    note: str | None = None


class MergeCheckOut(ApiModel):
    rows: list[dict[str, Any]]
    blocked: bool


class RejectIn(ApiModel):
    """POST /change-requests/{id}:reject：驳回原因必填（前端 5 个预设之一，后台不限定取值 [推断]）。"""

    reject_reason: str | None = None


# ---------------------------------------------------------------- 变更单写接口（handoff 写接口契约）

TargetKind = Literal["object", "link", "metric", "dimension", "knowledge", "action", "event", "rule"]
ItemOp = Literal["add", "update", "delete"]


class TargetIn(ApiModel):
    """变更目标（通用 kind + 业务编码）。name 只用于改动列表显示，可省。"""

    kind: TargetKind
    id: str = Field(min_length=1, max_length=64)
    is_new: bool = False
    name: str | None = Field(None, max_length=128)


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


class ItemsPutIn(ApiModel):
    target: TargetIn
    section: str = Field(min_length=1, max_length=32)
    rows: list[ItemIn]
    lock_version: int | None = None


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
