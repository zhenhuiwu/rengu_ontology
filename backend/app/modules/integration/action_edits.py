"""Bounded business-property assignments; ontology definitions still go through CRs."""

import hashlib
import json
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from sqlalchemy import Boolean, Date, DateTime, Enum, Integer, Numeric, String, insert, literal, select, update
from sqlalchemy.sql.visitors import replacement_traverse

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


def mapping_json(value):
    try:
        if not isinstance(value, str) or len(value.encode("utf-8")) > 65_536:
            raise ValueError
        result = json.loads(value)
        from app.modules.functions.runtime import json_value
        json_value(result)
        if not isinstance(result, dict) or not 1 <= len(result) <= 32:
            raise ValueError
        return result
    except (TypeError, ValueError):
        raise unavailable("执行映射须为有界 JSON 对象，自然语言不能执行") from None


def explicit_target_params(definition):
    """Only structured execution targets may intentionally differ from the run context."""
    keys = set()
    for edit in definition.get("writeback", {}).get("edits", []):
        try:
            spec = mapping_json(edit.get("field"))
        except AppError:
            continue
        for field in ("target", "from", "to"):
            source = spec.get(field)
            if isinstance(source, dict) and set(source) == {"param"} and isinstance(source["param"], str):
                keys.add(source["param"])
    return keys


async def value_source(session, user, source, params, *, depth=0):
    if depth > 4 or not isinstance(source, dict):
        raise unavailable("值映射无效或嵌套过深")
    if set(source) == {"param"}:
        key = source["param"]
        if not isinstance(key, str) or key not in params:
            raise unavailable("引用的实际参数不存在")
        return params[key]
    if set(source) == {"value"}:
        return source["value"]
    if set(source) == {"function", "version", "inputs"} and isinstance(source["inputs"], dict):
        if len(source["inputs"]) > 32:
            raise unavailable("函数最多32个输入")
        from app.contracts.functions import invoke_version
        inputs = {key: await value_source(session, user, value, params, depth=depth + 1)
                  for key, value in source["inputs"].items()}
        return await invoke_version(session, user, source["function"], source["version"], inputs)
    raise unavailable("值只能来自已声明参数、JSON字面量或固定版本函数")


async def resolved_params(session, user, definition, params):
    values = dict(params)
    for param in definition.get("params", []):
        raw = param.get("def", "")
        if not isinstance(raw, str) or not raw.lstrip().startswith("{"):
            continue
        spec = mapping_json(raw)
        if "function" not in spec:
            continue
        supplied = values.pop(param["api"], None)
        result = await value_source(session, user, spec, values)
        if supplied is not None and supplied != result:
            raise unavailable("函数计算参数不能被手工值覆盖")
        values[param["api"]] = result
    return structured_params(values)


async def validate_source_definition(session, source):
    """Validate publish-time bindings without needing business parameters or executing code."""
    if not isinstance(source, dict):
        raise unavailable("值映射须为明确的参数、固定值或函数绑定")
    if set(source) == {"value"}:
        return
    if set(source) == {"param"} and isinstance(source["param"], str) and source["param"]:
        return
    if (set(source) != {"function", "version", "inputs"} or not isinstance(source["inputs"], dict)
            or len(source["inputs"]) > 32 or not isinstance(source["function"], str)
            or not isinstance(source["version"], str) or not source["version"] or source["version"] == "current"):
        raise unavailable("函数映射须指定编号、固定发布版本和输入")
    from app.modules.functions.models import Function, FnVersion
    from app.modules.functions.runtime import signature
    record = (await session.execute(select(Function, FnVersion).join(FnVersion,
        FnVersion.function_id == Function.id).where(Function.code == source["function"],
        Function.status == "published", FnVersion.ver == source["version"]))).one_or_none()
    if record is None:
        raise unavailable("函数绑定版本未发布或不可用")
    snapshot = record[1].snapshot
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get("code"), str) or not isinstance(snapshot.get("name"), str):
        raise unavailable("函数绑定版本缺少有效源码")
    inputs, _ = signature(snapshot["code"], snapshot["name"])
    if set(source["inputs"]) != {p["name"] for p in inputs}:
        raise unavailable("函数输入映射与绑定版本不一致")
    for value in source["inputs"].values():
        await validate_source_definition(session, value)


async def object_binding(session, identity):
    if not isinstance(identity, str) or not identity or len(identity) > 128:
        raise unavailable("对象映射标识无效")
    from app.modules.static.models import ObjectStatus, ObjectType
    matches = list(await session.scalars(select(ObjectType).where(
        (ObjectType.code == identity) | (ObjectType.api_name == identity) |
        (ObjectType.display_name == identity)).with_for_update(read=True)))
    if len(matches) != 1 or matches[0].status != ObjectStatus.published:
        raise unavailable("编辑对象没有唯一的已发布定义")
    obj = await StaticRepo(session).object_type(matches[0].code, refresh=True)
    candidates = [g for g in governed_tables().values() if g.object_type == obj.code and not g.inherit]
    # source_table names the upstream system; local rows use the registered explorer binding.
    if len(candidates) != 1:
        raise unavailable("对象没有唯一的受控业务表映射")
    return obj, candidates[0]


async def object_operation(session, user, obj, definition):
    from app.modules.admin.models import SysRole, SysRoleMember, SysUser
    permissions = await session.scalars(select(SysRole.objs).join(
        SysRoleMember, SysRoleMember.role_id == SysRole.id).join(
        SysUser, SysUser.id == SysRoleMember.user_id).where(SysUser.code == user.id))
    names = {definition["id"], definition["api"], definition["name"]}
    for entries in permissions:
        if not isinstance(entries, list):
            continue
        matches = [entry for entry in entries if isinstance(entry, dict) and entry.get("api") == obj.api_name]
        if len(matches) == 1 and matches[0].get("view") is True and isinstance(matches[0].get("actions"), list):
            if names.intersection(a for a in matches[0]["actions"] if isinstance(a, str)):
                return
    raise Forbidden(f"没有对象「{obj.display_name}」的此动作操作授权")


async def existing_row(session, user, obj, governed, code, *, lock=True):
    if not isinstance(code, str) or not code or len(code) > 64 or "\x00" in code:
        raise unavailable("对象引用须为真实对象编号")
    # Count before row filtering: duplicate historical business codes are never silently selected.
    ids = list(await session.scalars(select(governed.table.c.id).where(
        governed.table.c.code == code).limit(2)))
    if len(ids) != 1:
        raise Forbidden("引用对象不存在、不唯一或超出当前用户行权限")
    expr = await row_filter(user, obj.code)
    if is_deny_all(expr):
        raise Forbidden("没有引用对象的行权限")
    query = select(governed.table).where(governed.table.c.id == ids[0])
    if expr is not None:
        query = query.where(to_sql(expr, governed.columns, user.attrs, governed.links))
    if lock:
        query = query.with_for_update()
    row = (await session.execute(query)).mappings().one_or_none()
    if row is None:
        raise Forbidden("引用对象不存在、不唯一或超出当前用户行权限")
    return row


async def result_scope(session, user, obj, governed, row_id, values):
    expr = await row_filter(user, obj.code)
    if is_deny_all(expr):
        raise Forbidden("没有编辑后对象的行权限")
    if expr is None:
        return
    predicate = to_sql(expr, governed.columns, user.attrs, governed.links)
    def projected(node):
        if getattr(node, "table", None) is governed.table and getattr(node, "name", None) in values:
            return literal(values[node.name], type_=node.type)
        return None
    predicate = replacement_traverse(predicate, {}, projected)
    if not await session.scalar(select(governed.table.c.id).where(
            governed.table.c.id == row_id, predicate)):
        raise Forbidden("编辑后的对象超出当前用户行权限")


def mapped_target(edit, spec, run, definition, obj, params):
    source = spec.get("target", {"context": "id"})
    if source == {"context": "id"}:
        if obj.code != run.obj_kind or run.obj_kind != definition.get("targetKind"):
            raise unavailable("上下文对象类型与编辑对象不一致")
        return run.obj_id
    if isinstance(source, dict) and set(source) == {"param"}:
        key = source["param"]
        declared = [p for p in definition["params"] if p.get("api") == key and
                    p.get("type") in {f"对象引用 · {x}" for x in (obj.code, obj.api_name, obj.display_name)}]
        if len(declared) == 1 and isinstance(params.get(key), str):
            return params[key]
    raise unavailable("目标必须来自上下文对象或对应类型的已声明对象引用参数")


async def relation_binding(session, identity):
    if not isinstance(identity, str) or not identity or len(identity) > 128:
        raise unavailable("关系映射标识无效")
    from app.modules.static.models import DefStatus, LinkType
    rows = list(await session.scalars(select(LinkType).where(
        (LinkType.code == identity) | (LinkType.api_name == identity)).with_for_update(read=True)))
    if len(rows) != 1 or rows[0].status != DefStatus.published or rows[0].via:
        raise unavailable("关系没有唯一的已发布一跳映射")
    link = rows[0]
    src, sg = await object_binding(session, link.from_code)
    dst, dg = await object_binding(session, link.to_code)
    path = sg.links.get(link.code)
    if path is None or path.target is not dg.table:
        raise unavailable("关系未登记真实受控外键映射")
    left = sg.columns.get(link.from_prop)
    right = dg.columns.get(link.to_prop)
    if left is None or right is None or not left.compare(path.local) or not right.compare(path.target_key):
        raise unavailable("已发布关系与受控外键映射不一致")
    column = sg.table.c.get(left.name)
    if column is None or not any(f.column is right for f in column.foreign_keys):
        raise unavailable("关联只允许真实数据库外键")
    return link, src, sg, dst, dg, column


async def business_edits(session, user, run, definition, *, apply=False):
    edits = definition["writeback"]["edits"]
    if not edits:
        return
    check_func(user, "action-center", "manage")
    params = structured_params(run.params if run.params is not None else run.params_text)
    touched = set()
    for edit in edits:
        spec = mapping_json(edit["field"])
        if edit.get("op") == "关联":
            if set(spec) != {"from", "to"}:
                raise unavailable("关联映射须显式指定from/to对象参数")
            link, obj, governed, dst, dg, column = await relation_binding(session, edit["obj"])
            await object_operation(session, user, obj, definition)
            await object_operation(session, user, dst, definition)
            source_code = mapped_target(edit, {"target": spec["from"]}, run, definition, obj, params)
            target_code = mapped_target(edit, {"target": spec["to"]}, run, definition, dst, params)
            row = await existing_row(session, user, obj, governed, source_code)
            target = await existing_row(session, user, dst, dg, target_code)
            if (obj.code, source_code, column.name) in touched:
                raise unavailable("同一关系属性不能重复赋值")
            touched.add((obj.code, source_code, column.name))
            values = {column.name: target[next(iter(column.foreign_keys)).column.name]}
            await result_scope(session, user, obj, governed, row["id"], values)
            # Reparenting may move a row out of scope: evaluate the resulting row before committing.
            if apply:
                await session.execute(update(governed.table).where(governed.table.c.id == row["id"])
                    .values(**values, updated_at=clock.now(), lock_version=row["lock_version"] + 1))
                await existing_row(session, user, obj, governed, source_code)
            continue
        obj, governed = await object_binding(session, edit.get("obj"))
        await object_operation(session, user, obj, definition)
        if obj.code not in WRITABLE:
            raise unavailable("此对象尚无可写白名单")
        modern = "values" in spec
        if modern and set(spec) - {"target", "values", "lockVersion"}:
            raise unavailable("编辑映射含未支持的字段")
        assignments = spec.get("values") if modern else spec
        if not isinstance(assignments, dict) or not 1 <= len(assignments) <= 20:
            raise unavailable("属性编辑必须是非空有界映射")
        code = mapped_target(edit, spec if modern else {}, run, definition, obj, params)
        row = await existing_row(session, user, obj, governed, code)
        if "lockVersion" in spec:
            expected = await value_source(session, user, spec["lockVersion"], params)
            if type(expected) is not int or expected != row["lock_version"]:
                raise AppError("编辑对象已变化，请重新确认", code="VERSION_CONFLICT", status_code=409)
        values = {}
        assignment_target = f"new:{run.code}" if edit.get("op") == "新建" else code
        for api, source in assignments.items():
            props = [p for p in obj.properties if p.api_name == api]
            if len(props) != 1:
                raise unavailable("编辑属性没有唯一的已发布映射")
            prop = props[0]
            column = prop.backing_column
            declared = {getattr(c, "name", None) for c in governed.columns.values()}
            if (column not in WRITABLE[obj.code] or column not in declared or prop.is_pk or prop.is_sensitive
                    or prop.visibility == "hidden" or (obj.code, assignment_target, column) in touched or column in values):
                raise unavailable("属性不在可写白名单或存在重复赋值")
            values[column] = column_value(governed.table.c[column],
                await value_source(session, user, source, params))
            touched.add((obj.code, assignment_target, column))
        await result_scope(session, user, obj, governed, row["id"], values)
        if edit.get("op") == "新建":
            identity = "NEW-" + hashlib.sha256(f"{run.code}:{obj.code}".encode()).hexdigest()[:24]
            if await session.scalar(select(governed.table.c.id).where(governed.table.c.code == identity)):
                raise AppError("新建对象身份已存在", code="IDEMPOTENCY_CONFLICT", status_code=409)
            required = {"code": identity}
            protected = {p.backing_column for p in obj.properties if p.is_sensitive or p.visibility == "hidden"}
            protected |= {"manager", "member_phone", "waybill", "amount", "discount", "price"}
            for column in governed.table.c:
                if (column.name in {"id", "code", "created_by"} or column.nullable or column.default is not None
                        or column.server_default is not None):
                    continue
                if column.name in protected or isinstance(column.type, Numeric) or column.foreign_keys:
                    raise unavailable(f"新建对象必填属性「{column.name}」缺少安全受控映射")
                if column.unique and isinstance(column.type, String):
                    required[column.name] = column_value(column, identity)
                else:
                    original = row[column.name]
                    if isinstance(original, date | datetime):
                        original = original.isoformat()
                    if hasattr(original, "value"):
                        original = original.value
                    required[column.name] = column_value(column, original)
            if apply:
                await session.execute(insert(governed.table).values(**{**required, **values},
                    created_by=user.id))
                await existing_row(session, user, obj, governed, identity)
        elif edit.get("op") == "修改":
            if apply:
                await session.execute(update(governed.table).where(governed.table.c.id == row["id"])
                    .values(**values, updated_at=clock.now(), lock_version=row["lock_version"] + 1))
        else:
            raise unavailable("不支持此业务操作")


async def target_access(session, user, run, definition):
    kind = definition.get("targetKind")
    if not kind:
        if run.obj_kind:
            raise unavailable("无关联对象的动作不能附加目标对象")
        return
    if run.obj_kind != kind or not run.obj_id:
        raise unavailable("动作目标与已发布定义不匹配")
    obj, _ = await object_binding(session, kind)
    await object_operation(session, user, obj, definition)
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
