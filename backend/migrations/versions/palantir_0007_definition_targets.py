"""Match action/event object references to the 64-character ontology code."""

import sqlalchemy as sa
from alembic import op

revision = "palantir_0007"
down_revision = "qa_0006"
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column("action_type", "target_kind", existing_type=sa.String(32), type_=sa.String(64))
    op.alter_column("event_type", "obj_kind", existing_type=sa.String(32), type_=sa.String(64))


def downgrade():
    for table, column in (("action_type", "target_kind"), ("event_type", "obj_kind")):
        if op.get_bind().scalar(sa.text(f"SELECT count(*) FROM {table} WHERE length({column}) > 32")):
            raise RuntimeError("Cannot shorten object references without losing existing definitions")
    op.alter_column("event_type", "obj_kind", existing_type=sa.String(64), type_=sa.String(32))
    op.alter_column("action_type", "target_kind", existing_type=sa.String(64), type_=sa.String(32))
