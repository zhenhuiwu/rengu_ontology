"""Restore scoped nullable business-key uniqueness without changing existing rows."""

from alembic import op
import sqlalchemy as sa

revision = "qa_0007"
down_revision = "dynamic_0030"
branch_labels = None
depends_on = None

KEYS = (
    ("qa_object_type_view", ("ontology_id", "kind", "release_id")),
    ("qa_synonym_link", ("ontology_id", "word", "cr_code")),
)


def upgrade():
    connection = op.get_bind()
    # Block concurrent writers before checking both keys; failure leaves every row and constraint intact.
    connection.execute(sa.text("LOCK TABLE qa_object_type_view, qa_synonym_link IN SHARE ROW EXCLUSIVE MODE"))
    connection.execute(sa.text("SET LOCAL row_security = off"))
    for table, columns in KEYS:
        fields = ", ".join(columns)
        duplicate = connection.execute(
            sa.text(f"SELECT 1 FROM {table} GROUP BY {fields} HAVING count(*) > 1 LIMIT 1")
        ).first()
        if duplicate is not None:
            raise RuntimeError(
                f"{table} 存在重复业务键；迁移已阻断，未删除或修改任何记录"
            )
    for table, columns in KEYS:
        name = f"uq_{table}_{'_'.join(columns)}"
        op.drop_constraint(name, table, type_="unique")
        op.create_unique_constraint(name, table, columns, postgresql_nulls_not_distinct=True)


def downgrade():
    raise RuntimeError("qa_0007 只允许向前修复，禁止恢复会破坏种子幂等的 NULL 唯一性语义")
