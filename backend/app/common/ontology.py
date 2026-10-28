"""Trusted transaction scope. PostgreSQL RLS is the boundary for ORM and native SQL alike."""

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.core.errors import AppError, NotFound

DEFAULT_ONTOLOGY_ID = 1
RUNTIME_ROLE = "rengu_ontology_scoped"


class WorkspaceInactive(NotFound):
    """Workspace was deleted or its background work was paused."""


def _check_active(connection, ontology_id: int) -> None:
    key = f"ontology-lifecycle:{ontology_id}"
    connection.execute(text("SELECT pg_advisory_xact_lock_shared(hashtextextended(:key, 0))"), {"key": key})
    active = connection.execute(text("SELECT deleted_at IS NULL FROM ontology_workspace WHERE id=:id"), {"id": ontology_id}).scalar_one_or_none()
    if active is not True:
        raise WorkspaceInactive("本体工作空间不存在或已删除")


def _check_worker_enabled(connection, ontology_id: int) -> None:
    enabled = connection.execute(text("SELECT NOT workers_paused FROM ontology_workspace WHERE id=:id"), {"id": ontology_id}).scalar_one_or_none()
    if enabled is not True:
        raise WorkspaceInactive("工作空间后台任务已暂停")


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
        await session.run_sync(lambda sync: _check_active(sync.connection(), ontology_id))
        if session.info.get("workspace_worker"):
            await session.run_sync(lambda sync: _check_worker_enabled(sync.connection(), ontology_id))
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
    if session.info.get("ontology_id") is not None:
        _check_active(connection, session.info["ontology_id"])
        if session.info.get("workspace_worker"):
            _check_worker_enabled(connection, session.info["ontology_id"])
