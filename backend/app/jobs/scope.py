"""Workers enumerate shared workspaces, then open sessions with one immutable domain scope."""

from contextlib import asynccontextmanager

from sqlalchemy import select

from app.common.ontology import set_ontology_scope
from app.db.session import get_sessionmaker


async def workspace_ids() -> list[int]:
    from app.modules.ontology.models import OntologyWorkspace

    async with get_sessionmaker()() as session:
        return list(await session.scalars(select(OntologyWorkspace.id).order_by(OntologyWorkspace.id)))


def scoped_sessions(ontology_id: int):
    @asynccontextmanager
    async def session_scope():
        async with get_sessionmaker()() as session:
            await set_ontology_scope(session, ontology_id)
            yield session

    return session_scope
