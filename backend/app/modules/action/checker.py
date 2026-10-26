"""Real, read-only action checks. Missing context and unsupported rules never pass."""

import json
from datetime import date, datetime
from enum import Enum
from typing import Any

from sqlalchemy import select

from app.common.auth import CurrentUser
from app.common.context import ViewContext
from app.common.row_filter import is_deny_all, to_sql
from app.common.sql_guard import RoleAccess, effective_expr, governed_tables
from app.core.errors import AppError, NotFound
from app.modules.action.condition_expr import Unavailable, evaluate
from app.modules.action.models import ActionType, TypeStatus
from app.modules.action.sections import action_def
from app.modules.admin.roles import RoleService
from app.modules.event.ingress_schema import valid_json
from app.modules.static.repo import StaticRepo, definition_draft_items
from app.modules.versioning.repo import ReleaseRepo


def unavailable(message: str) -> AppError:
    return AppError(message, code="ACTION_DEFINITION_UNAVAILABLE", status_code=503)


async def definition(session, code: str, ctx: ViewContext, view: str, cr: str | None) -> dict:
    if ctx.ver:
        release = await ReleaseRepo(session).by_ver_or_404(ctx.ver)
        definitions = (release.snapshot or {}).get("actionTypes")
        if not isinstance(definitions, dict):
            raise unavailable("所选发布版没有动作定义，不能使用当前定义代替")
        value = definitions.get(code)
        if not isinstance(value, dict):
            raise NotFound("所选发布版没有该动作类型")
        if not isinstance(value.get("params"), list) or not isinstance(value.get("conds"), list):
            raise unavailable("所选发布版没有完整动作参数与条件")
        if any(not isinstance(row, dict) for row in [*value["params"], *value["conds"]]):
            raise unavailable("所选发布版的动作定义格式无效")
        result = {
            **value,
            "params": [dict(p) for p in value.get("params", [])],
            "conds": [dict(c) for c in value.get("conds", [])],
        }
    else:
        model = await session.scalar(
            select(ActionType).where(ActionType.code == code).execution_options(populate_existing=True)
        )
        if model is None or model.retired_release_id is not None:
            raise NotFound("动作类型不存在或已下线")
        result = action_def(model)
        for row, param in zip(result["params"], model.params, strict=True):
            row["_ast"] = param.rule_expr
        for row, cond in zip(result["conds"], model.conds, strict=True):
            row["_ast"] = cond.expr_ast
    if result.get("status") == TypeStatus.draft.value:
        raise AppError("草稿动作类型尚未发布", code="TYPE_IS_DRAFT", status_code=409)
    if result.get("status") not in (TypeStatus.published.value, TypeStatus.changing.value):
        raise unavailable("动作状态不可用于试算")
    if view == "draft":
        if ctx.ver or not cr:
            raise AppError(
                "草稿试算须指定当前变更单，不能混合历史版本", code="VALIDATION_FAILED", status_code=422
            )
        for item in await definition_draft_items(session, cr, "action", code):
            section, key, op, after = (item[k] for k in ("section", "key", "op", "after"))
            if section == "def":
                if op not in ("add", "update") or key not in {
                    "name",
                    "api",
                    "group",
                    "targetKind",
                    "owner",
                    "desc",
                    "runners",
                    "approval",
                }:
                    raise unavailable("动作草稿基本字段或操作无效")
                result[key] = after
                if key == "targetKind":
                    if after is None:
                        result["target"], result["targetApi"] = "", ""
                    elif isinstance(after, str) and len(after) <= 64:
                        target = await StaticRepo(session).object_type(after, refresh=True)
                        if target.status.value != "已发布":
                            raise unavailable("草稿关联对象尚未发布")
                        result["target"], result["targetApi"] = target.display_name, target.api_name
                    else:
                        raise unavailable("动作草稿关联对象无效")
                continue
            if section not in ("params", "conds"):
                continue
            field = "api" if section == "params" else "name"
            matches = [i for i, row in enumerate(result[section]) if row.get(field) == key]
            if len(matches) > 1:
                raise unavailable("草稿目标键不唯一，不能确定试算定义")
            if op == "delete":
                result[section] = [row for row in result[section] if row.get(field) != key]
                continue
            if not isinstance(after, dict):
                raise unavailable("草稿行不是有效的结构化定义")
            previous = result[section][matches[0]] if matches else {}
            row = {**previous, **after}
            text_field = "rule" if section == "params" else "expr"
            if row.get(text_field) != previous.get(text_field):
                row["_ast"] = None
            if matches:
                result[section][matches[0]] = row
            else:
                result[section].append(row)
    elif view not in ("", "published"):
        raise AppError("view只能是published或draft", code="VALIDATION_FAILED", status_code=422)
    return result


def structured_params(value: Any) -> dict:
    if value is None:
        return {}
    try:
        if isinstance(value, str):
            if len(value.encode("utf-8")) > 65_536:
                raise ValueError()
            value = json.loads(value)
        if not isinstance(value, dict):
            raise ValueError()
        valid_json(value)
        if len(json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")) > 65_536:
            raise ValueError()
        return value
    except (ValueError, TypeError, RecursionError):
        raise Unavailable("参数必须是有界JSON对象；不能把参数摘要当作实际值") from None


def param_valid(type_: str, value: Any) -> bool:
    if type_ in ("字符串", "文本", "枚举") or type_.startswith("对象引用 ·"):
        return isinstance(value, str)
    if type_ in ("枚举列表",) or type_.startswith("对象引用列表 ·"):
        return isinstance(value, list) and all(isinstance(v, str) for v in value)
    if type_ == "整数":
        return type(value) is int
    if type_ in ("金额", "百分比", "小数", "数值"):
        return type(value) in (int, float)
    if type_ == "布尔":
        return type(value) is bool
    if type_ in ("日期", "日期时间") and isinstance(value, str):
        try:
            (date.fromisoformat if type_ == "日期" else datetime.fromisoformat)(value)
            return True
        except ValueError:
            return False
    raise Unavailable("参数类型尚不支持校验")


class DatabaseSubmissionChecker:
    def __init__(self, session, user: CurrentUser):
        self.session, self.user = session, user

    async def context_references(self, definition: dict, context: dict, bundles: list) -> dict:
        refs: dict[tuple[str, str], Any] = {
            ("当前用户", "编号"): self.user.id,
            ("当前用户", "角色"): sorted({value for b in bundles for value in (b.role.code, b.role.name)}),
        }
        # Alert facts are available only for an actual, uniquely identified current assignee.
        # Alert scopes have no governed mapping yet; only explicit unrestricted view contributes.
        alert_id = context.get("alertId")
        if alert_id and self.user.func.get("alerts") in ("view", "manage"):
            granted = any(
                any(p.get("objType") == "告警" and p.get("view") is True for p in b.role.objs)
                and not b.role.default_deny
                and not any(s.obj_type == "告警" for s in b.scopes)
                for b in bundles
            )
            if granted and isinstance(alert_id, str) and len(alert_id) <= 64:
                try:
                    valid_json(alert_id)
                except ValueError:
                    pass
                else:
                    from app.modules.dynamic.catalog import get_catalog
                    from app.modules.dynamic.repo import AlertRepo

                    alert = await AlertRepo(self.session).get_by_code(alert_id)
                    if alert is not None:
                        person = (await get_catalog(self.session)).person(alert.assignee)
                        if person and person["id"] == self.user.id:
                            refs[("告警", "处理人")] = self.user.id
                            refs[("告警", "状态")] = alert.status.value
                            refs[("告警", "编号")] = alert.code
        kind, code = context.get("objKind"), context.get("objId")
        if (
            not isinstance(kind, str)
            or not isinstance(code, str)
            or not kind
            or not code
            or len(kind) > 64
            or len(code) > 64
            or kind != definition.get("targetKind")
        ):
            return refs
        try:
            valid_json([kind, code])
        except ValueError:
            return refs
        g = next((g for g in governed_tables().values() if g.object_type == kind and not g.inherit), None)
        if g is None:
            return refs
        try:
            obj = await StaticRepo(self.session).object_type(kind, refresh=True)
        except NotFound:
            return refs
        if obj.status.value != "已发布":
            return refs
        roles = []
        for bundle in bundles:
            permission = next((p for p in bundle.role.objs if p.get("objType") == obj.display_name), {})
            scopes = tuple(
                {"prop": s.prop, "op": s.op, "values": s.values}
                for s in bundle.scopes
                if s.obj_type == obj.display_name
            )
            roles.append(
                RoleAccess(
                    view=permission.get("view") is True, default_deny=bundle.role.default_deny, scopes=scopes
                )
            )
        expr = effective_expr(kind, roles)
        if is_deny_all(expr):
            return refs
        props = [
            p
            for p in obj.properties
            if not p.is_sensitive
            and p.visibility != "hidden"
            and p.backing_column
            and p.backing_column in g.table.c
            and p.backing_column in {getattr(c, "name", None) for c in g.columns.values()}
        ]
        # Only declared, real non-sensitive bindings; no arbitrary table/field from an expression.
        if "code" not in g.table.c or not props:
            return refs
        stmt = select(*(g.table.c[p.backing_column].label(p.api_name) for p in props)).where(
            g.table.c.code == code
        )
        if expr is not None:
            stmt = stmt.where(to_sql(expr, g.columns, self.user.attrs, g.links))
        rows = (await self.session.execute(stmt.limit(2))).mappings().all()
        if len(rows) != 1:
            return refs
        for prop in props:
            value = rows[0][prop.api_name]
            if isinstance(value, Enum):
                value = value.value
            if value is None or isinstance(value, str | int | float | bool):
                for name in (obj.display_name, obj.api_name, "上下文对象"):
                    for field in (prop.display_name, prop.api_name):
                        refs[(name, field)] = value
        return refs

    async def run(self, action: dict, context: dict[str, Any]) -> dict:
        failed: list[dict[str, str]] = []
        conds, params_def = action.get("conds"), action.get("params")
        if not isinstance(conds, list) or not isinstance(params_def, list):
            raise unavailable("动作定义缺少结构化参数或条件")
        try:
            params = structured_params(context.get("params"))
        except Unavailable as exc:
            return {"passed": False, "checked": len(conds), "failed": [{"name": "参数", "fail": str(exc)}]}
        bundles, _ = await RoleService(self.session).effective_access(self.user.id)
        # Only a declared alert-reference parameter can supply missing alert context.
        if not context.get("alertId"):
            alert_params = [
                p.get("api") for p in params_def if isinstance(p, dict) and p.get("type") == "对象引用 · 告警"
            ]
            values = {params[key] for key in alert_params if isinstance(params.get(key), str)}
            if len(values) == 1:
                context = {**context, "alertId": values.pop()}
        refs = await self.context_references(action, context, bundles)
        name_counts: dict[str, int] = {}
        for p in params_def:
            if (
                not isinstance(p, dict)
                or not isinstance(p.get("api"), str)
                or not isinstance(p.get("type"), str)
                or not isinstance(p.get("name"), str)
                or type(p.get("required", False)) is not bool
            ):
                raise unavailable("动作参数定义无效")
            for name in {p["api"], p.get("name", p["api"])}:
                name_counts[name] = name_counts.get(name, 0) + 1
        if set(params) - {p["api"] for p in params_def}:
            failed.append({"name": "参数", "fail": "包含未声明参数"})
        for p in params_def:
            api, name = p["api"], p.get("name") or p["api"]
            value = params.get(api)
            if value is None or value == "":
                if p.get("required"):
                    failed.append({"name": name, "fail": "缺少必填参数"})
                continue
            try:
                if not param_valid(p["type"], value):
                    raise Unavailable("参数类型不符合已登记定义")
                for alias in (api, name):
                    if name_counts[alias] == 1:
                        refs[("参数", alias)] = value
            except Unavailable as exc:
                failed.append({"name": name, "fail": str(exc)})
        for p in params_def:
            rule = p.get("rule")
            if params.get(p["api"]) in (None, ""):
                continue
            if rule in (None, "", "—") and p.get("_ast") is None:
                continue
            name = p.get("name") or p["api"]
            try:
                if p.get("_ast") is not None:
                    raise Unavailable("参数AST格式尚未定义，不能按通过处理")
                if not evaluate(rule, refs):
                    failed.append({"name": name, "fail": "参数校验未通过"})
            except Unavailable as exc:
                failed.append({"name": name, "fail": str(exc)})
        for cond in conds:
            if (
                not isinstance(cond, dict)
                or not isinstance(cond.get("name"), str)
                or cond.get("kind") not in ("参数", "当前用户", "上下文对象")
            ):
                raise unavailable("提交条件定义无效")
            try:
                if cond.get("_ast") is not None:
                    raise Unavailable("条件AST格式尚未定义，不能按通过处理")
                if not evaluate(cond.get("expr") or "", refs):
                    message = cond.get("fail")
                    failed.append(
                        {
                            "name": cond["name"],
                            "fail": message if isinstance(message, str) and message else "条件未通过",
                        }
                    )
            except Unavailable as exc:
                failed.append({"name": cond["name"], "fail": str(exc)})
        return {"passed": not failed, "checked": len(conds), "failed": failed}

    async def check(self, action_type_code: str, context: dict[str, Any]) -> list[dict[str, str]]:
        action = await definition(self.session, action_type_code, ViewContext(), "published", None)
        return (await self.run(action, context))["failed"]
