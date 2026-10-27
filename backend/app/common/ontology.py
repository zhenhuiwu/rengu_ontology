"""Trusted transaction scope. PostgreSQL RLS is the boundary for ORM and native SQL alike."""

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.core.errors import AppError

DEFAULT_ONTOLOGY_ID = 1
RUNTIME_ROLE = "rengu_ontology_scoped"


def require_ontology_id(session: AsyncSession) -> int:
    ontology_id = session.info.get("ontology_id")
    if type(ontology_id) is not int or ontology_id <= 0:
        raise AppError("缺少本体工作空间上下文", code="ONTOLOGY_CONTEXT_REQUIRED", status_code=422)
    return ontology_id


async def set_ontology_scope(session: AsyncSession, ontology_id: int) -> None:
    if type(ontology_id) is not int or ontology_id <= 0:
        raise AppError("本体工作空间编号无效", code="VALIDATION_FAILED", status_code=422)
    previous = session.info.get("ontology_id")
    if previous is not None and previous != ontology_id:
        raise AppError("一个数据库会话不能切换本体工作空间", code="ONTOLOGY_SCOPE_CONFLICT", status_code=409)
    session.info["ontology_id"] = ontology_id
    if session.in_transaction():
        await session.execute(
            text("SELECT set_config('rengu.ontology_id', :ontology_id, true)"),
            {"ontology_id": str(ontology_id)},
        )
        from app.common.sql_guard import load_governed_tables

        await load_governed_tables(session, ontology_id)


@event.listens_for(Session, "after_begin")
def _configure_transaction(session, transaction, connection):
    if connection.dialect.name != "postgresql":
        raise RuntimeError("Ontology isolation requires PostgreSQL row level security")
    # Always downgrade, including shared-only sessions. SET LOCAL resets on commit/rollback/pool reuse.
    connection.exec_driver_sql(f"SET LOCAL ROLE {RUNTIME_ROLE}")
    connection.execute(
        text("SELECT set_config('rengu.ontology_id', :ontology_id, true)"),
        {"ontology_id": str(session.info.get("ontology_id") or "")},
    )
