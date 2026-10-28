"""Offline safety checks; requires no live database."""
from app.common.sql_guard import inject_row_filters
from app.modules.data.governed_binding import policy_safe


def test_binding_policy_and_projection():
    assert policy_safe("(ontology_id = (NULLIF(current_setting('rengu.ontology_id'::text, true), ''::text))::bigint)")
    assert not policy_safe("ontology_id = 1 OR true")
    sql, _ = inject_row_filters("SELECT * FROM asset", {"asset": ("asset.ontology_id = $1", [7])}, projections={"asset": frozenset({"code", "status"})})
    assert "SELECT code, status FROM public.asset" in sql
    assert "secret" not in sql
