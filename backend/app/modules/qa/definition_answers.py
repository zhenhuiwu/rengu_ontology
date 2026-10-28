"""只使用当前账号可见的本体定义生成可追溯的问数回答。"""

import json

from sqlalchemy import select

from app.common.context import ViewContext
from app.modules.action.checker import definition
from app.modules.static.capabilities import capabilities
from app.modules.static.service import StaticService
from app.modules.static.models import Metric


async def evidence(session, user, request=None):
    # shortcut: 最多提供 80 条定义，规模扩大时改为按问题检索并分页。
    try:
        async with session.begin_nested():
            catalog = await capabilities(session, user, request, readable_only=True)
    except Exception:
        catalog = {}
    result = {}

    def add(kind, item, text):
        if item.get("id") and text and len(result) < 80:
            result[f"{kind}:{item['id']}"] = text[:3000]

    objects = {o["id"]: o for o in catalog.get("objects", [])}
    for obj in objects.values():
        props = "、".join(p["name"] for p in obj.get("properties", [])) or "未配置可见属性"
        add(
            "object",
            obj,
            f"对象类型「{obj['name']}」：{obj.get('description') or '未填写说明'}；可见属性：{props}。",
        )
    for action in catalog.get("actions", []):
        if action.get("rowAccess") == "none":
            continue
        try:
            async with session.begin_nested():
                value = await definition(session, action["id"], ViewContext(), "published", None)
        except Exception:
            continue
        target = objects.get(action.get("object"), {}).get("name") or action.get("objectApi") or "未指定"
        conds = [f"{c.get('name', '')}：{c.get('expr') or '未填写表达式'}" for c in value.get("conds", [])]
        params = [
            f"{p.get('name', '')}（{p.get('type', '')}，{'必填' if p.get('required') else '选填'}{'，' + p['rule'] if p.get('rule') else ''}）"
            for p in value.get("params", [])
        ]
        text = (
            f"动作「{action['name']}」（{action['id']}，{action['version']}），目标对象：{target}。"
            f"提交条件：{'；'.join(conds) if conds else '未配置额外提交条件'}。"
            f"参数：{'；'.join(params) if params else '未配置参数'}。提交时还需通过目标对象、权限等系统校验。"
        )
        add("action", action, text)
    metric_ids = [item["id"] for item in catalog.get("metrics", [])]
    if metric_ids:
        for metric in await session.scalars(select(Metric).where(Metric.code.in_(metric_ids))):
            add(
                "metric",
                {"id": metric.code},
                f"指标「{metric.display_name}」（{metric.code}，{metric.version}）：计算公式 {metric.formula or '未配置'}，单位 {metric.unit or '未配置'}。",
            )
    service = StaticService(session, ViewContext())
    if user.func.get("link-types", "none") in ("view", "manage"):
        for link in await service._links():
            if link.get("status") == "已发布" and link.get("from") in objects and link.get("to") in objects:
                add(
                    "link",
                    link,
                    f"关系「{link['name']}」：{objects[link['from']]['name']} → {objects[link['to']]['name']}；{link.get('desc') or '未填写说明'}。",
                )
    if user.func.get("dimensions", "none") in ("view", "manage"):
        for dim in await service._dims():
            if dim.get("status") == "已发布" and (not dim.get("object") or dim["object"] in objects):
                add(
                    "dimension",
                    dim,
                    f"{dim.get('kind') or '维度'}「{dim['name']}」：{dim.get('desc') or '未填写说明'}；来源属性：{dim.get('attr') or '未配置'}。",
                )
    for kind, name_key, label in (
        ("functions", "title", "函数"),
        ("rules", "name", "规则"),
        ("events", "name", "事件类型"),
    ):
        for item in catalog.get(kind, []):
            if item.get("rowAccess") == "none":
                continue
            desc = item.get("description") or item.get("desc") or "未填写说明"
            add(kind, item, f"{label}「{item.get(name_key) or item.get('name')}」（{item['id']}）：{desc}。")
    if user.func.get("glossary", "none") in ("view", "manage"):
        for term in await service._terms():
            if term.get("status") == "已发布":
                add(
                    "knowledge",
                    term,
                    f"业务知识「{term['name']}」（{term['id']}）：{term.get('definition') or '未填写定义'}。计算说明：{term.get('calc') or '未填写'}。",
                )
    return result


def prompt_lines(items):
    return [json.dumps({"id": key, "内容": value}, ensure_ascii=False) for key, value in items.items()]


def snapshot(ids, items):
    if (
        not isinstance(ids, list)
        or not ids
        or len(ids) > 5
        or any(not isinstance(i, str) or i not in items for i in ids)
    ):
        return None
    chosen = list(dict.fromkeys(ids))
    text = "\n".join(items[i] for i in chosen) + "\n依据：当前本体中已发布的定义。"
    return {
        "answer": {
            "card": "table",
            "text": text,
            "canPin": False,
            "table": {
                "id": "definitions",
                "title": "本体定义",
                "columns": [{"key": "c0", "title": "定义"}],
                "items": [{"c0": text}],
                "total": 1,
                "summary": "共1行",
                "paged": False,
            },
        }
    }
