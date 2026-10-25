from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core import clock
from app.core.enums import LabelEnum
from app.db.base import Base, SeedMarkMixin
from app.db.types import JsonType, StrArray, enum_column


class ReleaseKind(LabelEnum):
    """枚举映射示范：库里存英文 code（publish / rollback），接口输出中文（发布 / 回滚）。

    [推断] 前端 Release 类型里没有这个字段（多出的字段不影响页面）；Releases 页的“回滚”记录用到这个区分。
    """

    publish = "发布"
    rollback = "回滚"


class OntRelease(SeedMarkMixin, Base):
    """本体整版发布记录。只追加、不可修改（没有 updated_* / deleted_* 列；仓储层也不提供 update）。

    当前版本 = published_at 最新的一条。回滚 = 新变更单 → 新版本，不改旧行。
    """

    __tablename__ = "ont_release"
    __table_args__ = (
        CheckConstraint(r"ver ~ '^v[0-9]{4}\.[0-9]{2}\.[0-9]+$'", name="ver_format"),
        {"comment": "本体整版发布（只追加）"},
    )

    ver: Mapped[str] = mapped_column(String(32), primary_key=True, comment="版本号 vYYYY.MM.N")
    parent_ver: Mapped[str | None] = mapped_column(String(32), comment="上一版")
    kind: Mapped[ReleaseKind] = mapped_column(
        enum_column(ReleaseKind, 16), nullable=False, default=ReleaseKind.publish, server_default="publish"
    )
    rollback_of: Mapped[str | None] = mapped_column(
        String(32), comment="回滚生成的版本：被回滚掉的版本号（内容恢复为它的上一版）"
    )
    snapshot: Mapped[dict] = mapped_column(JsonType, nullable=False, comment="整版定义快照")
    snapshot_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    change_order_ids: Mapped[list[str]] = mapped_column(StrArray(), nullable=False, default=list)
    change_summary: Mapped[dict] = mapped_column(JsonType, nullable=False, default=dict)
    note: Mapped[str] = mapped_column(Text, nullable=False, default="", comment="发布说明（页面显示）")
    published_by: Mapped[str] = mapped_column(String(64), nullable=False)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=clock.now, server_default="now()"
    )
