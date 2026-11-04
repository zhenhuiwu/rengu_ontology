"""Intersect discovery with the same purpose grants used at execution time."""

from app.modules.governance.policy import (
    decide,
    effective_policies,
    load_definition,
    policy_graph,
    policy_roles,
)
from app.modules.static.lineage import identity


async def filter_catalog(session, user, catalog):
    definition = await load_definition(session)
    if not definition["resources"]:
        return catalog
    graph = await policy_graph(session)
    states = effective_policies(definition, graph)
    roles = await policy_roles(session, user)
    purpose = session.info.get("data_purpose")

    def allowed(kind, code, operation="read", *parts):
        return decide(definition, states, roles, identity(kind, code, *parts), operation, purpose)

    result = {**catalog}
    if "objects" in result:
        objects = []
        for obj in catalog["objects"]:
            if not allowed("object", obj["id"])["allowed"]:
                continue
            properties = []
            for prop in obj.get("properties", []):
                decision = allowed("property", obj["id"], "read", prop["api"])
                if decision["allowed"]:
                    properties.append({**prop, "governanceMask": decision["mask"]})
            objects.append({**obj, "properties": properties})
        result["objects"] = objects
    for section, kind, operation in (
        ("metrics", "metric", "aggregate"),
        ("functions", "function", "invoke"),
        ("actions", "action", "execute"),
        ("rules", "rule", "read"),
        ("events", "event", "read"),
    ):
        if section in result:
            result[section] = [
                row for row in catalog[section] if allowed(kind, row["id"], operation)["allowed"]
            ]
    return result
