from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.enums import LabelEnum
from app.db.base import AppendOnlyTableMixin, Base, IdMixin
from app.db.types import JsonType, StrArray, enum_column
from app.modules.admin.models import SysUser


class ReleaseKind(LabelEnum):
    """枚举映射示范：库里存英文 code（publish / rollback），接口输出中文（发布 / 回滚）。

    这是**版本的产生方式**，不是状态：整版回滚 = 新版本 + 回滚变更单，
    旧版本不改、没有「已回滚」状态（PDE 定）。
    [推断] 前端 Release 类型里没有这个字段（多出的字段不影响页面）。
    """

    publish = "发布"
    rollback = "回滚"


class OntologyRelease(IdMixin, AppendOnlyTableMixin, Base):
    """本体整版发布记录。只追加、不可修改。

    没有 updated_at / lock_version / deleted_* 列；仓储层也不提供 update。

    当前版本 = published_at 最新的一条。回滚 = 新变更单 → 新版本，不改旧行。
    表名不加模块前缀；release 是 SQL 关键字，所以限定为 ontology_release。
    snapshot 的各 section 由负责模块组装（见 contracts.versioning.SECTION_OWNERS），版本层只当不透明 JSON 存。
    """

    __tablename__ = "ontology_release"
    __table_args__ = (
        CheckConstraint(r"ver ~ '^v[0-9]{4}\.[0-9]{2}\.[0-9]+$'", name="ver_format"),
        UniqueConstraint("ver"),
        {"comment": "本体整版发布（只追加）"},
    )

    ver: Mapped[str] = mapped_column(String(32), nullable=False, comment="版本号 vYYYY.MM.N（业务键，唯一）")
    scope: Mapped[str] = mapped_column(String(16), nullable=False, default="legacy", server_default="legacy")
    parent_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("ontology_release.id"), comment="上一版（ontology_release.id）"
    )
    kind: Mapped[ReleaseKind] = mapped_column(
        enum_column(ReleaseKind, 16), nullable=False, default=ReleaseKind.publish, server_default="publish"
    )
    rollback_of_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("ontology_release.id"),
        comment="回滚生成的版本：被回滚的版本（ontology_release.id；新版本内容 = 它的上一版）",
    )
    snapshot: Mapped[dict] = mapped_column(
        JsonType, nullable=False, comment="整版定义快照（各模块组装，版本层不解析）"
    )
    snapshot_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    change_order_ids: Mapped[list[str]] = mapped_column(StrArray(), nullable=False, default=list)
    change_summary: Mapped[dict] = mapped_column(JsonType, nullable=False, default=dict)
    note: Mapped[str] = mapped_column(Text, nullable=False, default="", comment="发布说明（页面显示）")
    published_by_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("sys_user.id"), nullable=False, comment="发布人（sys_user.id）"
    )
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)

    # 外键指向代理键 id；接口仍返回版本号（parentVer / rollbackOf），由下面两个关系取 ver。
    parent: Mapped["OntologyRelease | None"] = relationship(
        foreign_keys=[parent_id], remote_side="OntologyRelease.id", lazy="joined", join_depth=1
    )
    rollback_target: Mapped["OntologyRelease | None"] = relationship(
        foreign_keys=[rollback_of_id], remote_side="OntologyRelease.id", lazy="joined", join_depth=1
    )

    publisher: Mapped[SysUser] = relationship(foreign_keys=[published_by_id], lazy="joined")

    @property
    def published_by(self) -> str:
        """接口 by = 发布人姓名（前端显示）。"""
        return self.publisher.name

    @property
    def published_by_code(self) -> str:
        """接口 byId = 发布人 sys_user.code（如 U010001）；库里存 published_by_id 外键。"""
        return self.publisher.code

    @property
    def parent_ver(self) -> str | None:
        return self.parent.ver if self.parent is not None else None

    @property
    def rollback_of(self) -> str | None:
        return self.rollback_target.ver if self.rollback_target is not None else None
