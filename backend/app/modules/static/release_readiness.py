"""Read-only structural checks; never connect to a source or simulate publication."""

from sqlalchemy import select

from app.common.auth import check_func
from app.common.ontology import require_ontology_id
from app.core.errors import AppError
from app.modules.static import shapes
from app.modules.static.metric_trial import compile_metric, execution_bindings, metric_definition
from app.modules.static.models import ObjectType
from app.modules.static.writeback import _prop_type


def object_structure(values, properties):
    """Only verify saved definition structure, never source contents or uniqueness."""
    pk = values.get("pk")
    if not pk:
        return "警告", "尚未设置主键属性，未完成实例读取映射；未验证源数据唯一性或连通性"
    if not isinstance(pk, str) or pk not in properties:
        return "阻断", "主键须引用当前定义中真实存在的属性"
    for api, prop in properties.items():
        if not isinstance(api, str) or not api.strip() or not isinstance(prop, dict):
            return "阻断", "属性映射结构不合法"
        try:
            _prop_type(prop.get("type", "string"), api)
        except AppError as error:
            return "阻断", error.message
        source = prop.get("source")
        if source is not None and not isinstance(source, str):
            return "阻断", f"属性{api}来源列须为文本"
    source, table = values.get("source"), values.get("table")
    if any(value is not None and not isinstance(value, str) for value in (source, table)):
        return "阻断", "来源与源表须为文本"
    if bool(source) != bool(table):
        return "警告", "来源连接与源表尚未同时设置，未完成映射；未连接源库"
    if source and table and not str(properties[pk].get("source") or "").strip():
        return "阻断", f"主键属性{pk}尚未指定来源列"
    missing = [api for api, prop in properties.items() if not str(prop.get("source") or "").strip()]
    if missing:
        return "警告", f"{len(missing)}个属性尚无来源列映射；仅已核对主键引用和属性类型，未连接源库"
    if not source or not table:
        return "警告", "主键与属性结构已核对，尚未绑定来源连接及源表；未连接源库"
    return "通过", "主键引用、属性类型及来源列文本结构已核对；未验证源数据唯一性、列存在性或连通性"


async def release_readiness(session, cr, targets, items, user):
    results = {}
    objects = (
        list(await session.scalars(select(ObjectType)))
        if any(t.target_kind in ("object", "metric") for t in targets)
        else []
    )
    by_code = {obj.code: obj for obj in objects}
    object_results = []
    samples = []
    for target in targets:
        if target.target_kind != "object":
            continue
        current = by_code.get(target.target_id)
        if current is None and not target.is_new:
            object_results.append(("阻断", f"{target.target_id}：对象不存在"))
            continue
        values = (
            {"pk": current.pk_property, "source": current.datasource_name, "table": current.source_table}
            if current
            else {}
        )
        properties = {prop.api_name: shapes.prop(prop) for prop in current.properties} if current else {}
        for item in items:
            if item.target_kind != "object" or item.target_id != target.target_id:
                continue
            if item.section in ("def", "config") and item.item_key in values.keys() | {
                "pk",
                "source",
                "table",
            }:
                if item.op != "delete":  # Publication keeps these scalar fields on delete.
                    values[item.item_key] = item.after
            elif item.section == "properties":
                if item.op == "delete":
                    properties.pop(item.item_key, None)
                elif isinstance(item.after, dict):
                    properties[item.item_key] = {**properties.get(item.item_key, {}), **item.after}
                else:
                    object_results.append(("阻断", f"{target.target_id}.{item.item_key}：属性须为对象"))
        result, note = object_structure(values, properties)
        object_results.append((result, f"{target.target_id}：{note}"))
        if result == "阻断":
            samples.append(("不适用", f"{target.target_id}：定义结构未通过，未执行主键抽检"))
        else:
            from app.modules.static.pk_sample import sample_primary_key

            sampled, note = await sample_primary_key(
                session, target.target_id, current, values, properties, user
            )
            samples.append((sampled, f"{target.target_id}：{note}"))
    if object_results:
        severity = {"通过": 0, "警告": 1, "阻断": 2}
        results["V1"] = (
            max(object_results, key=lambda row: severity[row[0]])[0],
            "；".join(note for _, note in object_results),
        )
    if samples:
        severity = {"不适用": 0, "通过": 1, "警告": 2, "阻断": 3}
        result = max(samples, key=lambda row: severity[row[0]])[0]
        if result == "通过" and any(row[0] == "不适用" for row in samples):
            result = "警告"  # A checked target cannot certify a different unexamined target.
        results["V2"] = result, "；".join(note for _, note in samples)
    metric_results = []
    object_edits = any(target.target_kind == "object" for target in targets)
    for target in targets:
        if target.target_kind != "metric":
            continue
        if user is None:
            metric_results.append(("警告", "缺少可核验的指标查看身份，未执行映射编译"))
            continue
        if object_edits:
            metric_results.append(
                ("警告", "同单含对象结构改动，尚未认证发布后的属性映射；请在对象发布后复核指标")
            )
            continue
        try:
            check_func(user, "metrics", "view")
            definition = await metric_definition(session, target.target_id, user, cr_id=cr.code)
            compile_metric(
                definition,
                objects,
                require_ontology_id(session),
                await execution_bindings(session, objects, definition),
            )
        except AppError as error:
            mapping_errors = {
                "OBJECT_NOT_FOUND",
                "OBJECT_AMBIGUOUS",
                "OBJECT_UNBOUND",
                "PROPERTY_NOT_FOUND",
                "PROPERTY_AMBIGUOUS",
                "PROPERTY_UNBOUND",
                "PROPERTY_RESTRICTED",
            }
            issues = (error.details or {}).get("issues") or []
            blocked = (
                any(issue.get("reason") in mapping_errors for issue in issues) or error.status_code == 404
            )
            metric_results.append(
                (
                    "阻断" if blocked else "警告",
                    f"{target.target_id}：{error.message}；仅定义编译，未执行源查询或EXPLAIN",
                )
            )
        else:
            metric_results.append(
                (
                    "通过",
                    f"{target.target_id}：已按当前本体及本单指标草稿核对对象、属性、时间和过滤映射；未执行源查询或EXPLAIN",
                )
            )
    if metric_results:
        severity = {"通过": 0, "警告": 1, "阻断": 2}
        results["V5"] = (
            max(metric_results, key=lambda row: severity[row[0]])[0],
            "；".join(note for _, note in metric_results),
        )
    return results
