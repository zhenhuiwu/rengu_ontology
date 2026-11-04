"""Mark internal synchronization mirrors separately from user data connections."""
import sqlalchemy as sa
from alembic import op

revision = "data_0051_datasource_internal"
down_revision = "dynamic_0050_record_deletion"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "datasource",
        sa.Column(
            "internal",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
            comment="内部同步镜像，不出现在用户数据连接列表",
        ),
    )
    op.execute(
        "UPDATE datasource SET internal = true "
        "WHERE ontology_id = 6 AND code = 'DS-002' AND name = '联动同步库'"
    )


def downgrade():
    op.drop_column("datasource", "internal")
