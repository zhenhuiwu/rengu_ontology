"""Explicit structured conditions; display labels never determine execution semantics."""

import ast
import json
import math
import re
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from croniter import CroniterBadCronError, CroniterBadDateError, croniter
from sqlalchemy import select

from app.common.auth import check_func, resolve_user
from app.core.clock import now
from app.modules.dynamic.rule_runtime import parse_expression, threshold, unavailable
from app.modules.event.models import EventType
from app.modules.static.models import ObjectStatus, ObjectType


def condition_expression(condition):
    if not isinstance(condition, dict) or not isinstance(condition.get("op"), str):
        raise unavailable("条件必须包含明确比较方式")
    op = {
        "=": "==",
        "!=": "!=",
        "≠": "!=",
        "<": "<",
        ">": ">",
        "≤": "<=",
        "≥": ">=",
        "<=": "<=",
        ">=": ">=",
    }.get(condition.get("op"))
    if op is None:
        raise unavailable("条件比较方式不支持")
    if condition.get("metric"):
        if not isinstance(condition["metric"], str) or len(condition["metric"]) > 64:
            raise unavailable("指标编号不合法")
        version = condition.get("metricVer")
        period, transform = condition.get("period", "day"), condition.get("transform", "value")
        if (
            type(version) is not int
            or version < 1
            or period not in ("day", "week")
            or transform not in ("value", "prev", "wow")
        ):
            raise unavailable("指标条件必须选择明确版本、日/周时间桶和值/上期/周环比")
        left = (
            f"metric_value({json.dumps(condition['metric'])}, {version}, "
            f"{json.dumps(period)}, {json.dumps(transform)})"
        )
    else:
        left = condition.get("expression")
        if not isinstance(left, str) or not left.strip():
            raise unavailable("属性条件必须选择明确的对象属性表达式，不能从显示名推断")
        tree = parse_expression(left)
        if not isinstance(tree.body, (ast.Attribute, ast.Call)):
            raise unavailable("条件左值必须是明确属性或受支持函数")
    raw = condition.get("value")
    if not isinstance(raw, str) or not raw.strip() or len(raw) > 500:
        raise unavailable("条件值不能为空或超过500字")
    value, _ = threshold(raw)
    if isinstance(value, float) and not math.isfinite(value):
        raise unavailable("条件数值超过支持范围")
    if isinstance(value, str) and value in ("true", "false"):
        value = value == "true"
    return f"({left} {op} {value!r})"


def compile_conditions(conds, logic):
    if not conds:
        raise unavailable("请至少添加一个条件")
    return (" AND " if logic == "且" else " OR ").join(condition_expression(c) for c in conds)


def validate_trigger(trigger):
    if not isinstance(trigger, dict) or trigger.get("kind") not in ("定时", "事件"):
        raise unavailable("请选择定时或事件触发")
    if type(trigger.get("enabled", False)) is not bool:
        raise unavailable("启用配置必须是明确布尔值")
    if trigger.get("kind") == "定时":
        cron = trigger.get("cron")
        if not isinstance(cron, str) or len(cron.split()) != 5 or not croniter.is_valid(cron):
            raise unavailable("定时表达式必须为有效的五段cron")
        if re.search(r"(?<![A-Za-z])[rh](?=\W|$)", cron, re.IGNORECASE):
            raise unavailable("定时表达式不能包含随机或散列占位符")
        try:
            zone = ZoneInfo(trigger.get("timezone", "Asia/Shanghai"))
        except (ZoneInfoNotFoundError, ValueError, TypeError):
            raise unavailable("请选择有效时区") from None
        try:
            croniter(cron, now().astimezone(zone)).get_next(datetime)
        except (CroniterBadDateError, CroniterBadCronError, ValueError, OverflowError):
            raise unavailable("定时表达式没有可计算的后续执行时间") from None
        catchup = trigger.get("catchupMinutes", 0)
        if type(catchup) is not int or not 0 <= catchup <= 1440:
            raise unavailable("重启补偿范围须为0至1440分钟")
    elif not isinstance(trigger.get("eventId"), str) or not trigger["eventId"]:
        raise unavailable("请选择订阅事件")


async def validate_definition(session, user, definition):
    obj = await session.scalar(
        select(ObjectType).where(
            ObjectType.code == definition["objKind"], ObjectType.status == ObjectStatus.published
        )
    )
    if obj is None:
        raise unavailable("规则对象必须是已发布的真实对象")
    trigger = definition["trigger"]
    validate_trigger(trigger)
    if trigger["kind"] == "事件":
        event = await session.scalar(
            select(EventType).where(
                EventType.code == trigger["eventId"], EventType.retired_release_id.is_(None)
            )
        )
        if event is None or event.obj_kind != obj.code or event.status.value == "草稿":
            raise unavailable("事件必须已发布且绑定所选对象")
    if trigger.get("enabled"):
        actor = await resolve_user(session, trigger.get("runAsUserId") or user.id)
        for page in ("rules", "alerts"):
            check_func(actor, page, "manage")
        if trigger["kind"] == "事件":
            check_func(actor, "event-types", "manage")
        if not definition.get("conds") or not definition.get("expr"):
            raise unavailable("启用规则必须保存明确条件")
        if not definition.get("dedupe") or not re.fullmatch(
            r"不重复|[1-9]\d*\s*(秒|分钟|小时|天)", definition.get("cooldown", "")
        ):
            raise unavailable("启用规则需要明确去重字段和冷却时间")
    tree = parse_expression(definition.get("expr", ""))
    from app.modules.dynamic.rule_functions import bind_rule_functions
    await bind_rule_functions(session, user, tree)
    if any(isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "related" for n in ast.walk(tree)):
        from app.modules.integration.action_edits import object_binding
        from app.modules.dynamic.rule_relations import bind_rule_relations
        bound_obj, gov = await object_binding(session, obj.code)
        await bind_rule_relations(session, user, tree, bound_obj, gov)
