"""静态定义页的只读SQL分页；完整编辑目录和发布写回仍走原公开接口。"""

from sqlalchemy import Integer, String, case, cast, exists, false, func, literal, or_, select, true, union_all
from sqlalchemy.orm import raiseload, selectinload

from app.common.auth import check_func
from app.common.pagination import Page
from app.core.errors import AppError, NotFound
from app.db.dialects import get_adapter
from app.db.types import JsonType
from app.modules.static import shapes
from app.modules.static.models import (
    ChangeRequest,
    CrItem,
    CRStatus,
    CrTarget,
    DefStatus,
    Dimension,
    DimKind,
    DimValue,
    DimValueStatus,
    Knowledge,
    KnowledgeStatus,
    KnowledgeType,
    LinkType,
    Metric,
    ObjectType,
    Property,
)
from app.modules.versioning.models import OntologyRelease


async def object_detail(svc, code):
    data = (await snapshot(svc, "objectTypes")).get(code)
    if data is None:
        raise NotFound("对象类型在该版本不存在")
    props = list((await snapshot(svc, "properties")).get(code, {}).values())
    out = {
        **data,
        "meta": {key: data.get(key) for key in ("domain", "desc", "conflict", "lifecycle", "sync", "qs")},
        "brief": {
            **{key: data[key] for key in ("name", "api", "group", "source", "owner", "pk") if key in data},
            "key": code,
        },
        "db": "--",
    }
    out["props"] = props
    return out


async def object_links(svc, code):
    if svc.ver:
        objects = await snapshot(svc, "objectTypes")
        if code not in objects:
            raise NotFound("对象类型在该版本不存在")
        links = [
            d
            for d in (await snapshot(svc, "links")).values()
            if d["from"] == code and d["status"] == "已发布"
        ]
        names = {key: d["name"] for key, d in objects.items()}
    else:
        if await svc.session.scalar(select(ObjectType.id).where(ObjectType.code == code)) is None:
            raise NotFound("对象类型不存在")
        links = [
            shapes.link(row)
            for row in await svc.session.scalars(
                select(LinkType)
                .where(LinkType.from_code == code, LinkType.status == DefStatus.published)
                .order_by(LinkType.seq, LinkType.id)
            )
        ]
        ids = {key for row in links for key in (row["from"], row["to"])}
        names = (
            dict(
                (
                    await svc.session.execute(
                        select(ObjectType.code, ObjectType.display_name).where(ObjectType.code.in_(ids))
                    )
                ).all()
            )
            if ids
            else {}
        )
    return [
        {**row, "fromName": names.get(row["from"], row["from"]), "toName": names.get(row["to"], row["to"])}
        for row in links
    ]


def json(value):
    return literal(value, type_=JsonType)


def enum_is(column, enum, value):
    try:
        return column == enum(value)
    except ValueError:
        return false()


def contains_array(svc, column, word, *, exact=False):
    items = get_adapter(svc.session).json_array_rows(column, as_text=True)
    match = items.c.value == word if exact else items.c.value.contains(word, autoescape=True)
    return exists(select(1).select_from(items).where(match))


async def snapshot(svc, section):
    return (await svc._section(section)) or {}


async def object_list(svc, kw, p, focus=None):
    if svc.ver:
        from app.modules.static.service import paginate

        sec, props = await snapshot(svc, "objectTypes"), await snapshot(svc, "properties")
        rows = []
        for code, data in sec.items():
            if kw and kw not in data.get("name", "") and kw.lower() not in data.get("api", "").lower():
                continue
            rows.append(
                {
                    **{k: data[k] for k in ("id", "name", "api", "group", "table") if k in data},
                    "id": code,
                    "status": data.get("status", "已发布"),
                    "props": len(props.get(code, [])),
                    "count": "--",
                }
            )
        return paginate(rows, p, focus)
    stmt = select(ObjectType).options(raiseload("*")).order_by(ObjectType.seq, ObjectType.id)
    if kw:
        stmt = stmt.where(
            or_(
                ObjectType.display_name.contains(kw, autoescape=True),
                ObjectType.api_name.icontains(kw, autoescape=True),
            )
        )
    rows, total, actual = await svc.repo.page(
        stmt, p, focus_id=focus, key=ObjectType.code, order=(ObjectType.seq, ObjectType.id)
    )
    counts = await svc.repo.prop_counts([row.id for row in rows])
    return Page.of(
        [shapes.object_list_item(row, counts.get(row.id, 0)) for row in rows], total, actual
    ).model_dump()


async def metric_list(svc, kw, group, p, focus=None):
    if svc.ver:
        from app.modules.static.service import paginate

        rows = [
            {k: v for k, v in data.items() if k not in ("formula", "dimNames", "versionRows")}
            for data in (await snapshot(svc, "metrics")).values()
        ]
        rows = [
            r
            for r in rows
            if (not group or group == "全部" or r.get("group") == group)
            and (not kw or kw in r.get("name", "") or kw in r.get("api", ""))
        ]
        return paginate(rows, p, focus)
    stmt = select(Metric).order_by(Metric.seq, Metric.id)
    if group and group != "全部":
        stmt = stmt.where(Metric.metric_group == group)
    if kw:
        stmt = stmt.where(
            or_(
                Metric.display_name.contains(kw, autoescape=True),
                Metric.api_name.contains(kw, autoescape=True),
            )
        )
    rows, total, actual = await svc.repo.page(
        stmt, p, focus_id=focus, key=Metric.code, order=(Metric.seq, Metric.id)
    )
    return Page.of([shapes.metric_list_item(row) for row in rows], total, actual).model_dump()


async def dim_list(svc, q, kind, group, p, focus):
    if svc.ver:
        from app.modules.static.service import paginate

        dims = list((await snapshot(svc, "dimensions")).values())
        rows = [
            d
            for d in dims
            if (not kind or kind == "all" or d["kind"] == kind)
            and (not group or d["group"] == group)
            and (
                not q
                or q in d["name"]
                or q in d["api"]
                or q in d["objectName"]
                or any(q in v["name"] or q in v["synonyms"] for v in d["values"])
            )
        ]
        result = paginate(rows, p, focus)
        result["items"] = [svc._dim_summary(row) for row in result["items"]]
        result["groups"] = list(dict.fromkeys(d["group"] for d in dims))
        return result
    stmt = svc.repo.dimension_query().order_by(Dimension.seq, Dimension.id)
    if kind and kind != "all":
        stmt = stmt.where(enum_is(Dimension.kind, DimKind, kind))
    if group:
        stmt = stmt.where(Dimension.dim_group == group)
    if q:
        value_match = exists(
            select(1).where(
                DimValue.dimension_id == Dimension.id,
                or_(
                    DimValue.name.contains(q, autoescape=True),
                    contains_array(svc, DimValue.synonyms, q, exact=True),
                ),
            )
        )
        stmt = stmt.where(
            or_(
                Dimension.display_name.contains(q, autoescape=True),
                Dimension.api_name.contains(q, autoescape=True),
                Dimension.object_name.contains(q, autoescape=True),
                value_match,
            )
        )
    rows, total, actual = await svc.repo.page(
        stmt, p, focus_id=focus, key=Dimension.code, order=(Dimension.seq, Dimension.id)
    )
    ids = [d.id for d in rows]
    counts = (
        dict(
            (
                await svc.session.execute(
                    select(DimValue.dimension_id, func.count())
                    .where(
                        DimValue.dimension_id.in_(ids),
                        or_(DimValue.until_release.is_(None), DimValue.until_release == ""),
                        or_(DimValue.draft_cr.is_(None), DimValue.draft_cr == ""),
                    )
                    .group_by(DimValue.dimension_id)
                )
            ).all()
        )
        if ids
        else {}
    )
    items = [{**svc._dim_head(shapes.dimension(d, [], [])), "valueCount": counts.get(d.id, 0)} for d in rows]
    groups = list(
        await svc.session.scalars(
            select(Dimension.dim_group)
            .group_by(Dimension.dim_group)
            .order_by(func.min(Dimension.seq), Dimension.dim_group)
        )
    )
    return {**Page.of(items, total, actual).model_dump(), "groups": groups}


def shown_expr(svc):
    entries = get_adapter(svc.session).json_array_rows(Knowledge.history)
    draft = exists(
        select(1)
        .select_from(entries)
        .where(
            entries.c.value["status"].as_string() == "草稿",
            func.coalesce(entries.c.value["cr"].as_string(), "") != "",
        )
    )
    return case(
        (Knowledge.status == KnowledgeStatus.disabled, "已停用"),
        (or_(Knowledge.status == KnowledgeStatus.draft, draft), "草稿中"),
        else_="已发布",
    )


async def knowledge_list(svc, q, kind, status, p, focus):
    if svc.ver:
        from app.modules.static.service import paginate

        rows = []
        for data in (await snapshot(svc, "terms")).values():
            shown = svc._shown(data)
            if (
                (not kind or data["type"] == kind)
                and (not status or shown == status)
                and (not q or q in data["name"] or q in data["id"] or any(q in s for s in data["synonyms"]))
            ):
                rows.append({**data, "shownStatus": shown})
        return paginate(rows, p, focus)
    stmt = select(Knowledge).options(selectinload(Knowledge.applies)).order_by(Knowledge.seq, Knowledge.id)
    if kind:
        stmt = stmt.where(enum_is(Knowledge.type, KnowledgeType, kind))
    if status:
        stmt = stmt.where(shown_expr(svc) == status)
    if q:
        stmt = stmt.where(
            or_(
                Knowledge.name.contains(q, autoescape=True),
                Knowledge.code.contains(q, autoescape=True),
                contains_array(svc, Knowledge.synonyms, q),
            )
        )
    rows, total, actual = await svc.repo.page(
        stmt, p, focus_id=focus, key=Knowledge.code, order=(Knowledge.seq, Knowledge.id)
    )
    items = [shapes.knowledge(row, with_runtime=False) for row in rows]
    return Page.of([{**data, "shownStatus": svc._shown(data)} for data in items], total, actual).model_dump()


async def link_list(svc, q, src, status, p, focus):
    if svc.ver:
        from app.modules.static.service import paginate

        objects = await snapshot(svc, "objectTypes")
        rows = [
            d
            for d in (await snapshot(svc, "links")).values()
            if (not src or d["from"] == src)
            and (not status or d["status"] == status)
            and (
                not q
                or q in d["name"]
                or q in d.get("api", d["id"])
                or q in objects.get(d["from"], {}).get("name", d["from"])
                or q in objects.get(d["to"], {}).get("name", d["to"])
            )
        ]
        return paginate(rows, p, focus)
    stmt = select(LinkType).options(selectinload(LinkType.quality)).order_by(LinkType.seq, LinkType.id)
    if src:
        stmt = stmt.where(LinkType.from_code == src)
    if status:
        stmt = stmt.where(enum_is(LinkType.status, DefStatus, status))
    if q:
        object_codes = select(ObjectType.code).where(ObjectType.display_name.contains(q, autoescape=True))
        stmt = stmt.where(
            or_(
                LinkType.display_name.contains(q, autoescape=True),
                func.coalesce(LinkType.api_name, LinkType.code).contains(q, autoescape=True),
                LinkType.from_code.in_(object_codes),
                LinkType.to_code.in_(object_codes),
            )
        )
    rows, total, actual = await svc.repo.page(
        stmt, p, focus_id=focus, key=LinkType.code, order=(LinkType.seq, LinkType.id)
    )
    return Page.of([shapes.link(row) for row in rows], total, actual).model_dump()


async def draft(svc, cr_code, kind, code):
    if not cr_code:
        return None
    if svc.ctx.read_only:
        raise AppError("历史/预览模式不能叠加当前草稿", code="READONLY", status_code=403)
    if svc.user is None:
        raise AppError("缺少真实用户身份", code="NO_PERMISSION", status_code=403)
    check_func(svc.user, "releases", "view")
    cr = await svc.session.scalar(select(ChangeRequest).where(ChangeRequest.code == cr_code))
    if cr is None:
        raise NotFound("变更单不存在")
    if cr.status == CRStatus.published:
        raise AppError("变更单已发布，不能作为当前草稿", code="CR_STATE", status_code=409)
    target = await svc.session.scalar(
        select(CrTarget).where(
            CrTarget.cr_id == cr.id, CrTarget.target_kind == kind, CrTarget.target_id == code
        )
    )
    if target is None:
        raise NotFound("目标不在该变更单中")
    for item in await svc.session.scalars(
        select(CrItem).where(
            CrItem.cr_id == cr.id,
            CrItem.target_kind == kind,
            CrItem.target_id == code,
            CrItem.section.in_(["properties", "values", "mappings"]),
            CrItem.op != "delete",
        )
    ):
        if not isinstance(item.after, dict):
            raise AppError("该段草稿行必须为对象，无法计算分页", code="VALIDATION_FAILED", status_code=422)
        if (
            item.section == "properties"
            and "order" in item.after
            and (type(item.after["order"]) is not int or not 0 <= item.after["order"] <= 2147483647)
        ):
            raise AppError("草稿属性顺序必须为有效非负整数", code="VALIDATION_FAILED", status_code=422)
    return cr, target


async def target(svc, model, code, cr_code, kind):
    saved = await draft(svc, cr_code, kind, code)
    row = await svc.session.scalar(select(model).where(model.code == code).options(raiseload("*")))
    if row is None and not (saved and saved[1].is_new):
        raise NotFound("目标定义不存在", details={"id": code})
    return row, saved


def overlay(svc, base, saved, kind, code, section, key_field, defaults, *, tags=False):
    """先按真实目标/段合并数据库CR，再对候选关系SQL筛选和分页。"""
    if not saved:
        return base
    adapter = get_adapter(svc.session)
    cr = saved[0]
    changes = (
        select(CrItem.id, CrItem.item_key.label("key"), CrItem.after, CrItem.op, CrItem.seq)
        .where(
            CrItem.cr_id == cr.id,
            CrItem.target_kind == kind,
            CrItem.target_id == code,
            CrItem.section == section,
        )
        .subquery()
    )

    def merged(data):
        flags = {key_field: changes.c.key}
        if tags:
            flags.update(
                diff=case((changes.c.op == "add", "新增"), else_="修改"),
                diffTag=case((changes.c.op == "add", "新增"), else_="修改"),
                draft=case((changes.c.op == "add", cr.code), else_=None),
            )
        return adapter.json_merge(data, changes.c.after, adapter.json_object(**flags))

    keep = (
        select(
            base.c.key,
            base.c.seq,
            case((changes.c.id.is_not(None), merged(base.c.data)), else_=base.c.data).label("data"),
        )
        .select_from(base.outerjoin(changes, changes.c.key == base.c.key))
        .where(or_(changes.c.id.is_(None), changes.c.op != "delete"))
    )
    maximum = select(func.coalesce(func.max(base.c.seq), 0)).scalar_subquery()
    added = select(
        changes.c.key, (maximum + changes.c.seq + 1).label("seq"), merged(json(defaults)).label("data")
    ).where(changes.c.op != "delete", ~exists(select(1).where(base.c.key == changes.c.key)))
    return union_all(keep, added).subquery()


def property_rows(svc, object_id, saved, code):
    from app.modules.static.explorer import DISPLAY_FIELDS

    a = get_adapter(svc.session)
    body = a.json_object(
        api=Property.api_name,
        name=Property.display_name,
        type=Property.data_type,
        group=Property.prop_group,
        source=Property.source_column,
        desc=Property.description,
        **{key: getattr(Property, col) for key, col in DISPLAY_FIELDS.items()},
    )
    # 布尔标记原契约只在true时出现；nullable显示字段仍保留null。
    for name, column in (
        ("pk", Property.is_pk),
        ("title", Property.is_title),
        ("dim", Property.is_dimension),
        ("sensitive", Property.is_sensitive),
        ("enumName", Property.enum_name),
    ):
        body = a.json_merge(
            body,
            case(
                (
                    column.is_(True) if name != "enumName" else column.is_not(None),
                    a.json_object(**{name: column}),
                ),
                else_=json({}),
            ),
        )
    body = a.json_merge(
        body,
        a.json_object(
            _mapColumn=Property.map_column, _srcType=Property.src_type, _transform=Property.transform
        ),
    )
    base = (
        select(Property.api_name.label("key"), Property.seq.label("seq"), body.label("data"))
        .where(Property.object_type_id == object_id)
        .subquery()
    )
    defaults = {
        "name": "",
        "api": "",
        "type": "string",
        "group": "其他",
        "source": "--",
        "desc": "",
        "_mapColumn": "",
        "_srcType": "",
        "_transform": "",
    }
    return overlay(svc, base, saved, "object", code, "properties", "api", defaults)


async def object_properties(svc, code, kw, p, cr_code=None, *, mappings=False):
    if svc.ver:
        from app.modules.static.service import paginate

        if cr_code:
            await draft(svc, cr_code, "object", code)
        sec = await snapshot(svc, "objectTypes")
        if code not in sec:
            raise NotFound("对象类型在该版本不存在")
        items = list((await snapshot(svc, "properties")).get(code, {}).values())
        if mappings:
            items = [
                {
                    "prop": row["name"],
                    "col": row.get("source", ""),
                    "srcType": row.get("type", ""),
                    "transform": "",
                }
                for row in items
            ]
        elif kw:
            items = [r for r in items if kw in r["name"] or kw in r["api"]]
        result = paginate(items, p)
        if not mappings:
            result["diff"] = []
        return result
    parent, saved = await target(svc, ObjectType, code, cr_code, "object")
    rows = property_rows(svc, parent.id if parent else None, saved, code)
    stmt = select(rows.c.data).order_by(
        func.coalesce(cast(rows.c.data["order"].as_string(), Integer), rows.c.seq), rows.c.key
    )
    if kw and not mappings:
        stmt = stmt.where(
            or_(
                rows.c.data["name"].as_string().contains(kw, autoescape=True),
                rows.c.data["api"].as_string().contains(kw, autoescape=True),
            )
        )
    items, total, actual = await svc.repo.page(stmt, p)
    if mappings:
        items = [
            {
                "prop": d.get("name", ""),
                "col": d.get("_mapColumn") or d.get("source", ""),
                "srcType": d.get("_srcType") or d.get("type", ""),
                "transform": d.get("_transform", ""),
            }
            for d in items
        ]
    else:
        items = [{k: v for k, v in d.items() if not k.startswith("_")} for d in items]
    result = Page.of(items, total, actual).model_dump()
    if not mappings:
        result["diff"] = []
    return result


def value_rows(svc, dimension_id, saved, code):
    a = get_adapter(svc.session)
    body = a.json_object(
        key=DimValue.value_key,
        code=DimValue.code,
        name=DimValue.name,
        level=DimValue.level_name,
        status=case((DimValue.status == DimValueStatus.enabled, "启用"), else_="停用"),
        synonyms=DimValue.synonyms,
        parent=DimValue.parent,
        count=DimValue.record_count,
        src=DimValue.src,
        note=DimValue.note,
        ref=DimValue.ref,
        since=DimValue.since_release,
        until=DimValue.until_release,
        draft=DimValue.draft_cr,
        diff=DimValue.diff,
        pub=DimValue.pub,
    )
    if not saved:
        body = a.json_merge(body, DimValue.pub)
    draft_filter = or_(DimValue.draft_cr.is_(None), DimValue.draft_cr == "")
    if saved:
        draft_filter = or_(draft_filter, DimValue.draft_cr == saved[0].code)
    base = (
        select(DimValue.value_key.label("key"), DimValue.seq.label("seq"), body.label("data"))
        .where(
            DimValue.dimension_id == dimension_id,
            or_(DimValue.until_release.is_(None), DimValue.until_release == ""),
            draft_filter,
        )
        .subquery()
    )
    return overlay(
        svc,
        base,
        saved,
        "dimension",
        code,
        "values",
        "key",
        {"key": "", "code": "", "name": "", "level": "", "status": "启用", "synonyms": []},
        tags=True,
    )


def compact_value(data):
    return {
        k: v
        for k, v in data.items()
        if v is not None or k in ("key", "code", "name", "level", "status", "synonyms", "hits")
    }


async def dim_values(svc, code, level, q, status, p, cr_code=None):
    if svc.ver:
        from app.modules.static.service import paginate

        if cr_code:
            await draft(svc, cr_code, "dimension", code)
        d = (await snapshot(svc, "dimensions")).get(code)
        if d is None:
            raise NotFound("维度在该版本不存在")
        rows = svc._value_rows(d)
        off = sum(v["status"] == "停用" for v in rows)
        levels = list(dict.fromkeys(v["level"] for v in rows))
        selected = [
            v
            for v in rows
            if (not level or v["level"] == level)
            and (not status or v["status"] == status)
            and (not q or q in v["name"] or q in v["code"] or any(q in s for s in v["synonyms"]))
        ]
        return {
            **paginate(selected, p),
            "levels": levels,
            "summary": f"共 {len(rows)} 个取值" + (f"，停用 {off} 个" if off else ""),
        }
    parent, saved = await target(svc, Dimension, code, cr_code, "dimension")
    rows = value_rows(svc, parent.id if parent else None, saved, code)
    n, off = (
        await svc.session.execute(
            select(
                func.count(), func.count().filter(rows.c.data["status"].as_string() == "停用")
            ).select_from(rows)
        )
    ).one()
    level_column = rows.c.data["level"].as_string()
    levels = list(
        await svc.session.scalars(select(level_column).group_by(level_column).order_by(func.min(rows.c.seq)))
    )
    stmt = select(rows.c.data).order_by(rows.c.seq, rows.c.key)
    if level:
        stmt = stmt.where(rows.c.data["level"].as_string() == level)
    if status:
        stmt = stmt.where(rows.c.data["status"].as_string() == status)
    if q:
        stmt = stmt.where(
            or_(
                rows.c.data["name"].as_string().contains(q, autoescape=True),
                rows.c.data["code"].as_string().contains(q, autoescape=True),
                contains_array(svc, rows.c.data["synonyms"], q),
            )
        )
    items, total, actual = await svc.repo.page(stmt, p)
    return {
        **Page.of([compact_value(d) for d in items], total, actual).model_dump(),
        "levels": levels,
        "summary": f"共 {n} 个取值" + (f"，停用 {off} 个" if off else ""),
    }


async def single_dim(svc, code):
    if svc.ver:
        dims = list((await snapshot(svc, "dimensions")).values())
        data = next((row for row in dims if row["id"] == code), None)
        if data is None:
            raise NotFound("维度在该版本不存在")
        return {**data, "usage": []}, dims
    row = await svc.session.scalar(svc.repo.dimension_query().where(Dimension.code == code))
    if row is None:
        raise NotFound("维度不存在")
    values, maps = await svc.repo.dim_children([row.id])
    data = shapes.dimension(row, values[row.id], maps[row.id])
    return data, [data]


async def single_term(svc, code):
    if svc.ver:
        data = (await snapshot(svc, "terms")).get(code)
        if data is None:
            raise NotFound("业务知识在该版本不存在")
        return {**data, "refs": []}
    row = await svc.session.scalar(select(Knowledge).where(Knowledge.code == code))
    if row is None:
        raise NotFound("业务知识不存在")
    return shapes.knowledge(row)


async def knowledge_refs(svc, code, p):
    if svc.ver and code not in await snapshot(svc, "terms"):
        raise NotFound("业务知识在该版本不存在")
    result = await json_field_page(svc, Knowledge, Knowledge.refs, code, p, missing_ok=bool(svc.ver))
    if svc.ver:
        result["runtimeFromCurrent"] = True  # 引用是既有§2.8运行数据，仅读此资源，不补当前定义。
    return result


async def knowledge_history(svc, code, p):
    from app.modules.static.service import paginate

    if svc.ver:
        data = await single_term(svc, code)
        return paginate(data.get("history", []), p, key="ver")
    return await json_field_page(svc, Knowledge, Knowledge.history, code, p)


async def dim_usage(svc, code, p):
    if svc.ver:
        from app.modules.static.service import paginate

        data = (await snapshot(svc, "dimensions")).get(code)
        if data is None:
            raise NotFound("维度在该版本不存在")
        rows = []
        for metric in (await snapshot(svc, "metrics")).values():
            common = [name for name in metric.get("dimNames", []) if name in data.get("metricDims", [])]
            if common:
                note = (data.get("metricNote") or {}).get(metric["id"])
                rows.append(
                    {
                        "type": "指标",
                        "name": metric["name"],
                        "hover": {"kind": "metric", "id": metric["id"]},
                        "note": note if note is not None else "允许按“" + "”“".join(common) + "”拆分",
                    }
                )
        usage = await svc.session.scalar(select(Dimension.usage).where(Dimension.code == code))
        return {**paginate(rows + (usage if isinstance(usage, list) else []), p), "runtimeFromCurrent": True}
    row = (
        await svc.session.execute(
            select(Dimension.id, Dimension.metric_dims, Dimension.metric_note).where(Dimension.code == code)
        )
    ).first()
    if row is None:
        raise NotFound("维度不存在")
    a = get_adapter(svc.session)
    words = a.json_array_rows(Metric.dims, as_text=True)
    grouped = (
        select(
            Metric.code,
            Metric.display_name,
            Metric.status,
            Metric.stat_seq,
            a.text_aggregate(words.c.value, "”“", (words.c.ordinality,)).label("names"),
        )
        .select_from(Metric.__table__.join(words, true()))
        .where(
            Metric.stat_seq.is_not(None),
            words.c.value.in_(row.metric_dims if isinstance(row.metric_dims, list) else []),
        )
        .group_by(Metric.code, Metric.display_name, Metric.status, Metric.stat_seq)
        .subquery()
    )
    from app.modules.static.models import MetricStatus

    fallback = (
        "允许按“"
        + grouped.c.names
        + "”拆分"
        + case((grouped.c.status == MetricStatus.draft, "（指标草稿）"), else_="")
    )
    note = func.coalesce(json(row.metric_note or {})[grouped.c.code].as_string(), fallback)
    metric_rows = select(
        a.json_object(
            type="指标",
            name=grouped.c.display_name,
            hover=a.json_object(kind="metric", id=grouped.c.code),
            note=note,
        ).label("data"),
        literal(0).label("bucket"),
        grouped.c.stat_seq.label("seq"),
        grouped.c.code.label("key"),
    )
    saved = a.json_array_rows(select(Dimension.usage).where(Dimension.id == row.id).scalar_subquery())
    saved_rows = select(
        saved.c.value.label("data"),
        literal(1).label("bucket"),
        saved.c.ordinality.label("seq"),
        cast(saved.c.ordinality, String).label("key"),
    )
    candidates = union_all(metric_rows, saved_rows).subquery()
    items, total, actual = await svc.repo.page(
        select(candidates.c.data).order_by(candidates.c.bucket, candidates.c.seq, candidates.c.key), p
    )
    return Page.of(items, total, actual).model_dump()


async def json_field_page(svc, model, field, code, p, *, missing_ok=False):
    present = await svc.session.scalar(select(model.id).where(model.code == code))
    if present is None and not missing_ok:
        raise NotFound("目标定义不存在")
    source = select(field).where(model.code == code).scalar_subquery()
    rows = get_adapter(svc.session).json_array_rows(source)
    items, total, actual = await svc.repo.page(select(rows.c.value).order_by(rows.c.ordinality), p)
    if any(not isinstance(item, dict) for item in items):
        raise AppError("已存JSON列表包含非对象记录", code="DATA_INVALID", status_code=422)
    return Page.of(items, total, actual).model_dump()


async def link_history(svc, code, p):
    from app.modules.static.service import paginate

    if svc.ver:
        data = (await snapshot(svc, "links")).get(code)
        if data is None:
            raise NotFound("关系类型在该版本不存在")
        return paginate(data.get("history", []), p)
    return await json_field_page(svc, LinkType, LinkType.history, code, p)


async def synonym_conflicts(svc, cr_code=None):
    from app.modules.static.service import synonym_conflicts as compare

    if svc.ver:
        if cr_code:
            raise AppError("历史模式不能叠加当前草稿", code="READONLY", status_code=403)
        rows = list((await snapshot(svc, "terms")).values())
    else:
        rows = [
            {"id": code, "name": name, "status": status.value, "synonyms": words}
            for code, name, status, words in await svc.session.execute(
                select(Knowledge.code, Knowledge.name, Knowledge.status, Knowledge.synonyms).order_by(
                    Knowledge.seq, Knowledge.id
                )
            )
        ]
    if cr_code:
        # 只暴露该页面的已登记目标；其他类型的CR内容不会进入此读接口。
        targets = list(
            await svc.session.scalars(
                select(CrTarget)
                .join(ChangeRequest, ChangeRequest.id == CrTarget.cr_id)
                .where(ChangeRequest.code == cr_code, CrTarget.target_kind == "knowledge")
            )
        )
        if not targets:
            raise NotFound("该变更单没有业务知识目标")
        saved = await draft(svc, cr_code, "knowledge", targets[0].target_id)
        by_code = {row["id"]: row for row in rows}
        for entry in targets:
            if entry.target_id not in by_code:
                if not entry.is_new:
                    raise NotFound("业务知识目标不存在")
                by_code[entry.target_id] = {
                    "id": entry.target_id,
                    "name": entry.target_name or entry.target_id,
                    "status": "草稿",
                    "synonyms": [],
                }
        items = await svc.session.scalars(
            select(CrItem)
            .where(
                CrItem.cr_id == saved[0].id,
                CrItem.target_kind == "knowledge",
                CrItem.target_id.in_([t.target_id for t in targets]),
                CrItem.section.in_(["content", "status"]),
            )
            .order_by(CrItem.seq, CrItem.id)
        )
        for item in items:
            if item.item_key in ("name", "synonyms", "status") and item.op != "delete":
                by_code[item.target_id][item.item_key] = item.after
        rows = list(by_code.values())
    for row in rows:
        if (
            not isinstance(row.get("name"), str)
            or not isinstance(row.get("synonyms"), list)
            or any(not isinstance(word, str) for word in row["synonyms"])
            or row.get("status") not in ("草稿", "已发布", "已停用")
        ):
            raise AppError(
                "草稿名称、同义词或状态类型不合法，无法检查冲突", code="VALIDATION_FAILED", status_code=422
            )
    return {"conflicts": compare(rows), "rule": {}}


async def catalog(svc):
    if svc.ver:
        objects = list((await snapshot(svc, "objectTypes")).values())
        links = list((await snapshot(svc, "links")).values())
        paths = list((await snapshot(svc, "qaPaths")).values())
    else:
        objects = [shapes.object_link_obj(row) for row in await svc.repo.object_types(light=True)]
        links = [shapes.link(row, with_runtime=False) for row in await svc.repo.links(light=True)]
        paths = list(
            (
                await svc.session.scalar(
                    select(OntologyRelease.snapshot["qaPaths"]).order_by(OntologyRelease.id.desc()).limit(1)
                )
                or {}
            ).values()
        )
    names = {row["id"]: row["name"] for row in objects}
    return {
        "objects": objects,
        "links": [
            {
                **row,
                "fromName": names.get(row["from"], row["from"]),
                "toName": names.get(row["to"], row["to"]),
            }
            for row in links
        ],
        "paths": paths,
    }
