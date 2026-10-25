"""Pydantic 输出模型（继承 app.core.schema.ApiModel）。

[推断] 记录本身按前端 mock 的 TS interface 原样输出（shapes.py 生成，键名已是 camelCase、可选键缺省），
这里只约束分页外壳：`{items,total,page,size}` +
页面要的附加键（diff / summary / levels / groups / conflictCount）。
"""

from typing import Any

from pydantic import ConfigDict

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
