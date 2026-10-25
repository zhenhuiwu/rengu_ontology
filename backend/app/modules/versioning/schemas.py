"""对外 Schema。字段与前端 src/mock/releases.ts 的 `Release` 一一对应：
{ ver, publishedAt: 'YYYY-MM-DD HH:mm', by, note }；额外字段（parentVer、kind、rollbackOf、changeOrderIds）
不影响前端。字段名由 ApiModel 自动转 camelCase（published_at → publishedAt）；
by 是“接口名和库列名不同”的示范（库列 published_by）。
"""

from pydantic import Field

from app.core.schema import ApiModel, DateTimeMin
from app.modules.versioning.models import ReleaseKind


class ReleaseOut(ApiModel):
    ver: str
    published_at: DateTimeMin
    by: str = Field(validation_alias="published_by")
    note: str
    parent_ver: str | None = None
    kind: ReleaseKind = ReleaseKind.publish  # 库里 publish/rollback，接口 发布/回滚
    rollback_of: str | None = None
    change_order_ids: list[str] = []


class ReleaseDetailOut(ReleaseOut):
    snapshot_sha256: str
    change_summary: dict


class CurrentVersionOut(ApiModel):
    """对应前端 CURRENT_VERSION / NEXT_VERSION。"""

    current: str | None
    next: str
    release: ReleaseOut | None


class ReleaseListOut(ApiModel):
    """GET /releases：不分页。items = 前端 RELEASES（新→旧）；current = CURRENT_VERSION。"""

    items: list[ReleaseOut]
    current: str | None
