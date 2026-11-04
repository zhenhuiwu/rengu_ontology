"""Authorize retained outputs before SQL counts and pagination; never reinterpret old free text."""

from sqlalchemy import String, and_, cast, false, or_, select, true

from app.core.errors import Forbidden
from app.modules.governance.policy import (
    decide,
    effective_policies,
    load_definition,
    policy_graph,
    policy_roles,
)
from app.modules.static.lineage import identity

REASON = "治理策略已过滤无法确认来源、版本或明文权限的历史动态输出"


async def context(session, user):
    definition = await load_definition(session)
    if not definition["resources"]:
        return None
    if user is None or user.ontology_id != session.info.get("ontology_id"):
        raise Forbidden("动态输出缺少当前本体授权身份", code="GOVERNANCE_DYNAMIC_DENIED")
    graph = await policy_graph(session)
    roles = await policy_roles(session, user)
    key = (user.id, tuple(sorted(roles)), session.info.get("data_purpose"))
    cached = session.info.get("governance_runtime")
    if cached and cached[0] is definition and cached[1] is graph and cached[2] == key:
        return cached[3]
    states = effective_policies(definition, graph)
    allowed = {}
    for kind, versions in graph["originVersions"].items():
        allowed[kind] = {}
        for code, version in versions.items():
            decision = decide(definition, states, roles, identity(kind, code), "read", key[2])
            if decision["allowed"] and decision["mask"] == "none":
                allowed[kind][code] = version
    # Free-text records cannot be selectively masked without their original full provenance.
    from app.modules.versioning.repo import ReleaseRepo

    release = await ReleaseRepo(session).latest()
    value = {
        "allowed": allowed,
        "watermark": graph["metadataWatermark"],
        "eventTypes": graph["eventTypes"],
        "releaseId": release.id if release else None,
    }
    session.info["governance_runtime"] = definition, graph, key, value
    session.info["governance_dynamic_reason"] = REASON
    return value


def version_match(column, version):
    return or_(cast(column, String) == version, cast(column, String) == f"v{version}")


def origin_pairs(code_column, version_column, versions):
    return (
        or_(
            *[
                and_(code_column == code, version_match(version_column, version))
                for code, version in versions.items()
            ]
        )
        if versions
        else false()
    )


def predicate(model, ctx):
    if ctx is None:
        return true()
    from app.modules.dynamic.models import ActionRun, Alert
    from app.modules.dynamic.rules_models import Rule, RuleHit
    from app.modules.event.models import Event

    watermark = ctx["watermark"]
    if watermark is None:
        return false()
    if model is Alert:
        return and_(
            origin_pairs(model.rule_code, model.rule_version, ctx["allowed"]["rule"]),
            model.created_at > watermark,
        )
    if model is ActionRun:
        rule = or_(
            model.rule_code.is_(None),
            origin_pairs(model.rule_code, model.rule_version, ctx["allowed"]["rule"]),
        )
        alert = or_(
            model.alert_id.is_(None), model.alert_id.in_(select(Alert.id).where(predicate(Alert, ctx)))
        )
        return and_(
            origin_pairs(model.action_type_code, model.type_version, ctx["allowed"]["action"]),
            model.created_at > watermark,
            rule,
            alert,
        )
    if model is RuleHit:
        pairs = [
            and_(
                model.rule_id.in_(select(Rule.id).where(Rule.code == code)),
                version_match(model.rule_version, version),
            )
            for code, version in ctx["allowed"]["rule"].items()
        ]
        return and_(model.created_at > watermark, or_(*pairs) if pairs else false())
    if model is Event:
        snapshot = model.definition_snapshot
        clauses = [
            and_(
                model.event_type_id == ctx["eventTypes"][code],
                snapshot["id"].as_string() == code,
                snapshot["version"].as_string() == version,
            )
            for code, version in ctx["allowed"]["event"].items()
        ]
        action = or_(
            and_(
                model.obj_kind != "action",
                or_(model.producer_object_type.is_(None), model.producer_object_type != "ActionRun"),
            ),
            model.object_id.in_(select(ActionRun.code).where(predicate(ActionRun, ctx))),
        )
        return and_(
            or_(*clauses) if clauses else false(),
            model.created_at > watermark,
            model.release_id == ctx.get("releaseId") if ctx.get("releaseId") else false(),
            action,
        )
    # Retained output with no supported stable origin must not borrow current permissions.
    return false()


async def scope(session, user, statement, model):
    return statement.where(predicate(model, await context(session, user)))


async def read_scope(session, user, statement, model):
    """Public summaries also retain the existing object-row permission intersection."""
    if await context(session, user) is None:
        return statement
    from app.modules.event.models import Event

    if model is Event:
        from app.modules.event.exceptions import visible_events

        return statement.where(await visible_events(session, user))
    from app.modules.dynamic.row_scope import scoped_instances

    return await scoped_instances(session, statement, user, model.obj_kind, model.obj_id)


async def authorize_record(session, user, model, record):
    ctx = await context(session, user)
    if ctx is None:
        return
    exists = await session.scalar(select(model.id).where(model.id == record.id, predicate(model, ctx)))
    if exists is None:
        raise Forbidden(REASON, code="GOVERNANCE_DYNAMIC_DENIED")


async def authorize_records(session, user, model, records):
    if not records or await context(session, user) is None:
        return
    ids = {r.id for r in records}
    allowed = set(
        await session.scalars(await scope(session, user, select(model.id).where(model.id.in_(ids)), model))
    )
    if allowed != ids:
        raise Forbidden(REASON, code="GOVERNANCE_DYNAMIC_DENIED")


async def event_payload(session, user, event, operation="filter"):
    ctx = await context(session, user)
    if ctx is None:
        return
    from app.modules.event.models import Event

    await authorize_record(session, user, Event, event)
    snapshot = event.definition_snapshot or {}
    code = snapshot.get("id")
    await event_fields(session, user, code, snapshot.get("payload", []), operation)


async def event_fields(session, user, code, fields, operation="filter"):
    from app.modules.governance.policy import enforce

    resources = [identity("event", code)]
    resources += [
        identity("event_field", code, row["api"] if isinstance(row, dict) else row.api) for row in fields
    ]
    await enforce(session, user, resources, operation, unmasked=True)


async def delivery_payload(session, event, delivery, operation="filter"):
    if not (await load_definition(session))["resources"]:
        return
    from app.common.auth import resolve_user
    from app.modules.event.delivery import resolved_route

    route = await resolved_route(session, event, delivery)
    actor = route.get("user") or (event.created_by if event.is_test else None)
    if not isinstance(actor, str) or not actor:
        raise Forbidden("事件载荷处理缺少显式用途和执行身份", code="GOVERNANCE_DYNAMIC_DENIED")
    user = await resolve_user(session, actor)
    session.info["data_purpose"] = route.get("data_purpose")
    await event_payload(session, user, event, operation)


def notice(response, session):
    if session.info.get("governance_dynamic_reason"):
        response.headers["X-Governance-Reason"] = "unverified-source-version-or-plaintext-permission-filtered"
