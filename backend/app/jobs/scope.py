"""Workers enumerate shared workspaces, then open sessions with one immutable domain scope."""

from contextlib import asynccontextmanager
from functools import wraps

from sqlalchemy import select

from app.common.ontology import set_ontology_scope
from app.db.session import get_sessionmaker


async def workspace_ids() -> list[int]:
    from app.modules.ontology.models import OntologyWorkspace

    async with get_sessionmaker()() as session:
        return list(await session.scalars(select(OntologyWorkspace.id).where(OntologyWorkspace.deleted_at.is_(None), OntologyWorkspace.workers_paused.is_(False)).order_by(OntologyWorkspace.id)))


def scoped_sessions(ontology_id: int):
    @asynccontextmanager
    async def session_scope():
        async with get_sessionmaker()() as session:
            session.info["workspace_worker"] = True
            await set_ontology_scope(session, ontology_id)
            yield session

    return session_scope


def workspace_dispatch(function):
    """Hold the workspace lifecycle fence across claim, network send and receipt persistence."""
    @wraps(function)
    async def guarded(item_id, ontology_id):
        from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
        from sqlalchemy.pool import NullPool
        from app.common.ontology import _check_active
        from app.core.config import get_settings

        engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
        try:
            async with AsyncSession(engine) as session, session.begin():
                await session.run_sync(lambda sync: _check_active(sync.connection(), ontology_id))
                from app.modules.ontology.models import OntologyWorkspace
                from app.common.ontology import WorkspaceInactive
                if not await session.scalar(select(OntologyWorkspace.id).where(OntologyWorkspace.id == ontology_id, OntologyWorkspace.workers_paused.is_(False))):
                    raise WorkspaceInactive("工作空间后台任务已暂停")
                return await function(item_id, ontology_id)
        finally:
            await engine.dispose()
    return guarded
