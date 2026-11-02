"""Retain mirror history while publishing source deletions after complete scans."""
import sqlalchemy as sa
from alembic import op

revision = 'data_0048_mirror_scan_visibility'
down_revision = 'admin_0047_login_sessions'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('external_mirror_row', sa.Column('last_seen_scan', sa.BigInteger(), nullable=True))
    # Preserve visibility of every existing row; future inserts are pending until scanned/published.
    op.add_column('external_mirror_row', sa.Column('is_current', sa.Boolean(), nullable=False, server_default=sa.true()))
    op.alter_column('external_mirror_row', 'is_current', server_default=sa.false())
    op.create_index('ix_external_mirror_row_scan', 'external_mirror_row',
                    ['ontology_id', 'object_code', 'binding_signature', 'last_seen_scan'])


def downgrade():
    raise RuntimeError('镜像当前可见性不可自动撤销，以免重新暴露源已删除行')
