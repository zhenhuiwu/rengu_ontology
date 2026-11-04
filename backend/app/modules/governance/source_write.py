"""The same explicit source-write columns govern propagation and execution authorization."""

from sqlalchemy import and_, select
from sqlalchemy.orm import load_only, selectinload

from app.common.ontology import require_ontology_id
from app.core.errors import AppError
from app.modules.governance.policy import enforce
from app.modules.static.lineage import identity


def invalid():
    return AppError(
        "源数据库写回字段与当前本体已发布属性不能完整对应",
        code="GOVERNANCE_SOURCE_WRITE_UNRESOLVED",
        status_code=403,
    )


def mapped_properties(oid, system, grant, source, obj, action):
    from app.modules.data.models import DsType
    from app.modules.integration.models import ExtSystemStatus
    from app.modules.integration.source_database import source_signature, writable_columns
    from app.modules.qa.object_source import primary_property
    from app.modules.static.models import ObjectStatus

    if any(row is None or row.ontology_id != oid for row in (system, grant, source, obj)):
        raise invalid()
    if (
        grant.sys_id != system.id
        or grant.enabled is not True
        or system.status == ExtSystemStatus.disabled
        or not system.idempotent
        or system.endpoint != f"database://{grant.datasource_code}"
        or source.code != grant.datasource_code
        or source.type not in (DsType.mysql, DsType.dm8)
        or source_signature(source) != grant.source_signature
        or obj.code != grant.object_code
        or obj.status != ObjectStatus.published
        or obj.datasource_name not in (source.code, source.name)
        or obj.source_table != f"{grant.schema_name}.{grant.table_name}"
        or action.get("sysId") != system.code
        or action.get("targetKind") != obj.code
    ):
        raise invalid()
    entry = grant.actions.get(action["id"]) if isinstance(grant.actions, dict) else None
    fields = entry.get("fields") if isinstance(entry, dict) else None
    parameters = {p["api"] for p in action.get("params", [])}
    primary = primary_property(obj)
    if (
        not isinstance(entry, dict)
        or not action.get("version")
        or entry.get("version") != action["version"]
        or not isinstance(fields, dict)
        or not 1 <= len(fields) <= 32
        or any(not isinstance(k, str) or not isinstance(v, str) for k, v in fields.items())
        or not isinstance(grant.write_columns, list)
        or any(not isinstance(c, str) for c in grant.write_columns)
        or not fields.keys() <= set(grant.write_columns)
        or not fields.keys() <= writable_columns(obj)
        or not set(fields.values()) <= parameters
        or primary is None
        or primary.source_column != grant.key_column
        or primary.is_sensitive
        or primary.visibility == "hidden"
        or primary.transform
    ):
        raise invalid()
    writes = {identity("property", obj.code, p.api_name) for p in obj.properties if p.source_column in fields}
    keys = {
        identity("property", obj.code, p.api_name)
        for p in obj.properties
        if p.source_column == grant.key_column
    }
    if any(not any(p.source_column == column for p in obj.properties) for column in fields) or not keys:
        raise invalid()
    return writes, keys


async def bindings(session, *, system_ids=None, system_code=None):
    from app.modules.data.models import Datasource
    from app.modules.integration.models import ExtSystem, SourceWriteBinding
    from app.modules.static.models import ObjectType

    oid = require_ontology_id(session)
    query = (
        select(ExtSystem, SourceWriteBinding, Datasource, ObjectType)
        .outerjoin(
            SourceWriteBinding,
            and_(SourceWriteBinding.sys_id == ExtSystem.id, SourceWriteBinding.ontology_id == oid),
        )
        .outerjoin(
            Datasource,
            and_(Datasource.code == SourceWriteBinding.datasource_code, Datasource.ontology_id == oid),
        )
        .outerjoin(
            ObjectType, and_(ObjectType.code == SourceWriteBinding.object_code, ObjectType.ontology_id == oid)
        )
        .where(ExtSystem.ontology_id == oid)
        .options(
            load_only(
                ExtSystem.id,
                ExtSystem.ontology_id,
                ExtSystem.code,
                ExtSystem.name,
                ExtSystem.endpoint,
                ExtSystem.status,
                ExtSystem.idempotent,
            ),
            load_only(
                SourceWriteBinding.ontology_id,
                SourceWriteBinding.sys_id,
                SourceWriteBinding.enabled,
                SourceWriteBinding.datasource_code,
                SourceWriteBinding.source_signature,
                SourceWriteBinding.object_code,
                SourceWriteBinding.schema_name,
                SourceWriteBinding.table_name,
                SourceWriteBinding.key_column,
                SourceWriteBinding.actions,
                SourceWriteBinding.write_columns,
                SourceWriteBinding.updated_at,
            ),
            load_only(
                Datasource.ontology_id,
                Datasource.code,
                Datasource.name,
                Datasource.type,
                Datasource.host,
                Datasource.port,
                Datasource.database,
                Datasource.ssl,
            ),
            load_only(
                ObjectType.ontology_id,
                ObjectType.code,
                ObjectType.status,
                ObjectType.datasource_name,
                ObjectType.source_table,
                ObjectType.pk_property,
            ),
            selectinload(ObjectType.properties),
        )
    )
    if system_ids is not None:
        query = query.where(ExtSystem.id.in_(system_ids))
    if system_code is not None:
        query = query.where(ExtSystem.code == system_code)
    return list(await session.execute(query))


def add_edges(graph, actions, rows, oid):
    nodes = {n["id"] for n in graph["nodes"]}
    by_system = {system.id: (system, grant, source, obj) for system, grant, source, obj in rows}
    for action in actions:
        row = by_system.get(action.sys_id)
        if row is None or not row[0].endpoint.startswith("database://"):
            continue
        target, external = identity("action", action.code), identity("external", row[0].code)
        descriptor = {
            "id": action.code,
            "version": action.version,
            "sysId": row[0].code,
            "targetKind": action.target_kind,
            "params": [{"api": p.api} for p in action.params],
        }
        try:
            writes, keys = mapped_properties(oid, *row, descriptor)
            if target not in nodes or external not in nodes or not writes | keys <= nodes:
                raise invalid()
        except AppError:
            graph["unresolved"].append(
                {
                    "node": target,
                    "location": "source_write_binding.actions.fields",
                    "reason": "源数据库固定版本写列未能完整对应当前属性",
                }
            )
            continue
        graph["unresolved"] = [
            r
            for r in graph["unresolved"]
            if not (r["node"] == target and r["location"] == "actions.httpEndpointId")
        ]
        for source in writes | keys:
            graph["edges"].append(
                {
                    "id": identity("edge", source, target, "source-write"),
                    "source": source,
                    "target": target,
                    "kind": "governs_write",
                    "evidence": "source_write_binding.actions.fields",
                }
            )
        graph["edges"].append(
            {
                "id": identity("edge", target, external, "source-write"),
                "source": target,
                "target": external,
                "kind": "dispatches",
                "evidence": "source_write_binding.sys_id",
            }
        )


async def preflight(session, user, run, action):
    if not action.get("sysId"):
        return
    rows = await bindings(session, system_code=action["sysId"])
    if len(rows) != 1:
        raise invalid()
    system, grant, source, obj = rows[0]
    if not system.endpoint.startswith("database://"):
        return
    if run.obj_kind != action.get("targetKind") or run.type_version != action.get("version"):
        raise invalid()
    writes, keys = mapped_properties(require_ontology_id(session), system, grant, source, obj, action)
    await enforce(session, user, writes, "execute", unmasked=True)
    await enforce(session, user, writes | keys, "external", unmasked=True)
