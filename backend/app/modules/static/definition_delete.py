"""Current-definition deletion, preserving immutable versions and rejecting live references."""

import re
from dataclasses import dataclass
from types import SimpleNamespace

from fastapi import APIRouter
from pydantic import Field
from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError

from app.common.auth import CurrentUserDep, check_func
from app.common.ontology import require_ontology_id
from app.contracts.dynamic import definition_usages
from app.core.errors import AppError, NotFound, VersionConflict
from app.core.schema import ApiModel
from app.db.base import Base
from app.db.dialects import get_adapter
from app.db.session import SessionDep
from app.modules.action.models import ActionParam, ActionType, ActionTypeRule, OntologyEditRule, SideEffect
from app.modules.admin.repo import insert_audit
from app.modules.dynamic.models import ActionRun, Alert
from app.modules.dynamic.rules_models import Rule, RuleStatus
from app.modules.event.models import EventSubscription, EventType
from app.modules.functions.models import Function
from app.modules.functions.references import function_uses
from app.modules.integration.models import ExtSystem
from app.modules.static.cr_analysis import definition_impacts
from app.modules.static.cr_write import HOLDING
from app.modules.static.models import ChangeRequest, CrItem, CrTarget, Knowledge, LinkType, Metric
from app.modules.static.writeback import DIRTY, _hard_delete
from app.modules.versioning.repo import ReleaseRepo
from app.modules.versioning.service import PUBLISH_LOCK, VersioningService


@dataclass(frozen=True)
class Spec:
    model: type
    kind: str
    section: str
    page: str
    label: str
    mode: str = "hard"
    owned: tuple[str, ...] = ()


SPECS = {
    "link-types": Spec(LinkType, "link", "links", "link-types", "关系类型"),
    "metrics": Spec(Metric, "metric", "metrics", "metrics", "指标口径"),
    "knowledge": Spec(Knowledge, "knowledge", "terms", "glossary", "业务知识", owned=("knowledge_apply",)),
    "action-types": Spec(
        ActionType,
        "action",
        "actionTypes",
        "action-types",
        "动作类型",
        "retire",
        (
            "action_param",
            "submission_criterion",
            "ontology_edit_rule",
            "side_effect",
            "action_type_rule",
            "action_type_version",
            "action_run_daily_stat",
        ),
    ),
    "event-types": Spec(
        EventType,
        "event",
        "eventTypes",
        "event-types",
        "事件类型",
        "retire",
        ("event_payload_field", "event_subscription", "event_type_version"),
    ),
    "functions": Spec(
        Function,
        "function",
        "functions",
        "functions",
        "函数",
        "function",
        ("fn_param", "fn_use", "fn_version"),
    ),
    "rules": Spec(
        Rule, "rule", "rules", "rules", "规则", "rule", ("rule_version", "rule_draft", "rule_schedule_cursor")
    ),
    "ext-systems": Spec(ExtSystem, "external", "", "external-systems", "外部系统"),
}
router = APIRouter(tags=["definition-delete"])
data_router = APIRouter(tags=["definition-delete"])


class DeleteIn(ApiModel):
    lock_version: int = Field(ge=0, strict=True)
    target_version: int = Field(ge=0, strict=True)


def access(user, spec):
    check_func(user, spec.page, "manage")
    check_func(user, "releases", "manage")
    if (
        user.ontology_role not in ("owner", "editor")
        or user.is_service_account
        or user.ontology_ids is not None
    ):
        raise AppError("需要本体工作空间的人工编辑权限", code="FORBIDDEN", status_code=403)


def active(row, spec):
    if spec.mode == "retire":
        return row.retired_release_id is None
    if spec.mode == "function":
        return row.status != "deleted"
    if spec.mode == "rule":
        return row.version is not None
    return True


def mentions(value, aliases):
    if isinstance(value, dict):
        return any(key in aliases or mentions(child, aliases) for key, child in value.items())
    if isinstance(value, list):
        return any(mentions(child, aliases) for child in value)
    if not isinstance(value, str):
        return False
    if value in aliases:
        return True
    return any(
        re.search(r"(?<![A-Za-z0-9_])" + re.escape(alias) + r"(?![A-Za-z0-9_])", value) for alias in aliases
    )


async def fk_references(session, row, spec):
    """Actual FK metadata prevents unknown CASCADE references from being silently removed."""
    root = spec.model.__tablename__
    parents = [root, *(spec.owned if spec.mode == "hard" else ())]
    edges = (
        await session.execute(
            text("""
        SELECT ns.nspname, child.relname, a.attname, parent.relname, pa.attname, cardinality(c.conkey)
        FROM pg_constraint c JOIN pg_class child ON child.oid=c.conrelid
        JOIN pg_namespace ns ON ns.oid=child.relnamespace
        JOIN pg_class parent ON parent.oid=c.confrelid
        JOIN pg_attribute a ON a.attrelid=child.oid AND a.attnum=c.conkey[1]
        JOIN pg_attribute pa ON pa.attrelid=parent.oid AND pa.attnum=c.confkey[1]
        WHERE c.contype='f' AND parent.relnamespace='public'::regnamespace AND parent.relname=ANY(:parents)
    """),
            {"parents": parents},
        )
    ).all()
    result = []
    for schema, child, attr, parent, parent_attr, width in edges:
        if parent == root and child in spec.owned and width == 1 and parent_attr == "id":
            continue
        if schema != "public" or width != 1 or parent_attr != "id" or child not in Base.metadata.tables:
            result.append(f"未确认安全的外键 {schema}.{child}.{attr}")
            continue
        child_table = Base.metadata.tables[child]
        if parent == root:
            identities = [row.id]
        else:
            owned = Base.metadata.tables[parent]
            keys = [
                c
                for c in owned.c
                if any(f.column.table.name == root and f.column.name == "id" for f in c.foreign_keys)
            ]
            if len(keys) != 1:
                result.append(f"未确认安全的子项引用 {parent}")
                continue
            identities = select(owned.c.id).where(keys[0] == row.id)
        statement = select(1).select_from(child_table).where(child_table.c[attr].in_(identities)).limit(1)
        if "ontology_id" in child_table.c:
            statement = statement.where(child_table.c.ontology_id == require_ontology_id(session))
        if await session.scalar(statement):
            result.append(f"{child}.{attr} 有关联记录")
    return result


async def references(session, row, spec):
    aliases = {row.code}
    if api := getattr(row, "api_name", None) or getattr(row, "api", None):
        aliases.add(api)
    result = []
    pending = await session.execute(
        select(ChangeRequest.code, CrTarget.target_kind, CrTarget.target_id)
        .join(CrTarget, CrTarget.cr_id == ChangeRequest.id)
        .where(ChangeRequest.status.in_(HOLDING))
    )
    for cr, kind, code in pending:
        if kind == spec.kind and code == row.code:
            result.append(f"变更单 {cr} 仍包含该定义，请先放弃或发布")
    pending_items = await session.execute(
        select(ChangeRequest.code, CrItem.target_id, CrItem.after)
        .join(CrItem, CrItem.cr_id == ChangeRequest.id)
        .where(ChangeRequest.status.in_(HOLDING))
    )
    for cr, code, data in pending_items:
        if mentions(data, aliases):
            result.append(f"变更单 {cr} 的目标 {code} 引用该定义")
    if spec.kind in {"link", "metric"}:
        impacts = await definition_impacts(
            session,
            [
                SimpleNamespace(
                    target_kind=spec.kind,
                    target_id=row.code,
                    target_name=getattr(row, "display_name", row.code),
                )
            ],
        )
        result.extend(f"{item['kind']}：{item['name']}（{item['note']}）" for item in impacts["defs"])
    if spec.kind == "function":
        result.extend(
            f"{item['kind']}：{item['name']}" for item in (await function_uses(session, [row.code]))[row.code]
        )
    result.extend(
        f"{item['kind']}：{item['name']}（{item.get('note', '定义引用')}）"
        for item in await definition_usages(session, [{"kind": spec.kind, "id": row.code}])
    )
    if spec.kind == "action":
        linked = await session.execute(
            select(Rule.name)
            .join(ActionTypeRule, ActionTypeRule.rule_code == Rule.code)
            .where(ActionTypeRule.action_type_id == row.id, Rule.version.is_not(None))
        )
        result.extend(f"规则 {name} 的动作关联引用该定义" for (name,) in linked)
    if spec.kind == "rule":
        linked = await session.execute(
            select(EventType.name, EventSubscription.name)
            .join(EventSubscription, EventSubscription.event_type_id == EventType.id)
            .where(EventType.retired_release_id.is_(None), EventSubscription.rule_code == row.code)
        )
        result.extend(f"事件 {name} 的订阅 {subscriber} 引用该规则" for name, subscriber in linked)
    # Structured/string identities in current definitions complement real FK checks.
    scans = [
        (
            Rule,
            "规则",
            Rule.name,
            [Rule.expr, Rule.conds, Rule.trigger, Rule.auto_stage],
            Rule.version.is_not(None),
        ),
        (Metric, "指标", Metric.display_name, [Metric.formula, Metric.variants, Metric.def_rows], True),
        (Knowledge, "业务知识", Knowledge.name, [Knowledge.calc, Knowledge.definition], True),
        (
            ExtSystem,
            "外部系统授权",
            ExtSystem.name,
            [ExtSystem.action_authorizations, ExtSystem.http_endpoints],
            True,
        ),
        (
            EventType,
            "事件类型",
            EventType.name,
            [EventType.source_ref, EventType.rule_code],
            EventType.retired_release_id.is_(None),
        ),
    ]
    for model, label, name, fields, condition in scans:
        query = select(model.id, name, *fields).where(condition)
        if model is spec.model:
            query = query.where(model.id != row.id)
        for _identity, display, *values in await session.execute(query):
            if any(mentions(value, aliases) for value in values):
                result.append(f"{label} {display} 引用该定义")
    for model, attr in (
        (ActionParam, ActionParam.default_text),
        (OntologyEditRule, OntologyEditRule.field),
        (SideEffect, SideEffect.target),
    ):
        query = (
            select(ActionType.name, attr)
            .join(model, model.action_type_id == ActionType.id)
            .where(ActionType.retired_release_id.is_(None))
        )
        if spec.model is ActionType:
            query = query.where(ActionType.id != row.id)
        for name, value in await session.execute(query):
            if mentions(value, aliases):
                result.append(f"动作 {name} 的参数/效果引用该定义")
    if spec.kind in {"action", "rule", "external"}:
        field = {
            "action": ActionRun.action_type_code,
            "rule": ActionRun.rule_code,
            "external": ActionRun.sys_code,
        }[spec.kind]
        if await session.scalar(select(ActionRun.code).where(field == row.code).limit(1)):
            result.append("动作执行记录引用该定义（删除记录可见性不会清除审计引用）")
    if spec.kind == "rule" and await session.scalar(
        select(Alert.code).where(Alert.rule_code == row.code).limit(1)
    ):
        result.append("告警记录引用该规则")
    result.extend(await fk_references(session, row, spec))
    return list(dict.fromkeys(result))


async def load(session, spec, code, *, lock=False):
    statement = select(spec.model).where(spec.model.code == code)
    if lock:
        statement = statement.with_for_update(of=spec.model).execution_options(populate_existing=True)
    row = await session.scalar(statement)
    if row is None or not active(row, spec):
        raise NotFound(f"{spec.label}不存在")
    return row


async def remove(session, user, spec, code, body):
    access(user, spec)
    require_ontology_id(session)
    await get_adapter(session).xact_lock(session, PUBLISH_LOCK)
    await get_adapter(session).xact_lock(session, f"static.cr.target.{spec.kind}:{code}")
    latest = await ReleaseRepo(session).latest()
    row = await load(session, spec, code, lock=True)
    if body.lock_version != (latest.id if latest else 0) or body.target_version != row.lock_version:
        raise VersionConflict("工作版或定义已变化，请刷新后重试")
    refs = await references(session, row, spec)
    if refs:
        raise AppError(
            "无法删除：" + "；".join(refs),
            code="DEFINITION_DELETE_BLOCKED",
            status_code=409,
            details={"references": refs},
        )
    try:
        if spec.mode == "hard":
            await _hard_delete(session, spec.model, [row])
        elif spec.mode == "function":
            row.status = "deleted"  # Preserve immutable FnVersion rows and their identity.
        elif spec.mode == "rule":
            row.version, row.status, row.action_type_id = None, RuleStatus.disabled, None
            row.trigger = {**(row.trigger or {}), "enabled": False}
            row.auto_stage = {**(row.auto_stage or {}), "enabled": False}
        await session.flush()
        session.info.setdefault(DIRTY, set()).add(spec.section)
        versioning = VersioningService()
        base = latest.ver if latest else None
        snapshot = await versioning.provider.build_snapshot(session, base, [])
        if spec.section and isinstance(snapshot.get(spec.section), dict):
            snapshot[spec.section] = {
                key: value for key, value in snapshot[spec.section].items() if key != code
            }
        release = await versioning._append(
            session,
            base,
            snapshot,
            [],
            {},
            published_by=user.id,
            note=f"删除{spec.label} {code}",
            now=None,
            latest=latest,
            scope="working",
        )
        if spec.mode == "retire":
            row.retired_release_id = release.id
        await insert_audit(
            session,
            actor=f"{user.name}（{user.id}）",
            action="删除定义",
            target=f"{spec.label} {code}",
            ip="",
            created_by=user.id,
        )
        await session.flush()
    except SQLAlchemyError:
        raise AppError(
            "关联引用已变化，未删除；请刷新并检查引用", code="DEFINITION_DELETE_BLOCKED", status_code=409
        ) from None
    return {"ver": release.ver}


def handlers(spec):
    async def check(id: str, session: SessionDep, user: CurrentUserDep):
        access(user, spec)
        row = await load(session, spec, id)
        latest = await ReleaseRepo(session).latest()
        return {
            "lockVersion": latest.id if latest else 0,
            "targetVersion": row.lock_version,
            "references": await references(session, row, spec),
        }

    async def destroy(id: str, body: DeleteIn, session: SessionDep, user: CurrentUserDep):
        return await remove(session, user, spec, id, body)

    return check, destroy


for resource, specification in SPECS.items():
    destination = data_router if resource == "ext-systems" else router
    check, destroy = handlers(specification)
    destination.add_api_route(
        f"/{resource}/{{id}}:delete-check", check, methods=["GET"], name=f"{resource}-delete-check"
    )
    destination.add_api_route(
        f"/{resource}/{{id}}:delete", destroy, methods=["POST"], name=f"{resource}-delete"
    )
