"""Reviewable source installation and explicit per-object CDC controls."""
import uuid
from datetime import timedelta

from fastapi import Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from app.common.auth import CurrentUserDep
from app.common.ontology import require_ontology_id
from app.core.errors import AppError, VersionConflict
from app.db.dialects import get_adapter
from app.db.session import SessionDep
from app.modules.data import external_cdc as cdc
from app.modules.data.cdc_events import EventMapping
from app.modules.data.external_mirror import signature
from app.modules.data.governed_router import authorize, router
from app.modules.data.models import Datasource, DsSyncMode, DsType, ExternalReadBinding
from app.modules.static.models import ObjectType


class VersionIn(BaseModel):
    model_config = ConfigDict(extra='forbid', populate_by_name=True)
    version: int = Field(ge=1)
    server_id: int | None = Field(default=None, alias='serverId', ge=1, le=4294967295)


class InstallIn(VersionIn):
    plan_digest: str = Field(alias='planDigest', min_length=64, max_length=64)


class EnableIn(VersionIn):
    reinitialize: bool = False
    max_rows: int | None = Field(default=None, alias='maxRows', ge=1, le=1000)


class EventMappingIn(VersionIn):
    mapping: EventMapping | None = None
    mapping_version: int = Field(default=0, alias='mappingVersion', ge=0)


@router.get('/external-read-bindings/{code}/cdc/event-mapping')
async def cdc_event_options(code: str, session: SessionDep, user: CurrentUserDep):
    from app.common.auth import check_func
    from app.modules.data import cdc_events
    authorize(user)
    check_func(user, 'event-types', 'manage')
    sub = await cdc.subscription(session, code)
    if not sub or sub.capture.get('mode') != 'binlog':
        raise AppError('请先预览并确认MySQL binlog订阅配置', code='CDC_PLAN_REQUIRED', status_code=409)
    await cdc.context(session, user, code, sub)
    return await cdc_events.options(session, sub)


@router.put('/external-read-bindings/{code}/cdc/event-mapping')
async def cdc_event_save(code: str, body: EventMappingIn, session: SessionDep,
                         user: CurrentUserDep, request: Request):
    from app.common.auth import check_func
    from app.modules.data import cdc_events
    from app.modules.event.models import EventType
    authorize(user)
    check_func(user, 'event-types', 'manage')
    await _lock(session, code)
    sub = await cdc.require_paused(session, code)
    if not sub or sub.capture.get('mode') != 'binlog':
        raise AppError('请先确认MySQL binlog订阅配置', code='CDC_PLAN_REQUIRED', status_code=409)
    await _version_context(session, user, code, body.version, sub)
    if (sub.cursor_value or {}).get('eventMappingVersion', 0) != body.mapping_version:
        raise VersionConflict()
    value = None
    if body.mapping is not None:
        mapping = body.mapping
        event_type = await session.scalar(select(EventType).where(
            EventType.code == mapping.event_type_id).with_for_update(read=True))
        cdc_events.validate_mapping(mapping, event_type, code, sub.capture['readColumns'])
        value = mapping.model_dump(by_alias=True)
    sub.cursor_value = {**(sub.cursor_value or {}), 'eventMapping': value,
                        'eventMappingVersion': body.mapping_version + 1}
    sub.actor = user.id
    await _audit(session, user, code, '保存CDC行事实事件映射', request)
    await session.flush()
    return await cdc_events.options(session, sub)


async def _audit(session, user, code, action, request):
    from app.modules.admin.repo import insert_audit
    await insert_audit(session, actor=f'{user.name}（{user.id}）', action=action, target=code,
                       ip=request.client.host if request.client else '', created_by=user.id)


async def _lock(session, code):
    oid = require_ontology_id(session)
    await get_adapter(session).xact_lock(session, f'external.mirror.{oid}.{code}')


async def _version_context(session, user, code, version, sub=None):
    result = await cdc.context(session, user, code, sub)
    if result[2].lock_version != version:
        raise VersionConflict()
    return result


def _source_support(source):
    if source is None:
        return False, 'CDC_SOURCE_UNAVAILABLE', 'CDC源数据连接不存在或尚未保存读取绑定'
    if source.type in (DsType.dm8, DsType.mysql):
        return True, None, None
    message = '当前CDC通道支持达梦DM8与MySQL binlog，此数据源类型尚不支持CDC'
    return False, 'CDC_SOURCE_UNSUPPORTED', message


async def status_value(session, code):
    ready = await cdc.schema_ready(session)
    binding = await session.scalar(select(ExternalReadBinding).where(ExternalReadBinding.object_code == code))
    sub = await cdc.subscription(session, code) if ready else None
    source = await session.scalar(select(Datasource).where(Datasource.code == binding.datasource_code)) if binding else None
    supported, support_code, support_error = _source_support(source)
    mismatch = bool(sub and (not binding or binding.lock_version != sub.binding_version))
    if sub and binding:
        obj = await session.scalar(select(ObjectType).where(ObjectType.code == code))
        mismatch = mismatch or not obj or not source or signature(binding, source, obj) != sub.binding_signature
    restart = bool(mismatch or (sub and sub.restart_required))
    age = max(0, (cdc.now() - sub.oldest_pending_at).total_seconds()) if sub and sub.oldest_pending_at else None
    heartbeat = bool(sub and sub.heartbeat_at and sub.heartbeat_at > cdc.now() - timedelta(seconds=30))
    return {'phase': 'failed' if restart else sub.phase if sub else 'not_installed',
        'enabled': bool(supported and sub and sub.enabled), 'installed': bool(supported and sub and sub.installed),
        'captureMode': ('binlog' if source.type == DsType.mysql else 'trigger') if supported else 'unavailable', 'supported': supported,
        'serverId': sub.capture.get('serverId') if sub else None,
        'migrationRequired': not ready, 'version': binding.lock_version if binding else None,
        'subscriptionVersion': sub.binding_version if sub else None,
        'heartbeatAt': sub.heartbeat_at if sub else None, 'leaseUntil': sub.lease_until if sub else None,
        'workerActive': bool(supported and sub and sub.enabled and heartbeat and not restart),
        'pendingAckCount': len(sub.pending_ack or []) if sub else 0,
        'pendingCount': sub.pending_count if sub else None,
        'lastSourceEventAt': sub.last_source_event_at if sub else None,
        'lastObservedAt': sub.last_observed_at if sub else None, 'lastAppliedAt': sub.last_applied_at if sub else None,
        'lastCommitAt': ((sub.cursor_value or {}).get('binlogCursor') or {}).get('committedAt') if sub else None,
        'sourceCommitPosition': (sub.cursor_value or {}).get('binlogCursor') if sub else None, 'sourcePendingAgeSeconds': age,
        'error': (support_error if not supported else
                  'CDC存储需要升级 data_0049_external_cdc' if not ready else
                  'CDC绑定版本或源身份已变化，请重新计划并建立基线' if mismatch else sub.error if sub else None),
        'errorCode': (support_code if not supported else 'CDC_UPGRADE_REQUIRED' if not ready else 'CDC_BINDING_CHANGED' if mismatch else sub.error_code if sub else None),
        'restartRequired': restart,
        'recoveryAction': (None if not supported else 'reinitialize' if restart else 'install' if not sub or not sub.installed else
                           'resume' if sub.phase == 'paused' else 'enable' if not sub.enabled else None),
        'sourceGeneration': sub.capture.get('generation') if sub else None,
        'bootstrapFenced': bool(sub and sub.capture.get('bootstrapFenced')),
        'appliedCount': sub.applied_count if sub else 0,
        'workerHeartbeatAt': cdc._last_heartbeat, 'workerError': cdc._last_error}


@router.get('/external-read-bindings/{code}/cdc')
async def cdc_status(code: str, session: SessionDep, user: CurrentUserDep):
    authorize(user)
    return await status_value(session, code)


@router.post('/external-read-bindings/{code}/cdc/probe')
async def cdc_probe(code: str, session: SessionDep, user: CurrentUserDep):
    authorize(user)
    binding = await session.scalar(select(ExternalReadBinding).where(ExternalReadBinding.object_code == code))
    source = await session.scalar(select(Datasource).where(Datasource.code == binding.datasource_code)) if binding else None
    supported, support_code, support_error = _source_support(source)
    if not supported:
        return {'supported': False, 'captureMode': 'unavailable', 'nativeLogAvailable': None,
                'requiresSourceWrite': False, 'errorCode': support_code,
                'message': support_error + '；未连接源库或安装捕获资源'}
    _, source, _, cfg, _ = await cdc.context(session, user, code)
    details = await cdc.source_adapter(source).probe(source.type.value, cfg)
    mysql = source.type == DsType.mysql
    return {'supported': bool(details.get('binlogSupported') if mysql else details.get('triggerSupported')),
            'captureMode': 'binlog' if mysql else 'trigger',
            'nativeLogAvailable': bool(details.get('nativeLogConfigured')), 'requiresSourceWrite': not mysql,
            'message': 'MySQL ROW/FULL binlog与复制权限探测成功；需配置独有server-id建立订阅，不安装触发器' if mysql else '事务触发器能力可用；安装权限尚未验证，须先预览并明确安装选表捕获', 'details': details}


@router.post('/external-read-bindings/{code}/cdc/plan')
async def cdc_plan(code: str, body: VersionIn, session: SessionDep, user: CurrentUserDep, request: Request):
    authorize(user)
    await cdc.require_schema(session)
    await _lock(session, code)
    sub = await cdc.require_paused(session, code)
    _, source, binding, cfg, capture = await _version_context(session, user, code, body.version)
    same = bool(sub and not sub.restart_required and sub.binding_signature == capture['bindingSignature']
                and sub.capture.get('structureSignature') == capture['structureSignature'])
    capture['generation'] = sub.capture['generation'] if same else uuid.uuid4().hex
    if source.type == DsType.mysql:
        await get_adapter(session).xact_lock(session, f'mysql.cdc.server-id.{require_ontology_id(session)}.{source.code}')
        capture['serverId'] = body.server_id or (sub.capture.get('serverId') if sub else None)
        for other in await session.scalars(select(cdc.ExternalCdcSubscription).where(
            cdc.ExternalCdcSubscription.datasource_code == source.code,
            cdc.ExternalCdcSubscription.object_code != code,
        )):
            if other.capture.get('serverId') == capture['serverId']:
                raise AppError('复制server-id已被另一对象订阅占用', code='CDC_SERVER_ID_CONFLICT', status_code=409)
    plan = await cdc.source_adapter(source).plan(source.type.value, cfg, capture)
    if not sub:
        from app.modules.data.models import ExternalCdcSubscription
        sub = ExternalCdcSubscription(object_code=code, datasource_code=source.code,
            binding_signature=capture['bindingSignature'], binding_version=binding.lock_version,
            capture=plan['capture'], plan_digest=plan['planDigest'], installed=False, enabled=False,
            phase='not_installed', actor=user.id, pending_ack=[], applied_count=0, restart_required=False, updated_at=cdc.now())
        session.add(sub)
    else:
        retired = list((sub.cursor_value or {}).get('retiredCaptures', []))
        if not same:
            # Explicitly retain recovery details; old source objects are never silently dropped.
            # A failed DM installation may already have committed any subset of its DDL.
            retired.append({'capture': sub.capture, 'installed': sub.installed,
                            'pendingAck': list(sub.pending_ack or []), 'planDigest': sub.plan_digest})
        # Re-previewing an unchanged generation preserves proven object IDs and bootstrap fencing.
        sub.capture = {**(sub.capture if same else {}), **plan['capture']}
        sub.binding_signature, sub.binding_version = capture['bindingSignature'], binding.lock_version
        sub.datasource_code, sub.plan_digest, sub.actor = source.code, plan['planDigest'], user.id
        if not same:
            sub.installed, sub.phase, sub.pending_ack = False, 'not_installed', []
            sub.cursor_value = {'retiredCaptures': retired}
            sub.pending_count, sub.oldest_pending_at = None, None
            sub.last_observed_at, sub.last_source_event_at, sub.last_applied_at = None, None, None
            sub.applied_count = 0
        sub.error, sub.error_code, sub.updated_at = None, None, cdc.now()
    await _audit(session, user, code, '预览对象CDC源安装计划', request)
    await session.flush()
    return {**plan, 'requiresSourceWrite': source.type != DsType.mysql, 'retainedCaptureCount': len((sub.cursor_value or {}).get('retiredCaptures', []))}


@router.post('/external-read-bindings/{code}/cdc/install')
async def cdc_install(code: str, body: InstallIn, session: SessionDep, user: CurrentUserDep, request: Request):
    authorize(user)
    await cdc.require_schema(session)
    await _lock(session, code)
    sub = await cdc.require_paused(session, code)
    if not sub or sub.plan_digest != body.plan_digest:
        raise AppError('请先重新预览CDC安装计划', code='CDC_PLAN_REQUIRED', status_code=409)
    _, source, _, cfg, _ = await _version_context(session, user, code, body.version, sub)
    plan = await cdc.source_adapter(source).plan(source.type.value, cfg, sub.capture)
    if plan['planDigest'] != body.plan_digest:
        raise AppError('CDC安装计划已变化，请重新预览', code='CDC_PLAN_CHANGED', status_code=409)
    try:
        installed = await cdc.source_adapter(source).install(source.type.value, cfg, sub.capture)
    except AppError as exc:
        sub.phase, sub.error, sub.error_code, sub.updated_at = 'failed', exc.message, exc.code, cdc.now()
        await session.commit()  # Source DDL may have committed partially; retain its recovery plan and error.
        raise
    sub.capture = installed['capture']
    sub.installed, sub.phase, sub.error, sub.error_code = True, 'installed', None, None
    sub.pending_count, sub.oldest_pending_at = installed.get('pendingCount'), cdc._source_time(installed.get('oldestCapturedAt'))
    sub.updated_at = cdc.now()
    await _audit(session, user, code, '安装对象CDC事务触发器捕获', request)
    await session.flush()
    return await status_value(session, code)


async def _enable(code, body, session, user, request, resume=False):
    authorize(user)
    await cdc.require_schema(session)
    await _lock(session, code)
    sub = await cdc.subscription(session, code, lock=True)
    if not sub or not sub.installed:
        raise AppError('请先预览并安装该对象源端CDC捕获', code='CDC_INSTALL_REQUIRED', status_code=409)
    if not sub.capture.get('bootstrapFenced') and sub.capture.get('mode') != 'binlog':
        raise AppError('源捕获尚未完成既有事务栅栏，请重试同一安装计划后再启用',
                       code='CDC_BOOTSTRAP_REQUIRED', status_code=409)
    obj, source, binding, cfg, _ = await _version_context(session, user, code, body.version, sub)
    if source.sync_mode != DsSyncMode.cdc:
        raise AppError('请先将数据源同步模式保存为实时（CDC）', code='CDC_MODE_REQUIRED', status_code=409)
    await cdc.source_adapter(source).validate(source.type.value, cfg, sub.capture)
    if sub.enabled:
        if getattr(body, 'reinitialize', False):
            raise AppError('请先暂停CDC，再明确重新建立基线', code='CDC_ACTIVE', status_code=409)
        return await status_value(session, code)
    reinitialize = getattr(body, 'reinitialize', False)
    if sub.restart_required and not reinitialize:
        raise AppError('CDC状态已失效，请重新预览安装并明确建立新基线', code='CDC_REINITIALIZE_REQUIRED', status_code=409)
    from app.modules.data.models import ExternalMirrorState
    state = await session.scalar(select(ExternalMirrorState).where(ExternalMirrorState.object_code == code))
    if state and state.status in ('queued', 'running'):
        run_id = (sub.cursor_value or {}).get('snapshotRunId')
        if state.active_run_id != run_id:
            raise AppError('全量扫描仍在运行，请先等待或取消', code='MIRROR_BUSY', status_code=409)
    sub.actor, sub.enabled, sub.error, sub.error_code = user.id, True, None, None
    sub.lease_owner, sub.lease_until = None, None
    if reinitialize or not (sub.cursor_value or {}).get('snapshotRunId'):
        await cdc.queue_snapshot(session, sub, user, obj, source, binding, getattr(body, 'max_rows', None))
    else:
        sub.phase = 'catching_up' if (sub.cursor_value or {}).get('snapshotComplete') else 'snapshot'
    sub.updated_at = cdc.now()
    await cdc.publish_metadata(session, sub)
    await _audit(session, user, code, '恢复对象CDC消费' if resume else '启用对象CDC并建立基线', request)
    await session.flush()
    return await status_value(session, code)


@router.post('/external-read-bindings/{code}/cdc/enable')
async def cdc_enable(code: str, body: EnableIn, session: SessionDep, user: CurrentUserDep, request: Request):
    return await _enable(code, body, session, user, request)


@router.post('/external-read-bindings/{code}/cdc/resume')
async def cdc_resume(code: str, body: VersionIn, session: SessionDep, user: CurrentUserDep, request: Request):
    return await _enable(code, body, session, user, request, resume=True)


@router.post('/external-read-bindings/{code}/cdc/pause')
async def cdc_pause(code: str, session: SessionDep, user: CurrentUserDep, request: Request):
    authorize(user)
    await cdc.require_schema(session)
    await _lock(session, code)
    sub = await cdc.subscription(session, code, lock=True)
    if sub:
        sub.enabled, sub.phase, sub.updated_at = False, 'paused', cdc.now()
        sub.lease_owner, sub.lease_until = None, None
        await cdc.publish_metadata(session, sub)
        await _audit(session, user, code, '暂停对象CDC消费并保留源捕获日志', request)
        await session.flush()
    return await status_value(session, code)


@router.post('/external-read-bindings/{code}/cdc/consume-once')
async def cdc_consume_once(code: str, body: VersionIn, session: SessionDep, user: CurrentUserDep):
    """Advance only this authorized subscription; never scan or start a global worker."""
    authorize(user)
    sub = await cdc.subscription(session, code)
    if not sub or not sub.enabled or not sub.installed:
        raise AppError('请先确认并启用当前对象CDC订阅', code='CDC_INSTALL_REQUIRED', status_code=409)
    await _version_context(session, user, code, body.version, sub)
    oid = require_ontology_id(session)
    await session.commit()  # tick owns an independent transaction and the same binding locks.
    await cdc.tick(oid, code)
    session.expire_all()
    return await status_value(session, code)
