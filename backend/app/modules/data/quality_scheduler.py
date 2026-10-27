"""Published, explicitly armed quality schedules; one current-data check per due slot."""

import hashlib
import json
import logging
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from croniter import croniter
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from app.common.auth import check_func, resolve_user, row_filter
from app.common.ontology import require_ontology_id
from app.common.row_filter import is_deny_all
from app.core import clock
from app.core.config import get_settings
from app.core.errors import AppError, Forbidden
from app.db.dialects import get_adapter
from app.jobs.scope import scoped_sessions, workspace_ids
from app.modules.data.quality_models import QualityCheck, QualityRun, QualityScheduleCursor
from app.modules.versioning.repo import ReleaseRepo

CRONS = {"每 2 小时": "0 */2 * * *", "每天 23:30": "30 23 * * *", "每周一 06:00": "0 6 * * 1"}
logger = logging.getLogger(__name__)


def slot(schedule: str, at: datetime, *, previous: bool = False) -> datetime:
    if schedule not in CRONS:
        raise AppError("质量检查调度配置无效", code="QUALITY_SCHEDULE_INVALID", status_code=422)
    local = at.astimezone(ZoneInfo(get_settings().display_tz))
    iterator = croniter(CRONS[schedule], local + timedelta(microseconds=1) if previous else local)
    return (iterator.get_prev(datetime) if previous else iterator.get_next(datetime)).astimezone(UTC)


def fingerprint(user) -> str:
    value = [
        user.ontology_id,
        user.ontology_role,
        user.id,
        user.func,
        user.roles,
        user.row_filters,
        user.attrs,
    ]
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


async def actor(session, code: str, object_code: str):
    user = await resolve_user(session, code)
    if user.ontology_role not in ("owner", "editor"):
        raise Forbidden("质量检查调度身份须有本体编辑权限")
    check_func(user, "data-quality", "manage")
    if is_deny_all(await row_filter(user, object_code)):
        raise Forbidden("质量检查调度身份没有目标对象的数据权限")
    return user


async def reset(session, check: QualityCheck, obj, at: datetime):
    """Only an explicit CR publication arms a schedule; legacy values and rollbacks do not."""
    from app.modules.data.quality_cr import definition

    cursor = await session.scalar(
        select(QualityScheduleCursor).where(QualityScheduleCursor.check_id == check.id).with_for_update()
    )
    if check.retired_at is not None or check.schedule == "手动":
        if cursor:
            await session.delete(cursor)
        return
    user = await actor(session, check.created_by, obj.code)
    if cursor is None:
        cursor = QualityScheduleCursor(check_id=check.id, created_by=user.id)
        session.add(cursor)
    cursor.check_version = check.lock_version
    cursor.definition = {**definition(check, obj), "ownerId": check.created_by, "retired": False}
    cursor.permission_fingerprint = fingerprint(user)
    cursor.next_at, cursor.last_error = slot(check.schedule, at), None


async def process(session, cursor_id: int):
    # Publication and rollback use the same scoped lock; a definition cannot change during evaluation.
    await get_adapter(session).xact_lock(session, "ontology.publish")
    cursor = await session.scalar(
        select(QualityScheduleCursor)
        .where(QualityScheduleCursor.id == cursor_id, QualityScheduleCursor.next_at <= clock.now())
        .with_for_update(skip_locked=True)
    )
    if cursor is None:
        return
    check = await session.get(QualityCheck, cursor.check_id, with_for_update=True)
    if check is None or check.retired_at or check.schedule == "手动":
        await session.delete(cursor)
        return
    at = clock.now()
    try:
        async with session.begin_nested():
            from app.modules.data.quality import execute_check

            user = await actor(session, check.created_by, cursor.definition["typeKey"])
            if (
                check.lock_version != cursor.check_version
                or fingerprint(user) != cursor.permission_fingerprint
            ):
                raise AppError("调度定义或权限已变化", code="QUALITY_SCHEDULE_CHANGED", status_code=409)
            release = await ReleaseRepo(session).latest()
            published = (
                ((release.snapshot or {}).get("qualityChecks") or {}).get(check.code) if release else None
            )
            if published != cursor.definition:
                raise AppError("已发布检查定义已变化", code="QUALITY_SCHEDULE_CHANGED", status_code=409)
            # Missed slots coalesce to one evaluation of current rows, never synthetic historical results.
            scheduled_at = slot(check.schedule, at, previous=True)
            identity = [require_ontology_id(session), check.id, check.lock_version, scheduled_at.isoformat()]
            job_id = "QS-" + hashlib.sha256(json.dumps(identity).encode()).hexdigest()[:56]
            previous = await session.scalar(
                select(QualityRun.id).where(QualityRun.check_id == check.id, QualityRun.job_id == job_id)
            )
            if previous is None:
                await session.execute(text("SET LOCAL statement_timeout = 15000"))
                await execute_check(
                    session,
                    user,
                    check.code,
                    job_id,
                    scheduled_at=scheduled_at,
                    coalesced_from=cursor.next_at,
                )
    except AppError as error:
        # No quality result is fabricated when permission or configuration prevents execution.
        cursor.next_at = None
        cursor.last_error = f"调度已暂停（{error.code}）；请核对权限或定义后重新发布"
    except DBAPIError:
        cursor.next_at = slot(check.schedule, at)
        cursor.last_error = "本次数据库执行失败，未保存质量结果；下个时段重试"
    except Exception:
        cursor.next_at = None
        cursor.last_error = "调度执行异常，未保存质量结果；请核对后重新发布"
        logger.error(
            "Quality schedule interrupted: workspace %s cursor %s", require_ontology_id(session), cursor_id
        )
    else:
        cursor.next_at, cursor.last_error = slot(check.schedule, at), None


async def sweep(timestamp: int = 0) -> int:
    processed = 0
    for ontology_id in await workspace_ids():
        sessions = scoped_sessions(ontology_id)
        try:
            async with sessions() as session:
                ids = list(
                    await session.scalars(
                        select(QualityScheduleCursor.id)
                        .where(QualityScheduleCursor.next_at <= clock.now())
                        .order_by(QualityScheduleCursor.next_at)
                        .limit(100)
                    )
                )
            for cursor_id in ids:
                try:
                    async with sessions() as session, session.begin():
                        await process(session, cursor_id)
                    processed += 1
                except Exception:
                    logger.error(
                        "Quality schedule transaction interrupted: workspace %s cursor %s",
                        ontology_id,
                        cursor_id,
                    )
        except Exception:
            logger.error("Quality schedule discovery interrupted: workspace %s", ontology_id)
    return processed
