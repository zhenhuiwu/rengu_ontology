"""DM transactional trigger CDC and MySQL committed row-image binlog CDC.

Event IDs identify pending work, never a commit-order watermark. PG commits the
mirror and pending ACK list together; source ACK can safely be retried after a crash.
"""
import asyncio
import json
import logging
import time
import uuid
import zlib
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, text, update
from sqlalchemy.dialects.postgresql import insert

from app.common.auth import resolve_user
from app.common.ontology import set_ontology_scope
from app.core.errors import AppError
from app.db.dialects import get_adapter
from app.db.session import get_engine, get_sessionmaker
from app.jobs.scope import workspace_ids
from app.modules.data import connector, dm_cdc_source, mysql_cdc_source
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


def source_adapter(source):
    if source.type.value == 'MySQL':
        return mysql_cdc_source
    return dm_cdc_source


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
    return await session.scalar(query.with_for_update().execution_options(populate_existing=True) if lock else query)


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
    # Consumers of different objects share one datasource; FOR SHARE lets them
    # read the pinned credentials concurrently while datasource edits still wait.
    await session.scalar(select(Datasource.id).where(Datasource.id == source.id).with_for_update(read=True))
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
    if len(reflected) == 1 and reflected[0].kind == 'view':
        raise AppError('视图只能验证唯一键后执行镜像同步，不能订阅binlog CDC', code='CDC_SOURCE_UNSUPPORTED', status_code=422)
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


async def fast_context(session, user, code, sub):
    """Hot-path identity check without re-reflecting the source schema each tick.

    The source structure is verified by dm_cdc_source.validate/read_changes on the
    same tick. Here we only prove the stored binding still matches the subscription.
    """
    from app.modules.static.models import ObjectStatus, ObjectType
    from app.modules.data.external_mirror import signature_matches

    authorize(user)
    obj = await session.scalar(select(ObjectType).where(ObjectType.code == code))
    if not obj or obj.status != ObjectStatus.published:
        raise AppError('对象已撤回发布，CDC订阅不可继续', code='CDC_BINDING_CHANGED', status_code=409)
    source = await session.scalar(select(Datasource).where(Datasource.code == sub.datasource_code))
    binding = await session.scalar(select(ExternalReadBinding).where(ExternalReadBinding.object_code == code))
    if not source or not binding or binding.lock_version != sub.binding_version:
        raise AppError('只读绑定已变化，请重新确认并建立CDC基线', code='CDC_BINDING_CHANGED', status_code=409)
    if not signature_matches(sub.binding_signature, binding, source, obj):
        raise AppError('CDC订阅对应的读取绑定已变化，请重新计划安装并建立基线', code='CDC_BINDING_CHANGED', status_code=409)
    return source, binding, cfg_for(source)


_capture_checked: dict[tuple[int, str], float] = {}
CAPTURE_CHECK_SECONDS = 10


async def _idle_probe(oid, code, source, cfg, sub, session):
    """Return True when the object is idle; an empty journal only reads the log."""
    key = (oid, code)
    try:
        status = await dm_cdc_source.pending(source.type.value, cfg, sub.capture)
    except AppError:
        # Classify a missing/disabled capture with the full check instead of
        # leaving the subscription in an endless reconnect loop.
        await dm_cdc_source.validate(source.type.value, cfg, sub.capture)
        raise
    sub.pending_count = status.get('pendingCount')
    sub.oldest_pending_at = _source_time(status.get('oldestCapturedAt'))
    sub.last_observed_at, sub.heartbeat_at, sub.updated_at = now(), now(), now()
    if status.get('pendingCount'):
        return False
    state = await session.scalar(select(ExternalMirrorState).where(ExternalMirrorState.object_code == code))
    if not state or state.binding_signature != sub.binding_signature or not state.complete:
        raise AppError('CDC镜像基线不可用，请重新建立基线', code='CDC_STRUCTURE_CHANGED', status_code=409)
    if time.monotonic() - _capture_checked.get(key, 0) >= CAPTURE_CHECK_SECONDS:
        await dm_cdc_source.validate(source.type.value, cfg, sub.capture)
        _capture_checked[key] = time.monotonic()
    sub.phase, sub.error, sub.error_code = 'streaming', None, None
    # Idle ticks only refresh the lease/heartbeat; mirror metadata is published
    # when a batch actually changes data, so idle polling stays write-free.
    await session.commit()
    return True


def _lease_valid(sub, owner):
    return bool(sub and sub.enabled and sub.lease_owner == owner and sub.lease_until and sub.lease_until > now())


async def assert_lease(session, code, owner):
    sub = await subscription(session, code, lock=True)
    if not _lease_valid(sub, owner):
        raise AppError('CDC消费者租约已失效，本批未发布', code='CDC_LEASE_LOST', status_code=409)
    return sub


def metadata(sub):
    return {'mode': 'cdc', 'captureMode': sub.capture.get('mode', 'trigger'), 'phase': sub.phase, 'enabled': sub.enabled,
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
        # Data batches do not change the governed binding definitions. Invalidating
        # here would race concurrent QA sessions into a transient empty binding set.


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
    event_mapping = (sub.cursor_value or {}).get('eventMapping')
    sub.cursor_value = {'snapshotRunId': run.id, 'snapshotComplete': False,
                        'eventMappingVersion': (sub.cursor_value or {}).get('eventMappingVersion', 0),
                        **({'eventMapping': event_mapping} if event_mapping else {}),
                        **({'retiredCaptures': retired} if retired else {})}
    if sub.capture.get('mode') == 'binlog':
        state.cursor_value = {**(state.cursor_value or {}), 'binlogCatchupPending': True}
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
        source, _, cfg = await fast_context(session, user, code, sub)
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
        if not sub.capture.get('bootstrapFenced') and sub.capture.get('mode') != 'binlog':
            raise AppError('源捕获尚未完成既有事务栅栏，不能建立或消费基线', code='CDC_CAPTURE_INVALID', status_code=409)
        source, binding, cfg = await fast_context(session, user, code, sub)
        if source.sync_mode != DsSyncMode.cdc:
            raise AppError('数据源已退出CDC模式，请重新确认后恢复', code='CDC_MODE_CHANGED', status_code=409)
        if sub.capture.get('mode') == 'binlog':
            return await _consume_mysql(session, sub, source, binding, cfg, owner, user)
        sub.heartbeat_at, sub.updated_at = now(), now()
        sub.lease_until = now() + timedelta(seconds=LEASE_SECONDS)
        if sub.pending_ack:
            await session.commit()
            await _ack(oid, code, owner)
            return 1
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
            return 1
        if await _idle_probe(oid, code, source, cfg, sub, session):
            return 0
        changes = await dm_cdc_source.read_changes(source.type.value, cfg, sub.capture, limit=BATCH_SIZE)
        _capture_checked[(oid, code)] = time.monotonic()
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
    return len(events)


def _mysql_value(oid, sub, row, scan_id):
    payload = {column: scalar(row[column]) for column in sub.capture['readColumns']}
    if len(json.dumps(payload, ensure_ascii=False).encode()) > 262144:
        raise AppError('源数据行超过镜像大小限制', code='CDC_PAYLOAD_INVALID', status_code=409)
    return dict(ontology_id=oid, object_code=sub.object_code, source_key=key_json(payload[sub.capture['primaryKey']]),
                binding_signature=sub.binding_signature, payload=payload, is_current=False, last_seen_scan=scan_id)


async def _consume_mysql(session, sub, source, binding, cfg, owner, user):
    """One lease, complete committed transactions, cursor and mirror atomically published in PG."""
    from app.modules.data.models import ExternalCdcChange
    from app.modules.data import cdc_events
    event_mapping = await cdc_events.prepare(session, user, sub)
    oid, code = sub.ontology_id, sub.object_code
    state = await session.scalar(select(ExternalMirrorState).where(ExternalMirrorState.object_code == code).with_for_update())
    if not state or state.binding_signature != sub.binding_signature:
        raise AppError('CDC镜像绑定已变化', code='CDC_BINDING_CHANGED', status_code=409)
    if not (sub.cursor_value or {}).get('snapshotComplete'):
        scan_id = uuid.uuid4().int % (2**63 - 1)
        state.complete, state.processed, state.status = False, 0, 'running'
        await session.execute(update(ExternalMirrorRow).where(ExternalMirrorRow.object_code == code).values(is_current=False))
        async with mysql_cdc_source.snapshot(cfg, sub.capture) as (start, pages):
            async for rows in pages(state.batch_size):
                await assert_lease(session, code, owner)
                values = [_mysql_value(oid, sub, row, scan_id) for row in rows]
                if values:
                    statement = insert(ExternalMirrorRow).values(values)
                    await session.execute(statement.on_conflict_do_update(index_elements=['ontology_id', 'object_code', 'source_key'],
                        set_={'payload': statement.excluded.payload, 'binding_signature': statement.excluded.binding_signature,
                              'is_current': False, 'last_seen_scan': scan_id}))
                state.processed += len(rows)
                state.cursor_value = {**(state.cursor_value or {}), 'scanId': scan_id}
                if not _lease_valid(sub, owner):
                    raise AppError('CDC消费者租约失效，本批未发布', code='CDC_LEASE_LOST', status_code=409)
                sub.lease_until, sub.heartbeat_at = now() + timedelta(seconds=LEASE_SECONDS), now()
                await publish_metadata(session, sub, state)
                await session.commit()
            target = await mysql_cdc_source.current_position(cfg, sub.capture)
        await assert_lease(session, code, owner)
        sub.cursor_value = {**(sub.cursor_value or {}), 'snapshotComplete': True,
                            'binlogCursor': start, 'catchupTarget': {k: target[k] for k in ('file', 'position')}}
        state.cursor_value = {**(state.cursor_value or {}), 'scanId': scan_id}
        sub.phase, sub.lease_until = 'catching_up', now() + timedelta(seconds=LEASE_SECONDS)
        await session.commit()
        return state.processed
    cursor = sub.cursor_value['binlogCursor']
    changes = await mysql_cdc_source.read_changes(cfg, sub.capture, cursor)
    await assert_lease(session, code, owner)
    scan_id = state.cursor_value['scanId']
    for event in changes['events']:
        before, after = event['before'], event['after']
        record = dict(ontology_id=oid, object_code=code, event_key=event['eventKey'], source_position=event['position'],
                      operation=event['op'], before_image=before, after_image=after,
                      captured_at=_source_time(event['at']))
        await session.execute(insert(ExternalCdcChange).values(**record).on_conflict_do_nothing(
            index_elements=['ontology_id', 'object_code', 'event_key']))
        await cdc_events.emit(session, sub, event_mapping, event)
        if before is not None and (after is None or before[sub.capture['primaryKey']] != after[sub.capture['primaryKey']]):
            await session.execute(update(ExternalMirrorRow).where(ExternalMirrorRow.object_code == code,
                ExternalMirrorRow.source_key == key_json(before[sub.capture['primaryKey']])).values(is_current=False, last_seen_scan=None))
        if after is not None:
            value = _mysql_value(oid, sub, after, scan_id)
            value['is_current'] = state.complete
            statement = insert(ExternalMirrorRow).values(**value)
            await session.execute(statement.on_conflict_do_update(index_elements=['ontology_id', 'object_code', 'source_key'],
                set_={'payload': statement.excluded.payload, 'binding_signature': statement.excluded.binding_signature,
                      'is_current': statement.excluded.is_current, 'last_seen_scan': scan_id}))
        sub.last_source_event_at = _source_time(event['at'])
    sub.cursor_value = {**sub.cursor_value, 'binlogCursor': changes['position']}
    target = sub.cursor_value['catchupTarget']
    published = not state.complete and mysql_cdc_source.position_reached(changes['position'], target)
    if published:
        await session.execute(update(ExternalMirrorRow).where(ExternalMirrorRow.object_code == code).values(
            is_current=(ExternalMirrorRow.last_seen_scan == scan_id)))
        state.complete, state.status = True, 'succeeded'
        state.cursor_value = {**state.cursor_value, 'binlogCatchupPending': False}
        run = await session.get(ExternalMirrorRun, state.active_run_id) if state.active_run_id else None
        if run:
            run.status, run.complete, run.finished_at = 'succeeded', True, now()
            run.read_count, run.write_count = state.processed, state.processed
    sub.phase, sub.error, sub.error_code = ('streaming' if state.complete else 'catching_up'), None, None
    sub.applied_count += len(changes['events'])
    if not _lease_valid(sub, owner):
        raise AppError('CDC消费者租约失效，本批未发布', code='CDC_LEASE_LOST', status_code=409)
    sub.last_observed_at, sub.heartbeat_at, sub.lease_until = now(), now(), now() + timedelta(seconds=LEASE_SECONDS)
    if changes['events']:
        sub.last_applied_at = now()
    state.cursor_value = {**state.cursor_value, 'dataCheckedAt': now().isoformat(),
                          'deletionsAligned': state.complete}
    # An idle source check is not a data write or a newly published baseline.
    if changes['events'] or published:
        state.cursor_value = {**state.cursor_value, 'dataUpdatedAt': now().isoformat()}
    if published:
        state.cursor_value = {**state.cursor_value, 'dataPublishedAt': now().isoformat()}
    await publish_metadata(session, sub, state)
    if not _lease_valid(sub, owner):
        raise AppError('CDC消费者租约失效，本批未发布', code='CDC_LEASE_LOST', status_code=409)
    await session.commit()
    return len(changes['events'])


async def _record_error(oid, code, owner, exc):
    code_value = getattr(exc, 'code', '')
    permanent = code_value in {'CDC_CAPTURE_INVALID', 'CDC_BINDING_CHANGED', 'CDC_STRUCTURE_CHANGED',
        'CDC_PRIMARY_UNSUPPORTED', 'CDC_PAYLOAD_INVALID', 'CDC_MODE_CHANGED', 'NO_PERMISSION', 'NOT_FOUND',
        'CDC_SOURCE_NOT_ALLOWED', 'BINDING_UNAVAILABLE', 'SCHEMA_CHANGED', 'CDC_BINLOG_GAP',
        'CDC_BINLOG_CONFIG_INVALID', 'CDC_TRANSACTION_TOO_LARGE', 'CDC_SOURCE_UNSUPPORTED',
        'CDC_EVENT_MAPPING_INVALID'} or isinstance(exc, MirrorRestartRequired)
    async with get_sessionmaker()() as session:
        await set_ontology_scope(session, oid)
        sub = await subscription(session, code, lock=True)
        if not sub or sub.lease_owner != owner or not sub.enabled:
            return
        sub.phase = 'paused' if code_value == 'CDC_EVENT_MAPPING_INVALID' else 'failed' if permanent else 'reconnecting'
        sub.enabled = not permanent
        sub.restart_required = permanent and code_value != 'CDC_EVENT_MAPPING_INVALID'
        sub.error_code = code_value if code_value else 'CDC_SOURCE_OPERATION_FAILED'
        sub.error = ('CDC捕获、授权绑定或所选结构已失效；保留镜像和日志，请重新确认并建立基线' if permanent
                     else 'CDC源连接或消费暂时失败，保留已提交数据与精确确认批次，将自动重连')
        if isinstance(exc, AppError) and code_value in {'CDC_BINLOG_GAP', 'CDC_BINLOG_CONFIG_INVALID',
                'CDC_TRANSACTION_TOO_LARGE', 'CDC_STRUCTURE_CHANGED', 'CDC_EVENT_MAPPING_INVALID'}:
            sub.error = exc.message
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
                result = await _consume(oid, code, owner)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                await _record_error(oid, code, owner, exc)
                result = 0
            finally:
                async with get_sessionmaker()() as session:
                    await set_ontology_scope(session, oid)
                    sub = await subscription(session, code, lock=True)
                    if sub and sub.lease_owner == owner:
                        sub.lease_owner, sub.lease_until = None, None
                        await session.commit()
            return result
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
            worked = any(isinstance(r, int) and r > 0 for r in results)
        except asyncio.CancelledError:
            raise
        except Exception:
            _last_error = 'CDC调度暂时失败'
            worked = False
        await asyncio.sleep(0.1 if worked else 0.4)
