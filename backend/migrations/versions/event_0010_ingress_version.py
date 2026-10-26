"""Bind new ingress events to their actual ontology release, without backfilling seeds.

Revision ID: event_0010
Revises: event_0009
"""

import sqlalchemy as sa
from alembic import op

revision = "event_0010"
down_revision = "event_0009"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "event",
        sa.Column("release_id", sa.BigInteger(), nullable=True, comment="本体版本（ontology_release.id）"),
    )
    op.create_foreign_key(
        op.f("fk_event_release_id_ontology_release"), "event", "ontology_release", ["release_id"], ["id"]
    )
    op.create_index("ix_event_release_id", "event", ["release_id"])


def downgrade():
    op.drop_index("ix_event_release_id", table_name="event")
    op.drop_constraint(op.f("fk_event_release_id_ontology_release"), "event", type_="foreignkey")
    op.drop_column("event", "release_id")
