"""连接内省 → 静态定义变更单；只允许标识/名称编辑，不接受 SQL 或任意定义。"""

from typing import Annotated

from pydantic import ConfigDict, Field, StringConstraints, model_validator

from app.core.schema import ApiModel

Text64 = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)]
Identifier = Annotated[str, StringConstraints(pattern=r"^[A-Za-z][A-Za-z0-9_]*$", max_length=64)]
ObjectId = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_][A-Za-z0-9_-]*$", max_length=64)]


class Input(ApiModel):
    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def valid_text(self):
        pending = [self.model_dump()]
        while pending:
            value = pending.pop()
            if isinstance(value, str):
                if any(ord(c) < 32 for c in value):
                    raise ValueError("文本不能包含控制字符")
                try:
                    value.encode("utf-8")
                except UnicodeEncodeError:
                    raise ValueError("文本含无效 Unicode 字符") from None
            elif isinstance(value, dict):
                pending.extend(value.values())
            elif isinstance(value, list):
                pending.extend(value)
        return self


class ImportPreviewIn(Input):
    schema_: Text64 = Field(alias="schema")
    tables: list[Annotated[str, StringConstraints(min_length=1, max_length=128)]] = Field(
        min_length=1, max_length=60
    )
    sample_values: bool = Field(
        False, description="读取离散取值样本生成维度/取值提示（只读 SELECT DISTINCT … LIMIT）；默认关闭"
    )


class ImportObjectIn(Input):
    table: Annotated[str, StringConstraints(min_length=1, max_length=128)]
    id: ObjectId
    name: Text64
    api: Identifier
    owner: Text64 | None = None


class DraftIn(Input):
    cr_id: Annotated[str, StringConstraints(min_length=1, max_length=64)] | None = None
    lock_version: int | None = Field(None, ge=0)
    title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=40)] | None = None
    reason: Annotated[str, StringConstraints(max_length=4000)] = ""
    reviewer: Text64 | None = None

    @model_validator(mode="after")
    def draft_target(self):
        if self.cr_id and self.lock_version is None:
            raise ValueError("加入变更单必须带 lockVersion")
        if not self.cr_id and not self.title:
            raise ValueError("新建变更单必须填写 title")
        return self


class ImportToCrIn(ImportPreviewIn, DraftIn):
    fingerprint: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
    object_types: list[ImportObjectIn] = Field(min_length=1, max_length=60)
    include_links: bool = False
    link_ids: list[Annotated[str, StringConstraints(min_length=1, max_length=128)]] | None = Field(
        None, max_length=120
    )


class MappingIn(DraftIn):
    object_id: ObjectId
