"""Explicitly armed DM mirror schedules, evaluated by the API lifespan."""
import asyncio
import logging
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from croniter import croniter
from sqlalchemy import select

from app.common.auth import resolve_user
from app.common.ontology import set_ontology_scope
from app.core.config import get_settings
from app.modules.data.governed_router import router
from app.common.auth import CurrentUserDep
from app.modules.data.governed_router import authorize
from app.core.errors import AppError
from app.db.dialects import get_adapter
from app.db.session import get_engine, get_sessionmaker
from sqlalchemy.ext.asyncio import AsyncSession
from app.jobs.scope import workspace_ids
from app.modules.data.models import Datasource, DsSyncMode, DsType, ExternalMirrorRun, ExternalMirrorState, ExternalReadBinding

logger = logging.getLogger(__name__)
_last_tick = None
_last_error = None


def next_slot(mode, cron, now=None):
    if mode in (DsSyncMode.manual, DsSyncMode.cdc):
        return None
    if mode != DsSyncMode.full:
        raise AppError('外部镜像仅支持定时全量或手动', code='MIRROR_SCHEDULE_INVALID', status_code=422)
    if len(cron.split()) != 5 or not croniter.is_valid(cron):
        raise AppError('定时同步需要有效的五段 Cron 表达式', code='MIRROR_SCHEDULE_INVALID', status_code=422)
    local = (now or datetime.now(UTC)).astimezone(ZoneInfo(get_settings().display_tz))
    return croniter(cron, local).get_next(datetime).astimezone(UTC)


async def _scheduled_object(oid, code, source_code, actor, run_id):
    async with get_engine().connect() as connection:
        lock_session = AsyncSession(bind=connection)
        async with get_adapter(lock_session).session_lock(connection, f'qa.eval.mirror.{oid}.{code}'):
            await _scheduled_object_locked(oid, code, source_code, actor, run_id)


async def _scheduled_object_locked(oid, code, source_code, actor, run_id):
    from app.modules.data.external_mirror import preserved_binding_signature, require_scan_schema, run_batch, signature
    from app.modules.data.governed_router import authorize, external_binding_context

    async with get_sessionmaker()() as session:
        session.info['workspace_worker'] = True
        await set_ontology_scope(session, oid)
        await get_adapter(session).xact_lock(session, f'external.mirror.{oid}.{code}')
        state = await session.scalar(select(ExternalMirrorState).where(ExternalMirrorState.object_code == code).with_for_update())
        run = await session.scalar(select(ExternalMirrorRun).where(ExternalMirrorRun.id == run_id).with_for_update())
        if not run or run.status != 'queued':
            return
        try:
            async with session.begin_nested():
                user = await resolve_user(session, actor)
                authorize(user)
                await require_scan_schema(session)
                if state and state.status in ('queued', 'running'):
                    raise AppError('对象已有同步任务正在运行', code='MIRROR_BUSY', status_code=409)
                obj, source, schema, name, allowed, primary = await external_binding_context(session, user, code)
                from app.modules.data.external_cdc import require_paused
                await require_paused(session, code)
                binding = await session.scalar(select(ExternalReadBinding).where(ExternalReadBinding.object_code == code))
                if not binding or source.code != source_code or (binding.schema_name, binding.table_name) != (schema, name) or primary not in binding.read_columns or not set(binding.read_columns) <= allowed:
                    raise AppError('只读绑定已变更，请重新配置', code='MIRROR_BINDING_REQUIRED', status_code=409)
                if state is None:
                    state = ExternalMirrorState(object_code=code, binding_signature='', cursor_value=None,
                                                processed=0, complete=False, created_by=actor)
                    session.add(state)
                sig = signature(binding, source, obj)
                previous = preserved_binding_signature(state, binding, source, obj, sig)
                state.binding_signature = sig
                state.cursor_value = {'scanId': run.id, **({'previousBindingSignature': previous} if previous else {})}
                state.processed, state.complete = 0, False
                state.batch_size, state.status, state.error, state.active_run_id = 200, 'queued', None, run.id
                state.updated_at = datetime.now(UTC)
            await session.commit()
        except Exception:
            run.status, run.error, run.finished_at = 'failed', '调度授权、只读绑定或源结构不可用，请检查后重试', datetime.now(UTC)
            await session.commit()
            return
    while True:
        global _last_tick
        _last_tick = datetime.now(UTC)
        async with get_sessionmaker()() as session:
            session.info['workspace_worker'] = True
            await set_ontology_scope(session, oid)
            run = await session.get(ExternalMirrorRun, run.id)
            source = await session.scalar(select(Datasource).where(Datasource.code == source_code))
            if run is None or run.status not in ('queued', 'running'):
                return
            try:
                user = await resolve_user(session, actor)
                authorize(user)
                if source is None or source.mirror_schedule_actor != actor or source.sync_mode != DsSyncMode.full:
                    raise ValueError('schedule disabled')
            except Exception:
                state = await session.scalar(select(ExternalMirrorState).where(ExternalMirrorState.object_code == code).with_for_update())
                run.status, run.error, run.finished_at = 'failed', '同步已暂停或调度身份权限失效', datetime.now(UTC)
                if state and state.active_run_id == run.id:
                    state.status, state.error = 'failed', run.error
                    state.updated_at = datetime.now(UTC)
                await session.commit()
                return
        try:
            await run_batch(oid, code, user, scheduled=True, run_id=run.id)
        except Exception:
            async with get_sessionmaker()() as session:
                await set_ontology_scope(session, oid)
                run = await session.get(ExternalMirrorRun, run.id)
                state = await session.scalar(select(ExternalMirrorState).where(ExternalMirrorState.object_code == code).with_for_update())
                if run and run.status in ('queued', 'running'):
                    run.status, run.error, run.finished_at = 'failed', '工作空间暂停或同步执行中断', datetime.now(UTC)
                    if state and state.active_run_id == run.id:
                        state.status, state.error = 'failed', run.error
                        state.updated_at = datetime.now(UTC)
                    await session.commit()
            return


async def sweep():
    global _last_error
    for oid in await workspace_ids():
        try:
            async with get_sessionmaker()() as session:
                session.info['workspace_worker'] = True
                await set_ontology_scope(session, oid)
                ids = list(await session.scalars(select(Datasource.id).where(
                    Datasource.type.in_((DsType.dm8, DsType.mysql)), Datasource.mirror_schedule_actor.is_not(None),
                    Datasource.sync_next_run <= datetime.now(UTC)).order_by(Datasource.sync_next_run).limit(20)))
            for source_id in ids:
                async with get_sessionmaker()() as session:
                    session.info['workspace_worker'] = True
                    await set_ontology_scope(session, oid)
                    source = await session.scalar(select(Datasource).where(Datasource.id == source_id, Datasource.sync_next_run <= datetime.now(UTC)).with_for_update(skip_locked=True))
                    if not source or not source.mirror_schedule_actor:
                        continue
                    actor, source_code = source.mirror_schedule_actor, source.code
                    source.sync_next_run = next_slot(source.sync_mode, source.sync_cron)
                    codes = list(await session.scalars(select(ExternalReadBinding.object_code).where(ExternalReadBinding.datasource_code == source_code)))
                    runs = []
                    for code in codes:
                        run = ExternalMirrorRun(object_code=code, datasource_code=source_code, trigger='scheduled',
                                                status='queued', started_at=datetime.now(UTC), read_count=0, write_count=0,
                                                complete=False, actor=actor)
                        session.add(run)
                        runs.append((code, run))
                    if not codes:
                        session.add(ExternalMirrorRun(object_code='', datasource_code=source_code, trigger='scheduled',
                            status='failed', started_at=datetime.now(UTC), finished_at=datetime.now(UTC), read_count=0, write_count=0,
                            complete=False, actor=actor, error='没有已授权的外部对象绑定'))
                    await session.flush()
                    claimed = [(code, run.id) for code, run in runs]
                    await session.commit()
                for code, run_id in claimed:
                    await _scheduled_object(oid, code, source_code, actor, run_id)
        except Exception:
            _last_error = '调度器处理工作空间失败，请查看服务状态'
            logger.error('镜像调度失败：工作空间 %s', oid)


async def loop():
    global _last_tick, _last_error
    while True:
        _last_tick = datetime.now(UTC)
        try:
            _last_error = None
            from app.modules.data.external_mirror import recover_interrupted_mirrors
            await recover_interrupted_mirrors()
            async with get_engine().connect() as connection:
                lock_session = AsyncSession(bind=connection)
                async with get_adapter(lock_session).session_lock(connection, 'qa.eval.mirror.scheduler'):
                    await sweep()
        except Exception:
            _last_error = '调度器执行失败，请查看服务状态'
        await asyncio.sleep(60)


@router.get('/mirror-scheduler/status')
async def scheduler_status(user: CurrentUserDep):
    authorize(user)
    active = bool(not _last_error and _last_tick and (datetime.now(UTC) - _last_tick).total_seconds() < 180)
    return {'active': active, 'lastHeartbeat': _last_tick, 'error': _last_error}
