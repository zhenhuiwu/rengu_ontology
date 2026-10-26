"""对象的指标、视图和变更历史：按完整真实引用关系筛选后SQL分页。"""

import re

from sqlalchemy import and_, exists, false, func, or_, select
from sqlalchemy.orm import raiseload

from app.common.pagination import Page
from app.db.dialects import get_adapter
from app.modules.static import shapes
from app.modules.static.models import (
    ChangeRequest,
    CrItem,
    CRStatus,
    CrTarget,
    Dimension,
    DimLevel,
    LinkType,
    Metric,
    ObjectType,
    ObjectView,
    ViewKind,
)
from app.modules.static.object_history import object_change_history
from app.modules.static.read_pages import draft, enum_is, json, snapshot, target
from app.modules.versioning.models import OntologyRelease
from app.modules.versioning.repo import ReleaseRepo


def pattern(names):
    words = "|".join(re.escape(value) for value in names if value)
    return rf"(?<![A-Za-z0-9_\u3400-\u9fff])(?:{words})(?![A-Za-z0-9_\u3400-\u9fff])"


async def usage(svc, code, kind, q, flt, p, cr_code=None, affected_only=False):
    if svc.ver:
        obj = (await snapshot(svc, "objectTypes")).get(code)
        if obj is None:
            from app.core.errors import NotFound

            raise NotFound("该版本中对象类型不存在")
        parent = None
        name = obj["name"]
    else:
        parent, _ = await target(svc, ObjectType, code, cr_code, "object")
        name = parent.display_name if parent else code
    if kind == "cr":
        return await change_page(svc, code, name, q, flt, p)
    if kind == "view":
        return await view_page(svc, code, parent, q, flt, p, cr_code, affected_only)
    if parent is None and not svc.ver:
        return Page.of([], 0, p).model_dump()
    return await metric_page(svc, code, parent, q, flt, p)


async def view_page(svc, code, parent, q, flt, p, cr_code, affected_only):
    if svc.ver:
        # 对象视图是独立运行配置，不在本体快照；仅按该历史对象的稳定编码查运行数据。
        object_id = select(ObjectType.id).where(ObjectType.code == code).scalar_subquery()
    else:
        object_id = parent.id if parent else None
    saved = await draft(svc, cr_code, "object", code)
    changed = set()
    if saved:
        changed = set(
            await svc.session.scalars(
                select(CrItem.item_key).where(
                    CrItem.cr_id == saved[0].id,
                    CrItem.target_kind == "object",
                    CrItem.target_id == code,
                    CrItem.section == "properties",
                )
            )
        )
    stmt = (
        select(ObjectView)
        .options(raiseload("*"))
        .where(ObjectView.object_type_id == object_id)
        .order_by(ObjectView.seq, ObjectView.id)
    )
    if q:
        stmt = stmt.where(
            or_(ObjectView.name.contains(q, autoescape=True), ObjectView.owner.contains(q, autoescape=True))
        )
    if flt and flt != "全部":
        stmt = stmt.where(enum_is(ObjectView.kind, ViewKind, flt))
    if affected_only:
        props = get_adapter(svc.session).json_array_rows(ObjectView.props, as_text=True)
        stmt = stmt.where(
            exists(select(1).select_from(props).where(props.c.value.in_(changed))) if changed else false()
        )
    rows, total, actual = await svc.repo.page(stmt, p)
    out = [
        {**shapes.view(row, code), "affectedProps": [key for key in row.props or [] if key in changed]}
        for row in rows
    ]
    result = Page.of(out, total, actual).model_dump()
    if svc.ver:
        result["runtimeFromCurrent"] = True
    return result


async def metric_page(svc, code, parent, q, flt, p):
    if svc.ver:
        objects = await snapshot(svc, "objectTypes")
        dims = [d for d in (await snapshot(svc, "dimensions")).values() if d.get("object") == code]
        headers = [(key, row.get("name", ""), row.get("api", "")) for key, row in objects.items()]
        names = {
            name
            for d in dims
            for name in [
                d.get("name"),
                *(d.get("metricDims") or []),
                *(row.get("name") for row in d.get("levels", [])),
            ]
            if name
        }
        from app.modules.static.service import paginate

        out = []
        for row in (await snapshot(svc, "metrics")).values():
            formula = row.get("formula") or ""
            base = [key for key, name, api in headers if re.search(pattern([name, api]), formula)]
            via = [name for name in row.get("dimNames", []) if name in names]
            if (
                (code in base or via)
                and (flt != "base" or code in base)
                and (flt != "dim" or (via and code not in base))
                and (not q or q in row["name"] or q in row.get("api", ""))
            ):
                out.append(
                    {
                        **row,
                        "formula": formula,
                        "dims": row.get("dimNames", []),
                        "base": base,
                        "via": via,
                        "value": None,
                        "prev": None,
                        "updatedAt": None,
                    }
                )
        return paginate(out, p)
    headers = list(
        await svc.session.execute(
            select(ObjectType.code, ObjectType.display_name, ObjectType.api_name).order_by(
                ObjectType.seq, ObjectType.id
            )
        )
    )
    dim_rows = list(
        await svc.session.execute(
            select(Dimension.display_name, Dimension.metric_dims).where(Dimension.object_id == parent.id)
        )
    )
    names = {name for name, _ in dim_rows}
    names.update(name for _, aliases in dim_rows for name in aliases or [] if isinstance(name, str))
    names.update(
        await svc.session.scalars(
            select(DimLevel.name)
            .join(Dimension, Dimension.id == DimLevel.dimension_id)
            .where(Dimension.object_id == parent.id)
        )
    )
    a = get_adapter(svc.session)
    base_filter = a.text_matches(
        func.coalesce(Metric.formula, ""), pattern([parent.display_name, parent.api_name])
    )
    dims = a.json_array_rows(Metric.dims, as_text=True)
    via_filter = exists(select(1).select_from(dims).where(dims.c.value.in_(names))) if names else false()
    stmt = (
        select(Metric)
        .where(
            base_filter
            if flt == "base"
            else and_(via_filter, ~base_filter)
            if flt == "dim"
            else or_(base_filter, via_filter)
        )
        .order_by(Metric.stat_seq.asc().nulls_last(), Metric.seq, Metric.id)
    )
    if q:
        stmt = stmt.where(
            or_(
                Metric.display_name.contains(q, autoescape=True), Metric.api_name.contains(q, autoescape=True)
            )
        )
    rows, total, actual = await svc.repo.page(stmt, p)
    out = []
    for row in rows:
        data = shapes.metric_stat(row)
        out.append(
            {
                **data,
                "base": [
                    key
                    for key, name, api in headers
                    if re.search(pattern([name, api]), data["formula"] or "")
                ],
                "via": [name for name in data["dims"] if name in names],
            }
        )
    return Page.of(out, total, actual).model_dump()


async def change_page(svc, code, name, q, flt, p):
    if svc.ver:
        head = await ReleaseRepo(svc.session).by_ver_or_404(svc.ver)
        links = {
            key
            for key, row in ((head.snapshot or {}).get("links") or {}).items()
            if code in (row.get("from"), row.get("to"))
        }
    else:
        links = set(
            await svc.session.scalars(
                select(LinkType.code).where(or_(LinkType.from_code == code, LinkType.to_code == code))
            )
        )
    targets = exists(
        select(1).where(
            CrTarget.cr_id == ChangeRequest.id,
            or_(
                and_(CrTarget.target_kind == "object", CrTarget.target_id == code),
                and_(CrTarget.target_kind == "link", CrTarget.target_id.in_(links)),
            ),
        )
    )
    items = exists(
        select(1).where(
            CrItem.cr_id == ChangeRequest.id,
            or_(
                and_(CrItem.target_kind == "object", CrItem.target_id == code),
                and_(
                    CrItem.target_kind == "link",
                    CrItem.section.in_(["def", "config"]),
                    CrItem.item_key.in_(["from", "to"]),
                    or_(CrItem.before == json(code), CrItem.after == json(code)),
                ),
            ),
        )
    )
    a = get_adapter(svc.session)
    touches = a.json_array_rows(ChangeRequest.detail["touches"], as_text=True)
    touched = exists(
        select(1)
        .select_from(touches)
        .where(
            or_(
                touches.c.value == f"object:{code}",
                touches.c.value.startswith(f"property:{code}.", autoescape=True),
                touches.c.value.startswith(f"relation:{code}.", autoescape=True),
            )
        )
    )
    diffs = a.json_array_rows(ChangeRequest.detail["diffs"])
    legacy = and_(
        ChangeRequest.is_seed.is_(True),
        exists(
            select(1)
            .select_from(diffs)
            .where(
                or_(
                    diffs.c.value["target"].as_string() == name,
                    diffs.c.value["target"].as_string().startswith(name + ".", autoescape=True),
                )
            )
        ),
    )
    stmt = (
        select(ChangeRequest)
        .where(or_(targets, items, touched, legacy))
        .order_by(ChangeRequest.status_at.desc(), ChangeRequest.code.desc())
    )
    if svc.ver:
        stmt = stmt.join(OntologyRelease, OntologyRelease.id == ChangeRequest.release_id).where(
            OntologyRelease.published_at <= head.published_at
        )
    if q:
        stmt = stmt.where(
            or_(
                ChangeRequest.title.contains(q, autoescape=True),
                ChangeRequest.code.contains(q, autoescape=True),
            )
        )
    if flt == "进行中":
        stmt = stmt.where(ChangeRequest.status.in_([CRStatus.draft, CRStatus.in_review, CRStatus.approved]))
    elif flt and flt != "全部":
        stmt = stmt.where(enum_is(ChangeRequest.status, CRStatus, flt))
    rows, total, actual = await svc.repo.page(stmt, p)
    out = await object_change_history(svc.session, code, name, svc.ver, selected_ids=[row.id for row in rows])
    return Page.of(out, total, actual).model_dump()
