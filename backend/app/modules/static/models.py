"""静态本体定义表（第一批：对象类型 / 属性 / 对象视图 / 关系 / 指标 / 维度 / 业务知识）。

依据 docs/backend/static-api.md（表与接口）+ 前端 pages/ontology、mock/*.ts（最终依据）。
约定（CONVENTIONS §1、§12）：
- 主键 id bigint；前端 id 放 code（唯一；mock 有重复的用 unique_except_seed 只约束非 seed 行）；
- 外键只指向 id，且只指向本模块的表或骨架已有的表；其它模块（动态/动作/问数）一律按 code 软引用；
- 人员字段（owner 等定义上的人）存姓名字符串，不做外键（static-api.md §4「身份」）；
- 枚举库里存英文码、接口输出中文（LabelEnum），对照见 static-api.md §1.3；
- 只给页面展示、没有独立查询需求的嵌套数据（引用、历史、试问、问数默认口径…）原样存 jsonb，
  键保持 mock 的 camelCase。

[推断] 汇总（前端没定义，这里自行决定的地方，统一在 README 式注释里列出）：
- 版本号列（since_release / until_release / stopped_release）存字符串而不是 ontology_release 外键：
  mock 里有不在 RELEASES 里的旧版本（v2026.03.2、v2026.08.2…），外键写不进去；比较按字符串字典序（前端同）。
- 变更单号（cr_code / draft_cr / review_cr）先存字符串：change_request 表不在这一批。
- 草稿 / 已驳回的关系、草稿知识、草稿取值/说法：§1.3 说只放 cr_item，变更单流程不在本批，
  先留在定义表里，用 status / draft_cr 区分（读已发布视图时排除）。
- link_type 的两端对象：草稿关系 member_holds_cards 指向 stored_card，它只在 CR-0012 草稿里、不是对象类型，
  所以 from/to 同时存对象 code（from_code/to_code，前端 id）和可空外键 from_object_id/to_object_id。
- dim_mapping：§4.1 说「DIM_ITEMS.*.mappings → synonym（target=dimension）」；统一的 synonym 表等知识同义词
  一起做，这批先按维度建 dim_mapping。knowledge.synonyms 先存 jsonb。
- dim_level 只存当前层级（30）；levelsBefore（org 在 v2026.10.2 之前的层级、问数口径、说明）整块存 jsonb。
"""

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.enums import LabelEnum
from app.db.base import Base, CodeMixin, IdMixin, MutableTableMixin, unique_except_seed
from app.db.types import JsonType, enum_column

# ---------------------------------------------------------------- 枚举（static-api.md §1.3）


class ConflictStrategy(LabelEnum):
    user_edit_wins = "用户编辑优先"
    latest_source_ts = "源系统优先"


class ObjectStatus(LabelEnum):
    """对象类型自身状态。[推断] 前端 ObjectTypes.vue TYPES 页内常量里只有 已发布 / 草稿（purchase）。"""

    published = "已发布"
    draft = "草稿"


class Lifecycle(LabelEnum):
    active = "正式"


class ViewKind(LabelEnum):
    """对象视图类型（ViewsTab.vue / mock objectViews.ts）。"""

    workbench = "工作台视图"
    my_page = "我的页面"
    object_list = "对象列表"


class ViewVisibility(LabelEnum):
    only_me = "仅自己"
    specified = "指定人员"
    by_role = "角色"
    everyone = "全员"


class Cardinality(LabelEnum):
    one_to_one = "1 : 1"
    one_to_many = "1 : N"
    many_to_one = "N : 1"
    many_to_many = "N : M"


class DefStatus(LabelEnum):
    """定义状态：关系（已发布/草稿/已驳回）、维度（已发布）。"""

    published = "已发布"
    draft = "草稿"
    rejected = "已驳回"


class MetricStatus(LabelEnum):
    """指标列表状态（METRIC_LIST.status）。[推断] §1.3 的 active/superseded 是指标版本状态，列表状态另起。"""

    published = "已发布"
    draft = "草稿"
    offline = "已下线"


class DimKind(LabelEnum):
    dimension = "维度"
    codelist = "码表"


class DimValueStatus(LabelEnum):
    enabled = "启用"
    disabled = "停用"


class MatchMode(LabelEnum):
    exact = "精确"
    contains = "包含"
    multi = "多值"


class MappingSource(LabelEnum):
    value_synonym = "取值同义词"
    value_name = "取值名称"
    knowledge = "业务知识"
    qa_feedback = "问数反馈"


class KnowledgeType(LabelEnum):
    term = "术语"
    rule = "业务规则"
    fiscal_calendar = "财务日历"
    metric_note = "口径说明"


class KnowledgeStatus(LabelEnum):
    published = "已发布"
    draft = "草稿"
    disabled = "已停用"


class ApplyKind(LabelEnum):
    """knowledge.applies[].kind：前端原值就是英文（object-type / metric），接口原样输出。"""

    object_type = "object-type"
    metric = "metric"


# ---------------------------------------------------------------- 对象类型 / 属性 / 视图


class ObjectType(CodeMixin, MutableTableMixin, Base):
    """对象类型。code = 前端 id（order），api_name = SalesOrder。

    来源：OBJECT_PROPS 的键（18）+ derived objectMeta（域、说明、冲突策略、样例问题…）
    + LINK_OBJECTS（物理表）+ Q_OBJECT_TYPES（API 名兜底、负责人、数据源、行数、截止时间）[推断，见 seeds]。
    """

    __tablename__ = "object_type"
    __table_args__ = (UniqueConstraint("api_name"), {"comment": "对象类型"})

    api_name: Mapped[str] = mapped_column(String(64), nullable=False, comment="API 名（SalesOrder）")
    display_name: Mapped[str] = mapped_column(String(64), nullable=False)
    obj_group: Mapped[str] = mapped_column(String(32), nullable=False, comment="分组（交易/客户/…）")
    domain: Mapped[str | None] = mapped_column(String(64), comment="业务域（零售交易）")
    description: Mapped[str] = mapped_column(Text, nullable=False, default="", comment="说明（自然语言）")
    conflict_strategy: Mapped[ConflictStrategy] = mapped_column(enum_column(ConflictStrategy, 24))
    conflict_note: Mapped[str | None] = mapped_column(
        String(128), comment="冲突策略括号里的补充说明（mock 原文去掉主值后的部分）"
    )
    lifecycle: Mapped[Lifecycle] = mapped_column(enum_column(Lifecycle, 16), nullable=False)
    sync: Mapped[str | None] = mapped_column(String(128), comment="同步方式（文案）")
    sample_questions: Mapped[list[Any]] = mapped_column(
        JsonType, nullable=False, default=list, comment="样例问题 qs[{q,cert,query?}]"
    )
    qa_cards: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="问数卡片数（cards）")
    qa30: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="近 30 天问数次数")
    unused_props: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="未使用属性数")
    source_table: Mapped[str | None] = mapped_column(String(128), comment="物理表（LINK_OBJECTS.table）")
    datasource_name: Mapped[str | None] = mapped_column(String(64), comment="数据源（Q_OBJECT_TYPES.source）")
    owner: Mapped[str | None] = mapped_column(String(64), comment="负责人姓名（软引用）")
    pk_property: Mapped[str | None] = mapped_column(String(64), comment="主键属性 api（Q_OBJECT_TYPES.pk）")
    record_count: Mapped[int | None] = mapped_column(BigInteger, comment="行数（运行时，不进快照）")
    data_cutoff: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), comment="数据截止时间")
    status: Mapped[ObjectStatus] = mapped_column(
        enum_column(ObjectStatus, 16),
        nullable=False,
        default=ObjectStatus.published,
        server_default="published",
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="mock 顺序")

    properties: Mapped[list["Property"]] = relationship(
        back_populates="object_type", order_by="Property.seq", lazy="selectin"
    )


class Property(IdMixin, MutableTableMixin, Base):
    """对象属性（OBJECT_PROPS[obj][]，211）。同一对象内 api_name 唯一。"""

    __tablename__ = "property"
    __table_args__ = (
        UniqueConstraint("object_type_id", "api_name"),
        {"comment": "对象属性"},
    )

    object_type_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("object_type.id", ondelete="CASCADE"), nullable=False, index=True
    )
    api_name: Mapped[str] = mapped_column(String(64), nullable=False)
    display_name: Mapped[str] = mapped_column(String(64), nullable=False)
    data_type: Mapped[str] = mapped_column(
        String(32), nullable=False, comment="类型（string/decimal/…，原样）"
    )
    prop_group: Mapped[str] = mapped_column(String(32), nullable=False, comment="分组（基本/金额/…）")
    is_pk: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_title: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_dimension: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, comment="mock dim")
    is_sensitive: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    enum_name: Mapped[str | None] = mapped_column(String(64))
    source_column: Mapped[str] = mapped_column(String(256), nullable=False, comment="来源字段（mock source）")
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    src_type: Mapped[str] = mapped_column(String(64), nullable=False, default="", comment="映射页：源类型")
    transform: Mapped[str] = mapped_column(Text, nullable=False, default="", comment="映射页：转换说明")
    map_column: Mapped[str] = mapped_column(String(256), nullable=False, default="", comment="映射页：源字段")
    seq: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    object_type: Mapped[ObjectType] = relationship(back_populates="properties")


class ObjectView(IdMixin, MutableTableMixin, Base):
    """对象视图（derived viewsOf，18 个对象 / 285 条）。

    mock 里视图编号跨对象重复（V-ORD0002 同时出现在订单、订单行…，只有 220 个不同编号），
    所以 code 只对非 seed 行唯一；seed 键 = (object_type_id, code)。
    """

    __tablename__ = "object_view"
    __table_args__ = (
        unique_except_seed("object_view", "code"),
        UniqueConstraint("object_type_id", "code"),
        {"comment": "对象视图"},
    )

    code: Mapped[str] = mapped_column(String(64), nullable=False, index=True, comment="视图编号（前端 id）")
    object_type_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("object_type.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    kind: Mapped[ViewKind] = mapped_column(enum_column(ViewKind, 16), nullable=False)
    owner: Mapped[str] = mapped_column(String(64), nullable=False, comment="创建人姓名（软引用）")
    visibility: Mapped[ViewVisibility] = mapped_column(enum_column(ViewVisibility, 16), nullable=False)
    visible_to: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    uses_30d: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    filter_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    props: Mapped[list[str]] = mapped_column(JsonType, nullable=False, default=list)
    seq: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    object_type: Mapped[ObjectType] = relationship(lazy="joined")


# ---------------------------------------------------------------- 关系


class LinkType(CodeMixin, MutableTableMixin, Base):
    """关系类型（LINKS，27：已发布 25、草稿 1、已驳回 1）。"""

    __tablename__ = "link_type"
    __table_args__ = ({"comment": "关系类型"},)

    api_name: Mapped[str | None] = mapped_column(String(64), comment="API 名（mock api，缺省 = id）")
    display_name: Mapped[str] = mapped_column(String(64), nullable=False)
    from_code: Mapped[str] = mapped_column(String(64), nullable=False, index=True, comment="源对象前端 id")
    to_code: Mapped[str] = mapped_column(String(64), nullable=False, index=True, comment="目标对象前端 id")
    from_object_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("object_type.id"), index=True)
    to_object_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("object_type.id"), index=True, comment="[推断] 目标还不是对象类型时为空"
    )
    cardinality: Mapped[Cardinality] = mapped_column(enum_column(Cardinality, 16), nullable=False)
    key_expr: Mapped[str] = mapped_column(String(256), nullable=False, comment="关联键（mock key）")
    from_prop: Mapped[str] = mapped_column(String(64), nullable=False)
    to_prop: Mapped[str] = mapped_column(String(64), nullable=False)
    via: Mapped[str | None] = mapped_column(String(64), comment="中间表")
    join_sql: Mapped[str] = mapped_column(Text, nullable=False)
    reverse_name: Mapped[str] = mapped_column(String(64), nullable=False)
    nullable: Mapped[bool] = mapped_column(Boolean, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[DefStatus] = mapped_column(enum_column(DefStatus, 16), nullable=False)
    since_release: Mapped[str | None] = mapped_column(String(32), comment="首次发布版本号 [推断] 字符串")
    cr_code: Mapped[str | None] = mapped_column(String(32), comment="变更单号（软引用）")
    owner: Mapped[str] = mapped_column(String(64), nullable=False)
    usage: Mapped[list[Any]] = mapped_column(
        JsonType, nullable=False, default=list, comment="引用（mock 原样）"
    )
    history: Mapped[list[Any]] = mapped_column(JsonType, nullable=False, default=list, comment="变更历史")
    seq: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    quality: Mapped["LinkQuality | None"] = relationship(back_populates="link", lazy="selectin")


class LinkQuality(IdMixin, MutableTableMixin, Base):
    """关系数据质量（LINKS.*.quality，26）。运行时数据，不进快照。"""

    __tablename__ = "link_quality"
    __table_args__ = (UniqueConstraint("link_type_id"), {"comment": "关系数据质量"})

    link_type_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("link_type.id", ondelete="CASCADE"), nullable=False
    )
    hit: Mapped[Decimal] = mapped_column(Numeric(7, 3), nullable=False, comment="命中率 %")
    orphans: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    nulls: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    dup: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    note: Mapped[str | None] = mapped_column(Text)
    orphan_samples: Mapped[list[Any] | None] = mapped_column(JsonType)

    link: Mapped[LinkType] = relationship(back_populates="quality")


# ---------------------------------------------------------------- 指标


class Metric(CodeMixin, MutableTableMixin, Base):
    """指标（METRIC_LIST 15 条，合并 METRICS 7 条的口径 / 值）。

    [推断] §4.1 写的是 METRICS 7 + extra 2；现在 METRIC_LIST 已导出（15），直接按它建，不再要 extra。
    指标页的 定义 / 允许维度 / 版本 / 引用 在 mock 里是「所有指标共用」的示例（metricCatalog.ts），
    原样存到每个指标的 jsonb 列里（def_rows / dim_rows / version_rows / ref_rows）。
    """

    __tablename__ = "metric"
    __table_args__ = (UniqueConstraint("api_name"), {"comment": "指标（当前版本）"})

    api_name: Mapped[str] = mapped_column(String(64), nullable=False)
    display_name: Mapped[str] = mapped_column(String(64), nullable=False)
    metric_group: Mapped[str] = mapped_column(String(32), nullable=False)
    unit: Mapped[str] = mapped_column(String(16), nullable=False)
    version: Mapped[str] = mapped_column(String(16), nullable=False, comment="口径版本 v3")
    status: Mapped[MetricStatus] = mapped_column(enum_column(MetricStatus, 16), nullable=False)
    owner: Mapped[str] = mapped_column(String(64), nullable=False)
    review_cr: Mapped[str | None] = mapped_column(String(32), comment="审核中的变更单号（review.cr）")
    review_note: Mapped[str | None] = mapped_column(String(256))
    formula: Mapped[str | None] = mapped_column(Text, comment="METRICS.formula（7 个指标有）")
    dims: Mapped[list[str] | None] = mapped_column(JsonType, comment="允许拆分的维度名（METRICS.dims）")
    value: Mapped[Decimal | None] = mapped_column(Numeric(20, 4), comment="当前值（运行时，不进快照）")
    prev: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    stat_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), comment="METRICS.updatedAt（带秒）"
    )
    def_rows: Mapped[list[Any]] = mapped_column(JsonType, nullable=False, default=list)
    dim_rows: Mapped[list[Any]] = mapped_column(JsonType, nullable=False, default=list)
    version_rows: Mapped[list[Any]] = mapped_column(JsonType, nullable=False, default=list)
    ref_rows: Mapped[list[Any]] = mapped_column(JsonType, nullable=False, default=list)
    disallowed_rows: Mapped[list[Any]] = mapped_column(
        JsonType, nullable=False, default=list, comment="不允许的维度 {dim,reason}（Metrics.vue DENY）"
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="METRIC_LIST 顺序")
    stat_seq: Mapped[int | None] = mapped_column(Integer, comment="METRICS 顺序（有统计值的 7 个）")


# ---------------------------------------------------------------- 维度


class Dimension(CodeMixin, MutableTableMixin, Base):
    """维度 / 码表（DIM_ITEMS，17）。"""

    __tablename__ = "dimension"
    __table_args__ = (UniqueConstraint("api_name"), {"comment": "维度 / 码表"})

    api_name: Mapped[str] = mapped_column(String(64), nullable=False)
    display_name: Mapped[str] = mapped_column(String(64), nullable=False)
    kind: Mapped[DimKind] = mapped_column(enum_column(DimKind, 16), nullable=False)
    dim_group: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[DefStatus] = mapped_column(enum_column(DefStatus, 16), nullable=False)
    owner: Mapped[str] = mapped_column(String(64), nullable=False)
    object_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("object_type.id"), nullable=False, index=True
    )
    object_name: Mapped[str] = mapped_column(String(64), nullable=False, comment="mock objectName（原文）")
    attr: Mapped[str] = mapped_column(String(128), nullable=False)
    source_column: Mapped[str] = mapped_column(String(256), nullable=False, comment="mock column")
    value_src: Mapped[str] = mapped_column(String(256), nullable=False)
    sync: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    qa: Mapped[dict[str, Any]] = mapped_column(JsonType, nullable=False, comment="问数默认口径（原样）")
    levels_before: Mapped[dict[str, Any] | None] = mapped_column(
        JsonType, comment="[推断] 某版本之前的层级/口径（mock levelsBefore 原样）"
    )
    metric_dims: Mapped[list[str]] = mapped_column(JsonType, nullable=False, default=list)
    metric_note: Mapped[dict[str, Any] | None] = mapped_column(JsonType)
    usage: Mapped[list[Any]] = mapped_column(JsonType, nullable=False, default=list)
    history: Mapped[list[Any]] = mapped_column(JsonType, nullable=False, default=list)
    since_release: Mapped[str] = mapped_column(String(32), nullable=False)
    count_label: Mapped[str | None] = mapped_column(String(64))
    values_note: Mapped[str | None] = mapped_column(Text)
    draft_note: Mapped[str | None] = mapped_column(Text)
    seq: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    object: Mapped[ObjectType] = relationship(lazy="joined")
    levels: Mapped[list["DimLevel"]] = relationship(order_by="DimLevel.level_no", lazy="selectin")


class DimLevel(IdMixin, MutableTableMixin, Base):
    """维度层级（DIM_ITEMS.*.levels，30）。"""

    __tablename__ = "dim_level"
    __table_args__ = (UniqueConstraint("dimension_id", "level_no"), {"comment": "维度层级"})

    dimension_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("dimension.id", ondelete="CASCADE"), nullable=False
    )
    level_no: Mapped[int] = mapped_column(Integer, nullable=False, comment="从 1 起")
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    api_name: Mapped[str] = mapped_column(String(64), nullable=False)
    source: Mapped[str] = mapped_column(String(256), nullable=False)
    value_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    note: Mapped[str | None] = mapped_column(Text)


class DimValue(IdMixin, MutableTableMixin, Base):
    """维度取值（DIM_ITEMS.*.values，235）。同一维度内 value_key（层级:编码）唯一。"""

    __tablename__ = "dim_value"
    __table_args__ = (UniqueConstraint("dimension_id", "value_key"), {"comment": "维度取值"})

    dimension_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("dimension.id", ondelete="CASCADE"), nullable=False, index=True
    )
    value_key: Mapped[str] = mapped_column(String(128), nullable=False, comment="mock key（层级:编码）")
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    level_name: Mapped[str] = mapped_column(String(64), nullable=False)
    parent: Mapped[str | None] = mapped_column(String(128), comment="上级取值名称（mock parent 原文）")
    status: Mapped[DimValueStatus] = mapped_column(enum_column(DimValueStatus, 16), nullable=False)
    synonyms: Mapped[list[str]] = mapped_column(JsonType, nullable=False, default=list)
    record_count: Mapped[int | None] = mapped_column(BigInteger, comment="mock count（运行时）")
    src: Mapped[str | None] = mapped_column(String(256))
    note: Mapped[str | None] = mapped_column(Text)
    ref: Mapped[dict[str, Any] | None] = mapped_column(JsonType, comment="跳转引用（mock ref 原样）")
    since_release: Mapped[str | None] = mapped_column(String(32))
    until_release: Mapped[str | None] = mapped_column(String(32))
    draft_cr: Mapped[str | None] = mapped_column(String(32), comment="[推断] 草稿所在变更单（mock draft）")
    diff: Mapped[str | None] = mapped_column(String(16))
    pub: Mapped[dict[str, Any] | None] = mapped_column(
        JsonType, comment="草稿里改过的值的已发布原值（mock pub；当前种子没有）"
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class DimMapping(IdMixin, MutableTableMixin, Base):
    """说法映射（DIM_ITEMS.*.mappings，34）。[推断] 统一 synonym 表前先按维度建。"""

    __tablename__ = "dim_mapping"
    __table_args__ = (UniqueConstraint("dimension_id", "map_key"), {"comment": "维度说法映射"})

    dimension_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("dimension.id", ondelete="CASCADE"), nullable=False, index=True
    )
    map_key: Mapped[str] = mapped_column(String(256), nullable=False, comment="mock key")
    phrase: Mapped[str] = mapped_column(String(128), nullable=False)
    targets: Mapped[list[str]] = mapped_column(JsonType, nullable=False, default=list)
    match: Mapped[MatchMode] = mapped_column(enum_column(MatchMode, 16), nullable=False)
    source: Mapped[MappingSource] = mapped_column(enum_column(MappingSource, 24), nullable=False)
    hits: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="命中次数（运行时）")
    conflict: Mapped[str | None] = mapped_column(Text)
    resolve: Mapped[str | None] = mapped_column(Text)
    draft_cr: Mapped[str | None] = mapped_column(String(32))
    diff: Mapped[str | None] = mapped_column(String(16))
    seq: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


# ---------------------------------------------------------------- 业务知识


class Knowledge(CodeMixin, MutableTableMixin, Base):
    """业务知识（KNOWLEDGE，26：已发布 23、已停用 2、草稿 1）。"""

    __tablename__ = "knowledge"
    __table_args__ = ({"comment": "业务知识 / 术语"},)

    name: Mapped[str] = mapped_column(String(128), nullable=False)
    type: Mapped[KnowledgeType] = mapped_column(enum_column(KnowledgeType, 24), nullable=False)
    status: Mapped[KnowledgeStatus] = mapped_column(enum_column(KnowledgeStatus, 16), nullable=False)
    definition: Mapped[str] = mapped_column(Text, nullable=False, default="")
    calc: Mapped[str] = mapped_column(Text, nullable=False, default="")
    synonyms: Mapped[list[str]] = mapped_column(JsonType, nullable=False, default=list)
    period: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    owner: Mapped[str] = mapped_column(String(64), nullable=False)
    maps: Mapped[str] = mapped_column(Text, nullable=False, default="")
    filters: Mapped[list[Any]] = mapped_column(JsonType, nullable=False, default=list)
    tries: Mapped[list[Any]] = mapped_column(JsonType, nullable=False, default=list)
    periods: Mapped[list[Any] | None] = mapped_column(JsonType)
    refs: Mapped[list[Any]] = mapped_column(JsonType, nullable=False, default=list)
    history: Mapped[list[Any]] = mapped_column(JsonType, nullable=False, default=list)
    draft: Mapped[dict[str, Any] | None] = mapped_column(JsonType, comment="[推断] 草稿内容（应进 cr_item）")
    since_release: Mapped[str | None] = mapped_column(String(32))
    stopped_release: Mapped[str | None] = mapped_column(String(32), comment="mock stoppedIn")
    cr_code: Mapped[str | None] = mapped_column(String(32))
    content_updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, comment="mock updatedAt"
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    applies: Mapped[list["KnowledgeApply"]] = relationship(order_by="KnowledgeApply.seq", lazy="selectin")


class KnowledgeApply(IdMixin, MutableTableMixin, Base):
    """知识适用范围（KNOWLEDGE.*.applies，56）。指向对象类型 / 指标，按前端 id 软引用 [推断]。

    target_code 不做外键：applies 里有不是对象类型的 id（如 K-018 草稿里的 promotion 是对象，但 metric 指向
    指标 code），一张表两种目标，统一存 code。
    """

    __tablename__ = "knowledge_apply"
    __table_args__ = (
        UniqueConstraint("knowledge_id", "target_kind", "target_code"),
        {"comment": "知识适用范围"},
    )

    knowledge_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("knowledge.id", ondelete="CASCADE"), nullable=False, index=True
    )
    target_kind: Mapped[ApplyKind] = mapped_column(enum_column(ApplyKind, 16), nullable=False)
    target_code: Mapped[str] = mapped_column(String(64), nullable=False)
    target_name: Mapped[str] = mapped_column(String(128), nullable=False)
    seq: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
