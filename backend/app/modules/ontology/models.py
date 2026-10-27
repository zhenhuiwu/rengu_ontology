from sqlalchemy import BigInteger, Boolean, CheckConstraint, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, IdMixin, MutableTableMixin


class OntologyWorkspace(IdMixin, MutableTableMixin, Base):
    __tablename__ = "ontology_workspace"

    code: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    domain: Mapped[str] = mapped_column(String(64), nullable=False, default="", server_default="")
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")


class OntologyMember(IdMixin, Base):
    __tablename__ = "ontology_member"
    __table_args__ = (
        UniqueConstraint("ontology_id", "user_id"),
        CheckConstraint("role IN ('owner','editor','viewer')", name="ontology_member_role"),
    )

    ontology_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("ontology_workspace.id"), nullable=False)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("sys_user.id"), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)


class OntologyRoleBinding(IdMixin, Base):
    """Explicitly bind shared object permission definitions to an ontology."""

    __tablename__ = "ontology_role_binding"
    __table_args__ = (UniqueConstraint("ontology_id", "role_id"),)

    ontology_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("ontology_workspace.id"), nullable=False)
    role_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("sys_role.id"), nullable=False)
