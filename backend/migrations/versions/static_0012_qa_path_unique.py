"""Keep the natural QA path key and remove its redundant display-group constraint."""

from alembic import op

revision = "static_0012"
down_revision = "quality_cr_0011"
branch_labels = None
depends_on = None


def upgrade():
    op.drop_constraint("uq_onto_qa_path_group_key_seq", "onto_qa_path", type_="unique")


def downgrade():
    op.create_unique_constraint("uq_onto_qa_path_group_key_seq", "onto_qa_path", ["group_key", "seq"])
