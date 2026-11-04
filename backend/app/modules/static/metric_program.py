"""Version-pinned metric composition and calendar windows over governed aggregates."""

import asyncio
import calendar
import math
from collections import deque
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal, DecimalException, InvalidOperation, localcontext
from functools import wraps

from sqlglot import exp

from app.core.errors import AppError

MAX_CALLS, MAX_DEPTH, MAX_HISTORY_DAYS = 32, 8, 731
FUNCTIONS = {
    "METRIC",
    "PREV",
    "WOW",
    "DELTA",
    "PCT_CHANGE",
    "MOVING_AVG",
    "MOVING_SUM",
    "RUNNING_SUM",
    "COALESCE",
    "ABS",
    "ROUND",
    "GEO_DISTANCE",
}


def invalid(message):
    return AppError(message, code="METRIC_PROGRAM_INVALID", status_code=422)


def function_name(node):
    if isinstance(node, exp.Anonymous):
        return node.name.upper()
    if isinstance(node, exp.Coalesce):
        return "COALESCE"
    if isinstance(node, exp.Abs):
        return "ABS"
    if isinstance(node, exp.Round):
        return "ROUND"
    return ""


def arguments(node):
    if isinstance(node, exp.Anonymous):
        return list(node.expressions)
    if isinstance(node, exp.Coalesce):
        return [node.this, *node.expressions]
    if isinstance(node, exp.Abs):
        return [node.this]
    if isinstance(node, exp.Round):
        return [node.this, node.args.get("decimals") or exp.Literal.number(0)]
    return []


def is_program(tree):
    return any(function_name(n) == "METRIC" for n in tree.walk())


def references(tree):
    """Validate the complete expression before evaluating any referenced metric."""
    refs = []
    for node in tree.walk():
        if isinstance(node, (exp.Select, exp.Paren, exp.Add, exp.Sub, exp.Mul, exp.Div, exp.Neg)):
            if isinstance(node, exp.Select) and (
                node is not tree or any(v is not None for k, v in node.args.items() if k != "expressions")
            ):
                raise invalid("复合指标不能包含 SQL 查询或过滤片段")
            continue
        if isinstance(node, exp.Literal):
            if node.is_string and function_name(node.parent) != "METRIC":
                raise invalid("字符串只能用于固定版本指标引用")
            continue
        if isinstance(node, exp.Identifier) and isinstance(node.parent, exp.Anonymous):
            continue
        name = function_name(node)
        if name not in FUNCTIONS:
            raise invalid("复合指标仅允许已声明的指标引用、四则运算、窗口和受控计算函数")
        args = arguments(node)
        if name == "METRIC":
            if len(args) != 2 or not all(isinstance(a, exp.Literal) and a.is_string for a in args):
                raise invalid("METRIC 必须指定指标编号和固定版本，例如 METRIC('metric_api','v1')")
            ref = tuple(a.this for a in args)
            if not all(0 < len(v) <= 128 for v in ref):
                raise invalid("指标编号或版本长度无效")
            refs.append(ref)
        elif name in {"PREV", "DELTA", "PCT_CHANGE", "MOVING_AVG", "MOVING_SUM"}:
            if not 1 <= len(args) <= 2:
                raise invalid(f"{name} 需要指标表达式和可选窗口大小")
            if len(args) == 2:
                integer(args[1], 1, 93)
        elif name in {"WOW", "RUNNING_SUM", "ABS"} and len(args) != 1:
            raise invalid(f"{name} 需要一个指标表达式")
        elif name == "GEO_DISTANCE" and len(args) != 4:
            raise invalid("GEO_DISTANCE 需要两个点的纬度、经度，共四项")
        elif name == "COALESCE" and not 2 <= len(args) <= 8:
            raise invalid("COALESCE 需要2至8项")
        elif name == "ROUND":
            if len(args) != 2:
                raise invalid("ROUND 需要表达式与小数位数")
            integer(args[1], 0, 12)
    if not refs or len(refs) > 16:
        raise invalid("复合指标需要1至16个固定版本指标引用")
    return list(dict.fromkeys(refs))


def integer(node, low, high):
    if not isinstance(node, exp.Literal) or node.is_string or not node.this.isdigit():
        raise invalid("窗口大小和小数位数必须是整数常量")
    value = int(node.this)
    if not low <= value <= high:
        raise invalid(f"整数参数须在{low}至{high}之间")
    return value


@dataclass
class Budget:
    active: list[str] = field(default_factory=list)
    calls: int = 0


_budget = ContextVar("metric_program_budget", default=None)


def bounded(func):
    @wraps(func)
    async def execute(session, user, code, *args, **kwargs):
        budget = _budget.get()
        own = budget is None
        token = _budget.set(Budget()) if own else None
        budget = _budget.get()
        if code in budget.active or len(budget.active) >= MAX_DEPTH or budget.calls >= MAX_CALLS:
            if token is not None:
                _budget.reset(token)
            raise invalid("指标依赖存在循环、深度超过8层或求值次数超过32次")
        budget.calls += 1
        budget.active.append(code)
        try:
            if own:
                try:
                    async with asyncio.timeout(30):
                        return await func(session, user, code, *args, **kwargs)
                except TimeoutError:
                    raise invalid("指标求值超过30秒，请缩小时间范围") from None
            return await func(session, user, code, *args, **kwargs)
        finally:
            budget.active.pop()
            if token is not None:
                _budget.reset(token)

    return execute


def shift(day, amount, period):
    if period == "month":
        index = day.year * 12 + day.month - 1 + amount
        year, month = divmod(index, 12)
        return day.replace(
            year=year, month=month + 1, day=min(day.day, calendar.monthrange(year, month + 1)[1])
        )
    return day + timedelta(days=amount * (7 if period == "week" else 1))


def number(value):
    if value is None:
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise invalid("指标返回值不是数值") from None
    if not result.is_finite() or abs(result) > Decimal("1e100"):
        raise invalid("指标返回数值超出可计算范围")
    return result


def lift(values, operation):
    series = [v for v in values if isinstance(v, dict)]
    keys = set().union(*(v.keys() for v in series))
    if not series:
        return operation(*values)
    return {key: operation(*(v.get(key) if isinstance(v, dict) else v for v in values)) for key in keys}


def distance(lat1, lon1, lat2, lon2):
    if any(v is None for v in (lat1, lon1, lat2, lon2)):
        return None
    if abs(lat1) > 90 or abs(lat2) > 90 or abs(lon1) > 180 or abs(lon2) > 180:
        raise invalid("地理坐标超出纬度或经度范围")
    a, b, c, d = map(math.radians, map(float, (lat1, lon1, lat2, lon2)))
    h = math.sin((c - a) / 2) ** 2 + math.cos(a) * math.cos(c) * math.sin((d - b) / 2) ** 2
    return Decimal(str(6371.0088 * 2 * math.asin(math.sqrt(min(1, max(0, h))))))


async def evaluate(tree, loader, *, period, snapshot, start=None, end=None):
    references(tree)
    if start is not None and period in {"week", "month"}:
        start = start - timedelta(days=start.weekday()) if period == "week" else start.replace(day=1)
    cache, sources, cutoffs = {}, [], []

    async def visit(node, extra=0):
        if isinstance(node, exp.Paren):
            return await visit(node.this, extra)
        if isinstance(node, exp.Literal) and not node.is_string:
            return number(node.this)
        if isinstance(node, exp.Neg):
            return lift([await visit(node.this, extra)], lambda a: -a if a is not None else None)
        if isinstance(node, (exp.Add, exp.Sub, exp.Mul, exp.Div)):
            values = [await visit(node.this, extra), await visit(node.expression, extra)]

            def calculate(a, b):
                if a is None or b is None:
                    return None
                if isinstance(node, exp.Add):
                    return a + b
                if isinstance(node, exp.Sub):
                    return a - b
                if isinstance(node, exp.Mul):
                    return a * b
                return a / b if b else None

            return lift(values, calculate)
        name, args = function_name(node), arguments(node)
        if name == "METRIC":
            key = (*[a.this for a in args], extra)
            if key not in cache:
                result = await loader(key[0], key[1], extra)
                points = result.get("rows", [])
                if len(points) > 10000:
                    raise invalid("指标结果超过10000个时间桶")
                data = {}
                for row in points:
                    point = (row["date"], row.get("objectCode"))
                    if point in data:
                        raise invalid("指标存在重复时间桶，不能隐式合并")
                    data[point] = number(row["value"])
                cache[key] = data
                sources.extend(result.get("dataSources") or [])
                if result.get("dataCutoff"):
                    cutoffs.append(result["dataCutoff"])
            return cache[key]
        if name in {"PREV", "WOW", "DELTA", "PCT_CHANGE", "MOVING_AVG", "MOVING_SUM", "RUNNING_SUM"}:
            if snapshot or period == "none":
                raise invalid("窗口指标需要明确的日、周或月时间范围")
            size = integer(args[1], 1, 93) if len(args) == 2 else 1
            if name == "WOW":
                if period == "month":
                    raise invalid("周环比不能按月时间桶计算")
                size = 7 if period == "day" else 1
            padding = size if name in {"PREV", "WOW", "DELTA", "PCT_CHANGE"} else size - 1
            padding *= 31 if period == "month" else 7 if period == "week" else 1
            if extra + padding > MAX_HISTORY_DAYS:
                raise invalid("窗口依赖需要的历史范围超过731天")
            value = await visit(args[0], extra + padding)
            if not isinstance(value, dict):
                raise invalid("窗口函数必须引用有时间序列的指标")
            if name in {"PREV", "WOW", "DELTA", "PCT_CHANGE"}:
                previous = {
                    (str(shift(date.fromisoformat(k[0]), size, period)), k[1]): v for k, v in value.items()
                }
                if name == "PREV":
                    return previous

                def change(a, b):
                    if a is None or b is None:
                        return None
                    return a - b if name == "DELTA" else (a - b) / b * 100 if b else None

                return lift([value, previous], change)
            result, groups = {}, {}
            for key, item in value.items():
                groups.setdefault(key[1], []).append((key[0], item))
            for group, points in groups.items():
                frame, total = deque(), Decimal(0)
                for text, item in sorted(points):
                    day = date.fromisoformat(text)
                    lo = shift(day, -(size - 1), period) if name != "RUNNING_SUM" else start
                    if item is not None:
                        frame.append((text, item))
                        total += item
                    while frame and lo is not None and frame[0][0] < str(lo):
                        total -= frame.popleft()[1]
                    result[text, group] = (
                        (total / len(frame) if name == "MOVING_AVG" else total) if frame else None
                    )
            return result
        values = [await visit(arg, extra) for arg in args]
        if name == "COALESCE":
            return lift(values, lambda *v: next((n for n in v if n is not None), None))
        if name == "ABS":
            return lift(values, lambda a: abs(a) if a is not None else None)
        if name == "ROUND":
            precision = integer(args[1], 0, 12)
            return lift(
                [values[0]], lambda a: a.quantize(Decimal(1).scaleb(-precision)) if a is not None else None
            )
        if name == "GEO_DISTANCE":
            return lift(values, distance)
        raise invalid("不支持的指标计算表达式")

    try:
        with localcontext() as context:
            context.prec = 128
            result = await visit(tree.expressions[0])
    except DecimalException:
        raise invalid("指标计算超出数值精度范围") from None
    if not isinstance(result, dict):
        raise invalid("公式必须依赖已定义指标")
    rows = [
        {
            "date": day,
            "value": str(number(value)) if value is not None else None,
            **({"objectCode": group} if group is not None else {}),
        }
        for (day, group), value in sorted(result.items(), key=lambda item: (item[0][0], item[0][1] or ""))
        if snapshot or ((start is None or day >= str(start)) and (end is None or day <= str(end)))
    ]
    from app.modules.data.external_mirror import source_metadata_fields

    return {"rows": rows, "dataCutoff": min(cutoffs) if cutoffs else None, **source_metadata_fields(sources)}


async def query_programs(
    session, user, manifest, metrics, *, group_by=(), where=(), limit=1000, time_range=None, now=None
):
    """Execute published compositions without asking an LLM to reconstruct their formulas."""
    from datetime import datetime, time, timedelta
    from zoneinfo import ZoneInfo

    from app.common.metricflow_engine import METRIC_TIME
    from app.core import clock
    from app.core.config import get_settings
    from app.modules.data.external_mirror import source_metadata_fields
    from app.modules.static.metric_trial import evaluate_metric

    definitions = {m["name"]: m for m in manifest.get("metrics", [])}
    if not isinstance(metrics, (list, tuple)) or any(not isinstance(m, str) for m in metrics):
        raise invalid("指标名称须为文本数组")
    if not any(definitions.get(m, {}).get("type") == "program" for m in metrics):
        return None
    if not metrics or len(metrics) > 8 or len(set(metrics)) != len(metrics):
        raise invalid("组合指标一次支持1至8项，不能重复")
    if any(m not in definitions for m in metrics):
        raise invalid("查询包含不存在或未就绪的指标")
    if not isinstance(group_by, (list, tuple)) or any(not isinstance(g, str) for g in group_by):
        raise invalid("分组须为文本数组")
    if not isinstance(where, (list, tuple)):
        raise invalid("过滤条件须为数组")
    if where or set(group_by) - {METRIC_TIME}:
        raise invalid("组合指标使用各引用口径的已保存过滤；此查询不能附加未经映射的维度条件")
    if limit is not None and (
        isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 10000
    ):
        raise invalid("结果条数须为1至10000")
    tz = ZoneInfo(get_settings().display_tz)
    instant = now or clock.now()
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=tz)
    instant = instant.astimezone(tz)
    if time_range is not None and not isinstance(time_range, dict):
        raise invalid("时间范围须为对象")
    window = time_range or {}
    if set(window) - {"start", "end", "grain"}:
        raise invalid("时间范围包含未知字段")
    if "grain" in window and not isinstance(window["grain"], str):
        raise invalid("时间粒度须为文本")
    if any(key in window and not isinstance(window[key], str) for key in ("start", "end")):
        raise invalid("时间边界须为 ISO 文本")
    grain = window.get("grain") or ("day" if METRIC_TIME in group_by else "none")
    if not isinstance(grain, str) or grain not in {"day", "week", "month", "none"}:
        raise invalid("组合指标支持日、周、月或整体汇总")
    snapshot, at, days = not bool(window.get("start") or window.get("end")), None, 30
    if not snapshot:
        if not window.get("start") or not window.get("end"):
            raise invalid("组合指标的时间范围须同时指定开始与结束，结束不含当日")
        if any(not isinstance(window[key], str) for key in ("start", "end")):
            raise invalid("时间边界须为 ISO 文本")
        try:
            lo, hi = (datetime.fromisoformat(str(window[key])) for key in ("start", "end"))
            lo = lo.replace(tzinfo=tz) if lo.tzinfo is None else lo.astimezone(tz)
            hi = hi.replace(tzinfo=tz) if hi.tzinfo is None else hi.astimezone(tz)
        except (ValueError, TypeError):
            raise invalid("时间范围需使用 ISO 日期或时间") from None
        hi = min(hi, instant + timedelta(microseconds=1))
        if lo.timetz().replace(tzinfo=None) != time.min or lo >= hi:
            raise invalid("组合指标开始时间需为本地零点，且必须早于结束时间")
        at = hi - timedelta(microseconds=1)
        days = (at.date() - lo.date()).days + 1
        if not 1 <= days <= 93:
            raise invalid("一次组合指标查询的时间范围须为1至93天")
    elif grain != "none":
        raise invalid("时序或窗口查询需要明确的时间范围；整体快照可不指定时间")
    combined, sources = {}, []
    for name in metrics:
        definition = definitions[name]
        meta = definition.get("config", {}).get("meta", {}).get("rengu", {})
        if not meta.get("id") or not meta.get("version"):
            raise invalid("指标缺少真实已发布定义或版本")
        result = await evaluate_metric(
            session, user, meta["id"], days=days, period=grain, at=at, snapshot=snapshot
        )
        if result.get("version") != meta["version"]:
            raise invalid("指标版本已变化，请重新读取能力目录")
        for row in result["rows"]:
            if row.get("objectCode") is not None:
                raise invalid("未声明的对象分组不能隐式合并")
            combined.setdefault(row["date"], {})[name] = number(row["value"])
        sources.extend(result.get("dataSources") or [])
    columns = ([METRIC_TIME] if grain != "none" else []) + list(metrics)
    rows = [
        ([day] if grain != "none" else []) + [values.get(name) for name in metrics]
        for day, values in sorted(combined.items())
    ]
    return {
        "sql": None,
        "backend": "metric-program",
        "columns": columns,
        "rows": rows[:limit] if limit else rows,
        **source_metadata_fields(sources),
    }
