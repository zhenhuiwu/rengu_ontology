"""问数对外 Schema（字段对齐前端 mock：mock/qa.ts、qa-pages.ts、qa-tuning.ts、objects-qa.ts）。

时间按 qa-api §9.3 逐字段格式化，所以这里的时间字段都是已经格式化好的 str（service 里转）。
卡片、语义计划、轮次快照、刷新计划这些前端整体使用的结构用 dict 原样透传。
"""

from typing import Any, Literal

from pydantic import Field

from app.common.pagination import Page
from app.core.schema import ApiModel

# ---------------------------------------------------------------- 工作台


class SessionOut(ApiModel):
    """W1 · QaSession{id,title,date}。"""

    id: str
    title: str
    date: str  # YYYY-MM-DD


class SessionDetailOut(SessionOut):
    """W3：会话 + 全部轮次（快照，一次返回）。"""

    scope: str | None = None
    caliber: str  # 不含税 / 含税
    owner_id: str
    turns: list[dict[str, Any]]


class SessionCreateIn(ApiModel):
    """W2 请求体：{scope, caliber: 不含税|含税}（默认同 Workbench.vue:50-51）。"""

    scope: str | None = Field(default="华东", max_length=60)
    caliber: Literal["不含税", "含税"] = "不含税"


class SessionPatchIn(ApiModel):
    """W4 请求体：{title?} / {scope?, caliber?}。"""

    title: str | None = Field(default=None, min_length=1, max_length=60)
    scope: str | None = Field(default=None, max_length=60)
    caliber: Literal["不含税", "含税"] | None = None


class FavoriteOut(ApiModel):
    """W13 · QaFavorite。"""

    id: str
    question: str
    uses: int
    last_used: str | None = None  # MM-DD


class OptionOut(ApiModel):
    value: str
    label: str


class MetricRefOut(ApiModel):
    id: str
    name: str
    version: str


class SessionOptionsOut(ApiModel):
    """W17。metric 的 in 是 Python 关键字，用 dict。"""

    scopes: list[OptionOut]
    calibers: list[OptionOut]
    metric: dict[str, MetricRefOut]


# ---------------------------------------------------------------- 页面与模板


class PageBriefOut(ApiModel):
    """QaPage（左栏 / 固定到页面用的简版）。"""

    id: str
    name: str
    cards: int
    updated_at: str  # MM-DD
    shared: bool


class ShareMemberOut(ApiModel):
    user_id: str
    name: str
    dept: str | None = None
    perm: str


class ShareOut(ApiModel):
    mode: str
    members: list[ShareMemberOut]
    link: bool
    allow_export: bool


class SubOut(ApiModel):
    on: bool
    channel: str
    receivers: list[str]
    when: str
    only_changed: bool


class CaliberTagOut(ApiModel):
    label: str
    pending: str | None = None


class PageFullOut(ApiModel):
    """QaPageFull（P1 view=full / P3）。"""

    id: str
    name: str
    owner: str
    owner_id: str
    scope: str | None = None
    source: str | None = None
    source_kind: str
    cards: list[dict[str, Any]]
    data_as_of: str | None = None
    last_refresh: str | None = None
    updated_at: str
    refresh: dict[str, Any]
    sub: SubOut | None = None
    share: ShareOut
    status: str
    status_note: str | None = None
    views: int
    calibers: list[CaliberTagOut] | None = None


class PageListOut(Page[PageBriefOut | PageFullOut]):
    from_workbench_count: int


class TemplateOut(ApiModel):
    """QaTemplate（P11 / P12）。"""

    id: str
    name: str
    scene: str
    cards: list[dict[str, Any]]
    provider: str
    uses: int
    updated_at: str  # YYYY-MM-DD
    official: bool
    desc: str


# ---------------------------------------------------------------- 对象类型


class ObjectTypeOut(ApiModel):
    kind: str
    label: str
    unit: str | None = None
    kw_placeholder: str | None = None
    facets: list[dict[str, Any]]
    columns: list[dict[str, Any]]


# ---------------------------------------------------------------- 调优


class TuningSummaryOut(ApiModel):
    version: str
    data_as_of: str
    recert_count: int
    open_feedback: int
    uncert_freq: int
    eval_total: int


class TMetricOut(ApiModel):
    key: str
    name: str
    version: str
    cr: str | None = None


class PlanOptionsOut(ApiModel):
    metrics: list[TMetricOut]
    dims: list[str]
    times: list[str]


class CertOut(ApiModel):
    id: str
    question: str
    variants: list[str]
    plan: dict[str, Any]
    plan_text: str
    status: str
    cr: str | None = None
    certifier: str | None = None
    certified_at: str | None = None
    hits: int
    source: str
    note: str | None = None


class EvalRunOut(ApiModel):
    id: str
    time: str
    trigger: str
    version: str | None = None
    scope: str
    total: int
    passed: int
    pass_rate: float  # passed / total（0–1），total=0 时 0，跟 mock passRate 一致
    fail_count: int
    status: str
    operator: str
    cr: str | None = None
    crs: list[str] | None = None
    note: str | None = None


class FailCaseOut(ApiModel):
    qid: str
    question: str
    expect: str
    actual: str
    kind: str


class KindStatOut(ApiModel):
    kind: str
    n: int


class EvalQOut(ApiModel):
    id: str
    question: str
    group: str | None = None
    expect: dict[str, Any] | None = None
    expect_text: str
    last: str | None = None
    cr: str | None = None


class EvalRunDetailOut(EvalRunOut):
    fails: Page[FailCaseOut]
    kind_stats: list[KindStatOut]
    replay_questions: list[EvalQOut] = []
    replay_hints: dict[str, str] = {}


class GroupCountOut(ApiModel):
    group: str
    n: int


class EvalQListOut(Page[EvalQOut]):
    group_counts: list[GroupCountOut]


class FixOut(ApiModel):
    kind: str
    text: str
    target: str | None = None


class FeedbackOut(ApiModel):
    id: str
    time: str
    user: str
    user_id: str | None = None
    dept: str | None = None
    question: str
    answer: str
    reason: str
    fix: FixOut
    status: str
    resolved_by: str | None = None
    handler: str | None = None


class FeedbackDetailOut(FeedbackOut):
    cert_block: str
    existing_cert: str | None = None
    covered_by: str | None = None
    related_eval_count: int


class FreqOut(ApiModel):
    id: str
    rank: int
    question: str
    hits7: int
    hits30: int
    users: int
    plan: dict[str, Any]
    plan_text: str
    consistency: float
    last_asked: str | None = None
    cert_id: str | None = None
    cert_status: str | None = None


class SynonymOut(ApiModel):
    word: str
    target: str
    cr: str | None = None


class AskIn(ApiModel):
    """W6 请求体：{question, refs?, clientId?}。"""

    question: str = Field(min_length=1, max_length=500)
    refs: list[dict[str, Any]] = Field(default_factory=list)
    client_id: str | None = None


class AskOut(ApiModel):
    """W6 响应（202）。[推断] 本期同步跑完链路，直接带回轮次 turn；streamUrl 留给 W7 SSE（未实现）。"""

    turn_id: str
    stream_url: str
    turn: dict[str, Any]
