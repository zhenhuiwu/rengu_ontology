"""动作类型定义表（action-event-api §9；当前版本展开表 + 只追加的版本表）。

- 主键一律代理键 id bigint，外键指向 id；接口只出业务编码（action_type.code = 前端 id）。
- 枚举列存英文 code，接口输出中文（§3.5）。
- 规则引用（action_type_rule.rule_code）暂为**软引用**（varchar 业务编码 R-…）：动态的 rule 表还没合并，
  合并后改成 rule_id → rule.id 外键。
新增模型要在 app/db/models.py 里 import。
"""

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.enums import LabelEnum
from app.db.base import AppendOnlyTableMixin, Base, CodeMixin, IdMixin, MutableTableMixin, unique_except_seed
from app.db.types import JsonType, enum_column
from app.modules.integration.models import ExtSystem


class TypeStatus(LabelEnum):
    """动作 / 事件类型状态（§3.5）。版本「生效中/已替代」不落库，按 version 推出。"""

    published = "已发布"
    changing = "变更中"
    draft = "草稿"


class CriterionKind(LabelEnum):
    current_user = "当前用户"
    parameter = "参数"
    context_object = "上下文对象"


class EditOp(LabelEnum):
    modify = "修改"
    create = "新建"
    link = "关联"


class EffectKind(LabelEnum):
    notify = "通知"
    event = "事件"
    webhook = "Webhook"


class ActionType(CodeMixin, MutableTableMixin, Base):
    __tablename__ = "action_type"
    __table_args__ = (UniqueConstraint("api"), {"comment": "动作类型（当前版本）"})

    name: Mapped[str] = mapped_column(String(64), nullable=False)
    api: Mapped[str] = mapped_column(String(64), nullable=False, comment="API 名（唯一）")
    grp: Mapped[str] = mapped_column(String(32), nullable=False, comment="分组（接口 group）")
    status: Mapped[TypeStatus] = mapped_column(enum_column(TypeStatus, 16), nullable=False)
    version: Mapped[str] = mapped_column(String(16), nullable=False, comment="当前版本 vN")
    target: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    target_api: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    target_kind: Mapped[str | None] = mapped_column(
        String(32), comment="对象类型前端 id（软引用，静态合并后改外键）"
    )
    owner: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    desc_tpl: Mapped[str] = mapped_column(Text, nullable=False, default="", comment="说明模板（{sys} 占位）")
    runners: Mapped[str] = mapped_column(Text, nullable=False, default="")
    approval: Mapped[str] = mapped_column(Text, nullable=False, default="")
    sys_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("ext_system.id"),
        index=True,
        comment="写回外部系统（ext_system.id），空 = 本体内部",
    )
    endpoint: Mapped[str] = mapped_column(String(255), nullable=False, default="—")
    biz_key_template: Mapped[str] = mapped_column(
        String(255), nullable=False, default="", comment="接口 idemKey"
    )
    retired_release_id: Mapped[int | None] = mapped_column(
        BigInteger, comment="整版回滚下线于哪一版（ontology_release.id，软引用）"
    )

    system: Mapped[ExtSystem | None] = relationship(lazy="joined")
    params: Mapped[list["ActionParam"]] = relationship(lazy="selectin", order_by="ActionParam.seq")
    conds: Mapped[list["SubmissionCriterion"]] = relationship(
        lazy="selectin", order_by="SubmissionCriterion.seq"
    )
    edits: Mapped[list["OntologyEditRule"]] = relationship(lazy="selectin", order_by="OntologyEditRule.seq")
    effects: Mapped[list["SideEffect"]] = relationship(lazy="selectin", order_by="SideEffect.seq")
    rules: Mapped[list["ActionTypeRule"]] = relationship(lazy="selectin", order_by="ActionTypeRule.seq")


def _parent_fk() -> Mapped[int]:
    return mapped_column(BigInteger, ForeignKey("action_type.id", ondelete="CASCADE"), nullable=False)


class ActionTypeRule(IdMixin, MutableTableMixin, Base):
    """使用该动作的规则（ACTION_TYPES[].usedBy）。rule_code 软引用，动态 rule 合并后改 rule_id 外键。"""

    __tablename__ = "action_type_rule"
    __table_args__ = (UniqueConstraint("action_type_id", "rule_code"), {"comment": "动作类型 ↔ 规则"})

    action_type_id: Mapped[int] = _parent_fk()
    rule_code: Mapped[str] = mapped_column(String(32), nullable=False, comment="规则编码 R-…（软引用）")
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    mode_text: Mapped[str | None] = mapped_column(Text, comment="使用方式原文")
    generates: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        comment="规则命中生成本动作（RULE_ACTION；动态合并后改读 rule）",
    )


class ActionParam(IdMixin, MutableTableMixin, Base):
    """参数（paramsOf）。mock 里 claim_alert / reassign_alert 有两个 api=alert 的参数，
    所以 (action_type_id, api) 只约束非 seed 行，seed 按 (action_type_id, seq) upsert。"""

    __tablename__ = "action_param"
    __table_args__ = (
        UniqueConstraint("action_type_id", "seq"),
        unique_except_seed("action_param", "action_type_id", "api"),
        {"comment": "动作参数"},
    )

    action_type_id: Mapped[int] = _parent_fk()
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    api: Mapped[str] = mapped_column(String(64), nullable=False)
    type: Mapped[str] = mapped_column(String(64), nullable=False)
    required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    default_text: Mapped[str] = mapped_column(Text, nullable=False, default="", comment="接口 def")
    rule_text: Mapped[str] = mapped_column(Text, nullable=False, default="", comment="接口 rule")
    rule_expr: Mapped[dict | None] = mapped_column(JsonType)


class SubmissionCriterion(IdMixin, MutableTableMixin, Base):
    __tablename__ = "submission_criterion"
    __table_args__ = (UniqueConstraint("action_type_id", "name"), {"comment": "提交条件"})

    action_type_id: Mapped[int] = _parent_fk()
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[CriterionKind] = mapped_column(enum_column(CriterionKind, 16), nullable=False)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    expr_text: Mapped[str] = mapped_column(Text, nullable=False, default="", comment="接口 expr")
    expr_ast: Mapped[dict | None] = mapped_column(JsonType)
    fail_msg: Mapped[str] = mapped_column(Text, nullable=False, default="", comment="接口 fail")


class OntologyEditRule(IdMixin, MutableTableMixin, Base):
    __tablename__ = "ontology_edit_rule"
    __table_args__ = (UniqueConstraint("action_type_id", "seq"), {"comment": "本体编辑"})

    action_type_id: Mapped[int] = _parent_fk()
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    op: Mapped[EditOp] = mapped_column(enum_column(EditOp, 16), nullable=False)
    obj: Mapped[str] = mapped_column(String(128), nullable=False)
    field: Mapped[str] = mapped_column(Text, nullable=False, default="")


class SideEffect(IdMixin, MutableTableMixin, Base):
    __tablename__ = "side_effect"
    __table_args__ = (UniqueConstraint("action_type_id", "target"), {"comment": "副作用"})

    action_type_id: Mapped[int] = _parent_fk()
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[EffectKind] = mapped_column(enum_column(EffectKind, 16), nullable=False)
    target: Mapped[str] = mapped_column(String(255), nullable=False)
    when_text: Mapped[str] = mapped_column(Text, nullable=False, default="", comment="接口 when")
    note: Mapped[str] = mapped_column(Text, nullable=False, default="")


class ActionTypeVersion(IdMixin, AppendOnlyTableMixin, Base):
    """版本历史（只追加，不存状态）。release_ver 暂为软引用：mock 的 v2026.09.2 / v2026.08.1 / v2026.06.1
    不在 RELEASES 里，补齐后改 release_id → ontology_release.id 外键。"""

    __tablename__ = "action_type_version"
    __table_args__ = (UniqueConstraint("action_type_id", "ver"), {"comment": "动作类型版本（只追加）"})

    action_type_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("action_type.id"), nullable=False)
    ver: Mapped[str] = mapped_column(String(16), nullable=False)
    release_ver: Mapped[str] = mapped_column(String(32), nullable=False, comment="本体发布版本号（软引用）")
    change_note: Mapped[str] = mapped_column(Text, nullable=False, default="", comment="接口 change")
    by_name: Mapped[str] = mapped_column(
        String(64), nullable=False, default="", comment="接口 by（姓名原文）"
    )
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    definition: Mapped[dict | None] = mapped_column(JsonType, comment="该版定义（seed 没有，留空）")
