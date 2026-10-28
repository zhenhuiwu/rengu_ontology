"""Finite event rules; bound release definitions and durable hit keys, never inferred effects."""

import ast
import hashlib
import json
import re
from datetime import timedelta

from sqlalchemy import func, select

from app.common.auth import check_func, resolve_user
from app.core.clock import now
from app.db.dialects import get_adapter
from app.modules.action.condition_expr import Unavailable
from app.modules.dynamic.models import ActionRun, Alert, AlertLog, AlertOp, AlertStatus
from app.modules.dynamic.rule_runtime import Expression, parse_expression, unavailable
from app.modules.dynamic.rules_models import HitResult, Rule, RuleHit, RuleOutput, RuleStatus
from app.modules.event.ingress import validate_fields
from app.modules.event.models import Event, EventOutcome
from app.modules.event.schemas import FieldOut
from app.modules.event.simulation import filter_matches
from app.modules.versioning.repo import ReleaseRepo


def transition_pairs(snapshot, *, object_kind, version):
    machines = snapshot.get("lifecycles", {}) if isinstance(snapshot, dict) else {}
    machine = machines.get(object_kind, {}) if isinstance(machines, dict) else {}
    if not isinstance(machine, dict):
        raise unavailable("绑定发布快照没有完整生命周期定义")
    if type(version) is not int or machine.get("version") != version:
        raise unavailable("绑定发布快照缺少规则指定版本的生命周期，不能借用当前状态机")
    states, transitions = machine.get("states"), machine.get("transitions")
    if not isinstance(states, list) or not isinstance(transitions, list) or not states:
        raise unavailable("生命周期快照没有完整状态与迁移定义")
    keys = [s.get("key") for s in states if isinstance(s, dict)]
    if (
        len(keys) != len(states)
        or any(not isinstance(k, str) or not k for k in keys)
        or len(set(keys)) != len(keys)
    ):
        raise unavailable("生命周期快照状态不完整或不唯一")
    pairs = set()
    for transition in transitions:
        if (
            not isinstance(transition, dict)
            or transition.get("from") not in keys
            or transition.get("to") not in keys
        ):
            raise unavailable("生命周期快照包含无效迁移")
        pairs.add((transition["from"], transition["to"]))
    return frozenset(pairs)


class EventExpression(Expression):
    """Shared by the production subscriber and read-only event replay."""

    def __init__(self, tree, fields, snapshot, event_definition, obj=None, gov=None, functions=None, relations=None):
        self.snapshot, self.event_definition, self.transitions = snapshot, event_definition, {}
        super().__init__(tree, obj, gov, fields, functions, relations)

    def visit_Attribute(self, node):
        if isinstance(node.value, ast.Name) and node.value.id not in ("event", "事件") and self.obj:
            column = self.column(node)
            key = (node.value.id, node.attr)
            self.references[key] = f"__object__.{column}"
            self.labels[key] = self.column_labels[column]
            return node
        return super().visit_Attribute(node)

    def visit_Compare(self, node):  # noqa: N802 - ast.NodeTransformer dispatch name
        right = node.comparators[0] if len(node.comparators) == 1 else None
        if not (
            len(node.ops) == 1
            and isinstance(node.ops[0], (ast.In, ast.NotIn))
            and isinstance(right, ast.Attribute)
            and right.attr == "transitions"
            and isinstance(right.value, ast.Call)
            and isinstance(right.value.func, ast.Name)
            and right.value.func.id == "lifecycle"
        ):
            return self.generic_visit(node)
        call = right.value
        if (
            call.keywords
            or len(call.args) != 2
            or not all(isinstance(n, ast.Constant) for n in call.args)
            or call.args[0].value != self.event_definition.get("objectApi")
            or type(call.args[1].value) is not int
            or not isinstance(node.left, ast.Tuple)
            or len(node.left.elts) != 2
        ):
            raise unavailable("生命周期条件缺少明确对象、版本或原/新状态字段")
        columns = []
        for ref in node.left.elts:
            if (
                not isinstance(ref, ast.Attribute)
                or not isinstance(ref.value, ast.Name)
                or ref.value.id not in ("event", "事件")
                or ref.attr not in self.event_fields
            ):
                raise unavailable("生命周期条件只能读取已声明的原始事件状态字段")
            columns.append(ref.attr)
        pairs = transition_pairs(
            self.snapshot, object_kind=self.event_definition.get("objKind"), version=call.args[1].value
        )
        column = f"_transition_{len(self.transitions)}"
        key = ("生命周期", column)
        self.transitions[column] = (columns, pairs, isinstance(node.ops[0], ast.NotIn))
        self.references[key], self.labels[key] = column, f"生命周期v{call.args[1].value}迁移条件"
        return ast.Compare(
            ast.Attribute(ast.Name(key[0], ast.Load()), key[1], ast.Load()), [ast.Eq()], [ast.Constant(True)]
        )

    def run(self, row, at):
        values = dict(row)
        descriptions = []
        for column, (fields, pairs, negate) in self.transitions.items():
            if any(field not in row or not isinstance(row[field], str) for field in fields):
                raise Unavailable("原始事件缺少可读取的原/新状态")
            pair = tuple(row[field] for field in fields)
            values[column] = (pair not in pairs) if negate else (pair in pairs)
            descriptions.append(f"{pair[0]} → {pair[1]}")
        matched, details = super().run(values, at)
        return matched, " · ".join([*descriptions, details]).strip(" ·")


def dedupe_spec(rule, event, fields):
    """Only explicit identity fields and exact metadata names; no inferred dimensions/date."""
    tokens = [part.strip() for part in rule.dedupe.split("+")]
    values = []
    for token in tokens:
        if token == "规则":
            value = rule.code
        elif token in ("事件 ID", "事件ID", "eventId"):
            value = event.code
        elif token == (event.definition_snapshot or {}).get("object"):
            value = event.object_id
        else:
            matches = [f.api for f in fields if token in (f.name, f.api)]
            if len(matches) != 1 or matches[0] not in event.payload:
                raise unavailable("规则去重键尚未唯一映射真实事件身份字段")
            value = event.payload[matches[0]]
        if value is None or isinstance(value, (list, dict)) or value == "":
            raise unavailable("规则去重字段缺少真实标量值")
        values.append([token, value])
    if not values:
        raise unavailable("规则没有明确去重身份")
    if rule.ontology_id != 1:
        values.insert(0, ["ontologyId", rule.ontology_id])
    if rule.cooldown.strip() == "不重复":
        window = None
    else:
        match = re.fullmatch(r"([1-9]\d*)\s*(秒|分钟|小时|天)", rule.cooldown.strip())
        if not match:
            raise unavailable("规则冷却时间没有明确可执行时长")
        try:
            window = timedelta(
                seconds=int(match[1]) * {"秒": 1, "分钟": 60, "小时": 3600, "天": 86400}[match[2]]
            )
        except OverflowError:
            raise unavailable("规则冷却时间超出支持范围") from None
    return "rule-" + hashlib.sha256(json.dumps(values, ensure_ascii=False).encode()).hexdigest(), window


async def stage_action(session, user, rule, event, fields, alert, values):
    from app.modules.action.models import ActionParam, ActionType
    from app.modules.dynamic.catalog import get_catalog
    from app.modules.dynamic.ports import Ports, get_checker, get_emitter, get_scheduler, get_submitter
    from app.modules.dynamic.schemas import SourceIn, StageIn
    from app.modules.dynamic.service import ActionService

    check_func(user, "action-center", "manage")
    config = rule.auto_stage or {}
    action_type = await session.scalar(select(ActionType).where(ActionType.code == config.get("actionTypeId")))
    if action_type is None:
        raise unavailable("规则动作类型不存在")
    params = list(await session.scalars(select(ActionParam).where(ActionParam.action_type_id == action_type.id)))
    by_name = {p.name: p for p in params}
    values_by_source = {"告警.对象": event.object_id, "命中.对象": event.object_id,
                        "告警.ID": alert.code, "规则.命中说明": values}
    for field in fields:
        if field.api in event.payload:
            values_by_source[f"事件.{field.api}"] = event.payload[field.api]
            name_key = f"事件.{field.name}"
            if name_key in values_by_source and name_key != f"事件.{field.api}":
                raise unavailable("事件字段名称不唯一，无法解析动作映射")
            values_by_source[name_key] = event.payload[field.api]
    mapped = {}
    for item in config.get("mapping", []):
        param, source = item.get("param"), item.get("from")
        if not source:
            continue
        if param not in by_name or by_name[param].api in mapped:
            raise unavailable("规则动作参数映射无效或重复")
        if source.startswith("固定值："):
            value = source.removeprefix("固定值：")
        elif source in values_by_source:
            value = values_by_source[source]
        else:
            raise unavailable("规则动作参数来源不可计算")
        mapped[by_name[param].api] = value
    payload = json.dumps(mapped, ensure_ascii=False, allow_nan=False)
    ports = Ports(get_submitter(session, user), get_emitter(session), get_scheduler(session), get_checker(session, user))
    actions = ActionService(session, user, await get_catalog(session, user), ports)
    await actions.validate_launch(action_type.code, event.obj_kind, event.object_id, payload)
    run, created = await actions.stage(StageIn(
        type_id=action_type.code, obj_kind=event.obj_kind, obj_id=event.object_id,
        obj_label=event.obj_label or event.object_id, params=payload,
        source=SourceIn(type="规则", rule_id=rule.code, rule_version=str(rule.version),
                        alert_id=alert.code, confirmer=config.get("confirmer")),
    ))
    if created and config.get("executionMode", "confirm") == "auto":
        run = await actions.apply_policy(run)
    result = {"actionStatus": run.status.value, "actionId": run.code}
    if getattr(actions, "policy_overflow", None):
        result["actionReason"] = actions.policy_overflow
    return result


async def dispatch(session, user, *, rule_code, event_id):
    # Reload real role membership even if an internal caller passed an outdated identity.
    user = await resolve_user(session, user.id)
    for page in ("event-types", "rules", "alerts"):
        check_func(user, page, "manage")
    # shortcut: serializes local rule writes; use shared publication locks if throughput requires it.
    await get_adapter(session).xact_lock(session, "ontology.publish")
    event = await session.scalar(select(Event).where(Event.code == event_id).with_for_update(of=Event))
    if (
        event is None
        or event.is_seed
        or event.is_test
        or event.outcome == EventOutcome.deduped
        or not isinstance(event.payload, dict)
        or not isinstance(event.definition_snapshot, dict)
        or not event.definition_snapshot.get("version")
        or not event.release_id
        or not event.ingested_at
    ):
        raise unavailable("仅处理新入站、非测试、保留原始载荷和定义版本的真实事件")
    release = await ReleaseRepo(session).latest()
    if release is None or release.id != event.release_id:
        raise unavailable("入站后发布版本已变化，需要重新审核，不能用新定义处理旧事件")
    rule = await session.scalar(
        select(Rule).where(Rule.code == rule_code).with_for_update().execution_options(populate_existing=True)
    )
    snapshot = release.snapshot
    bound = snapshot.get("rules", {}).get(rule_code) if isinstance(snapshot, dict) else None
    if (
        rule is None
        or not isinstance(bound, dict)
        or rule.version is None
        or rule.status == RuleStatus.disabled
        or bound.get("version") != rule.version
    ):
        raise unavailable("规则未发布、已停用或不在入站绑定版本中")
    for key, current in {
        "expr": rule.expr,
        "conds": rule.conds,
        "logic": rule.logic,
        "dedupe": rule.dedupe,
        "cooldown": rule.cooldown,
        "trigger": rule.trigger,
        "objKind": rule.obj_kind,
        "output": rule.output.value,
        "level": rule.level.value,
        "autoStage": rule.auto_stage,
    }.items():
        if bound.get(key) != current:
            raise unavailable("当前规则与入站绑定发布定义不一致，不能执行")
    definition = event.definition_snapshot
    bound_event = snapshot.get("eventTypes", {}).get(definition.get("id"))
    if not isinstance(bound_event, dict) or bound_event != definition:
        raise unavailable("原始事件定义与入站绑定发布快照不一致，不能执行")
    trigger = bound.get("trigger") or {}
    if trigger.get("enabled") is False:
        return {
            "ruleId": rule.code,
            "ruleVersion": rule.version,
            "status": "not_matched",
            "reason": "规则触发已停用",
        }
    if (
        trigger.get("kind") != "事件"
        or trigger.get("eventId") != definition.get("id")
        or bound.get("objKind") != event.obj_kind
        or definition.get("objKind") != event.obj_kind
    ):
        raise unavailable("事件类型/对象与规则明确触发定义不一致")
    from app.modules.event.delivery import authorize_object

    await authorize_object(session, user, event)
    fields = [FieldOut.model_validate(field) for field in definition.get("payload", [])]
    validate_fields(fields, event.payload)
    result = {"ruleId": rule.code, "ruleVersion": rule.version, "status": "not_matched"}
    if not filter_matches(trigger.get("filter") or "全部", fields, event.payload):
        return result
    source = f"{event.code}\0{rule.code}"
    if event.ontology_id != 1:
        source = f"{event.ontology_id}\0{source}"
    identity = hashlib.sha256(source.encode()).hexdigest()[:60]
    hit_code = f"RH-{identity}"
    previous = await session.scalar(select(RuleHit).where(RuleHit.code == hit_code))
    action = {"actionStatus": "not_configured"}
    if previous:
        alert = await session.get(Alert, previous.alert_id) if previous.alert_id else None
        if alert and previous.result != HitResult.deduped:
            run = await session.scalar(select(ActionRun).where(ActionRun.alert_id == alert.id, ActionRun.rule_code == rule.code))
            if run:
                action = {"actionStatus": run.status.value, "actionId": run.code}
        return {
            **result,
            **action,
            "evaluatedAt": previous.at.isoformat(),
            "evaluationMode": "prior_result",
            "status": "deduped" if previous.result == HitResult.deduped else "alert_created",
            "hitId": previous.code,
            "alertId": alert.code if alert else None,
        }
    from app.common.ontology import require_ontology_id
    from app.common.sql_guard import governed_tables
    from app.modules.integration.action_edits import existing_row
    from app.modules.static.models import ObjectStatus, ObjectType

    obj = await session.scalar(select(ObjectType).where(
        ObjectType.code == event.obj_kind, ObjectType.status == ObjectStatus.published
    ))
    gov = next((g for g in governed_tables(require_ontology_id(session), session).values()
                if obj and g.object_type == obj.code and g.inherit is None), None)
    if obj is None or gov is None:
        raise unavailable("规则对象尚未映射受控业务表")
    from app.modules.dynamic.rule_functions import bind_rule_functions
    tree = parse_expression(bound.get("expr", ""))
    functions = await bind_rule_functions(session, user, tree)
    from app.modules.dynamic.rule_relations import bind_rule_relations
    relations = await bind_rule_relations(session, user, tree, obj, gov)
    expression = EventExpression(tree, {f.api: f.name for f in fields}, snapshot, definition, obj, gov, functions, relations)
    evaluated_at = now()
    inputs = dict(event.payload)
    if expression.columns:
        row = await existing_row(session, user, obj, gov, event.object_id)
        inputs.update({f"__object__.{column}": row[column] for column in expression.columns})
    matched, values = expression.run(inputs, evaluated_at)
    result.update({"evaluatedAt": evaluated_at.isoformat(), "evaluationMode": "current_object" if expression.columns else "event_payload"})
    if not matched:
        return result
    if rule.output != RuleOutput.alert:
        raise unavailable("通知规则缺少真实通知执行通道，不能冒充告警或已通知")
    key, window = dedupe_spec(rule, event, fields)
    at = now()
    recent = select(RuleHit).where(
        RuleHit.rule_id == rule.id,
        RuleHit.dedupe_key == key,
        RuleHit.result == HitResult.alert,
        RuleHit.is_seed.is_(False),
    )
    if window is not None:
        recent = recent.where(RuleHit.at > at - window)
    duplicate = await session.scalar(recent.order_by(RuleHit.at.desc()).limit(1))
    seq = int(await session.scalar(select(func.max(RuleHit.seq)).where(RuleHit.rule_id == rule.id)) or 0) + 1
    threshold = bound.get("expr", "")[:200]
    hit = RuleHit(
        code=hit_code,
        rule_id=rule.id,
        rule_version=rule.version,
        seq=seq,
        at=at,
        obj_kind=event.obj_kind,
        obj_id=event.object_id,
        obj_label=event.obj_label or event.object_id,
        dims=[],
        value=values[:200],
        threshold=threshold,
        dedupe_key=key,
        result=HitResult.deduped if duplicate else HitResult.alert,
        created_by=user.id,
    )
    if duplicate:
        hit.alert_id = duplicate.alert_id
        alert = await session.get(Alert, duplicate.alert_id) if duplicate.alert_id else None
    else:
        alert = Alert(
            code=f"AL-{identity}",
            rule_code=rule.code,
            rule_version=f"v{rule.version}",
            hit_code=hit_code,
            dedupe_key=key,
            level=rule.level,
            obj_kind=event.obj_kind,
            obj_id=event.object_id,
            obj_label=event.obj_label or event.object_id,
            dims=[],
            value=values[:200],
            threshold=threshold,
            triggered_at=at,
            data_cutoff=evaluated_at if expression.columns else event.occurred_at,
            status=AlertStatus.open,
            created_by=user.id,
        )
        session.add(alert)
        await session.flush()
        hit.alert_id = alert.id
        session.add(
            AlertLog(
                alert_id=alert.id,
                seq=1,
                at=at,
                actor=user.id,
                op=AlertOp.created,
                text=f"事件 {event.code} 命中规则 {rule.code} v{rule.version}",
                created_by=user.id,
            )
        )
    session.add(hit)
    await session.flush()
    if not duplicate and (bound.get("autoStage") or {}).get("enabled"):
        action = await stage_action(session, user, rule, event, fields, alert, values)
    return {
        **result,
        **action,
        "status": "deduped" if duplicate else "alert_created",
        "hitId": hit.code,
        "alertId": alert.code if alert else None,
    }
