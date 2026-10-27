"""Workspace-local relation candidates and validation, including saved objects in the same CR."""

import re

from sqlalchemy import select

from app.core.errors import AppError
from app.modules.static.models import CrItem, CrTarget, ObjectType, Property


async def modeling_objects(session, cr_id=None):
    rows = await session.execute(select(
        ObjectType.code, ObjectType.display_name, ObjectType.api_name, ObjectType.obj_group, ObjectType.source_table
    ).order_by(ObjectType.seq, ObjectType.id))
    objects = {code: {"id": code, "name": name, "api": api, "group": group, "table": table or ""}
               for code, name, api, group, table in rows}
    properties = {code: {} for code in objects}
    for code, api, name, typ in await session.execute(
        select(ObjectType.code, Property.api_name, Property.display_name, Property.data_type)
        .join(Property, Property.object_type_id == ObjectType.id).order_by(Property.seq, Property.id)
    ):
        properties[code][api] = {"api": api, "name": name, "type": typ}
    if cr_id is not None:
        for target in await session.scalars(select(CrTarget).where(
            CrTarget.cr_id == cr_id, CrTarget.target_kind == "object", CrTarget.is_new.is_(True)
        ).order_by(CrTarget.seq, CrTarget.id)):
            objects.setdefault(target.target_id, {"id": target.target_id, "name": target.target_name or target.target_id,
                "api": target.target_id, "group": "", "table": "", "draft": True})
            properties.setdefault(target.target_id, {})
        for item in await session.scalars(select(CrItem).where(
            CrItem.cr_id == cr_id, CrItem.target_kind == "object"
        ).order_by(CrItem.seq, CrItem.id)):
            if item.target_id not in objects:
                continue
            if item.section in ("def", "config") and item.item_key in ("name", "api", "group", "table"):
                if item.op != "delete":
                    if not isinstance(item.after, str):
                        raise AppError("对象草稿字段须为文本，无法读取关系候选", code="VALIDATION_FAILED", status_code=422)
                    objects[item.target_id][item.item_key] = item.after
            elif item.section == "properties":
                if item.op == "delete":
                    properties[item.target_id].pop(item.item_key, None)
                elif isinstance(item.after, dict):
                    properties[item.target_id][item.item_key] = {
                        **properties[item.target_id].get(item.item_key, {}), **item.after, "api": item.item_key
                    }
    return list(objects.values()), {code: [{"api": prop["api"], "name": prop.get("name", prop["api"]), "type": prop.get("type", "string")} for prop in props.values()] for code, props in properties.items()}


def link_error(data, objects, properties, *, previous_via=None, is_new=False):
    for key, maximum in (("name", 64), ("api", 64), ("from", 64), ("to", 64),
                         ("fromProp", 64), ("toProp", 64), ("via", 64), ("reverse", 64)):
        value = data.get(key, "")
        if key == "via" and value is None:
            continue
        if not isinstance(value, str) or len(value) > maximum or any(ord(char) < 32 or 0xD800 <= ord(char) <= 0xDFFF for char in value):
            return f"关系字段 {key} 须为至多{maximum}字的有效文本"
    if not isinstance(data.get("desc", ""), str) or "\x00" in data.get("desc", ""):
        return "关系说明须为有效文本"
    if not data.get("name", "").strip():
        return "请填写关系名称"
    if (is_new or data.get("api")) and not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", data.get("api", "")):
        return "关系API名需字母开头，仅含字母、数字和下划线，最多64位"
    by_code = {row["id"]: row for row in objects}
    if data.get("from") not in by_code or data.get("to") not in by_code:
        return "关系两端须选择当前本体中已存在或同单已保存的对象类型"
    if data.get("card") not in ("1 : 1", "1 : N", "N : 1", "N : M"):
        return "关系基数不合法"
    if not isinstance(data.get("nullable", False), bool):
        return "允许为空须为布尔值"
    if data["card"] == "N : M":
        via = data.get("via")
        if not via or via not in by_code and via not in {row.get("table") for row in objects} and via != previous_via:
            return "多对多关系须选择当前本体的中间对象；已有中间表可原样保留"
    else:
        endpoints = []
        for endpoint, key in (("from", "fromProp"), ("to", "toProp")):
            prop = next((row for row in properties.get(data[endpoint], []) if row["api"] == data.get(key)), None)
            if prop is None:
                return f"关系{('源' if endpoint == 'from' else '目标')}端属性不存在，请重新选择"
            endpoints.append("integer" if prop.get("type") == "int" else prop.get("type"))
        if not endpoints[0] or endpoints[0] != endpoints[1]:
            return "关系两端属性类型须一致"
    return None
