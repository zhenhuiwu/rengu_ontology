"""Read-only rule evaluation against governed current rows or retained event payloads."""

import ast
import io
import hashlib
import hmac
import json
import os
import re
import tokenize
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from enum import Enum

from sqlalchemy import func, select

from app.common.row_filter import apply_row_filter
from app.common.sql_guard import governed_tables
from app.core.clock import now
from app.core.config import get_settings
from app.core.errors import AppError
from app.core.schema import _fmt_dt_seconds
from app.modules.action.condition_expr import Unavailable, evaluate
from app.modules.event.models import Event, EventType
from app.modules.static.models import ObjectStatus, ObjectType


def anchor_error(message):
    return AppError(message, code="RULE_RUN_EXPIRED", status_code=409)


def anchor_scope(user, definition, body):
    return json.dumps(
        [user.id, user.row_filters, user.attrs, definition, body.mode, body.range],
        sort_keys=True,
        default=str,
    )


def sign_anchor(payload, scope):
    # Separate this signing domain from datasource encryption; reloads/workers share the protected key.
    configured = get_settings().rule_replay_signing_key
    raw = (
        configured.get_secret_value().strip()
        if configured
        else os.environ.get("RENGU_DS_SECRET_KEY", "").strip()
    )
    if not raw:
        raise AppError(
            "未配置规则试跑签名密钥，无法固定分页",
            code="RULE_ANCHOR_KEY_UNAVAILABLE",
            status_code=503,
        )
    key = hmac.new(
        raw.split(",")[0].strip().encode(), b"rengu:rules:replay-anchor:v1", hashlib.sha256
    ).digest()
    return hmac.new(key, f"{scope}|{payload}".encode(), hashlib.sha256).hexdigest()


def read_anchor(token, scope, current):
    if not token:
        return current.astimezone(timezone.utc).replace(microsecond=0), None
    try:
        stamp, digest, signature = token.split(".")
        payload = f"{stamp}.{digest}"
        if not hmac.compare_digest(signature, sign_anchor(payload, scope)):
            raise ValueError
        at = datetime.fromtimestamp(int(stamp), timezone.utc)
        if not timedelta(0) <= current - at <= timedelta(minutes=10):
            raise ValueError
        return at, digest
    except (ValueError, OverflowError, OSError):
        raise anchor_error("试跑已过期、身份/权限或定义已变化，请重新开始试跑") from None


def unavailable(message):
    return AppError(message, code="RULE_EVALUATION_UNAVAILABLE", status_code=422)


def parse_expression(text):
    try:
        if not text or len(text.encode("utf-8")) > 4096:
            raise ValueError
        tokens = list(tokenize.generate_tokens(io.StringIO(text).readline))
        parts = []
        for token in tokens:
            if token.type == tokenize.COMMENT:
                raise ValueError
            value = token.string
            if token.type == tokenize.NAME:
                value = {"AND": "and", "OR": "or", "NOT": "not", "IN": "in", "且": "and", "或": "or"}.get(value, value)
            elif value == "=":
                value = "=="
            parts.append((token.type, value))
        tree = ast.parse(tokenize.untokenize(parts), mode="eval")
        if sum(1 for _ in ast.walk(tree)) > 256:
            raise ValueError
        return tree
    except (ValueError, SyntaxError, tokenize.TokenError, UnicodeError, RecursionError):
        raise unavailable(
            "规则表达式没有可执行的有限条件；指标时间窗、历史值和跨对象函数尚未绑定执行口径"
        ) from None


def threshold(text):
    text = text.strip().replace("−", "-")
    numeric = re.fullmatch(r"([+-]?\d+(?:\.\d+)?)\s*(%|小时|分钟|天|元)?", text)
    if numeric:
        value = float(numeric[1])
        return (value / 100 if numeric[2] == "%" else value), numeric[2]
    return text, None


def selected_expression(base, definition):
    """Apply saved threshold edits only when their correspondence is unambiguous."""
    tree = parse_expression(definition.get("expr", ""))
    old, new = base.get("conds", []), definition.get("conds", [])
    if old == new or base.get("expr") != definition.get("expr"):
        return tree
    comparisons = [n for n in ast.walk(tree) if isinstance(n, ast.Compare)]
    comparisons.sort(key=lambda n: (n.lineno, n.col_offset))
    if len(old) != len(new) or len(comparisons) != len(old):
        raise unavailable("草稿条件与表达式无法逐项对应，不能猜测修改后的执行含义")
    operators = {ast.Eq: "=", ast.NotEq: "!=", ast.Lt: "<", ast.LtE: "≤", ast.Gt: ">", ast.GtE: "≥"}
    for before, after, node in zip(old, new, comparisons, strict=True):
        if before == after:
            continue
        if {k: v for k, v in before.items() if k != "value"} != {
            k: v for k, v in after.items() if k != "value"
        }:
            raise unavailable("草稿修改了条件结构，尚无明确可执行表达式")
        try:
            literal = ast.literal_eval(node.comparators[0])
        except (ValueError, TypeError):
            raise unavailable("草稿阈值没有可替换的标量表达式") from None
        original, unit = threshold(before["value"])
        replacement, new_unit = threshold(after["value"])
        if (
            len(node.ops) != 1
            or operators.get(type(node.ops[0])) != before.get("op")
            or literal != original
            or unit != new_unit
        ):
            raise unavailable("草稿阈值与原表达式不一致，不能猜测执行含义")
        node.comparators[0] = ast.Constant(replacement)
    return tree


class Expression(ast.NodeTransformer):
    def __init__(self, tree, obj, gov, event_fields=None):
        self.obj, self.gov, self.event_fields = obj, gov, event_fields
        self.columns, self.references, self.times = {}, {}, {}
        self.labels, self.column_labels = {}, {}
        self.source = ast.unparse(self.visit(tree))
        # Validate even an empty dataset; unavailable syntax must never become a successful zero.
        try:
            evaluate(self.source, {key: 0 for key in self.references})
        except Unavailable as exc:
            # Type-dependent comparisons are evaluated with actual values below.
            if str(exc) != "表达式无法在受限条件解释器中执行":
                raise unavailable(str(exc)) from None

    def column(self, node):
        if not isinstance(node, ast.Attribute) or not isinstance(node.value, ast.Name):
            raise unavailable("只支持明确的对象属性引用")
        if node.value.id not in {self.obj.code, self.obj.api_name, self.obj.display_name}:
            raise unavailable("历史对象状态、指标口径或跨对象上下文尚不可用")
        name = re.sub(r"(?<!^)(?=[A-Z])", "_", node.attr).lower()
        props = [
            p for p in self.obj.properties if node.attr in (p.api_name, p.display_name) or name == p.api_name
        ]
        if len(props) != 1:
            raise unavailable(f"属性「{node.attr}」未唯一绑定")
        prop = props[0]
        if (
            prop.is_sensitive
            or prop.visibility == "hidden"
            or not prop.backing_column
            or prop.backing_column not in self.gov.table.c
        ):
            raise unavailable(f"属性「{node.attr}」不可读取或尚未映射真实列")
        self.columns[prop.backing_column] = self.gov.table.c[prop.backing_column]
        self.column_labels[prop.backing_column] = prop.display_name
        return prop.backing_column

    def visit_Call(self, node):
        if (
            self.event_fields is not None
            or not isinstance(node.func, ast.Name)
            or node.func.id not in ("hours_since", "minutes_since")
            or len(node.args) != 1
            or node.keywords
        ):
            raise unavailable("该规则函数尚未绑定真实执行口径（不支持指标、prev、wow、跨对象查询）")
        column = self.column(node.args[0])
        key = ("计算", f"时长{len(self.times)}")
        self.times[key] = (column, 3600 if node.func.id == "hours_since" else 60)
        self.references[key] = None
        self.labels[key] = (
            f"距{self.column_labels[column]}（{'小时' if node.func.id == 'hours_since' else '分钟'}）"
        )
        return ast.Attribute(ast.Name(key[0], ast.Load()), key[1], ast.Load())

    def visit_Attribute(self, node):
        if self.event_fields is not None:
            if (
                not isinstance(node.value, ast.Name)
                or node.value.id not in ("event", "事件")
                or node.attr not in self.event_fields
            ):
                raise unavailable("历史回放只支持已声明的事件载荷字段；缺少历史对象/生命周期上下文")
            key = (node.value.id, node.attr)
            self.references[key] = node.attr
            self.labels[key] = self.event_fields[node.attr]
        else:
            column = self.column(node)
            key = (node.value.id, node.attr)
            self.references[key] = column
            self.labels[key] = self.column_labels[column]
        return node

    def run(self, row, at):
        refs = {}
        for key, column in self.references.items():
            if key in self.times:
                column, seconds = self.times[key]
                value = row.get(column)
                if value is not None and (not isinstance(value, datetime) or value.tzinfo is None):
                    raise Unavailable("时间字段缺少时区，不能推算时长")
                refs[key] = (at - value).total_seconds() / seconds if value is not None else None
                continue
            value = row.get(column)
            if self.event_fields is not None and column not in row:
                raise Unavailable(f"原始载荷缺少字段 {column}")
            refs[key] = (
                value.value
                if isinstance(value, Enum)
                else float(value)
                if isinstance(value, Decimal)
                else value
            )
        matched = evaluate(self.source, refs)

        def display(value):
            return (
                f"{value:.2f}".rstrip("0").rstrip(".")
                if isinstance(value, float)
                else "空"
                if value is None
                else str(value)[:120]
            )

        values = " · ".join(f"{self.labels[key]}：{display(value)}" for key, value in refs.items())
        return matched, values


async def run_rule(session, user, base, definition, body):
    obj = await session.scalar(
        select(ObjectType).where(
            ObjectType.code == definition["objKind"], ObjectType.status == ObjectStatus.published
        )
    )
    gov = next(
        (g for g in governed_tables().values() if obj and g.object_type == obj.code and g.inherit is None),
        None,
    )
    if obj is None or gov is None or "code" not in gov.table.c:
        raise unavailable("规则对象尚未唯一映射可按权限读取的受控业务表")
    table = gov.table
    scope = anchor_scope(user, definition, body)
    at, expected_digest = read_anchor(body.anchor, scope, now())
    visible = await apply_row_filter(select(table.c.code), user, obj.code, gov.columns, gov.links)
    trigger = definition.get("trigger") or {}
    tree = selected_expression(base, definition)
    if body.mode == "snapshot":
        if trigger.get("kind") != "定时":
            raise unavailable("事件规则需要原始事件载荷，请选择历史事件回放")
        expression = Expression(tree, obj, gov)
        columns = {"id": table.c.id, "code": table.c.code, **expression.columns}
        title = next(
            (
                p.backing_column
                for p in obj.properties
                if p.is_title
                and not p.is_sensitive
                and p.visibility != "hidden"
                and p.backing_column
                and p.backing_column in table.c
            ),
            None,
        )
        if title:
            columns[title] = table.c[title]
        statement = await apply_row_filter(
            select(*columns.values()).order_by(table.c.id), user, obj.code, gov.columns, gov.links
        )
        rows = (await session.execute(statement.limit(10001))).mappings().all()
        candidates = [
            (row["id"], row["code"], str(row[title]) if title else row["code"], at, row) for row in rows
        ]
        note = "按当前可见对象数据计算条件；不重建历史状态、不模拟调度，不计算新增/减少命中或冷却去重。"
    else:
        if trigger.get("kind") != "事件":
            raise unavailable("定时规则缺少历史对象快照，无法还原过去；可选择当前数据推演")
        event_type = await session.scalar(
            select(EventType).where(
                EventType.code == trigger.get("eventId"), EventType.retired_release_id.is_(None)
            )
        )
        if event_type is None or event_type.status.value == "草稿":
            raise unavailable("订阅事件类型不存在或尚未发布")
        fields = {p.api: p.name for p in event_type.payload}
        from app.modules.dynamic.event_rule_runtime import EventExpression
        from app.modules.versioning.repo import ReleaseRepo

        release = await ReleaseRepo(session).latest()
        expression = EventExpression(
            tree, fields, release.snapshot if release else {},
            {"objKind": obj.code, "objectApi": obj.api_name},
        )
        filter_text = trigger.get("filter")
        event_filter = None
        if filter_text and filter_text != "全部":
            # Legacy bare event names are scoped only to fields declared by this event type.
            filter_tree = parse_expression(filter_text.replace("∈", " in ").replace("∉", " not in "))

            class BindFields(ast.NodeTransformer):
                def visit_Name(self, node):
                    return (
                        ast.Attribute(ast.Name("event", ast.Load()), node.id, ast.Load())
                        if node.id in fields
                        else node
                    )

            event_filter = Expression(BindFields().visit(filter_tree), obj, gov, fields)
        unique = select(table.c.code).group_by(table.c.code).having(func.count() == 1)
        statement = (
            select(Event)
            .where(
                Event.event_type_id == event_type.id,
                Event.obj_kind == obj.code,
                Event.object_id.in_(visible),
                Event.object_id.in_(unique),
                Event.occurred_at >= at - timedelta(days=7 if body.range == "7d" else 30),
                Event.occurred_at <= at,
                Event.is_test.is_(False),
            )
            .order_by(Event.occurred_at, Event.id)
        )
        rows = list(await session.scalars(statement.limit(10001)))
        candidates = [
            (event.id, event.object_id, event.obj_label or event.object_id, event.occurred_at, event.payload)
            for event in rows
        ]
        note = "以所选规则重算保留的真实事件载荷；未保留载荷和缺失字段明确列为无法求值，不还原当时对象状态或冷却去重。"
    if len(candidates) > 10000:
        raise unavailable("可见样本超过单次 10000 条上限，请缩小时间或对象权限范围；本次未返回部分结果")
    digest = hashlib.sha256(
        json.dumps(
            [
                (identity, code, label, sample_at, dict(row) if isinstance(row, Mapping) else row)
                for identity, code, label, sample_at, row in candidates
            ],
            sort_keys=True,
            default=str,
        ).encode()
    ).hexdigest()
    if expected_digest and not hmac.compare_digest(expected_digest, digest):
        raise anchor_error("试跑的数据已发生变化，请重新开始试跑；未返回混合分页结果")
    payload = f"{int(at.timestamp())}.{digest}"
    anchor = f"{payload}.{sign_anchor(payload, scope)}"
    matched = unknown = evaluated = filtered = 0
    samples = []
    for identity, code, label, sample_at, row in candidates:
        try:
            if row is None:
                raise Unavailable("未保留原始事件载荷，不能从摘要还原")
            if not isinstance(row, Mapping):
                raise Unavailable("原始载荷不是可读取的字段对象")
            if body.mode == "events" and event_filter and not event_filter.run(row, sample_at)[0]:
                filtered += 1
                continue
            hit, values = expression.run(row, sample_at)
            evaluated += 1
            matched += int(hit)
            if not hit:
                continue
            diff = "条件满足（未执行动作）"
        except (Unavailable, AppError) as exc:
            unknown += 1
            values, diff = "—", f"无法求值：{exc}"
        samples.append(
            {
                "key": f"{body.mode}:{identity}",
                "at": _fmt_dt_seconds(sample_at),
                "objKind": obj.code,
                "objId": code,
                "objLabel": label,
                "dims": [],
                "value": values,
                "diff": diff,
            }
        )
    start = (body.page - 1) * body.size
    return {
        "mode": body.mode,
        "anchor": anchor,
        "ranAt": _fmt_dt_seconds(at),
        "ruleVersion": definition.get("version"),
        "candidates": len(candidates),
        "evaluated": evaluated,
        "unknown": unknown,
        "filtered": filtered,
        "would": matched,
        "actual": None,
        "added": None,
        "removed": None,
        "deduped": None,
        "dedupeReason": "缺少可靠历史基线，未模拟去重",
        "notice": note,
        "samples": samples[start : start + body.size],
        "total": len(samples),
        "page": body.page,
        "size": body.size,
    }
