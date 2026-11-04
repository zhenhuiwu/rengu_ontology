from sqlalchemy import Integer, UniqueConstraint, event
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.db.base import Base, IdMixin, MutableTableMixin
from app.db.types import JsonType


class GovernancePolicy(IdMixin, MutableTableMixin, Base):
    __tablename__ = "governance_policy"
    __table_args__ = (UniqueConstraint("ontology_id"),)

    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    definition: Mapped[dict] = mapped_column(JsonType, nullable=False, default=dict)


@event.listens_for(Session, "before_flush")
def capture_purpose(session, *_):
    from app.modules.dynamic.models import ActionRun

    for row in session.new:
        if isinstance(row, ActionRun) and row.data_purpose is None:
            row.data_purpose = session.info.get("data_purpose")


@event.listens_for(Session, "after_flush")
def invalidate_policy_reads(session, *_):
    tables = {
        "governance_policy",
        "object_type",
        "property",
        "link_type",
        "metric",
        "action_type",
        "action_param",
        "submission_criterion",
        "ontology_edit_rule",
        "fn_version",
        "rule",
        "function",
        "ext_system",
        "source_write_binding",
        "external_read_binding",
        "datasource",
        "event_type",
        "event_payload_field",
        "event_subscription",
        "side_effect",
        "ontology_role_binding",
        "ontology_member",
        "sys_role",
    }
    if any(
        getattr(row, "__tablename__", "") in tables
        for row in (*session.new, *session.dirty, *session.deleted)
    ):
        for key in (
            "governance_definition",
            "governance_states",
            "governance_graph",
            "governance_columns",
            "governance_roles",
            "governance_runtime",
        ):
            session.info.pop(key, None)
