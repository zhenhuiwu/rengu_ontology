"""Deleting a role cascades to its ontology_role_binding rows (in every ontology).

ontology_role_binding.role_id was ON DELETE NO ACTION, so DELETE /roles/{id} hit a FK violation
(500) whenever the role was bound to any ontology. Members and row scopes already cascade; the
binding follows the same rule. FK referential actions bypass the table's FORCE RLS, so bindings in
other ontologies (invisible to the scoped session) are removed too.
"""

from alembic import op

revision = "admin_0026"
down_revision = "admin_0025"
branch_labels = None
depends_on = None
FK = "fk_ontology_role_binding_role_id_sys_role"


def _recreate(ondelete):
    op.drop_constraint(FK, "ontology_role_binding", type_="foreignkey")
    op.create_foreign_key(FK, "ontology_role_binding", "sys_role", ["role_id"], ["id"], ondelete=ondelete)


def upgrade():
    _recreate("CASCADE")


def downgrade():
    _recreate(None)
