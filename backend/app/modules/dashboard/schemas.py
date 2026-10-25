"""首页看板对外 Schema（字段对齐 Home.vue / mock/metrics.ts）。"""

from decimal import Decimal

from pydantic import Field, field_serializer

from app.common.pagination import Page
from app.core.schema import ApiModel, DateTimeMin


class KpiMetricOut(ApiModel):
    """mock Metric 的子集：Home.vue:23-29 用到 id / name / unit / value / prev。"""

    id: str = Field(validation_alias="code")
    name: str = Field(validation_alias="display_name")
    unit: str
    value: Decimal | None = None
    prev: Decimal | None = None

    @field_serializer("value", "prev")
    def _num(self, v: Decimal | None) -> float | None:
        return None if v is None else float(v)


class KpisOut(ApiModel):
    """GET /dashboard/kpis：items 按请求 ids 的顺序；dataAsOf 同 GET /orders（"YYYY-MM-DD HH:mm"）。"""

    items: list[KpiMetricOut]
    data_as_of: DateTimeMin


class StoreRankOut(ApiModel):
    """Home.vue:77-81 fullRanking 的一行。amount = 不含税销售额（实付 ÷ 1.13，四舍五入到分）。"""

    rank: int
    id: str
    name: str
    city: str
    cnt: int
    amount: float


class StoreRankingPage(Page[StoreRankOut]):
    data_as_of: DateTimeMin
