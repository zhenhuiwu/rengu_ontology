"""Admin pages that read per-ontology data (role row scopes, object permissions, /me/row-scope).

Admin routes are "discovery" routes in app/common/auth.py and do not get a workspace scope there.
This dependency resolves X-Ontology-Id (default 1, same as business routes), checks the workspace
exists and binds the session to it, so FORCE RLS tables such as sys_role_scope read one workspace.
"""

from typing import Annotated

from fastapi import Depends, Header
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.ontology import DEFAULT_ONTOLOGY_ID, set_ontology_scope
from app.core.errors import AppError, NotFound
from app.db.session import SessionDep


def parse_ontology_header(raw: str | None) -> int:
    if raw is None:
        return DEFAULT_ONTOLOGY_ID
    if len(raw) > 19 or not raw.isascii() or not raw.isdecimal() or not 0 < int(raw) < 2**63:
        raise AppError("本体工作空间编号无效", code="VALIDATION_FAILED", status_code=422)
    return int(raw)


async def bind_admin_workspace(session: AsyncSession, raw: str | None) -> int:
    from app.modules.ontology.models import OntologyWorkspace

    ontology_id = parse_ontology_header(raw)
    if await session.get(OntologyWorkspace, ontology_id) is None:
        raise NotFound("本体工作空间不存在")
    await set_ontology_scope(session, ontology_id)
    return ontology_id


async def admin_workspace_scope(
    session: SessionDep,
    x_ontology_id: Annotated[str | None, Header(description="本体工作空间编号，默认 1")] = None,
) -> int:
    return await bind_admin_workspace(session, x_ontology_id)


AdminWorkspaceDep = Annotated[int, Depends(admin_workspace_scope)]
