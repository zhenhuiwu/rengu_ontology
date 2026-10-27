"""Real completed metric buckets; absent history and zero divisors stay unknown."""

import ast
import re
from datetime import timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from app.contracts.static import evaluate_metric
from app.modules.action.condition_expr import Unavailable
from app.modules.dynamic.rule_runtime import Expression, unavailable


class MetricCalls(ast.NodeTransformer):
    """Normalize only the documented, explicit one-object metric@vN(object, bucket) grammar."""

    def __init__(self, obj):
        self.obj = obj

    def visit_BinOp(self, node):
        if not isinstance(node.op, ast.MatMult):
            return self.generic_visit(node)
        call = node.right
        if not (
            isinstance(node.left, ast.Name)
            and isinstance(call, ast.Call)
            and isinstance(call.func, ast.Name)
            and re.fullmatch(r"v[1-9]\d*", call.func.id)
            and len(call.args) == 2
            and not call.keywords
            and all(isinstance(arg, ast.Name) for arg in call.args)
            and call.args[0].id in (self.obj.code, self.obj.api_name, self.obj.display_name)
            and call.args[1].id in ("day", "week")
        ):
            raise unavailable("指标调用缺少唯一规则对象、明确版本或日/周桶；多维口径没有执行映射")
        return ast.Call(
            ast.Name("metric_value", ast.Load()),
            [
                ast.Constant(node.left.id),
                ast.Constant(int(call.func.id[1:])),
                ast.Constant(call.args[1].id),
                ast.Constant("value"),
            ],
            [],
        )

    def visit_Call(self, node):
        node = self.generic_visit(node)
        if not isinstance(node.func, ast.Name) or node.func.id not in ("prev", "wow"):
            return node
        if (
            node.keywords
            or len(node.args) != 1
            or not isinstance(node.args[0], ast.Call)
            or not isinstance(node.args[0].func, ast.Name)
            or node.args[0].func.id != "metric_value"
            or len(node.args[0].args) != 4
            or not all(isinstance(arg, ast.Constant) for arg in node.args[0].args)
            or node.args[0].args[3].value != "value"
        ):
            raise unavailable("prev/wow必须明确引用完整指标版本、规则对象和时间桶")
        call = node.args[0]
        call.args[3] = ast.Constant(node.func.id)
        return call


def bucket_value(rows, *, at, timezone, period, transform):
    today = at.astimezone(ZoneInfo(timezone)).date()
    current = today - timedelta(days=1) if period == "day" else today - timedelta(days=today.weekday() + 7)
    offset = timedelta(days=1 if period == "day" else 7)
    if transform == "prev":
        current -= offset
    value = rows.get(str(current))
    if value is None:
        raise Unavailable(f"缺少真实时间桶 {current} 的指标数据")
    value = Decimal(value)
    if not value.is_finite():
        raise Unavailable("真实指标桶不是有限数值")
    if transform == "wow":
        prior = rows.get(str(current - timedelta(days=7)))
        if prior is None or not Decimal(prior).is_finite() or Decimal(prior) == 0:
            raise Unavailable("缺少真实上周基线或上周值为零，无法计算周环比")
        value = (value - Decimal(prior)) / Decimal(prior)
    return float(value)


class MetricExpression(Expression):
    def __init__(self, tree, obj, gov, bindings):
        self.bindings = bindings
        super().__init__(tree, obj, gov)

    def visit_Call(self, node):  # noqa: N802
        if isinstance(node.func, ast.Name) and node.func.id == "metric_value":
            key = ast.dump(node)
            if key not in self.bindings:
                raise unavailable("指标调用缺少已校验的真实版本和时间桶")
            column, _, label = self.bindings[key]
            ref = ("指标", column)
            self.references[ref], self.labels[ref] = column, label
            return ast.Attribute(ast.Name("指标", ast.Load()), column, ast.Load())
        return super().visit_Call(node)

    def run(self, row, at):
        values = dict(row)
        for column, by_code, _ in self.bindings.values():
            value = by_code.get(row["code"])
            if isinstance(value, str) or value is None:
                raise Unavailable(value or "该对象缺少真实指标时间桶")
            values[column] = value
        return super().run(values, at)


async def bind_metrics(session, user, tree, obj, gov, at, timezone):
    tree = MetricCalls(obj).visit(tree)
    calls = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "metric_value"
    ]
    bindings = {}
    results = {}
    for node in calls:
        if node.keywords or len(node.args) != 4 or not all(isinstance(n, ast.Constant) for n in node.args):
            raise unavailable("指标调用需要明确编号、版本、时间桶和变换")
        code, version, period, transform = [n.value for n in node.args]
        if (
            not isinstance(code, str)
            or type(version) is not int
            or period not in ("day", "week")
            or transform not in ("value", "prev", "wow")
        ):
            raise unavailable("指标版本或时间桶参数不合法")
        cache_key = (code, version, period)
        if cache_key not in results:
            result = await evaluate_metric(
                session, user, code, days=93, period=period, at=at, timezone=timezone, object_kind=obj.code
            )
            if str(result["version"]).lstrip("v") != str(version):
                raise unavailable("指标所选版本没有完整执行映射，不能借用当前版本口径")
            results[cache_key] = result
        series = {}
        for row in results[cache_key]["rows"]:
            series.setdefault(row["objectCode"], {})[row["date"]] = row["value"]
        values = {}
        for code_key, rows in series.items():
            try:
                values[code_key] = bucket_value(
                    rows, at=at, timezone=timezone, period=period, transform=transform
                )
            except Unavailable as exc:
                values[code_key] = str(exc)
        key = ast.dump(node)
        bindings[key] = (f"桶{len(bindings)}", values, f"{code} v{version} {period}/{transform}")
    return MetricExpression(tree, obj, gov, bindings)
