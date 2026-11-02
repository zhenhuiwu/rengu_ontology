"""Bounded, workspace-scoped read-only source mirror batches."""
import asyncio
import hashlib
import json
import logging
import re
from datetime import UTC, date, datetime
from decimal import Decimal

from fastapi import BackgroundTasks
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import column, func, or_, select, table, text, update
from sqlalchemy.dialects.postgresql import insert

from app.common.auth import CurrentUserDep, resolve_user
from app.common.ontology import require_ontology_id, set_ontology_scope
from app.db.dialects import get_adapter
from app.db.session import SessionDep, get_engine, get_sessionmaker
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.errors import AppError
from app.modules.data import connector
from app.modules.data.governed_router import authorize, external_binding_context, router
from app.modules.data.models import Datasource, ExternalMirrorRow, ExternalMirrorRun, ExternalMirrorState, ExternalReadBinding
from app.modules.data.secrets import decrypt_password
from app.modules.data.service import _require_real
from app.modules.static.models import ObjectStatus, ObjectType


class MirrorError(ValueError):
    pass


class MirrorRestartRequired(MirrorError):
    pass


_manual_tasks = set()
logger = logging.getLogger(__name__)


def _manual_done(task):
    _manual_tasks.discard(task)
    if not task.cancelled() and task.exception() is not None:
        logger.error('镜像手动任务中断，已提交批次由恢复检查保留')


async def start_manual_mirror(oid, code, user, run_id):
    # Keep the task alive independently of the HTTP request; recovery retains committed cursors.
    task = asyncio.create_task(run_batch(oid, code, user, run_id=run_id))
    _manual_tasks.add(task)
    task.add_done_callback(_manual_done)


class MirrorIn(BaseModel):
    model_config = ConfigDict(extra='forbid', populate_by_name=True)
    max_rows: int = Field(default=200, ge=1, le=1000, alias='maxRows')
    restart: bool = False


def signature(binding, source, obj):
    """Only source identity and selected semantic/security mappings invalidate a mirror."""
    from app.modules.qa.object_source import primary_property

    primary = primary_property(obj)
    selected = set(binding.read_columns or [])
    data = [binding.datasource_code, binding.schema_name, binding.table_name, sorted(selected), obj.source_table, obj.status.value,
            source.code,
            [source.type.value, source.host, source.port, source.database, source.username, source.ssl],
            primary.source_column if primary else None,
            sorted((p.source_column, p.data_type, p.is_sensitive, p.visibility, p.transform or '')
                   for p in obj.properties if p.source_column in selected)]
    return hashlib.sha256(json.dumps(data, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


def _legacy_signature(binding, source, obj):
    # Existing hashes can be checked internally; never expose this credential-derived value.
    data = [binding.datasource_code, binding.schema_name, binding.table_name, binding.read_columns,
            binding.lock_version, [source.type.value, source.host, source.port, source.database, source.username, source.ssl, source.password_enc], obj.lock_version,
            sorted((p.api_name, p.source_column, p.data_type, p.is_sensitive, p.visibility, p.transform or '', p.lock_version)
                   for p in obj.properties)]
    return hashlib.sha256(json.dumps(data, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


def signature_matches(value, binding, source, obj):
    return value == signature(binding, source, obj) or value == _legacy_signature(binding, source, obj)


def preserved_binding_signature(state, binding, source, obj, sig):
    """Carry a proven legacy current set across repeated restarts of an unfinished upgrade."""
    if state.binding_signature != sig and signature_matches(state.binding_signature, binding, source, obj):
        return state.binding_signature
    previous = (state.cursor_value or {}).get('previousBindingSignature')
    if not state.complete and isinstance(previous, str) and len(previous) == 64 and previous != sig:
        # A matching stable signature also proves the source/mappings under which we saved previous.
        if state.binding_signature == sig or signature_matches(previous, binding, source, obj):
            return previous
    return None


def scalar(value):
    if value is None or type(value) in (str, int, bool, float):
        return value
    if isinstance(value, (date, datetime, Decimal)):
        return str(value)
    raise MirrorError('源字段类型暂不支持镜像')


def structure_signature(schema, name, reflected, columns):
    physical = {column.name: column.kind for column in reflected.columns}
    if reflected.name != name or not set(columns) <= physical.keys():
        raise MirrorRestartRequired('源结构变化：已授权字段或源表不可用，请重新确认只读绑定')
    data = [schema, name, reflected.pk, sorted((column, physical[column]) for column in columns)]
    return hashlib.sha256(json.dumps(data, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


def current_rows(state):
    """Unfinished scans retain old current rows; only a complete scan publishes removals."""
    scan_id = (state.cursor_value or {}).get('scanId')
    current = ExternalMirrorRow.is_current.is_(True)
    return or_(current, ExternalMirrorRow.last_seen_scan == scan_id) if not state.complete and type(scan_id) is int else current


def current_rows_sql(state):
    scan_id = (state.cursor_value or {}).get('scanId')
    return f'(is_current = TRUE OR last_seen_scan = {scan_id})' if not state.complete and type(scan_id) is int else 'is_current = TRUE'


def mirror_binding_signatures(state):
    signatures = [state.binding_signature]
    previous = (state.cursor_value or {}).get('previousBindingSignature')
    if not state.complete and isinstance(previous, str) and len(previous) == 64 and previous not in signatures:
        signatures.append(previous)
    return signatures


async def scan_schema_ready(session):
    return bool(await session.scalar(text(
        "SELECT count(*) = 2 FROM pg_attribute WHERE attrelid = to_regclass('public.external_mirror_row') "
        "AND attname IN ('last_seen_scan', 'is_current') AND NOT attisdropped")))


async def require_scan_schema(session):
    if not await scan_schema_ready(session):
        raise AppError('镜像存储需要增量升级，请管理员先应用 data_0048_mirror_scan_visibility',
                       code='MIRROR_UPGRADE_REQUIRED', status_code=409)


def mirror_metadata(state):
    cursor = state.cursor_value or {}
    full = bool(state.complete and state.status == 'succeeded' and cursor.get('deletionsAligned'))
    cdc = cursor.get('cdc') or {}
    return {'objectType': state.object_code, 'mode': 'cdc' if cdc else 'mirror', 'status': state.status,
            'mirrorCompleteness': 'full' if full else 'partial', 'deletionsAligned': full,
            'dataUpdatedAt': cursor.get('dataUpdatedAt'), 'dataCheckedAt': cursor.get('dataCheckedAt'),
            'dataPublishedAt': cursor.get('dataPublishedAt'), **({'cdc': cdc} if cdc else {})}


def source_metadata_fields(sources):
    return ({'dataSources': sources,
             'mirrorCompleteness': 'partial' if any(s['mirrorCompleteness'] == 'partial' for s in sources) else 'full'}
            if sources else {})


async def run_batch(oid, code, user, scheduled=False, run_id=None):
    if scheduled:
        await _run_batch(oid, code, user, True, run_id)
        return
    try:
        async with get_engine().connect() as connection:
            lock_session = AsyncSession(bind=connection)
            async with get_adapter(lock_session).session_lock(connection, f'qa.eval.mirror.{oid}.{code}'):
                while await _run_batch(oid, code, user, False, run_id):
                    await asyncio.sleep(0)
    except Exception:
        await _fail_batch(oid, code, run_id, '工作空间暂停或同步执行中断，已保留完成的批次，请重试')


async def _fail_batch(oid, code, run_id, message):
    async with get_sessionmaker()() as session:
        await set_ontology_scope(session, oid)
        await get_adapter(session).xact_lock(session, f'external.mirror.{oid}.{code}')
        state = await session.scalar(select(ExternalMirrorState).where(ExternalMirrorState.object_code == code).with_for_update())
        if state and state.status in ('queued', 'running') and state.active_run_id == run_id:
            state.status, state.error = 'failed', message
            state.updated_at = datetime.now(UTC)
            run = await session.get(ExternalMirrorRun, run_id) if run_id else None
            if run:
                run.status, run.error, run.finished_at = 'failed', message, datetime.now(UTC)
            await session.commit()


async def _run_batch(oid, code, user, scheduled, run_id, cdc_lease=None):
    async with get_sessionmaker()() as session:
        session.info['workspace_worker'] = True
        await set_ontology_scope(session, oid)
        await require_scan_schema(session)
        await get_adapter(session).xact_lock(session, f'external.mirror.{oid}.{code}')
        if cdc_lease:
            from app.modules.data.external_cdc import assert_lease
            await assert_lease(session, code, cdc_lease)
        state = await session.scalar(select(ExternalMirrorState).where(ExternalMirrorState.object_code == code).with_for_update())
        if not state or state.status != 'queued' or (run_id is not None and state.active_run_id != run_id):
            return False
        run_id = state.active_run_id
        state.status = 'running'
        state.updated_at = datetime.now(UTC)
        run = await session.get(ExternalMirrorRun, state.active_run_id) if state.active_run_id else None
        if run:
            run.status = 'running'
        await session.commit()
    try:
        async with get_sessionmaker()() as session:
            session.info['workspace_worker'] = True
            await set_ontology_scope(session, oid)
            await get_adapter(session).xact_lock(session, f'external.mirror.{oid}.{code}')
            if cdc_lease:
                from app.modules.data.external_cdc import assert_lease
                await assert_lease(session, code, cdc_lease)
            state = await session.scalar(select(ExternalMirrorState).where(ExternalMirrorState.object_code == code).with_for_update())
            if not state or state.status != 'running' or state.active_run_id != run_id:
                return False
            user = await resolve_user(session, user.id)
            authorize(user)
            await get_adapter(session).xact_lock(session, f'external.read.binding.{oid}.{code}')
            obj, source, schema, name, allowed, primary = await external_binding_context(session, user, code)
            await session.scalar(select(Datasource.id).where(Datasource.id == source.id).with_for_update())
            await session.refresh(source)
            binding = await session.scalar(select(ExternalReadBinding).where(ExternalReadBinding.object_code == code))
            if (not binding or binding.read_columns is None or primary not in binding.read_columns
                or not set(binding.read_columns) <= allowed
                or (binding.datasource_code, binding.schema_name, binding.table_name) != (source.code, schema, name)
                or not signature_matches(state.binding_signature, binding, source, obj)):
                raise MirrorRestartRequired('源连接或只读绑定已改变，请重新开始同步')
            password = decrypt_password(source.password_enc)
            cfg = {'host': source.host, 'port': source.port, 'database': source.database,
                   'user': source.username, 'password': password, 'ssl': source.ssl}
            _require_real(source.type, cfg)
            if not password:
                raise MirrorError('源连接密码不可用')
            reflected = await connector.inspect_tables(source.type.value, cfg, schema, [name])
            if len(reflected) != 1 or reflected[0].pk != [primary]:
                raise MirrorRestartRequired('源结构变化：需要与对象映射一致的单列源主键')
            structure = structure_signature(schema, name, reflected[0], binding.read_columns)
            previous_structure = (state.cursor_value or {}).get('structure')
            if previous_structure and previous_structure != structure:
                raise MirrorRestartRequired('源结构已改变，已保留完成批次；请重新确认对象映射及只读绑定后重新开始')
            cursor = (state.cursor_value or {}).get('value')
            scan_id = (state.cursor_value or {}).get('scanId')
            if type(scan_id) is not int or scan_id <= 0:
                if state.processed:
                    raise MirrorRestartRequired('旧任务没有扫描标识，请重新开始以安全核对源已删除行')
                scan_id = run_id
            if type(scan_id) is not int or scan_id <= 0:
                raise MirrorRestartRequired('扫描标识不可用，请重新开始同步')
            cols = list(binding.read_columns)
            src = table(name, *(column(c) for c in cols), schema=schema)
            query = select(*(src.c[c] for c in cols)).order_by(src.c[primary]).limit(state.batch_size + 1)
            if cursor is not None:
                query = query.where(src.c[primary] > cursor)
            rows = await connector._read(source.type.value, cfg, lambda conn: [dict(row._mapping) for row in conn.execute(query)])
            batch = rows[:state.batch_size]
            values = []
            seen_keys = set()
            for row in batch:
                key = row[primary]
                if type(key) not in (str, int) or len(str(key)) > 400:
                    raise MirrorError('主键缺失或类型不支持：仅支持长度不超过 400 的整数或文本单列主键')
                if key in seen_keys:
                    raise MirrorError('源数据包含重复主键，镜像未写入')
                seen_keys.add(key)
                payload = {k: scalar(row[k]) for k in cols}
                if len(json.dumps(payload, ensure_ascii=False).encode()) > 262144:
                    raise MirrorError('源数据行超过镜像大小限制')
                values.append({'ontology_id': oid, 'object_code': code, 'source_key': json.dumps(key, ensure_ascii=False),
                               'binding_signature': state.binding_signature, 'payload': payload,
                               'last_seen_scan': scan_id, 'is_current': False})
            if values:
                stmt = insert(ExternalMirrorRow).values(values)
                await session.execute(stmt.on_conflict_do_update(index_elements=['ontology_id', 'object_code', 'source_key'], set_={
                    'payload': stmt.excluded.payload, 'binding_signature': stmt.excluded.binding_signature,
                    'last_seen_scan': stmt.excluded.last_seen_scan}))
            checked_at = datetime.now(UTC)
            state.cursor_value = {**(state.cursor_value or {}), 'scanId': scan_id, 'structure': structure,
                                  'dataCheckedAt': checked_at.isoformat(),
                                  **({'value': batch[-1][primary], 'dataUpdatedAt': checked_at.isoformat()} if batch else {})}
            state.processed += len(batch)
            state.complete = len(rows) <= state.batch_size
            if state.complete:
                seen = func.coalesce(ExternalMirrorRow.last_seen_scan == scan_id, False)
                await session.execute(update(ExternalMirrorRow).where(
                    ExternalMirrorRow.ontology_id == oid, ExternalMirrorRow.object_code == code,
                    ExternalMirrorRow.binding_signature == state.binding_signature,
                    ExternalMirrorRow.is_current.is_distinct_from(seen),
                ).values(is_current=seen))
                state.cursor_value = {key: value for key, value in state.cursor_value.items() if key != 'previousBindingSignature'}
                state.cursor_value = {**state.cursor_value, 'deletionsAligned': True, 'dataPublishedAt': checked_at.isoformat()}
            run = await session.get(ExternalMirrorRun, state.active_run_id) if state.active_run_id else None
            if run:
                run.read_count += len(batch)
                run.write_count += len(batch)
                run.complete = state.complete
            more = not state.complete
            state.status = 'queued' if more else 'succeeded'
            state.updated_at = checked_at
            state.error = None
            if run:
                run.status = 'running' if more else 'succeeded'
                if not more:
                    run.finished_at = checked_at
            if state.complete:
                from app.common.sql_guard import _invalidate_bound
                _invalidate_bound(session, oid)
            if cdc_lease:
                from app.modules.data.external_cdc import assert_lease
                await assert_lease(session, code, cdc_lease)
            await session.commit()
            return more
    except Exception as exc:
        async with get_sessionmaker()() as session:
            await set_ontology_scope(session, oid)
            await get_adapter(session).xact_lock(session, f'external.mirror.{oid}.{code}')
            state = await session.scalar(select(ExternalMirrorState).where(ExternalMirrorState.object_code == code).with_for_update())
            if state and state.status == 'running' and state.active_run_id == run_id:
                state.status = 'failed'
                state.updated_at = datetime.now(UTC)
                if isinstance(exc, MirrorRestartRequired):
                    state.cursor_value = {**(state.cursor_value or {}), 'restartRequired': True}
                state.error = (str(exc) if isinstance(exc, MirrorError) else connector.err_text(exc, None) if isinstance(exc, connector.SourceStageError) else '源读取、结构核验或镜像写入失败，请核对源连接及只读绑定后重试')
                run = await session.get(ExternalMirrorRun, state.active_run_id) if state.active_run_id else None
                if run:
                    run.status, run.error, run.finished_at = 'failed', state.error, datetime.now(UTC)
                await session.commit()
        return False


@router.get('/external-read-bindings/{code}/mirror')
async def mirror_status(code: str, session: SessionDep, user: CurrentUserDep):
    authorize(user)
    state = await session.scalar(select(ExternalMirrorState).where(ExternalMirrorState.object_code == code))
    count = await session.scalar(select(func.count()).select_from(ExternalMirrorRow).where(ExternalMirrorRow.object_code == code))
    binding = await session.scalar(select(ExternalReadBinding).where(ExternalReadBinding.object_code == code))
    obj = await session.scalar(select(ObjectType).where(ObjectType.code == code))
    source = await session.scalar(select(Datasource).where(Datasource.code == binding.datasource_code)) if binding else None
    schema_ready = await scan_schema_ready(session)
    stale = bool(state and ((state.cursor_value or {}).get('restartRequired') or not binding or not obj or not source or not signature_matches(state.binding_signature, binding, source, obj)))
    run = await session.get(ExternalMirrorRun, state.active_run_id) if state and state.active_run_id else None
    current_count = await session.scalar(select(func.count()).select_from(ExternalMirrorRow).where(
        ExternalMirrorRow.object_code == code, ExternalMirrorRow.binding_signature.in_(mirror_binding_signatures(state)),
        current_rows(state))) if state and schema_ready else 0
    restart_required = bool(state and (stale or state.complete or (state.cursor_value or {}).get('restartRequired')))
    return {'mode': 'full', 'status': state.status if state else 'not_started', 'processed': state.processed if state else 0,
            'totalStored': count, 'currentStored': current_count,
            'complete': bool(state and state.complete and not stale and schema_ready), 'stale': stale,
            'error': (state.error if state else None) if schema_ready else '镜像存储需要增量升级，请管理员应用 data_0048_mirror_scan_visibility',
            'errorCode': None if schema_ready else 'MIRROR_UPGRADE_REQUIRED', 'migrationRequired': not schema_ready,
            'updatedAt': state.updated_at if state else None, 'lastFinishedAt': run.finished_at if run else None,
            'dataUpdatedAt': (state.cursor_value or {}).get('dataUpdatedAt') if state else None,
            'dataCheckedAt': (state.cursor_value or {}).get('dataCheckedAt') if state else None,
            'dataPublishedAt': (state.cursor_value or {}).get('dataPublishedAt') if state else None,
            'runId': state.active_run_id if state else None, 'batchSize': state.batch_size if state else None,
            'resumable': bool(state and state.status in ('failed', 'cancelled') and not restart_required and schema_ready),
            'restartRequired': restart_required,
            'deletionsAligned': bool(state and state.complete and (state.cursor_value or {}).get('deletionsAligned') and not stale and schema_ready),
            'sourceFingerprint': signature(binding, source, obj) if binding and source and obj else None,
            'structureFingerprint': (state.cursor_value or {}).get('structure') if state else None}


@router.post('/external-read-bindings/{code}/mirror')
async def queue_mirror(code: str, body: MirrorIn, background: BackgroundTasks, session: SessionDep, user: CurrentUserDep):
    oid = require_ontology_id(session)
    authorize(user)
    await require_scan_schema(session)
    await get_adapter(session).xact_lock(session, f'external.mirror.{oid}.{code}')
    from app.modules.data.external_cdc import require_paused
    cdc_sub = await require_paused(session, code)
    obj, source, schema, name, allowed, primary = await external_binding_context(session, user, code)
    binding = await session.scalar(select(ExternalReadBinding).where(ExternalReadBinding.object_code == code))
    if not binding or primary not in binding.read_columns or not set(binding.read_columns) <= allowed or (binding.schema_name, binding.table_name, binding.datasource_code) != (schema, name, source.code):
        raise AppError('请先保存与对象一致的只读绑定', code='MIRROR_BINDING_REQUIRED', status_code=409)
    sig = signature(binding, source, obj)
    state = await session.scalar(select(ExternalMirrorState).where(ExternalMirrorState.object_code == code).with_for_update())
    if state and state.status in ('queued', 'running'):
        previous_run = await session.get(ExternalMirrorRun, state.active_run_id) if state.active_run_id else None
        if not (cdc_sub and not cdc_sub.enabled and previous_run and previous_run.trigger == 'cdc_snapshot'):
            raise AppError('已有同步任务正在运行', code='MIRROR_BUSY', status_code=409)
        previous_run.status, previous_run.finished_at = 'cancelled', datetime.now(UTC)
    if cdc_sub:
        cdc_sub.restart_required = True
        cdc_sub.error_code, cdc_sub.error = 'CDC_REINITIALIZE_REQUIRED', '手动全量替换了CDC基线，请重新建立CDC基线后恢复'
    if state is None:
        state = ExternalMirrorState(object_code=code, binding_signature=sig, cursor_value=None, processed=0, complete=False, created_by=user.id)
        session.add(state)
    elif not body.restart and (not signature_matches(state.binding_signature, binding, source, obj) or state.complete
                              or (state.cursor_value or {}).get('restartRequired')
                              or (state.processed and not (state.cursor_value or {}).get('scanId'))):
        raise AppError('源结构或绑定已变化，或同步已完成，请确认后重新开始', code='MIRROR_RESTART_REQUIRED', status_code=409)
    if body.restart:
        previous = preserved_binding_signature(state, binding, source, obj, sig)
        state.cursor_value = {'previousBindingSignature': previous} if previous else None
        state.processed, state.complete = 0, False
        state.binding_signature = sig
    state.batch_size, state.status, state.error = body.max_rows, 'queued', None
    state.updated_at = datetime.now(UTC)
    run = ExternalMirrorRun(object_code=code, datasource_code=source.code, trigger='manual', status='queued',
                            started_at=datetime.now(UTC), read_count=0, write_count=0, complete=False, actor=user.id)
    session.add(run)
    await session.flush()
    state.active_run_id = run.id
    if not (state.cursor_value or {}).get('scanId'):
        state.cursor_value = {**(state.cursor_value or {}), 'scanId': run.id}
    await session.flush()
    await session.commit()  # The background worker must only see a committed queue record.
    background.add_task(start_manual_mirror, oid, code, user, run.id)
    return {'status': 'queued', 'mode': 'full', 'processed': state.processed, 'complete': False, 'runId': run.id}


@router.post('/external-read-bindings/{code}/mirror/cancel')
async def cancel_mirror(code: str, session: SessionDep, user: CurrentUserDep):
    """Cancel at the next committed batch boundary, preserving rows and the resume cursor."""
    oid = require_ontology_id(session)
    authorize(user)
    await get_adapter(session).xact_lock(session, f'external.mirror.{oid}.{code}')
    from app.modules.data.external_cdc import require_paused, publish_metadata
    cdc_sub = await require_paused(session, code)
    state = await session.scalar(select(ExternalMirrorState).where(ExternalMirrorState.object_code == code).with_for_update())
    if state and state.status in ('queued', 'running'):
        state.status, state.error = 'cancelled', None
        state.updated_at = datetime.now(UTC)
        run = await session.get(ExternalMirrorRun, state.active_run_id) if state.active_run_id else None
        if run and run.status in ('queued', 'running'):
            run.status, run.finished_at = 'cancelled', datetime.now(UTC)
        if cdc_sub and run and run.trigger == 'cdc_snapshot':
            cdc_sub.restart_required, cdc_sub.phase = True, 'failed'
            cdc_sub.error_code, cdc_sub.error = 'CDC_REINITIALIZE_REQUIRED', 'CDC基线已取消，请明确重新建立基线'
            await publish_metadata(session, cdc_sub, state)
        await session.commit()
    return {'status': state.status if state else 'not_started', 'processed': state.processed if state else 0,
            'complete': bool(state and state.complete)}


async def recover_interrupted_mirrors():
    from app.common.ontology import WorkspaceInactive
    from app.jobs.scope import workspace_ids

    for oid in await workspace_ids():
        try:
            async with get_sessionmaker()() as session:
                await set_ontology_scope(session, oid)
                states = list(await session.scalars(select(ExternalMirrorState).where(ExternalMirrorState.status.in_(['queued', 'running']))))
                for state in states:
                    if not await get_adapter(session).try_xact_lock(session, f'qa.eval.mirror.{oid}.{state.object_code}'):
                        continue
                    await session.refresh(state)
                    if state.status in ('queued', 'running'):
                        run = await session.get(ExternalMirrorRun, state.active_run_id) if state.active_run_id else None
                        if run and run.trigger == 'cdc_snapshot':
                            # The durable CDC subscription owns resuming this scan, including after restart/pause.
                            continue
                        state.status = 'failed'
                        state.updated_at = datetime.now(UTC)
                        state.error = '任务因服务重启中断，已保留完成的批次，请重试'
                        run = await session.get(ExternalMirrorRun, state.active_run_id) if state.active_run_id else None
                        if run and run.status in ('queued', 'running'):
                            run.status, run.error, run.finished_at = 'failed', state.error, datetime.now(UTC)
                if await get_adapter(session).try_xact_lock(session, 'qa.eval.mirror.scheduler'):
                    orphaned = list(await session.scalars(select(ExternalMirrorRun).where(
                        ExternalMirrorRun.trigger == 'scheduled', ExternalMirrorRun.status.in_(['queued', 'running']))))
                    for run in orphaned:
                        if await get_adapter(session).try_xact_lock(session, f'qa.eval.mirror.{oid}.{run.object_code}'):
                            run.status, run.error, run.finished_at = 'failed', '任务因服务重启中断，请等待下次计划或手动重试', datetime.now(UTC)
                await session.commit()
        except WorkspaceInactive:
            continue


_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")
_CASTS = {"integer": "bigint", "int": "bigint", "decimal": "numeric", "boolean": "boolean", "date": "date", "timestamp": "timestamptz", "string": "text", "enum": "text"}


def _sql_text(value):
    return "'" + str(value).replace("'", "''") + "'"


def _sql_ident(name):
    if name.islower() and _IDENT.fullmatch(name):
        return name
    return '"' + name.replace('"', '""') + '"'


def _logical_name(code, taken):
    from pydantic.alias_generators import to_snake
    name = to_snake(re.sub(r"[^A-Za-z0-9_]", "_", code))
    if not _IDENT.fullmatch(name):
        name = "ext_" + name[:58]
    if not _IDENT.fullmatch(name) or name == "external_mirror_row":
        return None
    return name if name.casefold() not in {value.casefold() for value in taken} else None


def _internal_column(name, taken):
    occupied = {value.casefold() for value in taken}
    candidate = name
    suffix = 0
    while candidate.casefold() in occupied:
        suffix += 1
        candidate = f'{name}_{suffix}'
    return candidate


async def governed_mirrors(session, oid, taken_names, taken_objects):
    """已同步的外部只读镜像，作为当前本体问数可查询的表。未完成同步的只记录原因。"""
    from sqlalchemy import BigInteger, Boolean, Column, Date, DateTime, MetaData, Numeric, String, Table
    from sqlalchemy.orm import selectinload
    from app.common.sql_guard import GovernedTable
    from app.modules.qa.object_source import primary_property

    states = list(await session.scalars(select(ExternalMirrorState)))
    if not await scan_schema_ready(session):
        return {}, [state.object_code for state in states if state.object_code not in taken_objects]
    from collections import Counter
    proposed = Counter(_logical_name(state.object_code, ()) for state in states if state.object_code not in taken_objects)
    seen = {state.object_code for state in states}
    gaps = []
    for binding in await session.scalars(select(ExternalReadBinding)):
        if binding.object_code not in seen and binding.object_code not in taken_objects:
            gaps.append(binding.object_code)
    metadata = {
        obj.code: (obj, binding, source)
        for obj, binding, source in await session.execute(
            select(ObjectType, ExternalReadBinding, Datasource)
            .outerjoin(ExternalReadBinding, (ExternalReadBinding.object_code == ObjectType.code)
                       & (ExternalReadBinding.ontology_id == oid))
            .outerjoin(Datasource, (Datasource.code == ExternalReadBinding.datasource_code)
                       & (Datasource.ontology_id == oid))
            .where(ObjectType.ontology_id == oid, ObjectType.code.in_(seen))
            .options(selectinload(ObjectType.properties))
        )
    } if states else {}
    result = {}
    names = set(taken_names)
    def column_type(kind):
        if kind == "timestamp":
            return DateTime(timezone=True)
        return {"integer": BigInteger, "int": BigInteger, "decimal": Numeric, "boolean": Boolean, "date": Date, "string": String, "enum": String}[kind]()
    for state in states:
        obj, binding, source = metadata.get(state.object_code, (None, None, None))
        label = obj.display_name if obj else state.object_code
        if obj is None or obj.code in taken_objects:
            continue
        if obj.status != ObjectStatus.published:
            gaps.append(label)
            continue
        ready = state.status in ('queued', 'running', 'succeeded', 'failed', 'cancelled') and not (state.cursor_value or {}).get('restartRequired')
        if (not ready or not binding or not source or
            obj.datasource_name not in (source.code, source.name) or
            obj.source_table != f"{binding.schema_name}.{binding.table_name}"):
            gaps.append(label)
            continue
        if not signature_matches(state.binding_signature, binding, source, obj):
            gaps.append(label)
            continue
        props = [p for p in obj.properties if p.source_column in set(binding.read_columns) and _IDENT.fullmatch(p.source_column or "")
                 and p.data_type in _CASTS and not p.is_sensitive and p.visibility != "hidden" and not p.transform]
        primary = primary_property(obj)
        logical = _logical_name(obj.code, names)
        if primary not in props or logical is None or proposed[logical] != 1:
            gaps.append(label)
            continue
        unique_props = {prop.source_column: prop for prop in props}
        if any(_CASTS[prop.data_type] != _CASTS[unique_props[prop.source_column].data_type] for prop in props):
            gaps.append(label)
            continue
        row_identity = _internal_column('__mirror_row_id', unique_props)
        scope_column = _internal_column('__mirror_ontology_id', [*unique_props, row_identity])
        columns = [Column(row_identity, BigInteger), Column(scope_column, BigInteger)]
        projected = []
        for prop in unique_props.values():
            columns.append(Column(prop.source_column, column_type(prop.data_type), comment=prop.display_name or prop.api_name))
            projected.append(f"(payload->>{_sql_text(prop.source_column)})::{_CASTS[prop.data_type]} AS {_sql_ident(prop.source_column)}")
        table = Table(logical, MetaData(), *columns, comment=f"{label}（外部数据同步）")
        fields = {prop.source_column: table.c[prop.source_column] for prop in props}
        names.add(logical)
        # The old registry used this fallback only when the original code could not be a SQL identifier.
        legacy = "ext_" + re.sub(r"[^A-Za-z0-9_]", "_", obj.code)[:50]
        aliases = (obj.code, legacy) if not _IDENT.fullmatch(obj.code) and _IDENT.fullmatch(legacy) else (obj.code,)
        result[logical] = GovernedTable(
            table, obj.code, fields, field_of_prop={prop.display_name: prop.source_column for prop in props if prop.display_name},
            ontology_id=oid, projection=frozenset(fields), aliases=aliases,
            row_identity=row_identity, scope_column=scope_column, source_metadata=mirror_metadata(state), source_sql=(
                f"SELECT id AS {_sql_ident(row_identity)}, ontology_id AS {_sql_ident(scope_column)}, " + ", ".join(projected)
                + " FROM public.external_mirror_row WHERE object_code = " + _sql_text(obj.code)
                + " AND binding_signature IN (" + ', '.join(_sql_text(value) for value in mirror_binding_signatures(state)) + ")"
                + " AND ontology_id = " + str(oid)
                + " AND " + current_rows_sql(state)
            ),
        )
    return result, gaps


async def mirror_source(session, obj):
    from app.core.config import get_settings
    from app.modules.qa.object_source import ObjectSource, TYPES, primary_property

    oid = require_ontology_id(session)
    state = await session.scalar(select(ExternalMirrorState).where(ExternalMirrorState.object_code == obj.code))
    if not state:
        return None
    await require_scan_schema(session)
    if state.status not in ('queued', 'running', 'succeeded', 'failed', 'cancelled'):
        raise AppError('镜像没有可读取的已提交结果', code='MIRROR_INCOMPLETE', status_code=409)
    if (state.cursor_value or {}).get('restartRequired'):
        raise AppError('源结构或绑定已变化，请重新确认后重新同步', code='MIRROR_STALE', status_code=409)
    binding = await session.scalar(select(ExternalReadBinding).where(ExternalReadBinding.object_code == obj.code))
    if not binding:
        raise AppError('镜像绑定已撤销，请重新配置', code='MIRROR_STALE', status_code=409)
    source = await session.scalar(select(Datasource).where(Datasource.code == binding.datasource_code))
    if (not source or obj.datasource_name not in (source.code, source.name) or
        obj.source_table != f"{binding.schema_name}.{binding.table_name}"):
        raise AppError('对象来源与原读取授权不一致，请重新配置授权后同步', code='MIRROR_STALE', status_code=409)
    if not signature_matches(state.binding_signature, binding, source, obj):
        raise AppError('镜像绑定或对象定义已变化，请重新同步', code='MIRROR_STALE', status_code=409)
    props = [p for p in obj.properties if p.source_column in binding.read_columns and not p.is_sensitive
             and p.visibility != 'hidden' and not p.transform and p.data_type in TYPES]
    primary = primary_property(obj)
    if not primary or primary not in props or not get_settings().qa_readonly_database_url:
        raise AppError('镜像主键或平台只读连接不可用', code='MIRROR_UNAVAILABLE', status_code=503)
    physical = {p.source_column: TYPES[p.data_type]() for p in props}
    row = ExternalMirrorRow.__table__
    projection = [row.c.payload[name].as_string().cast(typ).label(name) for name, typ in physical.items()]
    view = select(*projection).where(row.c.ontology_id == oid, row.c.object_code == obj.code,
                                    row.c.binding_signature.in_(mirror_binding_signatures(state)), current_rows(state)).subquery('external_mirror')
    columns = {p.api_name: p.source_column for p in props}
    result = ObjectSource(view, columns, primary.source_column, get_settings().qa_readonly_database_url, source.code, oid)
    result.mirror_metadata = mirror_metadata(state)
    result.mirror_completeness = result.mirror_metadata['mirrorCompleteness']
    return result


@router.get('/external-read-bindings/{code}/mirror/runs')
async def mirror_runs(code: str, session: SessionDep, user: CurrentUserDep, page: int = 1, size: int = 20):
    authorize(user)
    if page < 1 or not 1 <= size <= 100:
        raise AppError('分页参数无效', code='VALIDATION_FAILED', status_code=422)
    q = select(ExternalMirrorRun).where(ExternalMirrorRun.object_code == code)
    total = await session.scalar(select(func.count()).select_from(ExternalMirrorRun).where(ExternalMirrorRun.object_code == code))
    rows = list(await session.scalars(q.order_by(ExternalMirrorRun.id.desc()).offset((page - 1) * size).limit(size)))
    return {'items': [{'id': r.id, 'datasourceId': r.datasource_code, 'trigger': r.trigger, 'status': r.status,
                       'startedAt': r.started_at, 'finishedAt': r.finished_at, 'readCount': r.read_count,
                       'writeCount': r.write_count, 'complete': r.complete, 'error': r.error} for r in rows],
            'total': total, 'page': page, 'size': size}
