"""P1 draft: real role scopes affect every facet; unbinding yields explicit DENY_ALL."""

import os

from sqlalchemy import delete, select

from app.common.auth import resolve_user, row_filter
from app.common.row_filter import DENY_ALL
from app.modules.ontology.models import OntologyRoleBinding
from app.modules.static.models import ObjectType, Property
from tests.test_object_row_access import VIEWER, facts, grant  # noqa: F401


async def test_real_role_scope_facets_and_unbound_deny_all(client, facts):  # noqa: F811
    session, user = facts
    database = session.get_bind().engine.url.database
    assert database == os.environ["RENGU_TEST_DB"] and database.startswith(("rengu_test_", "rengu_fix_"))
    role = await grant(facts, "P1-FACETS-EAST", [("门店", "大区", ["华东"])], default_deny=True)
    store_type = await session.scalar(select(ObjectType).where(ObjectType.code == "store"))
    for api, label, column in (("region_code", "大区", "big_region"), ("city", "城市", "city")):
        store_type.properties.append(
            Property(
                api_name=api,
                display_name=label,
                data_type="enum",
                prop_group="范围",
                source_column=column,
                backing_column=column,
                filterable=True,
            )
        )
    await session.flush()
    url = "/api/ontology/v1/object-types/store/facets"
    response = await client.get(url, headers=VIEWER)
    assert response.status_code == 200, response.text
    facets = {row["key"]: row for row in response.json()}
    assert set(facets) == {"region_code", "city"}
    assert facets["region_code"]["values"] == [{"value": "华东", "count": 1}]
    assert facets["city"]["values"] == [{"value": "上海", "count": 1}]
    assert all(
        row["hasData"] and sum(value["count"] for value in row["values"]) == 1 for row in facets.values()
    )
    # Keep the real global function grant and workspace membership; revoke only this ontology binding.
    await session.execute(
        delete(OntologyRoleBinding).where(
            OntologyRoleBinding.ontology_id == 1, OntologyRoleBinding.role_id == role.id
        )
    )
    actor = await resolve_user(session, user.code)
    assert actor.func["object-types"] == "view" and await row_filter(actor, "store") == DENY_ALL
    denied = await client.get(url, headers=VIEWER)
    assert denied.status_code == 200, denied.text
    empty = {row["key"]: row for row in denied.json()}
    assert set(empty) == set(facets)
    assert all(row["hasData"] and row["values"] == [] for row in empty.values())
