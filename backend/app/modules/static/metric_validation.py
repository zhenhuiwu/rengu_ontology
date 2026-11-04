"""Shared publication validation; resolve definitions without executing metric values."""

from sqlalchemy import select

from app.common.auth import check_func
from app.common.ontology import require_ontology_id
from app.common.sql_guard import ensure_governed_tables
from app.modules.static.metric_program import is_program
from app.modules.static.metric_trial import (
    compile_metric,
    execution_bindings,
    parse_formula,
    program_details,
)
from app.modules.static.models import ObjectType


async def validate_metric_definition(session, user, definition, objects=None):
    check_func(user, "metrics", "view")
    if is_program(parse_formula(definition.get("formula"))):
        return {"program": True, **await program_details(session, user, definition)}
    from app.modules.governance.policy import enforce
    from app.modules.static.lineage import identity

    await enforce(session, user, [identity("metric", definition["id"])], "read")
    await ensure_governed_tables(session)
    if objects is None:
        objects = list(await session.scalars(select(ObjectType)))
    _, _, temporal, _, _ = compile_metric(
        definition, objects, require_ontology_id(session),
        await execution_bindings(session, objects, definition), session=session,
    )
    return {"program": False, "hasTimeField": temporal is not None}
