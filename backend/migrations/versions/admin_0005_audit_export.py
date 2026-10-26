"""Persist immutable audit export membership.

Revision ID: admin_0005
Revises: dynamic_0004
"""

import sqlalchemy as sa
from alembic import op

from app.db.ddl import disable_append_only, enable_append_only
from app.db.types import JsonType

revision: str = "admin_0005"
down_revision: str | None = "dynamic_0004"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "audit_export_job",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("code", sa.String(64), nullable=False, comment="业务编号（前端 id）"),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("filters", JsonType, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("created_by", sa.String(64), nullable=True),
        sa.Column("is_seed", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_export_job")),
        sa.UniqueConstraint("code", name=op.f("uq_audit_export_job_code")),
        sa.ForeignKeyConstraint(
            ["user_id"], ["sys_user.id"], name=op.f("fk_audit_export_job_user_id_sys_user")
        ),
    )
    op.create_table(
        "audit_export_row",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("job_id", sa.BigInteger(), nullable=False),
        sa.Column("audit_id", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_export_row")),
        sa.UniqueConstraint("job_id", "audit_id", name=op.f("uq_audit_export_row_job_id_audit_id")),
        sa.ForeignKeyConstraint(
            ["job_id"], ["audit_export_job.id"], name=op.f("fk_audit_export_row_job_id_audit_export_job")
        ),
        sa.ForeignKeyConstraint(
            ["audit_id"], ["audit_log.id"], name=op.f("fk_audit_export_row_audit_id_audit_log")
        ),
    )
    for table in ("audit_export_job", "audit_export_row"):
        for sql in enable_append_only(table):
            op.execute(sql)


def downgrade() -> None:
    for table in ("audit_export_row", "audit_export_job"):
        for sql in disable_append_only(table):
            op.execute(sql)
        op.drop_table(table)
