"""Explicit DM transactional trigger CDC, with a private queue and exact ACKs.

Event IDs identify pending work, never a commit-order watermark. PG commits the
mirror and pending ACK list together; source ACK can safely be retried after a crash.
"""
import asyncio
import json
import logging
import uuid
import zlib
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, text, update
from sqlalchemy.dialects.postgresql import insert

from app.common.auth import resolve_user
from app.common.ontology import set_ontology_scope
from app.common.sql_guard import _invalidate_bound
from app.core.errors import AppError
from app.db.dialects import get_adapter
from app.db.session import get_engine, get_sessionmaker
from app.jobs.scope import workspace_ids
from app.modules.data import connector, dm_cdc_source
from app.modules.data.external_mirror import (MirrorRestartRequired, scalar, signature, structure_signature,
    preserved_binding_signature)
from app.modules.data.governed_router import authorize, external_binding_context
from app.modules.data.models import (Datasource, DsSyncMode, ExternalCdcSubscription,
    ExternalMirrorRow, ExternalMirrorRun, ExternalMirrorState, ExternalReadBinding)
from app.modules.data.secrets import decrypt_password
from app.modules.data.service import _require_real

logger = logging.getLogger(__name__)
LEASE_SECONDS = 90
BATCH_SIZE = 100
_last_heartbeat = None
_last_error = None


def now():
    return datetime.now(UTC)


async def schema_ready(session):
    return bool(await session.scalar(text("SELECT to_regclass('public.external_cdc_subscription') IS NOT NULL")))


async def require_schema(session):
    if not await schema_ready(session):
        raise AppError('CDC存储需要增量升级，请管理员应用 data_0049_external_cdc',
                       code='CDC_UPGRADE_REQUIRED', status_code=409)


async def subscription(session, code, lock=False):
    if not await schema_ready(session):
        return None
    query = select(ExternalCdcSubscription).where(ExternalCdcSubscription.object_code == code)
    return await session.scalar(query.with_for_update() if lock else query)


async def require_paused(session, code):
    sub = await subscription(session, code, lock=True)
    if sub and sub.enabled:
        raise AppError('请先暂停该对象CDC，再修改读取绑定或执行全量同步', code='CDC_ACTIVE', status_code=409)
    return sub


def cfg_for(source):
    cfg = {'host': source.host, 'port': source.port, 'database': source.database,
           'user': source.username, 'password': decrypt_password(source.password_enc), 'ssl': source.ssl}
    _require_real(source.type, cfg)
    if not cfg['password']:
        raise AppError('源连接密码不可用', code='CDC_SOURCE_UNAVAILABLE', status_code=409)
    return cfg


async def context(session, user, code, sub=None):
    """Recheck authorization, exact binding version, source identity and selected DDL."""
    oid = session.info['ontology_id']
    await get_adapter(session).xact_lock(session, f'external.read.binding.{oid}.{code}')
    obj, source, schema, name, allowed, primary = await external_binding_context(session, user, code)
    await session.scalar(select(Datasource.id).where(Datasource.id == source.id).with_for_update())
    await session.refresh(source)
    binding = await session.scalar(select(ExternalReadBinding).where(ExternalReadBinding.object_code == code))
    if (not binding or primary not in binding.read_columns or not set(binding.read_columns) <= allowed
        or (binding.datasource_code, binding.schema_name, binding.table_name) != (source.code, schema, name)):
        raise AppError('只读绑定已变化，请重新确认并建立CDC基线', code='CDC_BINDING_CHANGED', status_code=409)
    sig = signature(binding, source, obj)
    if sub and (sub.binding_version != binding.lock_version or sub.binding_signature != sig):
        raise AppError('CDC订阅对应的读取绑定已变化，请重新计划安装并建立基线', code='CDC_BINDING_CHANGED', status_code=409)
    cfg = cfg_for(source)
    reflected = await connector.inspect_tables(source.type.value, cfg, schema, [name])
    if len(reflected) != 1 or reflected[0].pk != [primary]:
        raise AppError('源主键结构已变化，请重新确认映射和CDC基线', code='CDC_STRUCTURE_CHANGED', status_code=409)
    try:
        structure = structure_signature(schema, name, reflected[0], binding.read_columns)
    except MirrorRestartRequired:
        raise AppError('源所选字段结构已变化，请重新确认映射和CDC基线', code='CDC_STRUCTURE_CHANGED', status_code=409) from None
    if sub and sub.capture.get('structureSignature') != structure:
        raise AppError('源所选字段结构已变化，请重新确认映射和CDC基线', code='CDC_STRUCTURE_CHANGED', status_code=409)
    kind = next(c.kind for c in reflected[0].columns if c.name == primary)
    if kind not in ('integer', 'string'):
        raise AppError('CDC仅支持整数或文本单列源主键', code='CDC_PRIMARY_UNSUPPORTED', status_code=422)
    capture = {'schema': schema, 'table': name, 'primaryKey': primary, 'primaryKind': kind,
               'readColumns': list(binding.read_columns), 'structureSignature': structure, 'bindingSignature': sig}
    return obj, source, binding, cfg, capture


def _lease_valid(sub, owner):
    return bool(sub and sub.enabled and sub.lease_owner == owner and sub.lease_until and sub.lease_until > now())


async def assert_lease(session, code, owner):
    sub = await subscription(session, code, lock=True)
    if not _lease_valid(sub, owner):
        raise AppError('CDC消费者租约已失效，本批未发布', code='CDC_LEASE_LOST', status_code=409)
    return sub


def metadata(sub):
    return {'mode': 'cdc', 'captureMode': 'trigger', 'phase': sub.phase, 'enabled': sub.enabled,
            'heartbeatAt': sub.heartbeat_at.isoformat() if sub.heartbeat_at else None,
            'lastObservedAt': sub.last_observed_at.isoformat() if sub.last_observed_at else None,
            'lastAppliedAt': sub.last_applied_at.isoformat() if sub.last_applied_at else None,
            'lastSourceEventAt': sub.last_source_event_at.isoformat() if sub.last_source_event_at else None,
            'pendingCount': sub.pending_count, 'pendingAckCount': len(sub.pending_ack or []),
            'error': sub.error, 'restartRequired': sub.restart_required}


async def publish_metadata(session, sub, state=None):
    state = state or await session.scalar(select(ExternalMirrorState).where(ExternalMirrorState.object_code == sub.object_code))
    if state:
        state.cursor_value = {**(state.cursor_value or {}), 'cdc': metadata(sub),
                              **({'restartRequired': True} if sub.restart_required else {})}
        state.updated_at = now()
        _invalidate_bound(session, sub.ontology_id)


async def queue_snapshot(session, sub, user, obj, source, binding, batch_size=None):
    """Capture is already installed; keep every dirty key until baseline finishes."""
    code = sub.object_code
    await get_adapter(session).xact_lock(session, f'external.mirror.{sub.ontology_id}.{code}')
    state = await session.scalar(select(ExternalMirrorState).where(ExternalMirrorState.object_code == code).with_for_update())
    if state and state.status in ('queued', 'running'):
        run = await session.get(ExternalMirrorRun, state.active_run_id) if state.active_run_id else None
        if not run or run.trigger != 'cdc_snapshot':
            raise AppError('已有全量同步任务，请先等待完成或取消', code='MIRROR_BUSY', status_code=409)
        if run.status in ('queued', 'running'):
            run.status, run.finished_at = 'cancelled', now()
    if not state:
        state = ExternalMirrorState(object_code=code, binding_signature=sub.binding_signature, cursor_value=None,
                                    processed=0, complete=False, created_by=user.id)
        session.add(state)
    chosen_size = batch_size if batch_size is not None else state.batch_size
    chosen_size = chosen_size if type(chosen_size) is int and 1 <= chosen_size <= 1000 else BATCH_SIZE
    run = ExternalMirrorRun(object_code=code, datasource_code=sub.datasource_code, trigger='cdc_snapshot',
        status='queued', started_at=now(), read_count=0, write_count=0, complete=False, actor=user.id)
    session.add(run)
    await session.flush()
    # The stable signature pins the approved source; old rows remain physically retained.
    previous = preserved_binding_signature(state, binding, source, obj, sub.binding_signature)
    state.binding_signature = sub.binding_signature
    state.cursor_value = {'scanId': run.id, **({'previousBindingSignature': previous} if previous else {})}
    state.processed, state.complete, state.status, state.error = 0, False, 'queued', None
    state.active_run_id, state.batch_size, state.updated_at = run.id, chosen_size, now()
    retired = (sub.cursor_value or {}).get('retiredCaptures', [])
    sub.cursor_value = {'snapshotRunId': run.id, 'snapshotComplete': False, **({'retiredCaptures': retired} if retired else {})}
    sub.phase, sub.restart_required = 'snapshot', False
    await publish_metadata(session, sub, state)


def _source_time(value):
    if isinstance(value, datetime):
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    if isinstance(value, str):
        try:
            return _source_time(datetime.fromisoformat(value.replace('Z', '+00:00')))
        except ValueError:
            return None
    return None


def key_json(value):
    if type(value) not in (str, int) or len(str(value)) > 400:
        raise AppError('源主键类型或长度不受支持', code='CDC_PRIMARY_UNSUPPORTED', status_code=409)
    return json.dumps(value, ensure_ascii=False)


async def _ack(oid, code, owner):
    async with get_sessionmaker()() as session:
        session.info['workspace_worker'] = True
        await set_ontology_scope(session, oid)
        sub = await assert_lease(session, code, owner)
        if not sub.pending_ack:
            return
        user = await resolve_user(session, sub.actor)
        authorize(user)
        _, source, _, cfg, _ = await context(session, user, code, sub)
        # Exact ACK only after PG's mirror+pending_ack transaction has committed.
        await dm_cdc_source.ack(source.type.value, cfg, sub.capture, list(sub.pending_ack))
        sub.pending_ack = []
        sub.updated_at, sub.heartbeat_at = now(), now()
        sub.lease_until = now() + timedelta(seconds=LEASE_SECONDS)
        await publish_metadata(session, sub)
        await session.commit()


async def _snapshot(oid, code, owner, user, run_id):
    from app.modules.data.external_mirror import _run_batch
    await _run_batch(oid, code, user, True, run_id, cdc_lease=owner)
    async with get_sessionmaker()() as session:
        session.info['workspace_worker'] = True
        await set_ontology_scope(session, oid)
        sub = await assert_lease(session, code, owner)
        state = await session.scalar(select(ExternalMirrorState).where(ExternalMirrorState.object_code == code))
        if not state or state.active_run_id != run_id:
            raise AppError('CDC基线扫描已被替换，请重新建立基线', code='CDC_BINDING_CHANGED', status_code=409)
        if state.status == 'failed':
            permanent = (state.cursor_value or {}).get('restartRequired')
            raise AppError('CDC基线结构失效，请重新确认映射' if permanent else 'CDC基线读取暂时失败，将从已提交批次重试',
                           code='CDC_STRUCTURE_CHANGED' if permanent else 'CDC_SOURCE_OPERATION_FAILED', status_code=409)
        if state.status not in ('queued', 'succeeded'):
            raise AppError('CDC基线已取消或中断，请明确重新建立基线', code='CDC_STRUCTURE_CHANGED', status_code=409)
        sub.last_observed_at, sub.heartbeat_at, sub.updated_at = now(), now(), now()
        sub.lease_until = now() + timedelta(seconds=LEASE_SECONDS)
        if state.complete:
            sub.cursor_value = {**(sub.cursor_value or {}), 'snapshotComplete': True}
            sub.phase, sub.last_applied_at = 'catching_up', now()
        await publish_metadata(session, sub, state)
        await session.commit()


async def _consume(oid, code, owner):
    async with get_sessionmaker()() as session:
        session.info['workspace_worker'] = True
        await set_ontology_scope(session, oid)
        sub = await assert_lease(session, code, owner)
        user = await resolve_user(session, sub.actor)
        authorize(user)
        if not sub.capture.get('bootstrapFenced'):
            raise AppError('源捕获尚未完成既有事务栅栏，不能建立或消费基线', code='CDC_CAPTURE_INVALID', status_code=409)
        _, source, binding, cfg, _ = await context(session, user, code, sub)
        if source.sync_mode != DsSyncMode.cdc:
            raise AppError('数据源已退出CDC模式，请重新确认后恢复', code='CDC_MODE_CHANGED', status_code=409)
        health = await dm_cdc_source.validate(source.type.value, cfg, sub.capture)
        sub.pending_count = health.get('pendingCount')
        sub.oldest_pending_at = _source_time(health.get('oldestCapturedAt'))
        sub.heartbeat_at, sub.updated_at = now(), now()
        sub.lease_until = now() + timedelta(seconds=LEASE_SECONDS)
        if sub.pending_ack:
            await session.commit()
            await _ack(oid, code, owner)
            return
        if not (sub.cursor_value or {}).get('snapshotComplete'):
            run_id = (sub.cursor_value or {}).get('snapshotRunId')
            state = await session.scalar(select(ExternalMirrorState).where(ExternalMirrorState.object_code == code).with_for_update())
            if not state or state.active_run_id != run_id or (state.cursor_value or {}).get('restartRequired'):
                raise AppError('CDC基线状态失效，请重新建立基线', code='CDC_STRUCTURE_CHANGED', status_code=409)
            if state.status == 'cancelled':
                raise AppError('CDC基线已取消，请明确重新建立基线', code='CDC_STRUCTURE_CHANGED', status_code=409)
            if state.status in ('failed', 'running'):
                # The session advisory lock and fresh subscription token exclude a live older worker.
                # A crash can leave the first _run_batch transition committed as running.
                state.status, state.error = 'queued', None
            sub.phase = 'snapshot'
            await session.commit()
            await _snapshot(oid, code, owner, user, run_id)
            return
        changes = await dm_cdc_source.read_changes(source.type.value, cfg, sub.capture, limit=BATCH_SIZE)
        events = changes['events']
        sub.pending_count = changes.get('pendingCount')
        sub.oldest_pending_at = _source_time(changes.get('oldestCapturedAt'))
        sub.last_observed_at, sub.heartbeat_at, sub.updated_at = now(), now(), now()
        state = await session.scalar(select(ExternalMirrorState).where(ExternalMirrorState.object_code == code).with_for_update())
        if not state or state.binding_signature != sub.binding_signature or not state.complete:
            raise AppError('CDC镜像基线不可用，请重新建立基线', code='CDC_STRUCTURE_CHANGED', status_code=409)
        if events:
            primary, columns = sub.capture['primaryKey'], binding.read_columns
            keys = {key_json(event[k]) for event in events for k in ('oldKey', 'newKey') if event.get(k) is not None}
            rows = {key_json(row[primary]): row for row in changes['rows']}
            if not rows.keys() <= keys:
                raise AppError('源变更批包含非订阅主键，已拒绝发布', code='CDC_CAPTURE_INVALID', status_code=409)
            values = []
            for key, row in rows.items():
                payload = {col: scalar(row[col]) for col in columns}
                if len(json.dumps(payload, ensure_ascii=False).encode()) > 262144:
                    raise AppError('源数据行超过镜像大小限制', code='CDC_PAYLOAD_INVALID', status_code=409)
                values.append({'ontology_id': oid, 'object_code': code, 'source_key': key,
                    'binding_signature': sub.binding_signature, 'payload': payload, 'is_current': True,
                    'last_seen_scan': (state.cursor_value or {}).get('scanId')})
            if values:
                stmt = insert(ExternalMirrorRow).values(values)
                await session.execute(stmt.on_conflict_do_update(index_elements=['ontology_id', 'object_code', 'source_key'],
                    set_={'payload': stmt.excluded.payload, 'binding_signature': stmt.excluded.binding_signature,
                          'is_current': True, 'last_seen_scan': stmt.excluded.last_seen_scan}))
            absent = list(keys - rows.keys())
            if absent:
                await session.execute(update(ExternalMirrorRow).where(ExternalMirrorRow.object_code == code,
                    ExternalMirrorRow.binding_signature == sub.binding_signature, ExternalMirrorRow.source_key.in_(absent))
                    .values(is_current=False, last_seen_scan=None))
            event_ids = [event['eventId'] for event in events]
            if any(type(v) is not int or v <= 0 for v in event_ids) or len(event_ids) != len(set(event_ids)):
                raise AppError('源变更事件身份无效，已拒绝发布', code='CDC_CAPTURE_INVALID', status_code=409)
            sub.pending_ack = event_ids
            sub.applied_count += len(events)
            sub.last_applied_at = now()
            times = [_source_time(e.get('capturedAt')) for e in events]
            times = [t for t in times if t]
            if times:
                sub.last_source_event_at = max([*times, *([sub.last_source_event_at] if sub.last_source_event_at else [])])
            sub.cursor_value = {**(sub.cursor_value or {}), 'lastObservedEventId': event_ids[-1]}
            state.cursor_value = {**(state.cursor_value or {}), 'dataUpdatedAt': now().isoformat(),
                                  'dataPublishedAt': now().isoformat()}
        sub.phase, sub.error, sub.error_code = ('catching_up' if events else 'streaming'), None, None
        state.cursor_value = {**(state.cursor_value or {}), 'dataCheckedAt': now().isoformat()}
        await publish_metadata(session, sub, state)
        # Source calls can outlive their lease. A stale owner cannot publish data or ACK.
        if not _lease_valid(sub, owner):
            raise AppError('CDC消费者租约已失效，本批未发布', code='CDC_LEASE_LOST', status_code=409)
        await session.commit()
    if events:
        await _ack(oid, code, owner)


async def _record_error(oid, code, owner, exc):
    code_value = getattr(exc, 'code', '')
    permanent = code_value in {'CDC_CAPTURE_INVALID', 'CDC_BINDING_CHANGED', 'CDC_STRUCTURE_CHANGED',
        'CDC_PRIMARY_UNSUPPORTED', 'CDC_PAYLOAD_INVALID', 'CDC_MODE_CHANGED', 'NO_PERMISSION', 'NOT_FOUND',
        'CDC_SOURCE_NOT_ALLOWED', 'BINDING_UNAVAILABLE', 'SCHEMA_CHANGED'} or isinstance(exc, MirrorRestartRequired)
    async with get_sessionmaker()() as session:
        await set_ontology_scope(session, oid)
        sub = await subscription(session, code, lock=True)
        if not sub or sub.lease_owner != owner or not sub.enabled:
            return
        sub.phase = 'failed' if permanent else 'reconnecting'
        sub.enabled = not permanent
        sub.restart_required = permanent
        sub.error_code = code_value if code_value else 'CDC_SOURCE_OPERATION_FAILED'
        sub.error = ('CDC捕获、授权绑定或所选结构已失效；保留镜像和日志，请重新确认并建立基线' if permanent
                     else 'CDC源连接或消费暂时失败，保留已提交数据与精确确认批次，将自动重连')
        sub.updated_at = now()
        sub.lease_owner, sub.lease_until = None, None
        await publish_metadata(session, sub)
        await session.commit()


async def tick(oid, code):
    owner = uuid.uuid4().hex
    # This is the existing mirror lock: manual scans, recovery, and CDC share it.
    key = zlib.crc32(f'qa.eval.mirror.{oid}.{code}'.encode())
    async with get_engine().connect() as connection:
        locked = bool(await connection.scalar(text('SELECT pg_try_advisory_lock(:k)'), {'k': key}))
        await connection.commit()
        if not locked:
            return
        try:
            async with get_sessionmaker()() as session:
                session.info['workspace_worker'] = True
                await set_ontology_scope(session, oid)
                sub = await subscription(session, code, lock=True)
                if not sub or not sub.enabled or not sub.installed or sub.restart_required:
                    return
                # Owning the source/object session lock proves an older process no longer owns it.
                # Fence its durable token immediately, including after an unclean process restart.
                sub.lease_owner, sub.lease_until = owner, now() + timedelta(seconds=LEASE_SECONDS)
                sub.heartbeat_at, sub.updated_at = now(), now()
                await session.commit()
            try:
                await _consume(oid, code, owner)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                await _record_error(oid, code, owner, exc)
            finally:
                async with get_sessionmaker()() as session:
                    await set_ontology_scope(session, oid)
                    sub = await subscription(session, code, lock=True)
                    if sub and sub.lease_owner == owner:
                        sub.lease_owner, sub.lease_until = None, None
                        await session.commit()
        finally:
            try:
                await connection.rollback()
                await connection.execute(text('SELECT pg_advisory_unlock(:k)'), {'k': key})
                await connection.commit()
            except BaseException:
                await connection.invalidate()
                raise


async def loop():
    global _last_heartbeat, _last_error
    while True:
        try:
            jobs = []
            for oid in await workspace_ids():
                async with get_sessionmaker()() as session:
                    session.info['workspace_worker'] = True
                    await set_ontology_scope(session, oid)
                    if not await schema_ready(session):
                        continue
                    codes = list(await session.scalars(select(ExternalCdcSubscription.object_code).where(
                        ExternalCdcSubscription.enabled.is_(True), ExternalCdcSubscription.installed.is_(True))))
                    jobs.extend((oid, code) for code in codes)
            # Bounded source concurrency, each consumer keeps one immutable workspace scope.
            semaphore = asyncio.Semaphore(4)
            async def bounded(oid, code):
                async with semaphore:
                    await tick(oid, code)
            results = await asyncio.gather(*(bounded(oid, code) for oid, code in jobs), return_exceptions=True)
            _last_error = 'CDC工作空间消费失败' if any(isinstance(r, BaseException) for r in results) else None
            _last_heartbeat = now()
        except asyncio.CancelledError:
            raise
        except Exception:
            _last_error = 'CDC调度暂时失败'
        await asyncio.sleep(1)
