"""integration（负责人 Palantir专家）。只有一个路由，挂在 /api/data/v1（见 app/api.py）：

- 外部系统 /ext-systems*、回写调用 /writeback-calls*
  （action-event-api.md:140、:168，PDE 定外部系统属数据分组）。
- integration **不在 /api/ontology/v1 下开任何路由**（Palantir 定）：动作类型、事件类型、事件、对账等
  /api/ontology/v1 接口由 action / event 模块的路由提供，执行记录由 dynamic 提供。

冒号动作按 PDE api-index：`POST /ext-systems/{id}:test` 等。
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.common.auth import CurrentUser, CurrentUserDep, require_func
from app.db.session import SessionDep
from app.modules.integration.schemas import (
    ExtSystemDetailOut,
    ExtSystemListOut,
    ExtSystemOut,
    LinkedActionTypeOut,
)
from app.modules.integration.service import ExtSystemService

router = APIRouter(tags=["integration"])  # /api/data/v1

_ext_view = require_func("external-systems", "view")
_action_view = require_func("action-types", "view")


async def ext_list_access(
    user: CurrentUserDep, writeback: Annotated[int, Query(ge=0, le=1)] = 0
) -> CurrentUser:
    """external-systems · view；`writeback=1`（动作类型写回 tab 选系统用）有 action-types · view 也可以。

    EXTENSION POINT (PDE)：require_func 现在是桩；落地后这里改成「两者满足其一」。
    """
    return await (_action_view if writeback else _ext_view)(user)


@router.get("/ext-systems", response_model=ExtSystemListOut, summary="外部系统列表（不分页）")
async def list_ext_systems(
    session: SessionDep,
    _user: Annotated[CurrentUser, Depends(ext_list_access)],
    writeback: Annotated[int, Query(ge=0, le=1, description="1 = 只要可写回的（生产环境且未停用）")] = 0,
    kw: Annotated[str | None, Query(description="名称 / 编号 / 厂商包含")] = None,
) -> ExtSystemListOut:
    rows = await ExtSystemService(session).search(writeback=bool(writeback), kw=kw)
    return ExtSystemListOut(items=[ExtSystemOut.model_validate(r) for r in rows])


@router.get(
    "/ext-systems/{sys_id}",
    response_model=ExtSystemDetailOut,
    summary="外部系统详情（含关联的动作类型）",
    dependencies=[Depends(_ext_view)],
)
async def get_ext_system(sys_id: str, session: SessionDep) -> ExtSystemDetailOut:
    s, actions = await ExtSystemService(session).get(sys_id)
    out = ExtSystemDetailOut.model_validate(s)
    out.action_types = [LinkedActionTypeOut.model_validate(a) for a in actions]
    return out
