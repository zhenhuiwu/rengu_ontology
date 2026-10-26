"""对象定义是映射来源；旧连接名兼容，新连接引用使用稳定 DS 编号。"""

import re

from sqlalchemy import select

from app.core.errors import AppError
from app.db.dialects import get_adapter
from app.modules.data.models import Datasource
from app.modules.static.models import ChangeRequest, CrItem, ObjectType

MAPPING_LOCK = "data.ontology_mapping"


async def mapped_objects(session, ds: Datasource) -> list[ObjectType]:
    return list(
        await session.scalars(
            select(ObjectType)
            .where(ObjectType.datasource_name.in_([ds.code, ds.name]))
            .order_by(ObjectType.code)
        )
    )


async def lock_source(session, source: str | None) -> None:
    """保留旧自由文本来源；仅稳定编号格式作为数据连接引用校验，不能只看 DS 前缀。"""
    if not isinstance(source, str) or not re.fullmatch(r"DS-[0-9]+", source):
        return
    row = await session.scalar(select(Datasource).where(Datasource.code == source).with_for_update())
    if row is None:
        raise AppError(
            "映射的数据连接不存在，请先调整来源",
            code="DS_NOT_FOUND",
            status_code=409,
            details={"datasourceId": source},
        )
    # 与导入/删除顺序一致：先父行，后映射事务锁。
    await get_adapter(session).xact_lock(session, f"{MAPPING_LOCK}:{source}")


async def guard_delete(session, ds: Datasource) -> None:
    from app.modules.static.cr_write import HOLDING

    await get_adapter(session).xact_lock(session, f"{MAPPING_LOCK}:{ds.code}")
    objects = await mapped_objects(session, ds)
    pending = list(
        await session.scalars(
            select(CrItem)
            .join(ChangeRequest, ChangeRequest.id == CrItem.cr_id)
            .where(
                ChangeRequest.status.in_(HOLDING),
                CrItem.target_kind == "object",
                CrItem.section.in_(["def", "config"]),
                CrItem.item_key == "source",
                CrItem.op != "delete",
            )
        )
    )
    refs = [i.target_id for i in pending if i.after in (ds.code, ds.name)]
    if objects or refs:
        raise AppError(
            "连接已被对象定义或未发布变更单引用，请先发布解除映射的变更",
            code="DS_IN_USE",
            status_code=409,
            details={"objects": sorted({o.code for o in objects} | set(refs))},
        )
