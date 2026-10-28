"""Small definition-only catalog for explicit execution mappings."""

from sqlalchemy import select

from app.core.errors import AppError
from app.modules.action.models import TypeStatus
from app.modules.event.models import EventType
from app.modules.functions.models import FnVersion, Function
from app.modules.integration.action_edits import WRITABLE, object_binding, relation_binding
from app.modules.integration.adapters import get_adapter
from app.modules.integration.models import ExtSystem
from app.modules.static.models import LinkType, ObjectStatus, ObjectType


async def action_operation_objects(session, definition):
    """Objects on which this explicit published action may be granted; grants remain separate."""
    found = {}
    identities = [definition.get("targetKind")]
    for edit in definition.get("writeback", {}).get("edits", []):
        if edit.get("op") == "关联":
            try:
                _, src, _, dst, _, _ = await relation_binding(session, edit.get("obj"))
                found[src.code], found[dst.code] = src, dst
            except AppError:
                continue
        else:
            identities.append(edit.get("obj"))
    for effect in definition.get("writeback", {}).get("effects", []):
        if effect.get("kind") == "事件":
            event = await session.scalar(
                select(EventType).where(
                    EventType.code == effect.get("target"),
                    EventType.status == TypeStatus.published,
                    EventType.retired_release_id.is_(None),
                )
            )
            if event is not None:
                identities.append(event.obj_kind)
    for identity in identities:
        if not identity:
            continue
        try:
            obj, _ = await object_binding(session, identity)
            found[obj.code] = obj
        except AppError:
            continue
    return list(found.values())


async def execution_options(session):
    objects, relations, targets = [], [], []
    for identity in await session.scalars(
        select(ObjectType.code)
        .where(ObjectType.status == ObjectStatus.published)
        .order_by(ObjectType.code)
        .limit(200)
    ):
        try:
            obj, governed = await object_binding(session, identity)
        except AppError:
            continue
        writable = governed.write_columns if governed.write_columns is not None else WRITABLE.get(obj.code, set())
        if not writable:
            continue
        objects.append(
            {
                "id": obj.code,
                "name": obj.display_name,
                "api": obj.api_name,
                "properties": [
                    {"api": p.api_name, "name": p.display_name, "type": p.data_type}
                    for p in obj.properties
                    if (p.backing_column or p.source_column) in writable
                    and not p.is_pk
                    and not p.is_sensitive
                    and p.visibility != "hidden"
                    and (p.backing_column or p.source_column) in {getattr(c, "name", None) for c in governed.columns.values()}
                ],
            }
        )
    for identity in await session.scalars(select(LinkType.code).order_by(LinkType.code).limit(200)):
        try:
            link, src, _, dst, _, _ = await relation_binding(session, identity)
            relations.append({"id": link.code, "name": link.display_name, "from": src.code, "to": dst.code})
        except AppError:
            continue
    versions = (
        await session.execute(
            select(Function, FnVersion)
            .join(FnVersion, FnVersion.function_id == Function.id)
            .where(Function.status == "published")
            .order_by(Function.code, FnVersion.id.desc())
            .limit(200)
        )
    ).all()
    functions = [
        {
            "id": fn.code,
            "name": fn.title,
            "version": version.ver,
            "inputs": version.snapshot.get("inputs", []),
        }
        for fn, version in versions
    ]
    events = list(
        await session.scalars(
            select(EventType)
            .where(EventType.status == TypeStatus.published, EventType.retired_release_id.is_(None))
            .order_by(EventType.code)
            .limit(200)
        )
    )
    for system in await session.scalars(select(ExtSystem).order_by(ExtSystem.code).limit(200)):
        try:
            adapter = get_adapter(system, "effect")
        except AppError:
            continue
        targets.extend(
            {
                "id": f"{system.code}:{key}",
                "name": f"{system.name} · {key}",
                "actions": adapter.config.actions,
            }
            for key in adapter.config.effect_paths
        )
    return {
        "objects": objects,
        "relations": relations,
        "functions": functions,
        "events": [
            {
                "id": e.code,
                "name": e.name,
                "version": e.version,
                "objectKind": e.obj_kind,
                "payload": [
                    {"api": p.api, "name": p.name, "type": p.type, "required": p.required} for p in e.payload
                ],
            }
            for e in events
        ],
        "targets": targets,
    }
