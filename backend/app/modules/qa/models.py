"""问数（qa）模块 ORM 模型（qa-api.md §9.2）。新增模型在 app/db/models.py 里 import。

约定（跟骨架一致）：
- 每张表代理主键 id bigint（IdMixin），业务编号放 code（CodeMixin），外键指向 id；
- 枚举列存英文码（成员名），接口输出 mock 里的中文值（LabelEnum.value），对照见 qa-api §1.3；
- 只追加表（qa_eval_fail、qa_object_type_view、qa_query_log）在迁移里挂 enable_append_only 触发器，
  它们的外键都不级联。

跟 §9.2 的差异（报告 [推断] 清单）：
- 静态 `metric`、`change_request` 和动态 `action_run` 还没合入骨架：`metric_id` 改成软引用
  `metric_code varchar(64)` + `metric_version int`，`cr_id` 改成 `cr_code varchar(32)`；
  `action_run.qa_session_id` 外键不建。等那些表合入后由新迁移（qa_000N）换成外键。
- `release_id`（qa_turn、qa_eval_run、qa_object_type_view）用骨架的 OntologyVersionMixin，
  是指向 ontology_release(id) 的真外键（qa_0002，ON DELETE NO ACTION，CONVENTIONS §6；qa_0001 时
  是软引用）。列可空：种子环境的 ontology_release 可能为空（tests/test_seed_fresh_db），种子不填 release_id，
  另存版本号文字：qa_turn.answered_ver、
  qa_eval_run.version_label（mock 里还有「v2026.10.4（候选）」「待定」这种不是版本号的值）。
- 订阅按前端 Subscription：单个 channel、receivers、onlyChanged（文档写 channels[]，前端是单选）。
- 共享成员多存一列 dept（ShareMember.dept 快照）；qa_page 多 views、source_ref 存 mock 的 source 原文。
- embedding vector 列、gin_trgm 索引第一批不建（[第二期]），搜索先用 ILIKE。
"""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

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

# ---------------------------------------------------------------- 枚举（库码 = 成员名，接口值 = value）

# 会话口径：库里 ex / in（in 是 Python 关键字，用函数式写法）
SessionCaliber = LabelEnum("SessionCaliber", [("ex", "不含税"), ("in", "含税")])  # type: ignore[misc]


class TurnKind(LabelEnum):
    answer = "answer"
    denied = "denied"
    empty = "empty"
    system = "system"
    failed = "failed"  # [推断] mock 里没有


class TurnStatus(LabelEnum):
    streaming = "streaming"
    done = "done"
    failed = "failed"


class PermRequestStatus(LabelEnum):
    pending = "待审批"
    approved = "已通过"  # [推断] 前端只有待审批
    rejected = "已驳回"  # [推断]


class PageStatus(LabelEnum):
    ok = "正常"
    refresh_failed = "刷新失败"
    caliber_pending = "口径待更新"


class PageSourceKind(LabelEnum):
    workbench = "问数工作台"
    template = "模板"
    manual = "手动新建"


class ShareMode(LabelEnum):
    private = "仅自己"
    members = "指定成员"
    dept = "所在部门"


class SharePerm(LabelEnum):
    view = "可查看"
    edit = "可编辑"


class CardViz(LabelEnum):
    kpi = "指标卡"
    line = "折线图"
    bar = "柱状图"
    table = "表格"


class SubChannel(LabelEnum):
    email = "邮件"
    wecom = "企业微信"
    inbox = "站内信"


class SubWhen(LabelEnum):
    after_refresh = "每次刷新后"
    weekly_mon_0900 = "每周一 09:00"
    daily_0900 = "每天 09:00"


class CertStatus(LabelEnum):
    pending = "待认证"
    certified = "已认证"
    recert = "待重新认证"
    disabled = "已停用"


class EvalKind(LabelEnum):
    semplan = "semplan"
    sql = "sql"


EvalLast = LabelEnum("EvalLast", [("pass", "通过"), ("fail", "失败")])  # type: ignore[misc]


class RunStatus(LabelEnum):
    queued = "待运行"
    running = "运行中"
    done = "已完成"


class FailKind(LabelEnum):
    metric_not_recalled = "未召回指标"
    wrong_metric = "指标选错"
    dim_mismatch = "维度不匹配"
    time_parse = "时间解析错误"
    missing_filter = "过滤条件缺失"


class FbStatus(LabelEnum):
    open = "待处理"
    to_synonym = "已转同义词"
    to_cert = "已转认证问答"
    await_release = "待发布生效"
    ignored = "已忽略"


class FixKind(LabelEnum):
    synonym = "同义词"
    cert = "认证问答"


EVAL_GROUPS = ("销售", "退款", "履约", "毛利", "复购", "订单")


def _in(col: str, values: tuple[str, ...]) -> str:
    return f"{col} IN ({', '.join(repr(v) for v in values)})"


# ---------------------------------------------------------------- 工作台


class QaSession(CodeMixin, MutableTableMixin, Base):
    """问数会话（W1–W5）。code = S0001。"""

    __tablename__ = "qa_session"
    __table_args__ = (
        Index("ix_qa_session_owner_id_last_at", "owner_id", "last_at"),
        {"comment": "问数会话"},
    )

    owner_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("sys_user.id"), nullable=False, comment="拥有者（sys_user.id）"
    )
    title: Mapped[str] = mapped_column(String(60), nullable=False)
    scope: Mapped[str | None] = mapped_column(String(60), comment="范围（QA_SCOPE_OPTIONS.value，如 华东）")
    caliber: Mapped[SessionCaliber] = mapped_column(  # type: ignore[valid-type]
        enum_column(SessionCaliber, 8),
        nullable=False,
        default=SessionCaliber["ex"],
        server_default="ex",
        comment="会话口径 ex/in（接口 不含税/含税）",
    )
    last_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, comment="最近活动时间（接口 date 只取日期）"
    )

    owner = relationship("SysUser", lazy="joined")
    turns: Mapped[list["QaTurn"]] = relationship(
        back_populates="session",
        order_by="QaTurn.seq",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class QaSessionOption(IdMixin, MutableTableMixin, Base):
    """输入框的范围 / 口径下拉（W17）。"""

    __tablename__ = "qa_session_option"
    __table_args__ = (
        UniqueConstraint("kind", "option_value"),
        CheckConstraint("kind IN ('scope', 'caliber')", name="kind"),
        {"comment": "问数会话选项（范围 / 口径）"},
    )

    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    option_value: Mapped[str] = mapped_column(String(60), nullable=False)
    label: Mapped[str] = mapped_column(String(60), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class QaTurn(CodeMixin, OntologyVersionMixin, MutableTableMixin, Base):
    """一轮问答（W3 快照）。snapshot = mock QaDemoTurn 去掉 id/kind/time 的其余字段。"""

    __tablename__ = "qa_turn"
    __table_args__ = (UniqueConstraint("session_id", "seq"), {"comment": "问数轮次"})

    session_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("qa_session.id", ondelete="CASCADE"), nullable=False
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[TurnKind] = mapped_column(enum_column(TurnKind, 12), nullable=False)
    question: Mapped[str | None] = mapped_column(Text)
    refs: Mapped[list | None] = mapped_column(JsonType)
    sem_plan: Mapped[dict | None] = mapped_column(JsonType, comment="语义计划 SemPlan")
    snapshot: Mapped[dict] = mapped_column(JsonType, nullable=False, default=dict)
    metric_code: Mapped[str | None] = mapped_column(String(64), comment="[推断] 指标软引用（metric 未合入）")
    metric_version: Mapped[int | None] = mapped_column(Integer)
    answered_ver: Mapped[str | None] = mapped_column(
        String(32), comment="[推断] 回答时的版本号文字（种子环境 ontology_release 可能为空）"
    )
    sql_text: Mapped[str | None] = mapped_column(Text, comment="执行的 SQL（不出接口）")
    status: Mapped[TurnStatus] = mapped_column(
        enum_column(TurnStatus, 12), nullable=False, default=TurnStatus.done, server_default="done"
    )
    vote: Mapped[str] = mapped_column(String(4), nullable=False, default="", server_default="")
    caliber_variant: Mapped[str | None] = mapped_column(String(8))
    asked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    elapsed_ms: Mapped[int | None] = mapped_column(Integer)

    session: Mapped[QaSession] = relationship(back_populates="turns")


class QaTurnEvent(IdMixin, MutableTableMixin, Base):
    """SSE 事件序列（W7 续传按 (turn_id, seq)）。服务层只 INSERT；随会话级联删除。"""

    __tablename__ = "qa_turn_event"
    __table_args__ = (UniqueConstraint("turn_id", "seq"), {"comment": "问数轮次事件"})

    turn_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("qa_turn.id", ondelete="CASCADE"), nullable=False
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    event_name: Mapped[str] = mapped_column(String(16), nullable=False)
    payload: Mapped[dict] = mapped_column(JsonType, nullable=False, default=dict)


class QaFavorite(CodeMixin, MutableTableMixin, Base):
    __tablename__ = "qa_favorite"
    __table_args__ = (
        UniqueConstraint("owner_id", "question"),
        Index("ix_qa_favorite_owner_id_uses", "owner_id", "uses"),
        {"comment": "常用问题"},
    )

    owner_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("sys_user.id"), nullable=False)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    uses: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_used: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class QaPermissionRequest(CodeMixin, MutableTableMixin, Base):
    __tablename__ = "qa_permission_request"
    __table_args__ = (
        Index("ix_qa_permission_request_requester_id_status", "requester_id", "status"),
        CheckConstraint("duration_days IN (30, 90, 180)", name="duration_days"),
        {"comment": "数据权限申请（W16）"},
    )

    requester_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("sys_user.id"), nullable=False)
    scope: Mapped[str] = mapped_column(String(60), nullable=False)
    object_label: Mapped[str] = mapped_column(String(60), nullable=False)
    reason: Mapped[str] = mapped_column(String(200), nullable=False)
    duration_days: Mapped[int] = mapped_column(Integer, nullable=False)
    source_turn_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("qa_turn.id", ondelete="SET NULL")
    )
    approver: Mapped[str | None] = mapped_column(String(64), comment="审批人姓名软引用")
    status: Mapped[PermRequestStatus] = mapped_column(
        enum_column(PermRequestStatus, 12), nullable=False, default=PermRequestStatus.pending
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# ---------------------------------------------------------------- 页面与模板


class QaPage(CodeMixin, MutableTableMixin, Base):
    __tablename__ = "qa_page"
    __table_args__ = (
        UniqueConstraint("owner_id", "name"),
        Index("ix_qa_page_owner_id_updated_at", "owner_id", "updated_at"),
        Index("ix_qa_page_next_run", "next_run"),
        {"comment": "我的页面"},
    )

    owner_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("sys_user.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(30), nullable=False)
    scope: Mapped[str | None] = mapped_column(String(60))
    source_kind: Mapped[PageSourceKind] = mapped_column(enum_column(PageSourceKind, 12), nullable=False)
    source_ref: Mapped[str | None] = mapped_column(String(200), comment="来源原文（mock source）")
    status: Mapped[PageStatus] = mapped_column(
        enum_column(PageStatus, 16), nullable=False, default=PageStatus.ok, server_default="ok"
    )
    status_note: Mapped[str | None] = mapped_column(Text)
    refresh: Mapped[dict] = mapped_column(JsonType, nullable=False, comment="RefreshPlan（接口值原样存）")
    next_run: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_refresh: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    data_as_of: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    share_mode: Mapped[ShareMode] = mapped_column(
        enum_column(ShareMode, 8), nullable=False, default=ShareMode.private, server_default="private"
    )
    share_link: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    allow_export: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    link_token: Mapped[str | None] = mapped_column(String(64))
    views: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, comment="[推断] 浏览次数（mock views）"
    )

    owner = relationship("SysUser", lazy="joined")
    cards: Mapped[list["QaPageCard"]] = relationship(
        order_by="QaPageCard.sort_order", cascade="all, delete-orphan", passive_deletes=True
    )
    members: Mapped[list["QaPageShareMember"]] = relationship(
        order_by="QaPageShareMember.id", cascade="all, delete-orphan", passive_deletes=True
    )
    subscription: Mapped["QaPageSubscription | None"] = relationship(
        cascade="all, delete-orphan", passive_deletes=True, uselist=False
    )


class QaPageCard(IdMixin, MutableTableMixin, Base):
    __tablename__ = "qa_page_card"
    __table_args__ = (UniqueConstraint("page_id", "card_key"), {"comment": "页面卡片"})

    page_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("qa_page.id", ondelete="CASCADE"), nullable=False
    )
    card_key: Mapped[str] = mapped_column(String(32), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(60), nullable=False)
    viz: Mapped[CardViz] = mapped_column(enum_column(CardViz, 8), nullable=False)
    sem_plan: Mapped[dict | None] = mapped_column(
        JsonType, comment="冻结的语义计划（mock 卡片没有，种子为空）"
    )
    metric_name: Mapped[str | None] = mapped_column(String(64), comment="指标名快照（mock card.metric）")
    metric_code: Mapped[str | None] = mapped_column(String(64))
    metric_version: Mapped[int | None] = mapped_column(Integer)
    pending_cr: Mapped[str | None] = mapped_column(Text, comment="口径待更新说明（mock pending）")
    source_turn_id: Mapped[int | None] = mapped_column(BigInteger, comment="来源轮次（不加外键）")
    result_cache: Mapped[dict] = mapped_column(
        JsonType, nullable=False, default=dict, comment="value/unit/sub/series/labels/rows"
    )
    perm_fingerprint: Mapped[str | None] = mapped_column(String(64))


class QaPageShareMember(IdMixin, MutableTableMixin, Base):
    __tablename__ = "qa_page_share_member"
    __table_args__ = (UniqueConstraint("page_id", "user_id"), {"comment": "页面共享成员"})

    page_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("qa_page.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("sys_user.id"), nullable=False)
    perm: Mapped[SharePerm] = mapped_column(enum_column(SharePerm, 8), nullable=False)
    dept: Mapped[str | None] = mapped_column(String(64), comment="[推断] 部门快照（ShareMember.dept）")

    user = relationship("SysUser", lazy="joined")


class QaPageSubscription(IdMixin, MutableTableMixin, Base):
    __tablename__ = "qa_page_subscription"
    __table_args__ = (UniqueConstraint("page_id"), {"comment": "页面订阅"})

    page_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("qa_page.id", ondelete="CASCADE"), nullable=False
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    channel: Mapped[SubChannel] = mapped_column(enum_column(SubChannel, 8), nullable=False)
    recipients: Mapped[list[str]] = mapped_column(
        StrArray(64), nullable=False, default=list, comment="人名软引用"
    )
    send_when: Mapped[SubWhen] = mapped_column(enum_column(SubWhen, 16), nullable=False)
    only_changed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class QaTemplate(CodeMixin, MutableTableMixin, Base):
    __tablename__ = "qa_template"
    __table_args__ = (
        UniqueConstraint("name"),
        Index("ix_qa_template_scene", "scene"),
        {"comment": "页面模板"},
    )

    name: Mapped[str] = mapped_column(String(60), nullable=False)
    scene: Mapped[str] = mapped_column(String(32), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    provider: Mapped[str] = mapped_column(String(64), nullable=False, comment="提供方软引用")
    official: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    uses: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    cards: Mapped[list["QaTemplateCard"]] = relationship(
        order_by="QaTemplateCard.sort_order", cascade="all, delete-orphan", passive_deletes=True
    )


class QaTemplateCard(IdMixin, MutableTableMixin, Base):
    __tablename__ = "qa_template_card"
    __table_args__ = (UniqueConstraint("template_id", "card_key"), {"comment": "模板卡片"})

    template_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("qa_template.id", ondelete="CASCADE"), nullable=False
    )
    card_key: Mapped[str] = mapped_column(String(32), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(60), nullable=False)
    viz: Mapped[CardViz] = mapped_column(enum_column(CardViz, 8), nullable=False)
    sem_plan: Mapped[dict | None] = mapped_column(JsonType)
    metric_name: Mapped[str | None] = mapped_column(String(64), comment="指标名快照（mock card.metric）")
    metric_code: Mapped[str | None] = mapped_column(String(64))
    metric_version: Mapped[int | None] = mapped_column(Integer)
    pending_cr: Mapped[str | None] = mapped_column(Text)
    result_cache: Mapped[dict] = mapped_column(JsonType, nullable=False, default=dict)


# ---------------------------------------------------------------- 调优


class QaCert(CodeMixin, MutableTableMixin, Base):
    __tablename__ = "qa_cert"
    __table_args__ = (
        Index("uq_qa_cert_lower_question", text("lower(question)"), unique=True),
        Index("ix_qa_cert_status", "status"),
        {"comment": "认证问答"},
    )

    question: Mapped[str] = mapped_column(String(40), nullable=False)
    sem_plan: Mapped[dict] = mapped_column(JsonType, nullable=False)
    metric_code: Mapped[str | None] = mapped_column(String(64))
    metric_version: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[CertStatus] = mapped_column(enum_column(CertStatus, 12), nullable=False)
    cr_code: Mapped[str | None] = mapped_column(String(32), comment="变更单编号软引用")
    certifier: Mapped[str | None] = mapped_column(String(64), comment="认证人姓名软引用")
    certified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    hits: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    source: Mapped[str] = mapped_column(String(20), nullable=False)
    note: Mapped[str | None] = mapped_column(Text)

    variants: Mapped[list["QaCertVariant"]] = relationship(
        order_by="QaCertVariant.id", cascade="all, delete-orphan", passive_deletes=True, lazy="selectin"
    )


class QaCertVariant(IdMixin, MutableTableMixin, Base):
    __tablename__ = "qa_cert_variant"
    __table_args__ = (
        Index("uq_qa_cert_variant_lower_question", text("lower(question)"), unique=True),
        UniqueConstraint("cert_id", "question"),
        {"comment": "认证问答同问法"},
    )

    cert_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("qa_cert.id", ondelete="CASCADE"), nullable=False
    )
    question: Mapped[str] = mapped_column(String(40), nullable=False)


class QaEvalQuestion(CodeMixin, MutableTableMixin, Base):
    __tablename__ = "qa_eval_question"
    __table_args__ = (
        UniqueConstraint("question"),
        CheckConstraint("kind <> 'sql' OR gold_sql IS NOT NULL", name="sql_has_gold"),
        CheckConstraint(f"group_name IS NULL OR {_in('group_name', EVAL_GROUPS)}", name="group_name"),
        Index("ix_qa_eval_question_kind_group_name", "kind", "group_name"),
        {"comment": "评测题"},
    )

    question: Mapped[str] = mapped_column(Text, nullable=False)
    group_name: Mapped[str | None] = mapped_column(String(8), comment="分组（中文，sql 题为空）")
    kind: Mapped[EvalKind] = mapped_column(
        enum_column(EvalKind, 8), nullable=False, default=EvalKind.semplan, server_default="semplan"
    )
    gold_sql: Mapped[str | None] = mapped_column(Text)
    expect: Mapped[dict | None] = mapped_column(JsonType, comment="期望 SemPlan（semplan 题）")
    meta: Mapped[dict | None] = mapped_column(
        JsonType, comment="[推断] sql 题的其余字段（expected/category/tags…）"
    )
    last_result: Mapped[EvalLast | None] = mapped_column(enum_column(EvalLast, 8))
    cr_code: Mapped[str | None] = mapped_column(String(32))


class QaEvalRun(CodeMixin, OntologyVersionMixin, MutableTableMixin, Base):
    __tablename__ = "qa_eval_run"
    __table_args__ = (Index("ix_qa_eval_run_run_at", "run_at"), {"comment": "评测运行"})

    run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    time_label: Mapped[str | None] = mapped_column(String(16))
    trigger_label: Mapped[str] = mapped_column(String(40), nullable=False)
    version_label: Mapped[str | None] = mapped_column(String(32), comment="[推断] mock version 原文")
    scope: Mapped[str] = mapped_column(String(60), nullable=False)
    total: Mapped[int] = mapped_column(Integer, nullable=False)
    passed: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[RunStatus] = mapped_column(enum_column(RunStatus, 8), nullable=False)
    operator: Mapped[str] = mapped_column(String(64), nullable=False, comment="操作人软引用（含「系统」）")
    cr_code: Mapped[str | None] = mapped_column(String(32))
    crs: Mapped[list[str] | None] = mapped_column(StrArray(32))
    note: Mapped[str | None] = mapped_column(Text)

    fails: Mapped[list["QaEvalFail"]] = relationship(order_by="QaEvalFail.id")


class QaEvalFail(IdMixin, AppendOnlyTableMixin, Base):
    __tablename__ = "qa_eval_fail"
    __table_args__ = (
        UniqueConstraint("run_id", "question_id"),
        Index("ix_qa_eval_fail_run_id_kind", "run_id", "kind"),
        {"comment": "评测失败明细（只追加）"},
    )

    run_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("qa_eval_run.id"), nullable=False)
    question_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("qa_eval_question.id"), nullable=False)
    actual: Mapped[str] = mapped_column(Text, nullable=False, comment="[推断] 实际结果文字（mock 是字符串）")
    kind: Mapped[FailKind] = mapped_column(enum_column(FailKind, 24), nullable=False)

    question: Mapped[QaEvalQuestion] = relationship(lazy="joined")


class QaFeedback(CodeMixin, MutableTableMixin, Base):
    __tablename__ = "qa_feedback"
    __table_args__ = (
        Index("ix_qa_feedback_status_fed_back_at", "status", "fed_back_at"),
        Index("ix_qa_feedback_fix_text", "fix_text"),
        {"comment": "反馈与纠错"},
    )

    turn_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("qa_turn.id", ondelete="SET NULL"))
    fed_back_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    user_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("sys_user.id"))
    user_name: Mapped[str] = mapped_column(String(64), nullable=False)
    dept: Mapped[str | None] = mapped_column(String(64))
    question: Mapped[str] = mapped_column(Text, nullable=False)
    answer: Mapped[str] = mapped_column(Text, nullable=False, default="")
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    fix_kind: Mapped[FixKind] = mapped_column(enum_column(FixKind, 8), nullable=False)
    fix_text: Mapped[str] = mapped_column(String(64), nullable=False)
    fix_target: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[FbStatus] = mapped_column(enum_column(FbStatus, 16), nullable=False)
    cr_code: Mapped[str | None] = mapped_column(String(32))
    cert_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("qa_cert.id"))
    handler: Mapped[str | None] = mapped_column(String(64), comment="处理人软引用")
    resolved_by: Mapped[str | None] = mapped_column(String(32), comment="CR 编号软引用（不是人）")


class QaFreqQuestion(CodeMixin, MutableTableMixin, Base):
    __tablename__ = "qa_freq_question"
    __table_args__ = (UniqueConstraint("question"), {"comment": "高频问题（物化统计）"})

    question: Mapped[str] = mapped_column(Text, nullable=False)
    hits7: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    hits30: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    users: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    sem_plan: Mapped[dict] = mapped_column(JsonType, nullable=False)
    consistency: Mapped[Decimal] = mapped_column(Numeric(4, 3), nullable=False)
    last_asked: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cert_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("qa_cert.id", ondelete="SET NULL"))
    computed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    cert: Mapped[QaCert | None] = relationship(lazy="joined")


class QaSynonymLink(IdMixin, MutableTableMixin, Base):
    __tablename__ = "qa_synonym_link"
    __table_args__ = (
        UniqueConstraint("word", "cr_code", postgresql_nulls_not_distinct=True),
        {"comment": "已走变更单的同义词"},
    )

    word: Mapped[str] = mapped_column(String(64), nullable=False)
    target: Mapped[str] = mapped_column(String(64), nullable=False)
    cr_code: Mapped[str | None] = mapped_column(String(32))


# ---------------------------------------------------------------- 对象展示定义 / 查询日志


class QaObjectTypeView(IdMixin, OntologyVersionMixin, AppendOnlyTableMixin, Base):
    """对象搜索 / 详情的展示定义（按版本，只追加）。跟静态 object_view 区分命名。"""

    __tablename__ = "qa_object_type_view"
    __table_args__ = (
        UniqueConstraint("kind", "release_id", postgresql_nulls_not_distinct=True),
        {"comment": "问数对象类型展示定义（只追加）"},
    )

    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    label: Mapped[str] = mapped_column(String(16), nullable=False)
    unit: Mapped[str | None] = mapped_column(String(8))
    kw_placeholder: Mapped[str | None] = mapped_column(String(64))
    facet_defs: Mapped[list] = mapped_column(JsonType, nullable=False, default=list)
    column_defs: Mapped[list] = mapped_column(JsonType, nullable=False, default=list)
    field_groups: Mapped[list] = mapped_column(JsonType, nullable=False, default=list)
    kpis: Mapped[list] = mapped_column(JsonType, nullable=False, default=list)
    quick_questions: Mapped[list] = mapped_column(JsonType, nullable=False, default=list)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class QaQueryLog(IdMixin, AppendOnlyTableMixin, Base):
    """提问日志（高频统计 / 一致率）。turn_id 不加外键。预览和评测不写。"""

    __tablename__ = "qa_query_log"
    __table_args__ = (
        Index("ix_qa_query_log_asked_at", "asked_at"),
        Index("ix_qa_query_log_norm_question_asked_at", "norm_question", "asked_at"),
        {"comment": "问数查询日志（只追加）"},
    )

    turn_id: Mapped[int | None] = mapped_column(BigInteger)
    user_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("sys_user.id"))
    question: Mapped[str] = mapped_column(Text, nullable=False)
    norm_question: Mapped[str] = mapped_column(Text, nullable=False)
    sem_plan: Mapped[dict | None] = mapped_column(JsonType)
    plan_hash: Mapped[str | None] = mapped_column(String(64))
    metric_code: Mapped[str | None] = mapped_column(String(64))
    metric_version: Mapped[int | None] = mapped_column(Integer)
    cert_id: Mapped[int | None] = mapped_column(BigInteger)
    asked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    elapsed_ms: Mapped[int | None] = mapped_column(Integer)
    row_count: Mapped[int | None] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(12), nullable=False)


APPEND_ONLY_TABLES = ("qa_eval_fail", "qa_object_type_view", "qa_query_log")
