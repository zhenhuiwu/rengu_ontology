"""说法映射的SQL候选集：显式行、自动说法和完整跨维度冲突共用后端分页。"""

from sqlalchemy import and_, case, exists, func, literal, or_, select, true, union_all

from app.common.pagination import Page
from app.db.dialects import get_adapter
from app.modules.static.models import (
    Dimension,
    DimMapping,
    DimValue,
    DimValueStatus,
    MappingSource,
    MatchMode,
)
from app.modules.static.read_pages import compact_value, draft, json, overlay, single_dim, target, value_rows


def label(column, enum):
    return case(*[(column == value, value.value) for value in enum], else_=None)


def mapping_rows(svc, dimension_id, saved, code):
    a = get_adapter(svc.session)
    body = a.json_object(
        key=DimMapping.map_key,
        phrase=DimMapping.phrase,
        targets=DimMapping.targets,
        match=label(DimMapping.match, MatchMode),
        source=label(DimMapping.source, MappingSource),
        hits=DimMapping.hits,
        conflict=DimMapping.conflict,
        resolve=DimMapping.resolve,
        draft=DimMapping.draft_cr,
        diff=DimMapping.diff,
    )
    allowed = or_(DimMapping.draft_cr.is_(None), DimMapping.draft_cr == "")
    if saved:
        allowed = or_(allowed, DimMapping.draft_cr == saved[0].code)
    base = (
        select(DimMapping.map_key.label("key"), DimMapping.seq.label("seq"), body.label("data"))
        .where(DimMapping.dimension_id == dimension_id, allowed)
        .subquery()
    )
    return overlay(
        svc,
        base,
        saved,
        "dimension",
        code,
        "mappings",
        "key",
        {"key": "", "phrase": "", "targets": [], "match": "精确", "source": "业务知识", "hits": 0},
        tags=True,
    )


def candidates(svc, parent, saved, code):
    a = get_adapter(svc.session)
    values = value_rows(svc, parent.id if parent else None, saved, code)
    maps = mapping_rows(svc, parent.id if parent else None, saved, code)
    own_name = parent.display_name if parent else saved[1].target_name or code
    own_seq = parent.seq if parent else 2147483647
    raw = a.json_merge(
        a.json_object(
            name=DimValue.name, status=label(DimValue.status, DimValueStatus), synonyms=DimValue.synonyms
        ),
        DimValue.pub,
    )
    other_values = (
        select(
            Dimension.code.label("dim"),
            Dimension.display_name.label("dim_name"),
            Dimension.seq.label("dim_seq"),
            DimValue.seq.label("seq"),
            raw.label("data"),
        )
        .join(DimValue, DimValue.dimension_id == Dimension.id)
        .where(
            Dimension.code != code,
            or_(DimValue.until_release.is_(None), DimValue.until_release == ""),
            or_(DimValue.draft_cr.is_(None), DimValue.draft_cr == ""),
        )
    )
    own_values = select(
        literal(code).label("dim"),
        literal(own_name).label("dim_name"),
        literal(own_seq).label("dim_seq"),
        values.c.seq,
        values.c.data,
    )
    all_values = union_all(other_values, own_values).cte()
    enabled = select(all_values).where(all_values.c.data["status"].as_string() == "启用").cte()
    synonyms = a.json_array_rows(enabled.c.data["synonyms"], as_text=True)
    names = select(
        enabled.c.dim,
        enabled.c.dim_name,
        enabled.c.dim_seq,
        enabled.c.seq,
        enabled.c.data["name"].as_string().label("value_name"),
        enabled.c.data["name"].as_string().label("phrase"),
    )
    synonym_hits = select(
        enabled.c.dim,
        enabled.c.dim_name,
        enabled.c.dim_seq,
        enabled.c.seq,
        enabled.c.data["name"].as_string().label("value_name"),
        synonyms.c.value.label("phrase"),
    ).select_from(enabled.join(synonyms, true()))
    other_maps = (
        select(
            Dimension.code.label("dim"),
            DimMapping.phrase.label("phrase"),
            DimMapping.targets.label("targets"),
        )
        .join(DimMapping, DimMapping.dimension_id == Dimension.id)
        .where(Dimension.code != code, or_(DimMapping.draft_cr.is_(None), DimMapping.draft_cr == ""))
    )
    own_maps = select(
        literal(code).label("dim"),
        maps.c.data["phrase"].as_string().label("phrase"),
        maps.c.data["targets"].label("targets"),
    )
    all_maps = union_all(other_maps, own_maps).cte()
    targets = a.json_array_rows(all_maps.c.targets, as_text=True)
    map_hits = select(
        enabled.c.dim,
        enabled.c.dim_name,
        enabled.c.dim_seq,
        enabled.c.seq,
        enabled.c.data["name"].as_string().label("value_name"),
        all_maps.c.phrase,
    ).select_from(
        all_maps.join(targets, true()).join(
            enabled,
            and_(enabled.c.dim == all_maps.c.dim, enabled.c.data["name"].as_string() == targets.c.value),
        )
    )
    union = union_all(names, synonym_hits, map_hits).cte()
    hits = (
        select(
            union.c.dim,
            union.c.dim_name,
            union.c.dim_seq,
            union.c.value_name,
            union.c.phrase,
            func.min(union.c.seq).label("seq"),
        )
        .group_by(union.c.dim, union.c.dim_name, union.c.dim_seq, union.c.value_name, union.c.phrase)
        .cte()
    )
    own_enabled = select(values).where(values.c.data["status"].as_string() == "启用").cte()
    own_synonyms = a.json_array_rows(own_enabled.c.data["synonyms"], as_text=True)
    occurrences = union_all(
        select(
            own_enabled.c.key,
            own_enabled.c.seq,
            own_enabled.c.data["name"].as_string().label("value_name"),
            own_enabled.c.data["name"].as_string().label("phrase"),
            literal(0).label("ordinal"),
            literal("取值名称").label("source"),
        ),
        select(
            own_enabled.c.key,
            own_enabled.c.seq,
            own_enabled.c.data["name"].as_string().label("value_name"),
            own_synonyms.c.value.label("phrase"),
            own_synonyms.c.ordinality.label("ordinal"),
            literal("取值同义词").label("source"),
        ).select_from(own_enabled.join(own_synonyms, true())),
    ).cte()
    ranked = select(
        occurrences,
        func.row_number()
        .over(
            partition_by=occurrences.c.phrase,
            order_by=(occurrences.c.seq, occurrences.c.ordinal, occurrences.c.key),
        )
        .label("rank"),
    ).cte()
    has_other = exists(
        select(1).where(
            hits.c.phrase == ranked.c.phrase,
            or_(hits.c.dim != code, hits.c.value_name != ranked.c.value_name),
        )
    )
    has_explicit = exists(select(1).where(maps.c.data["phrase"].as_string() == ranked.c.phrase))
    generated = select(
        (ranked.c.phrase + ">" + ranked.c.value_name).label("key"),
        ranked.c.seq,
        a.json_object(
            key=ranked.c.phrase + ">" + ranked.c.value_name,
            phrase=ranked.c.phrase,
            targets=a.json_array(ranked.c.value_name),
            match="精确",
            source=ranked.c.source,
            hits=None,
        ).label("data"),
        literal(True).label("automatic"),
        literal(1).label("bucket"),
        ranked.c.ordinal.label("subseq"),
    ).where(ranked.c.rank == 1, ~has_explicit, or_(ranked.c.source != "取值名称", has_other))
    explicit = select(
        maps.c.key,
        maps.c.seq,
        maps.c.data,
        literal(False).label("automatic"),
        literal(0).label("bucket"),
        literal(0).label("subseq"),
    )
    rows = union_all(explicit, generated).cte()
    destinations = a.json_array_rows(rows.c.data["targets"], as_text=True)
    same_value = exists(select(1).select_from(destinations).where(destinations.c.value == hits.c.value_name))
    others = (
        select(
            a.text_aggregate(
                hits.c.dim_name + " · " + hits.c.value_name,
                "、",
                (hits.c.dim_seq, hits.c.seq, hits.c.dim, hits.c.value_name),
            )
        )
        .where(hits.c.phrase == rows.c.data["phrase"].as_string(), ~and_(hits.c.dim == code, same_value))
        .scalar_subquery()
    )
    collision = func.coalesce(others, "")
    intrinsic = func.coalesce(rows.c.data["conflict"].as_string(), "")
    auto = case(
        (or_(rows.c.automatic, and_(intrinsic == "", collision != "")), a.json_object(auto=collision)),
        else_=json({}),
    )
    text = case((intrinsic != "", intrinsic), (collision != "", "与 " + collision + " 同名"), else_="")
    return select(
        rows.c.key,
        rows.c.seq,
        rows.c.bucket,
        rows.c.subseq,
        a.json_merge(rows.c.data, auto, a.json_object(conflictText=text)).label("data"),
    ).subquery()


async def dim_mappings(svc, code, conflict, p, cr_code=None):
    if svc.ver:
        from app.modules.static.service import paginate

        if cr_code:
            await draft(svc, cr_code, "dimension", code)
        d, dims = await single_dim(svc, code)
        rows = svc._map_rows(d, dims)
        return {
            **paginate([r for r in rows if r["conflictText"]] if conflict else rows, p),
            "conflictCount": sum(bool(r["conflictText"]) for r in rows),
        }
    parent, saved = await target(svc, Dimension, code, cr_code, "dimension")
    rows = candidates(svc, parent, saved, code)
    conflict_filter = rows.c.data["conflictText"].as_string() != ""
    conflict_count = int(
        await svc.session.scalar(select(func.count()).select_from(rows).where(conflict_filter)) or 0
    )
    stmt = select(rows.c.data).order_by(rows.c.bucket, rows.c.seq, rows.c.subseq, rows.c.key)
    if conflict:
        stmt = stmt.where(conflict_filter)
    items, total, actual = await svc.repo.page(stmt, p)
    return {
        **Page.of([compact_value(row) for row in items], total, actual).model_dump(),
        "conflictCount": conflict_count,
    }
