from sqlalchemy import select, text
from sqlalchemy.orm import noload
from sqlalchemy.exc import SQLAlchemyError

from app.common.auth import check_func
from app.common.ontology import require_ontology_id
from app.core.errors import AppError, NotFound
from app.db.dialects import get_adapter
from app.modules.static.cr_write import HOLDING
from app.modules.static.models import ChangeRequest, CrTarget, Dimension, Metric
from app.modules.static.writeback import DIRTY, _hard_delete
from app.modules.versioning.repo import ReleaseRepo
from app.modules.versioning.service import PUBLISH_LOCK, VersioningService

KNOWN_CHILDREN = {("dim_level", "dimension_id"), ("dim_value", "dimension_id"), ("dim_mapping", "dimension_id")}


async def delete_dimension(session, user, code: str, lock_version: int, dimension_version: int):
    """删除当前维度或码表。取值和层级随外键级联去掉；历史版本快照保留。"""
    check_func(user, "dimensions", "manage")
    check_func(user, "releases", "manage")
    require_ontology_id(session)
    if user.ontology_role not in ("owner", "editor") or user.is_service_account or user.ontology_ids is not None:
        raise AppError("需要本体工作空间的编辑权限", code="FORBIDDEN", status_code=403)
    await get_adapter(session).xact_lock(session, PUBLISH_LOCK)
    await get_adapter(session).xact_lock(session, f"static.cr.target.dimension:{code}")
    latest = await ReleaseRepo(session).latest()
    if (latest.id if latest else 0) != lock_version:
        raise AppError("工作版本已变更，请刷新后重试", code="VERSION_CONFLICT", status_code=409)
    dim = await session.scalar(
        select(Dimension)
        .where(Dimension.code == code)
        .options(noload(Dimension.object))
        .with_for_update(of=Dimension)
    )
    if dim is None:
        raise NotFound("维度或码表不存在")
    if dim.lock_version != dimension_version:
        raise AppError("定义已变更，请刷新后重试", code="VERSION_CONFLICT", status_code=409)
    reasons = []
    holding = await session.scalar(
        select(ChangeRequest.code)
        .join(CrTarget, CrTarget.cr_id == ChangeRequest.id)
        .where(ChangeRequest.status.in_(HOLDING), CrTarget.target_kind == "dimension", CrTarget.target_id == code)
        .limit(1)
    )
    if holding:
        reasons.append(f"变更单 {holding} 仍包含该定义，请先放弃或发布")
    aliases = {s for s in (dim.code, dim.api_name, dim.display_name) if s}
    metrics = await session.execute(select(Metric.display_name, Metric.dims))
    for name, allowed in metrics:
        if any(isinstance(item, str) and item in aliases for item in allowed or []):
            reasons.append(f"指标 {name} 允许按该维度拆分")
    fk_rows = (await session.execute(text("""
        SELECT ns.nspname, child.relname, a.attname, cardinality(c.conkey)
        FROM pg_constraint c JOIN pg_class child ON child.oid=c.conrelid
        JOIN pg_namespace ns ON ns.oid=child.relnamespace
        JOIN pg_attribute a ON a.attrelid=child.oid AND a.attnum=c.conkey[1]
        WHERE c.contype='f' AND c.confrelid='public.dimension'::regclass
    """))).all()
    for schema, name, attr, width in fk_rows:
        if schema != "public" or width != 1 or (name, attr) not in KNOWN_CHILDREN:
            reasons.append(f"无法确认外键引用安全：{schema}.{name}.{attr}")
    if reasons:
        raise AppError("无法删除：" + "；".join(dict.fromkeys(reasons)), code="DIMENSION_DELETE_BLOCKED", status_code=409)
    label = str(dim.kind)
    try:
        for level in list(dim.levels):
            session.expunge(level)
        await _hard_delete(session, Dimension, [dim])
        session.info.setdefault(DIRTY, set()).add("dimensions")
        service = VersioningService()
        base = latest.ver if latest else None
        snapshot = await service.provider.build_snapshot(session, base, [])
        dimensions = snapshot.get("dimensions")
        if isinstance(dimensions, dict):
            snapshot["dimensions"] = {key: value for key, value in dimensions.items() if key != code}
        release = await service._append(
            session, base, snapshot, [], {}, published_by=user.id,
            note=f"删除{label} {code}",
            now=None, latest=latest, scope="working",
        )
    except SQLAlchemyError:
        raise AppError("无法安全删除，请检查关联引用后重试", code="DIMENSION_DELETE_BLOCKED", status_code=409) from None
    return {"ver": release.ver}
