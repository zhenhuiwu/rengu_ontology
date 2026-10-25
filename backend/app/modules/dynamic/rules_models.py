"""动态本体第二部分：规则 / 命中 / 情景推演 / 生命周期（dynamic-api.md §2.3–2.7，迁移 dynamic_0002）。

外键：规则 → action_type、生命周期 → object_type / event_type / action_type、命中 / 状态变化 → alert、
推演 → ontology_release 都是真外键（这些表都已在 master）。人员字段照 mock 存姓名。
草稿（RULE_DRAFTS / LC_DRAFT_EDITS）规格写的是静态的 cr_item，但 master 还没有变更单表，
本批先落在本模块的 rule_draft / lifecycle_draft，变更单号 cr_code 是软引用（FK to add later）。
"""

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.enums import LabelEnum
from app.db.base import (
    AppendOnlyTableMixin,
    Base,
    CodeMixin,
    IdMixin,
    MutableTableMixin,
    OntologyVersionMixin,
)
from app.db.types import JsonType, StrArray, enum_column
from app.modules.dynamic.models import AlertLevel


class RuleStatus(LabelEnum):
    published = "已发布"
    changing = "变更中"
    disabled = "已停用"


class RuleOutput(LabelEnum):
    alert = "告警"
    notify = "通知"


class HitResult(LabelEnum):
    """命中结果：alert = 生成了告警（接口输出告警当前状态），notified / deduped 原样。"""

    alert = "告警"
    notified = "已通知"
    deduped = "已去重"


class ScenarioStatus(LabelEnum):
    draft = "草稿"
    running = "运行中"
    done = "已完成"
    failed = "失败"


class LcStateKind(LabelEnum):
    initial = "初始"
    middle = "中间"
    final = "终态"


class LcTrigger(LabelEnum):
    event = "事件"
    action = "动作"
    system = "系统"
    manual = "人工"


# ---------------- 规则 ----------------


class Rule(CodeMixin, MutableTableMixin, OntologyVersionMixin, Base):
    __tablename__ = "rule"
    __table_args__ = ({"comment": "规则（当前定义；RULES 5 条核心 + RULE_DEFS 16 条合并）"},)

    version: Mapped[int | None] = mapped_column(Integer, comment="当前生效版本；null = 回滚后不在快照里")
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    desc: Mapped[str] = mapped_column(Text, nullable=False, default="")
    owner: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[RuleStatus] = mapped_column(enum_column(RuleStatus, 16), nullable=False, index=True)
    core: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    obj_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    level: Mapped[AlertLevel] = mapped_column(enum_column(AlertLevel, 16), nullable=False)
    output: Mapped[RuleOutput] = mapped_column(enum_column(RuleOutput, 16), nullable=False)
    conds: Mapped[list] = mapped_column(
        JsonType, nullable=False, comment="[{metric,metricVer,field,op,value}]"
    )
    logic: Mapped[str] = mapped_column(String(8), nullable=False, default="且")
    expr: Mapped[str] = mapped_column(Text, nullable=False, default="")
    dedupe: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    cooldown: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    trigger: Mapped[dict] = mapped_column(JsonType, nullable=False, comment="{kind,cron,text,eventId,filter}")
    action_type_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("action_type.id"), index=True)
    auto_stage: Mapped[dict] = mapped_column(
        JsonType, nullable=False, comment="{enabled,actionTypeId,confirmer,mapping[{param,from}]}"
    )
    weekly: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="周命中数（mock weekly）")
    confirmer: Mapped[str | None] = mapped_column(String(64), comment="固定确认人（RULE_CONFIRMER）")
    published_by: Mapped[str | None] = mapped_column(String(64))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    change_note: Mapped[str | None] = mapped_column(Text)


class RuleVersion(IdMixin, AppendOnlyTableMixin, Base):
    __tablename__ = "rule_version"
    __table_args__ = (UniqueConstraint("rule_id", "version"), {"comment": "规则版本历史（只追加）"})

    rule_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("rule.id"), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    by: Mapped[str] = mapped_column(String(64), nullable=False)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    change: Mapped[str] = mapped_column(Text, nullable=False)


class RuleDraft(IdMixin, MutableTableMixin, Base):
    __tablename__ = "rule_draft"
    __table_args__ = (UniqueConstraint("rule_id", "cr_code"), {"comment": "规则草稿改动（暂代 cr_item）"})

    rule_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("rule.id"), nullable=False, index=True)
    cr_code: Mapped[str] = mapped_column(
        String(32), nullable=False, comment="变更单号（软引用，FK to add later）"
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    patch: Mapped[dict] = mapped_column(
        JsonType, nullable=False, comment="RULE_DRAFTS 的改动（Partial<RuleDef>）"
    )
    by: Mapped[str | None] = mapped_column(String(64))
    at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    change: Mapped[str] = mapped_column(Text, nullable=False, default="", comment="版本历史里草稿行的说明")


class RuleHit(CodeMixin, AppendOnlyTableMixin, Base):
    __tablename__ = "rule_hit"
    __table_args__ = ({"comment": "规则命中记录（只追加）"},)

    rule_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("rule.id"), nullable=False, index=True)
    rule_version: Mapped[int | None] = mapped_column(Integer)
    seq: Mapped[int] = mapped_column(
        Integer, nullable=False, comment="同一规则内 hitsOf 顺序（at 倒序，同秒按 mock 顺序）"
    )
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    obj_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    obj_id: Mapped[str] = mapped_column(String(64), nullable=False)
    obj_label: Mapped[str] = mapped_column(String(200), nullable=False)
    store_id: Mapped[str | None] = mapped_column(String(64))
    store_label: Mapped[str | None] = mapped_column(String(200))
    value: Mapped[str] = mapped_column(String(200), nullable=False)
    threshold: Mapped[str] = mapped_column(String(200), nullable=False)
    dedupe_key: Mapped[str | None] = mapped_column(String(200))
    result: Mapped[HitResult] = mapped_column(enum_column(HitResult, 16), nullable=False)
    alert_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("alert.id"), index=True)


# ---------------- 情景推演 ----------------


class Scenario(CodeMixin, MutableTableMixin, Base):
    __tablename__ = "scenario"
    __table_args__ = ({"comment": "情景推演方案"},)

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    owner: Mapped[str] = mapped_column(String(64), nullable=False)
    created_on: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, comment="mock createdAt"
    )
    base_release_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("ontology_release.id"))
    status: Mapped[ScenarioStatus] = mapped_column(
        enum_column(ScenarioStatus, 16), nullable=False, index=True
    )
    scope: Mapped[dict] = mapped_column(JsonType, nullable=False)
    drivers: Mapped[dict] = mapped_column(JsonType, nullable=False)
    fail_reason: Mapped[str | None] = mapped_column(Text)
    staged: Mapped[list[str]] = mapped_column(
        StrArray(64), nullable=False, default=list, comment="已转为暂存动作的动作编号（action_run.code）"
    )


class ScenarioResult(IdMixin, MutableTableMixin, Base):
    __tablename__ = "scenario_result"
    __table_args__ = (UniqueConstraint("scenario_id"), {"comment": "推演结果（每个方案最近一次）"})

    scenario_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("scenario.id"), nullable=False)
    ran_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    scope: Mapped[dict] = mapped_column(JsonType, nullable=False)
    drivers: Mapped[dict] = mapped_column(JsonType, nullable=False)
    weeks: Mapped[list] = mapped_column(JsonType, nullable=False)
    base_sales: Mapped[list] = mapped_column(JsonType, nullable=False)
    sales: Mapped[list] = mapped_column(JsonType, nullable=False)
    base_margin: Mapped[list] = mapped_column(JsonType, nullable=False)
    margin: Mapped[list] = mapped_column(JsonType, nullable=False)
    kpis: Mapped[list] = mapped_column(JsonType, nullable=False)


class ScenarioImpact(IdMixin, MutableTableMixin, Base):
    __tablename__ = "scenario_impact"
    __table_args__ = (
        UniqueConstraint("scenario_id", "sku", "store_id"),
        {"comment": "推演影响明细（SKU × 门店）"},
    )

    scenario_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("scenario.id"), nullable=False, index=True
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False, comment="结果里的顺序（按影响绝对值降序）")
    sku: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    category: Mapped[str] = mapped_column(String(64), nullable=False)
    store_id: Mapped[str] = mapped_column(String(64), nullable=False)
    store_name: Mapped[str] = mapped_column(String(200), nullable=False)
    base_sales: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sales: Mapped[int] = mapped_column(BigInteger, nullable=False)
    stockout_base: Mapped[float] = mapped_column(nullable=False)
    stockout: Mapped[float] = mapped_column(nullable=False)
    suggest: Mapped[str] = mapped_column(String(64), nullable=False)


# ---------------- 生命周期 ----------------


class Lifecycle(CodeMixin, MutableTableMixin, Base):
    """code = 对象类型前端 id（order / store …），接口 objectId。"""

    __tablename__ = "lifecycle"
    __table_args__ = (UniqueConstraint("object_type_id"), {"comment": "对象类型生命周期（当前版本指针）"})

    object_type_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("object_type.id"), nullable=False)
    object_name: Mapped[str] = mapped_column(String(64), nullable=False)
    version: Mapped[int | None] = mapped_column(Integer, comment="当前生效版本；null = 回滚后不在快照里")
    field: Mapped[str] = mapped_column(String(128), nullable=False)
    owner: Mapped[str] = mapped_column(String(64), nullable=False)
    updated_on: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, comment="mock updatedAt"
    )
    rule_code: Mapped[str | None] = mapped_column(
        String(32), comment="状态机校验规则编号（SM-ORD，非 rule 表）"
    )
    desc: Mapped[str] = mapped_column(Text, nullable=False, default="")


class LifecycleVersion(CodeMixin, AppendOnlyTableMixin, OntologyVersionMixin, Base):
    """code = "<objectId>:v<ver>"。snapshot = 那一版生效时的状态机（lifecycleAsOf）。"""

    __tablename__ = "lifecycle_version"
    __table_args__ = (UniqueConstraint("lifecycle_id", "version"), {"comment": "生命周期版本（只追加）"})

    lifecycle_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("lifecycle.id"), nullable=False, index=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    by: Mapped[str] = mapped_column(String(64), nullable=False)
    change: Mapped[str] = mapped_column(Text, nullable=False)
    undo: Mapped[dict | None] = mapped_column(JsonType)
    snapshot: Mapped[dict | None] = mapped_column(JsonType, comment="{states, transitions}")


class LifecycleState(CodeMixin, MutableTableMixin, Base):
    """code = "<objectId>:v<ver>:<key>"。"""

    __tablename__ = "lifecycle_state"
    __table_args__ = (UniqueConstraint("version_id", "key"), {"comment": "生命周期状态"})

    version_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("lifecycle_version.id"), nullable=False, index=True
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    key: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    kind: Mapped[LcStateKind] = mapped_column(enum_column(LcStateKind, 16), nullable=False)
    desc: Mapped[str] = mapped_column(Text, nullable=False, default="")
    sla: Mapped[str | None] = mapped_column(String(200))
    count: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, comment="当前处于该状态的对象数"
    )
    x: Mapped[int] = mapped_column(Integer, nullable=False)
    y: Mapped[int] = mapped_column(Integer, nullable=False)


class LifecycleTransition(CodeMixin, MutableTableMixin, Base):
    """code = "<objectId>:v<ver>:<T1>"；接口 id = tr_code。"""

    __tablename__ = "lifecycle_transition"
    __table_args__ = (UniqueConstraint("version_id", "tr_code"), {"comment": "生命周期迁移"})

    version_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("lifecycle_version.id"), nullable=False, index=True
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    tr_code: Mapped[str] = mapped_column(String(16), nullable=False)
    from_state_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("lifecycle_state.id"), nullable=False)
    to_state_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("lifecycle_state.id"), nullable=False)
    trigger: Mapped[LcTrigger] = mapped_column(enum_column(LcTrigger, 16), nullable=False)
    by: Mapped[str] = mapped_column(String(200), nullable=False)
    event_type_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("event_type.id"))
    action_type_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("action_type.id"))
    guard: Mapped[str] = mapped_column(Text, nullable=False, default="")
    effect: Mapped[str] = mapped_column(Text, nullable=False, default="")
    weekly: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class LifecycleDraft(IdMixin, MutableTableMixin, Base):
    __tablename__ = "lifecycle_draft"
    __table_args__ = (
        UniqueConstraint("lifecycle_id", "cr_code"),
        {"comment": "生命周期草稿改动（LcDraft，暂代 cr_item）"},
    )

    lifecycle_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("lifecycle.id"), nullable=False)
    cr_code: Mapped[str] = mapped_column(
        String(32), nullable=False, comment="变更单号（软引用，FK to add later）"
    )
    edits: Mapped[dict] = mapped_column(
        JsonType, nullable=False, comment="{addStates,addTransitions,editTransitions,removeTransitions}"
    )


class LifecycleChange(CodeMixin, AppendOnlyTableMixin, Base):
    __tablename__ = "lifecycle_change"
    __table_args__ = ({"comment": "对象状态变化记录（数据同步逐条；只追加）"},)

    lifecycle_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("lifecycle.id"), nullable=False, index=True
    )
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    obj_id: Mapped[str] = mapped_column(String(64), nullable=False)
    obj_label: Mapped[str] = mapped_column(String(200), nullable=False)
    from_state: Mapped[str] = mapped_column(String(64), nullable=False)
    to_state: Mapped[str] = mapped_column(String(64), nullable=False)
    transition_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("lifecycle_transition.id"), comment="命中的迁移；null = 非法迁移"
    )
    by: Mapped[str] = mapped_column(String(200), nullable=False)
    alert_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("alert.id"))
