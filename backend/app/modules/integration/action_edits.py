"""Bounded business-property assignments; ontology definitions still go through CRs."""

import json
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from sqlalchemy import Boolean, Date, DateTime, Enum, Integer, Numeric, String, select, update

from app.common.auth import check_func, row_filter
from app.common.row_filter import is_deny_all, to_sql
from app.common.sql_guard import governed_tables
from app.core import clock
from app.core.errors import AppError, Forbidden
from app.modules.action.checker import structured_params
from app.modules.action.models import ActionType, TypeStatus
from app.modules.action.sections import action_def
from app.modules.static.repo import StaticRepo

# Identity, money, personal data and scope columns are deliberately not writable here.
WRITABLE = {"store": {"name", "status", "is_test"},
            "order": {"status", "warehouse", "shipped_at", "is_test"}}


def unavailable(message):
    return AppError(message, code="ACTION_EDITS_UNAVAILABLE", status_code=503)


async def execution_definition(session, run):
    action = await session.scalar(select(ActionType).where(ActionType.code == run.action_type_code)
                                  .with_for_update().execution_options(populate_existing=True))
    if (action is None or action.retired_release_id is not None
            or action.status not in (TypeStatus.published, TypeStatus.changing)
            or not run.type_version or action.version != run.type_version):
        raise AppError("执行时的已发布动作定义版本不可用，不能替换为当前定义",
                       code="ACTION_DEFINITION_VERSION_UNAVAILABLE", status_code=503)
    value = action_def(action)
    for row, param in zip(value["params"], action.params, strict=True):
        row["_ast"] = param.rule_expr
    for row, cond in zip(value["conds"], action.conds, strict=True):
        row["_ast"] = cond.expr_ast
    if value.get("sysId") != run.sys_code:
        raise unavailable("动作系统绑定已改变，不能执行旧记录")
    if not run.sys_code and not value["writeback"]["edits"] and not value["writeback"]["effects"]:
        raise unavailable("动作没有可执行的业务属性编辑或事件定义")
    for effect in value["writeback"]["effects"]:
        if (effect["kind"] != "事件" or effect["target"].split(" ", 1)[0]
                not in {"ActionExecuted", "ActionOutcomeUnknown"}):
            raise unavailable("此动作含尚未配置的通知、Webhook或自定义事件副作用")
    return value


def column_value(column, value):
    """Validate a JSON scalar against the actual column, without coercing booleans/numbers."""
    if value is None:
        if not column.nullable:
            raise unavailable("属性不允许空值")
        return None
    type_ = column.type
    if isinstance(type_, Enum):
        members = list(type_.enum_class)
        matches = [m for m in members if value in (m.name, m.value)]
        if len(matches) == 1:
            return matches[0]
    elif isinstance(type_, Boolean):
        if type(value) is bool:
            return value
    elif isinstance(type_, Integer):
        if type(value) is int and -(2 ** 31) <= value < 2 ** 31:
            return value
    elif isinstance(type_, Numeric):
        if type(value) in (int, float):
            try:
                number = Decimal(str(value))
                if number.is_finite() and number == number.quantize(Decimal(1).scaleb(-type_.scale)):
                    if abs(number) < Decimal(10) ** (type_.precision - type_.scale):
                        return number
            except InvalidOperation:
                pass
    elif isinstance(type_, DateTime):
        if isinstance(value, str):
            try:
                parsed = datetime.fromisoformat(value)
                if not type_.timezone or parsed.tzinfo is not None:
                    return parsed
            except ValueError:
                pass
    elif isinstance(type_, Date):
        if isinstance(value, str):
            try:
                return date.fromisoformat(value)
            except ValueError:
                pass
    elif isinstance(type_, String) and isinstance(value, str):
        if (not type_.length or len(value) <= type_.length) and not any(ord(c) < 32 for c in value):
            return value
    raise unavailable("属性值不符合实际数据库列的类型、枚举或长度")


async def business_edits(session, user, run, definition, *, apply=False):
    edits = definition["writeback"]["edits"]
    if not edits:
        return
    check_func(user, "action-center", "manage")
    await target_access(session, user, run, definition)
    kind = run.obj_kind
    if kind != definition.get("targetKind") or kind not in WRITABLE or not run.obj_id:
        raise unavailable("仅支持动作绑定的真实门店/订单对象属性修改")
    obj = await StaticRepo(session).object_type(kind, refresh=True)
    if obj.status.value != "已发布":
        raise unavailable("关联对象未发布")
    governed = next((g for g in governed_tables().values() if g.object_type == kind and not g.inherit), None)
    if governed is None or obj.source_table not in (governed.table.name, f"public.{governed.table.name}"):
        raise unavailable("对象没有唯一的受控业务表映射")
    params = structured_params(run.params if run.params is not None else run.params_text)
    values = {}
    for edit in edits:
        if edit.get("op") != "修改" or edit.get("obj") not in (kind, obj.api_name, obj.display_name):
            raise unavailable("新建、关联或跨对象编辑尚无受控执行映射")
        try:
            if len(edit["field"].encode("utf-8")) > 65_536:
                raise ValueError
            assignments = json.loads(edit["field"])
        except (TypeError, ValueError):
            raise unavailable('属性编辑须为JSON，例如 {"status":{"param":"status"}}') from None
        if not isinstance(assignments, dict) or not 1 <= len(assignments) <= 20:
            raise unavailable("属性编辑必须是非空有界映射")
        for api, source in assignments.items():
            props = [p for p in obj.properties if p.api_name == api]
            if len(props) != 1:
                raise unavailable("编辑属性没有唯一的已发布映射")
            prop = props[0]
            column = prop.backing_column
            declared = {getattr(c, "name", None) for c in governed.columns.values()}
            if (column not in WRITABLE[kind] or column not in declared or prop.is_pk
                    or prop.is_sensitive or prop.visibility == "hidden" or column in values):
                raise unavailable("属性不在可写白名单或存在重复赋值")
            if not isinstance(source, dict) or set(source) not in ({"param"}, {"value"}):
                raise unavailable("属性值只允许显式参数引用或JSON字面量")
            if "param" in source:
                key = source["param"]
                if not isinstance(key, str) or key not in params:
                    raise unavailable("属性引用的实际参数不存在")
                value = params[key]
            else:
                value = source["value"]
            values[column] = column_value(governed.table.c[column], value)
    expr = await row_filter(user, kind)
    if is_deny_all(expr):
        raise Forbidden("没有目标对象的行权限")
    predicate = governed.table.c.code == run.obj_id
    if expr is not None:
        predicate &= to_sql(expr, governed.columns, user.attrs, governed.links)
    rows = (await session.execute(select(governed.table.c.id, governed.table.c.lock_version)
                                 .where(predicate).with_for_update().limit(2))).all()
    if len(rows) != 1:
        raise Forbidden("目标对象不存在、不唯一或超出当前用户行权限")
    if apply:
        await session.execute(update(governed.table).where(governed.table.c.id == rows[0].id)
                              .values(**values, updated_at=clock.now(), lock_version=rows[0].lock_version + 1))


async def target_access(session, user, run, definition):
    kind = definition.get("targetKind")
    if not kind:
        if run.obj_kind:
            raise unavailable("无关联对象的动作不能附加目标对象")
        return
    if run.obj_kind != kind or not run.obj_id:
        raise unavailable("动作目标与已发布定义不匹配")
    governed = next((g for g in governed_tables().values() if g.object_type == kind and not g.inherit), None)
    if governed is None or "code" not in governed.table.c:
        raise unavailable("目标对象没有可执行的受控业务映射")
    identities = (await session.execute(select(governed.table.c.id)
        .where(governed.table.c.code == run.obj_id).with_for_update().limit(2))).all()
    if len(identities) != 1:
        raise Forbidden("目标对象不存在、不唯一或超出当前用户行权限")
    expr = await row_filter(user, kind)
    if is_deny_all(expr):
        raise Forbidden("没有目标对象的行权限")
    predicate = governed.table.c.code == run.obj_id
    if expr is not None:
        predicate &= to_sql(expr, governed.columns, user.attrs, governed.links)
    rows = (await session.execute(select(governed.table.c.id).where(predicate)
                                 .with_for_update().limit(2))).all()
    if len(rows) != 1:
        raise Forbidden("目标对象不存在、不唯一或超出当前用户行权限")
