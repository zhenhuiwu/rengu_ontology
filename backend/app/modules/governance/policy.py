"""One policy evaluator for every delivery channel; no cross-request authorization cache."""

from collections import defaultdict

from app.core.errors import AppError, Forbidden
from app.modules.static.lineage import identity

RAW_OPERATIONS = {"filter", "aggregate", "export", "external"}


def effective_policies(definition, graph):
    rules = {r["resource"]: r for r in definition.get("resources", [])}
    if not rules:
        return {}
    nodes = {n["id"] for n in graph["nodes"]} | set(rules)
    parents = defaultdict(set)
    for edge in graph["edges"]:
        parents[edge["target"]].add(edge["source"])
    for target, rule in rules.items():
        parents[target].update(rule.get("dependencies", []))
        nodes.update(rule.get("dependencies", []))
    unknown = {row["node"] for row in graph.get("unresolved", [])}
    ranks = {row["code"]: i for i, row in enumerate(definition.get("levels", []))}
    states = {}
    for _ in range(len(nodes) * (len(ranks) + len(definition.get("classes", [])) + 3) + 1):
        changed = False
        for resource in sorted(nodes):
            rule = rules.get(resource)
            inputs = [states[p] for p in parents[resource] if p in states and states[p]["protected"]]
            unresolved = resource in unknown and not (rule or {}).get("dependencies_complete", False)
            protected = rule is not None or bool(inputs) or unresolved
            classes = set((rule or {}).get("classes", []))
            levels = [(rule or {}).get("level")]
            purposes = set(rule["purposes"]) if rule is not None else None
            mask = (rule or {}).get("mask", "none")
            for state in inputs:
                classes.update(state["classes"])
                levels.append(state["level"])
                purposes = set(state["purposes"]) if purposes is None else purposes & set(state["purposes"])
                unresolved |= state["unresolved"]
                if state["mask"] == "null" or (state["mask"] == "redact" and mask == "none"):
                    mask = state["mask"]
            level = max((v for v in levels if v is not None), key=lambda v: ranks.get(v, 10**6), default=None)
            downgrade = (rule or {}).get("downgrade")
            if downgrade:
                if downgrade not in definition.get("downgrades", []) or level != downgrade.get("from_level"):
                    unresolved = True
                else:
                    classes -= set(downgrade.get("remove_classes", []))
                    level = downgrade.get("to_level")
            state = {
                "protected": protected,
                "classes": sorted(classes),
                "level": level,
                "purposes": sorted(purposes or []),
                "mask": mask,
                "unresolved": bool(unresolved),
                "dependencies": sorted(parents[resource]),
                "declared": rule is not None,
            }
            if states.get(resource) != state:
                states[resource], changed = state, True
        if not changed:
            return states
    raise Forbidden("治理标签依赖未能收敛，已阻止读取", code="GOVERNANCE_DEPENDENCY_UNRESOLVED")


def decide(definition, states, roles, resource, operation, purpose, *, unmasked=False):
    state = states.get(resource)
    if not definition.get("resources"):
        return {"allowed": True, "mask": "none", "legacy": True, "resource": resource}
    if state is None:
        state = {"protected": True, "unresolved": True}
    if not state["protected"]:
        return {"allowed": True, "mask": "none", "legacy": True, "resource": resource}
    denied = {"allowed": False, "mask": "null", "legacy": False, "resource": resource}
    if state["unresolved"]:
        return {**denied, "reason": "依赖未完整确认"}
    if not purpose or purpose not in state["purposes"]:
        return {**denied, "reason": "用途未声明或不在资源允许范围"}
    ranks = {row["code"]: i for i, row in enumerate(definition.get("levels", []))}
    grants = [
        g
        for g in definition.get("grants", [])
        if g["role"] in roles
        and purpose in g["purposes"]
        and operation in g["operations"]
        and set(state["classes"]) <= set(g["classes"])
        and (
            state["level"] is None
            or (g.get("level") in ranks and ranks[g["level"]] >= ranks.get(state["level"], 10**6))
        )
    ]
    if not grants:
        return {**denied, "reason": "角色未获得相应用途、操作或标签等级授权"}
    plain = any(g.get("unmask") is True for g in grants)
    if (unmasked or operation in RAW_OPERATIONS) and not plain:
        return {**denied, "reason": "筛选、聚合、导出或外发需要明文授权"}
    return {
        "allowed": True,
        "mask": "none" if plain else state["mask"],
        "legacy": False,
        "resource": resource,
        "purpose": purpose,
        "markings": state,
    }


async def load_definition(session):
    from sqlalchemy import select, text

    from app.common.ontology import require_ontology_id
    from app.modules.governance.models import GovernancePolicy
    from app.modules.governance.schemas import PolicyDefinition

    oid = require_ontology_id(session)
    cached = session.info.get("governance_definition")
    if cached and cached[0] == oid:
        return cached[1]
    ready = await session.scalar(text("SELECT to_regclass('public.governance_policy') IS NOT NULL"))
    row = (
        await session.scalar(select(GovernancePolicy).where(GovernancePolicy.ontology_id == oid))
        if ready
        else None
    )
    try:
        definition = PolicyDefinition.model_validate(row.definition if row else {}).model_dump(mode="json")
    except ValueError as exc:
        raise Forbidden("治理策略无效，已阻止读取", code="GOVERNANCE_POLICY_INVALID") from exc
    session.info["governance_definition"] = (oid, definition)
    return definition


async def policy_graph(session):
    from types import SimpleNamespace

    from sqlalchemy import select

    from app.common.ontology import require_ontology_id
    from app.modules.action.models import ActionType, TypeStatus
    from app.modules.dynamic.rules_models import Rule, RuleStatus
    from app.modules.event.models import EventType
    from app.modules.functions.models import FnVersion, Function
    from app.modules.static.lineage import current_lineage
    from app.modules.static.models import Metric, MetricStatus, ObjectStatus, ObjectType

    oid = require_ontology_id(session)
    cached = session.info.get("governance_graph")
    if cached and cached[0] == oid:
        return cached[1]
    objects = list(
        await session.scalars(
            select(ObjectType).where(
                ObjectType.ontology_id == oid, ObjectType.status == ObjectStatus.published
            )
        )
    )
    metrics = list(
        await session.scalars(
            select(Metric).where(Metric.ontology_id == oid, Metric.status == MetricStatus.published)
        )
    )
    actions = list(
        await session.scalars(
            select(ActionType).where(
                ActionType.ontology_id == oid,
                ActionType.status.in_([TypeStatus.published, TypeStatus.changing]),
                ActionType.retired_release_id.is_(None),
            )
        )
    )
    action_codes = {a.id: a.code for a in actions}
    rules = list(
        await session.scalars(
            select(Rule).where(
                Rule.ontology_id == oid,
                Rule.status.in_([RuleStatus.published, RuleStatus.changing]),
                Rule.version.is_not(None),
            )
        )
    )
    catalog = {
        "objects": [
            {
                "id": o.code,
                "api": o.api_name,
                "name": o.display_name,
                "properties": [{"api": p.api_name, "name": p.display_name} for p in o.properties],
            }
            for o in objects
        ],
        "metrics": [
            {"id": m.code, "api": m.api_name, "name": m.display_name, "version": m.version} for m in metrics
        ],
        "actions": [
            {
                "id": a.code,
                "name": a.name,
                "version": a.version,
                "params": [{"api": p.api, "name": p.name} for p in a.params],
            }
            for a in actions
        ],
        "rules": [
            {
                "id": r.code,
                "name": r.name,
                "object": r.obj_kind,
                "version": r.version,
                "actionType": action_codes.get(r.action_type_id),
            }
            for r in rules
        ],
    }
    internal = SimpleNamespace(
        ontology_id=oid, action_allowlist=None, func={"connections": "view", "external-systems": "view"}
    )
    graph = await current_lineage(session, internal, _catalog=catalog)
    from app.modules.governance.source_write import add_edges, bindings

    write_bindings = await bindings(session, system_ids={a.sys_id for a in actions if a.sys_id is not None})
    add_edges(graph, actions, write_bindings, oid)
    functions = list(
        await session.scalars(
            select(Function).where(Function.ontology_id == oid, Function.status == "published")
        )
    )
    versions = {}
    version_refs = set()
    codes = {f.id: f.code for f in functions}
    for version in await session.scalars(
        select(FnVersion).where(
            FnVersion.ontology_id == oid, FnVersion.function_id.in_([f.id for f in functions])
        )
    ):
        versions.setdefault(version.function_id, []).append(version.snapshot)
        if not version.is_seed:
            version_refs.add((codes[version.function_id], version.ver))
    for function in functions:
        key = identity("function", function.code)
        graph["nodes"].append(
            {"id": key, "kind": "function", "code": function.code, "name": function.title or function.name}
        )
        snapshots = versions.get(function.id, [])
        # Fixed older versions remain callable. Every version must prove pure local execution.
        if snapshots and all(
            isinstance(s, dict) and s.get("runtimeMode", "local") == "local" for s in snapshots
        ):
            from app.modules.functions.executor import definition_signature

            try:
                for snapshot in snapshots:
                    definition_signature(snapshot)
                continue
            except (ValueError, TypeError, AppError):
                pass
        graph["unresolved"].append(
            {"node": key, "location": "functions.dependencies", "reason": "远端或未知函数来源须显式确认"}
        )
    from app.modules.governance.function_edges import add_function_edges

    add_function_edges(graph, objects, rules, actions, version_refs)
    events = list(
        await session.scalars(
            select(EventType).where(
                EventType.ontology_id == oid,
                EventType.status.in_([TypeStatus.published, TypeStatus.changing]),
                EventType.retired_release_id.is_(None),
            )
        )
    )
    known_rules = {r.code for r in rules}
    for event in events:
        key = identity("event", event.code)
        graph["nodes"].append({"id": key, "kind": "event", "code": event.code, "name": event.name})
        if event.rule_code in known_rules:
            graph["edges"].append(
                {
                    "id": identity("edge", "event.origin", event.code),
                    "source": identity("rule", event.rule_code),
                    "target": key,
                    "kind": "emits",
                    "evidence": "events.rule_code",
                }
            )
        else:
            graph["unresolved"].append(
                {"node": key, "location": "events.origin", "reason": "事件载荷固有来源须显式声明"}
            )
        for field in event.payload:
            target = identity("event_field", event.code, field.api)
            graph["nodes"].append(
                {
                    "id": target,
                    "kind": "event_field",
                    "code": field.api,
                    "name": f"{event.name} · {field.name}",
                }
            )
            graph["edges"].append(
                {
                    "id": identity("edge", key, target),
                    "source": key,
                    "target": target,
                    "kind": "contains",
                    "evidence": "events.payload",
                }
            )
            graph["edges"].append(
                {
                    "id": identity("edge", target, key),
                    "source": target,
                    "target": key,
                    "kind": "carries",
                    "evidence": "events.opaque_payload_output",
                }
            )
        for rule in rules:
            if (
                isinstance(rule.trigger, dict)
                and rule.trigger.get("kind") == "事件"
                and rule.trigger.get("eventId") == event.code
            ):
                graph["edges"].append(
                    {
                        "id": identity("edge", key, rule.code),
                        "source": key,
                        "target": identity("rule", rule.code),
                        "kind": "triggers",
                        "evidence": "rules.trigger.eventId",
                    }
                )
    graph["referenceResources"] = {
        o.code: [
            identity("object", o.code),
            *[identity("property", o.code, p.api_name) for p in o.properties if p.is_pk or p.is_title],
        ]
        for o in objects
    }
    references = [(identity("rule", r.code), r.obj_kind) for r in rules]
    references += [(identity("action", a.code), a.target_kind) for a in actions]
    references += [(identity("event", e.code), e.obj_kind) for e in events]
    for target, code in references:
        for source in graph["referenceResources"].get(code, []):
            graph["edges"].append(
                {
                    "id": identity("edge", source, target, "reference"),
                    "source": source,
                    "target": target,
                    "kind": "references",
                    "evidence": "runtime.declared_object_identity_title",
                }
            )
    for rule in rules:
        if rule.dedupe:
            graph["unresolved"].append(
                {
                    "node": identity("rule", rule.code),
                    "location": "rules.dedupe",
                    "reason": "保留的维度与去重输出需补全依赖依据",
                }
            )
    metadata = [*objects, *metrics, *actions, *rules, *functions, *events]
    metadata += [grant for _, grant, _, _ in write_bindings if grant is not None]
    metadata += [p for obj in objects for p in obj.properties]
    metadata += [child for action in actions for child in [*action.params, *action.conds, *action.edits]]
    metadata += [field for event in events for field in event.payload]
    graph["metadataWatermark"] = max(
        (r.updated_at for r in metadata if r.updated_at is not None), default=None
    )
    graph["originVersions"] = {
        "rule": {r.code: str(r.version) for r in rules},
        "action": {a.code: str(a.version) for a in actions},
        "event": {e.code: str(e.version) for e in events},
    }
    graph["eventTypes"] = {e.code: e.id for e in events}
    session.info["governance_graph"] = (oid, graph)
    return graph


async def enforce(session, user, resources, operation="read", *, purpose=None, unmasked=False):
    from app.common.ontology import require_ontology_id

    if user.ontology_id != require_ontology_id(session):
        raise Forbidden("治理身份与本体不一致", code="ONTOLOGY_SCOPE_CONFLICT")
    session.info["governance_user"] = user
    definition = await load_definition(session)
    cached = session.info.get("governance_states")
    if cached is not None and cached[0] is definition:
        states = cached[1]
    else:
        states = (
            effective_policies(definition, await policy_graph(session)) if definition["resources"] else {}
        )
        session.info["governance_states"] = (definition, states)
    purpose = purpose if purpose is not None else session.info.get("data_purpose")
    if isinstance(resources, str):
        resources = [resources]
    roles = await policy_roles(session, user) if definition["resources"] else user.roles
    decisions = {
        resource: decide(definition, states, roles, resource, operation, purpose, unmasked=unmasked)
        for resource in dict.fromkeys(resources)
    }
    if any(not d["allowed"] for d in decisions.values()):
        reason = next(d["reason"] for d in decisions.values() if not d["allowed"])
        raise Forbidden(reason, code="GOVERNANCE_DENIED", details={"operation": operation})
    return decisions


async def policy_roles(session, user):
    """A role grant only exists inside this workspace, including service accounts."""
    from sqlalchemy import select

    from app.common.ontology import require_ontology_id
    from app.modules.admin.models import SysRole, SysUser
    from app.modules.ontology.models import OntologyMember, OntologyRoleBinding

    oid = require_ontology_id(session)
    if user.ontology_id != oid:
        raise Forbidden("治理身份与本体不一致", code="ONTOLOGY_SCOPE_CONFLICT")
    key = (oid, user.id, getattr(user, "is_service_account", False), tuple(sorted(user.roles)))
    cached = session.info.get("governance_roles")
    if cached and cached[0] == key:
        return cached[1]
    roles = set(
        await session.scalars(
            select(SysRole.code)
            .join(OntologyRoleBinding, OntologyRoleBinding.role_id == SysRole.id)
            .where(OntologyRoleBinding.ontology_id == oid, SysRole.code.in_(user.roles))
        )
    )
    if not getattr(user, "is_service_account", False):
        membership = await session.scalar(
            select(OntologyMember.role)
            .join(SysUser, SysUser.id == OntologyMember.user_id)
            .where(OntologyMember.ontology_id == oid, SysUser.code == user.id)
        )
        if membership in ("owner", "editor", "viewer"):
            roles.add(f"ontology:{membership}")
    session.info["governance_roles"] = key, roles
    return roles


async def role_catalog(session):
    from sqlalchemy import select

    from app.common.ontology import require_ontology_id
    from app.modules.admin.models import SysRole
    from app.modules.ontology.models import OntologyMember, OntologyRoleBinding

    oid = require_ontology_id(session)
    rows = [
        {"code": code, "name": name}
        for code, name in await session.execute(
            select(SysRole.code, SysRole.name)
            .join(OntologyRoleBinding, OntologyRoleBinding.role_id == SysRole.id)
            .where(OntologyRoleBinding.ontology_id == oid)
        )
    ]
    names = {"owner": "本体所有者", "editor": "本体编辑者", "viewer": "本体查看者"}
    memberships = set(
        await session.scalars(select(OntologyMember.role).where(OntologyMember.ontology_id == oid).distinct())
    )
    return rows + [
        {"code": f"ontology:{role}", "name": name} for role, name in names.items() if role in memberships
    ]


async def mask_properties(session, user, object_code, values, *, operation="read", purpose=None):
    keys = {key: identity("property", object_code, key) for key in values}
    decisions = await enforce(session, user, keys.values(), operation, purpose=purpose)
    return {
        key: ("已脱敏" if isinstance(value, str) else None)
        if decisions[keys[key]]["mask"] == "redact"
        else None
        if decisions[keys[key]]["mask"] == "null"
        else value
        for key, value in values.items()
    }


async def plain_reference(session, user, object_code):
    if not (await load_definition(session))["resources"]:
        return True
    resources = (await policy_graph(session))["referenceResources"].get(object_code)
    if resources is None:
        return False
    try:
        decisions = await enforce(session, user, resources)
        return all(d["mask"] == "none" for d in decisions.values())
    except Forbidden as exc:
        if exc.code == "GOVERNANCE_DENIED":
            return False
        raise
