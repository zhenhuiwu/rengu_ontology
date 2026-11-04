"""Read-only current-definition lineage; history never borrows current definitions."""

from fastapi import APIRouter, Depends, Request

from app.common.auth import CurrentUserDep, require_func
from app.common.context import ViewContextDep
from app.core.errors import AppError
from app.db.session import SessionDep
from app.modules.admin.api_keys import require_api_scope
from app.modules.static.lineage import current_lineage

router = APIRouter(tags=["lineage"])


@router.get(
    "/ontology/lineage",
    dependencies=[
        Depends(require_api_scope("meta:read")),
        Depends(require_func("ontology-overview", "view")),
    ],
)
async def lineage(session: SessionDep, user: CurrentUserDep, ctx: ViewContextDep, request: Request):
    if ctx.ver or ctx.preview_as:
        raise AppError(
            "血缘当前仅支持当前身份的当前定义，请退出历史或身份预览",
            code="LINEAGE_CURRENT_ONLY",
            status_code=422,
        )
    return await current_lineage(session, user, request)
