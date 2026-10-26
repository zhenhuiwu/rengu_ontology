"""Pydantic 输入输出（字段对齐 src/mock/datasources.ts 的 Datasource / DsTable / DsTest / DsRun）。

密码只写：DatasourceCreate / DatasourceUpdate 有 password，输出模型没有。
"""

from typing import Annotated

from pydantic import Field, StringConstraints

from app.core.schema import ApiModel, CodeId, DateTimeMin, DisplayDate
from app.modules.data.models import DsRunResult, DsStatus, DsSyncMode, DsType

Str = Annotated[str, StringConstraints(strip_whitespace=True)]
Required = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class DsTest(ApiModel):
    time: str
    ok: bool
    ms: int
    msg: str


class DsSync(ApiModel):
    mode: DsSyncMode
    cron: str
    cron_text: str
    window: str
    cursor_field: str
    next_run: DateTimeMin | None = None


class DsRun(ApiModel):
    time: DateTimeMin
    result: DsRunResult
    rows: int
    secs: int
    msg: str


class DsRefOnly(ApiModel):
    objects: list[str]
    source: str


class DatasourceOut(ApiModel):
    """Datasource（不含密码）。可选字段为空时输出 null（mock 里是缺省 / undefined）。"""

    id: CodeId
    name: str
    type: DsType
    host: str
    port: int
    database: str
    user: str
    compat_mode: str | None = None
    database_mode: str | None = None
    erp_system: str | None = None
    app_id: str | None = None
    ssl: bool
    status: DsStatus
    last_test: DsTest | None = None
    owner: str
    object_types: list[str]
    sync: DsSync
    runs: list[DsRun]
    table_count: int
    created_at: DisplayDate
    note: str | None = None
    ref_only: DsRefOnly | None = None


class DsTableOut(ApiModel):
    schema_: str = Field(alias="schema")
    name: str
    comment: str
    rows: int
    cols: int
    object_type: str | None = None
    updated_at: DateTimeMin
    ref: str | None = None


class DsMeta(ApiModel):
    types: list[str]
    default_port: dict[str, int]
    dm_compat: list[str]
    kb_modes: list[str]
    erp_systems: list[str]


class _DsWriteBase(ApiModel):
    """Datasource 的可写字段（前端抽屉表单 Form：mode / cron 写进 sync）。"""

    type: DsType = DsType.postgresql
    port: int | None = Field(None, ge=1, le=65535, description="不传 = 该类型默认端口")
    compat_mode: Str | None = None
    database_mode: Str | None = None
    erp_system: Str | None = None
    app_id: Str | None = None
    ssl: bool = True
    owner: Str = ""
    mode: DsSyncMode = DsSyncMode.incremental
    cron: Str = "0 * * * *"


class DatasourceCreate(_DsWriteBase):
    name: Required
    host: Required
    database: Required
    user: Required
    password: Annotated[str, StringConstraints(min_length=1)]


class DatasourceUpdate(_DsWriteBase):
    """PUT：整表单提交；password 不传或为空 = 不修改。"""

    name: Required
    host: Required
    database: Required
    user: Required
    password: str | None = None


class DsTestIn(ApiModel):
    """POST /datasources:test：表单里还没保存的连接参数（不落库）。"""

    type: DsType = DsType.postgresql
    host: Required
    port: int | None = Field(None, ge=1, le=65535)
    database: Str = ""
    user: Str = ""
    password: str | None = None
    compat_mode: Str | None = None
    database_mode: Str | None = None
    erp_system: Str | None = None
    app_id: Str | None = None
    ssl: bool = True
    mock_fail: bool = Field(False, description="兼容旧调用保留；真实测试忽略，不影响连接结果")
