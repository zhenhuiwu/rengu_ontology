"""Explicit private trigger CDC subscriptions; retain every existing mirror row."""
import sqlalchemy as sa
from alembic import op
from app.db.types import JsonType

revision = 'data_0049_external_cdc'
down_revision = 'data_0048_mirror_scan_visibility'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('external_cdc_subscription',
        sa.Column('id', sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column('ontology_id', sa.BigInteger(), sa.ForeignKey('ontology_workspace.id'), nullable=False,
                  server_default=sa.text("NULLIF(current_setting('rengu.ontology_id', true), '')::bigint")),
        sa.Column('object_code', sa.String(64), nullable=False),
        sa.Column('datasource_code', sa.String(64), nullable=False),
        sa.Column('binding_signature', sa.String(64), nullable=False),
        sa.Column('binding_version', sa.Integer(), nullable=False),
        sa.Column('capture', JsonType, nullable=False),
        sa.Column('plan_digest', sa.String(64), nullable=False),
        sa.Column('installed', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('enabled', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('phase', sa.String(24), nullable=False, server_default='not_installed'),
        sa.Column('actor', sa.String(64), nullable=False),
        sa.Column('cursor_value', JsonType), sa.Column('pending_ack', JsonType, nullable=False, server_default='[]'),
        sa.Column('lease_owner', sa.String(64)), sa.Column('lease_until', sa.DateTime(timezone=True)),
        sa.Column('heartbeat_at', sa.DateTime(timezone=True)),
        sa.Column('last_observed_at', sa.DateTime(timezone=True)),
        sa.Column('last_applied_at', sa.DateTime(timezone=True)),
        sa.Column('last_source_event_at', sa.DateTime(timezone=True)),
        sa.Column('oldest_pending_at', sa.DateTime(timezone=True)),
        sa.Column('pending_count', sa.BigInteger()),
        sa.Column('applied_count', sa.BigInteger(), nullable=False, server_default='0'),
        sa.Column('restart_required', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('error', sa.Text()), sa.Column('error_code', sa.String(64)),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint('ontology_id', 'object_code'))
    op.create_index('ix_external_cdc_subscription_ontology_id', 'external_cdc_subscription', ['ontology_id'])
    op.execute('ALTER TABLE external_cdc_subscription ENABLE ROW LEVEL SECURITY')
    op.execute('ALTER TABLE external_cdc_subscription FORCE ROW LEVEL SECURITY')
    op.execute("CREATE POLICY ontology_scope ON external_cdc_subscription USING (ontology_id=NULLIF(current_setting('rengu.ontology_id',true),'')::bigint) WITH CHECK (ontology_id=NULLIF(current_setting('rengu.ontology_id',true),'')::bigint)")
    op.execute('GRANT SELECT, INSERT, UPDATE, DELETE ON external_cdc_subscription TO rengu_ontology_scoped')
    op.execute('GRANT USAGE, SELECT ON SEQUENCE external_cdc_subscription_id_seq TO rengu_ontology_scoped')


def downgrade():
    raise RuntimeError('CDC恢复位点与待确认事件不可自动丢弃')
