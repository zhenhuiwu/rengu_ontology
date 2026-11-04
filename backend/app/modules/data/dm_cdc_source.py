"""Opt-in, subscription-private DM transactional dirty-key capture.

Sequence values identify events, never commit order. Every read includes all
visible pending rows; only exact IDs applied durably by the sink may be ACKed.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
import threading
from datetime import UTC, datetime
from sqlalchemy import column, inspect, select, table, text
from sqlalchemy.sql import quoted_name

from app.core.errors import AppError
from app.modules.data import connector

KIND = '达梦 DM8'


class SourceCaptureInvalid(AppError):
    status_code, code, message = 409, 'CDC_CAPTURE_INVALID', '源CDC捕获配置已失效，请重新建立完整基线'


def _quote(value):
    if not isinstance(value, str) or not value or '\x00' in value or len(value) > 128:
        raise SourceCaptureInvalid('CDC源标识符无效')
    return '"' + value.replace('"', '""') + '"'


def _hash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _capture(value):
    result = dict(value)
    generation = result.get('generation', '')
    if not isinstance(generation, str) or not re.fullmatch('[0-9a-f]{32}', generation):
        raise SourceCaptureInvalid('CDC generation必须是新的UUID hex值')
    for name in ('schema', 'table', 'primaryKey'):
        _quote(result.get(name))
    columns = result.get('readColumns')
    if (not isinstance(columns, list) or not columns or len(columns) > 1000 or not all(isinstance(name, str) for name in columns)
            or len(set(columns)) != len(columns) or result['primaryKey'] not in columns):
        raise SourceCaptureInvalid('CDC必须使用包含单列主键的明确授权字段')
    for name in columns:
        _quote(name)
    if result.get('primaryKind') not in ('integer', 'string'):
        raise SourceCaptureInvalid('CDC仅支持整数或文本单列主键')
    for name in ('structureSignature', 'bindingSignature'):
        if not isinstance(result.get(name), str) or not re.fullmatch('[0-9a-f]{64}', result[name]):
            raise SourceCaptureInvalid('CDC源签名无效')
    resources = {name: f'RGCDC_{prefix}_{generation}' for name, prefix in
                 (('metadata', 'M'), ('journal', 'J'), ('trigger', 'T'))}
    if result.get('resources', resources) != resources:
        raise SourceCaptureInvalid('CDC资源名称与generation不匹配')
    result['resources'] = resources
    return result


def _config_hash(capture):
    return _hash({key: capture[key] for key in ('generation', 'schema', 'table', 'primaryKey',
                  'primaryKind', 'readColumns', 'structureSignature', 'bindingSignature', 'resources')})


def _qualified(capture, resource):
    return _quote(capture['schema']) + '.' + _quote(capture['resources'][resource])


def _trigger_body(capture):
    journal = _qualified(capture, 'journal')
    primary = _quote(capture['primaryKey'])
    generation = capture['generation']  # Validated hex, never arbitrary SQL text.
    return f'''BEGIN
IF INSERTING THEN
INSERT INTO {journal} ("generation", "operation", "new_key", "captured_at") VALUES ('{generation}', 'I', :NEW.{primary}, SYS_EXTRACT_UTC(CURRENT_TIMESTAMP));
ELSIF DELETING THEN
INSERT INTO {journal} ("generation", "operation", "old_key", "captured_at") VALUES ('{generation}', 'D', :OLD.{primary}, SYS_EXTRACT_UTC(CURRENT_TIMESTAMP));
ELSE
INSERT INTO {journal} ("generation", "operation", "old_key", "new_key", "captured_at") VALUES ('{generation}', 'U', :OLD.{primary}, :NEW.{primary}, SYS_EXTRACT_UTC(CURRENT_TIMESTAMP));
END IF;
END;'''


def _tokens(sql):
    # Ignore formatting only outside quoted identifiers/literals.
    tokens = re.findall(r'"(?:[^"]|"")*"|\'(?:[^\']|\'\')*\'|[\w$#]+|[^\s]', sql)
    return tuple(token if token.startswith(('"', "'")) else token.upper() for token in tokens)


def _trigger_matches(value, capture):
    actual = _tokens(str(value))
    # DM 2024 returns a complete CREATE OR REPLACE statement in TRIGGER_BODY;
    # other versions expose only the PL body. Compare either complete form.
    if actual[:4] == ('CREATE', 'OR', 'REPLACE', 'TRIGGER'):
        actual = ('CREATE',) + actual[3:]
    return actual in (_tokens(_trigger_body(capture)), _tokens(_statements(capture)[2]))


def _bootstrap_fence_sql(capture):
    return f'LOCK TABLE {_quote(capture["schema"])}.{_quote(capture["table"])} IN EXCLUSIVE MODE NOWAIT'


def _statements(capture):
    meta, journal, trigger = (_qualified(capture, name) for name in ('metadata', 'journal', 'trigger'))
    key_type = 'BIGINT' if capture['primaryKind'] == 'integer' else 'VARCHAR(1600)'
    source = _quote(capture['schema']) + '.' + _quote(capture['table'])
    # CTAS stores ownership with table creation, so an interruption cannot leave
    # an empty unowned metadata table that a retry might accidentally adopt.
    ownership = f'''CREATE TABLE {meta} AS SELECT
CAST('{capture['generation']}' AS VARCHAR(32)) AS "generation",
CAST('{_config_hash(capture)}' AS VARCHAR(64)) AS "config_hash"
FROM DUAL'''
    create_journal = f'''CREATE TABLE {journal} (
"event_id" BIGINT IDENTITY(1,1) PRIMARY KEY,
"generation" VARCHAR(32) DEFAULT '{capture['generation']}' NOT NULL,
"operation" CHAR(1) NOT NULL,
"old_key" {key_type}, "new_key" {key_type},
"captured_at" TIMESTAMP(6) NOT NULL)'''
    create_trigger = f'CREATE TRIGGER {trigger} AFTER INSERT OR UPDATE OR DELETE ON {source} FOR EACH ROW\n{_trigger_body(capture)}'
    return [ownership, create_journal, create_trigger]


_ENGINE_LOCK = threading.Lock()
_ENGINES = {}


def _cached_engine(kind, cfg, host):
    key = (kind, host, int(cfg['port']), cfg.get('user') or '',
           hashlib.sha256((cfg.get('password') or '').encode()).hexdigest(), bool(cfg.get('ssl')))
    with _ENGINE_LOCK:
        engine = _ENGINES.get(key)
        if engine is None:
            if len(_ENGINES) >= 8:
                _, retired = _ENGINES.popitem()
                retired.dispose()
            engine = connector._engine(kind, {**cfg, 'host': host}, pooled=True)
            _ENGINES[key] = engine
        return engine


async def _run(kind, cfg, operation, *, write=False, stage='源CDC检查'):
    if kind != KIND:
        raise AppError('当前事务触发器CDC仅支持达梦DM8', code='CDC_SOURCE_UNSUPPORTED', status_code=422)
    if cfg.get('ssl'):
        raise AppError('达梦TLS主机验证尚不可用', code='CDC_SOURCE_UNSUPPORTED', status_code=422)
    if not connector.permitted(kind, cfg):
        raise AppError('未授权连接此CDC数据源', code='CDC_SOURCE_NOT_ALLOWED', status_code=403)
    pinned = connector._approved_ip(cfg) if connector.real_connect_enabled() else cfg['host']
    if not pinned:
        raise AppError('未授权连接此CDC数据源', code='CDC_SOURCE_NOT_ALLOWED', status_code=403)

    def execute():
        engine = _cached_engine(kind, cfg, pinned)
        try:
            with engine.connect() as conn:
                if not write:
                    # A pooled DM connection may carry an implicit transaction from
                    # the previous checkout; READ ONLY must start a clean transaction.
                    conn.rollback()
                    conn.exec_driver_sql('SET TRANSACTION READ ONLY')
                result = operation(conn)
                if write:
                    conn.commit()
                return result
        except AppError:
            raise
        except Exception:
            # Do not expose driver SQL, credentials, keys or source payloads.
            raise AppError(f'{stage}失败；已提交的捕获对象和未确认变更均保留，可检查权限或连接后重试',
                           code='CDC_SOURCE_OPERATION_FAILED', status_code=409) from None

    return await asyncio.to_thread(execute)


def _check_source(conn, capture):
    from app.modules.data.external_mirror import structure_signature

    reflected = connector.inspect_connection_tables(conn, capture['schema'], [capture['table']])
    if len(reflected) != 1 or reflected[0].pk != [capture['primaryKey']]:
        raise SourceCaptureInvalid('CDC源需要与绑定一致的单列主键')
    kinds = {item.name: item.kind for item in reflected[0].columns}
    if kinds.get(capture['primaryKey']) != capture['primaryKind']:
        raise SourceCaptureInvalid('CDC源主键类型已变化')
    try:
        signature = structure_signature(capture['schema'], capture['table'], reflected[0], capture['readColumns'])
    except ValueError:
        raise SourceCaptureInvalid() from None
    if signature != capture['structureSignature']:
        raise SourceCaptureInvalid('CDC所选源结构已变化，请重新确认绑定并建立完整基线')


def _objects(conn, capture):
    values = {'owner': capture['schema'], **capture['resources']}
    result = conn.execute(text('SELECT OBJECT_NAME, OBJECT_TYPE, OBJECT_ID, STATUS FROM ALL_OBJECTS '
                              'WHERE OWNER=:owner AND OBJECT_NAME IN (:metadata,:journal,:trigger)'), values)
    return {row[0]: {'type': row[1], 'id': int(row[2]), 'status': row[3]} for row in result}


def _owned(conn, capture, objects):
    metadata = objects.get(capture['resources']['metadata'])
    if not metadata or metadata['type'] != 'TABLE':
        raise SourceCaptureInvalid('CDC所有权元数据缺失或同名对象冲突；不会覆盖现有对象')
    columns = inspect(conn).get_columns(capture['resources']['metadata'], schema=capture['schema'])
    if {item['name'] for item in columns} != {'generation', 'config_hash'}:
        raise SourceCaptureInvalid('CDC所有权元数据定义变化；不会接管现有对象')
    rows = conn.exec_driver_sql(f'SELECT "generation", "config_hash" FROM {_qualified(capture, "metadata")}').fetchmany(2)
    if len(rows) != 1 or tuple(rows[0]) != (capture['generation'], _config_hash(capture)):
        raise SourceCaptureInvalid('CDC所有权或generation不匹配；不会接管现有对象')


def _check_journal(conn, capture):
    columns = inspect(conn).get_columns(capture['resources']['journal'], schema=capture['schema'])
    expected = {'event_id', 'generation', 'operation', 'old_key', 'new_key', 'captured_at'}
    if {item['name'] for item in columns} != expected:
        raise SourceCaptureInvalid('CDC变更日志定义已变化')
    types = {item['name']: str(item['type']).upper() for item in columns}
    definitions = {item['name']: item for item in columns}
    key_type = 'BIGINT' if capture['primaryKind'] == 'integer' else 'VARCHAR'
    if (not all(types[name].startswith(key_type) for name in ('old_key', 'new_key'))
            or not types['event_id'].startswith('BIGINT')):
        raise SourceCaptureInvalid('CDC变更日志字段类型已变化')
    default = _tokens(str(definitions['generation'].get('default') or ''))
    if (not definitions['event_id'].get('autoincrement')
            or tuple(token for token in default if token not in ('(', ')')) != (f"'{capture['generation']}'",)):
        raise SourceCaptureInvalid('CDC日志identity或generation定义不匹配；不会接管现有对象')
    pk = inspect(conn).get_pk_constraint(capture['resources']['journal'], schema=capture['schema'])
    if pk.get('constrained_columns') != ['event_id']:
        raise SourceCaptureInvalid('CDC变更日志唯一身份约束已变化')


def _validate_conn(conn, capture):
    _check_source(conn, capture)
    objects = _objects(conn, capture)
    _owned(conn, capture, objects)
    for name, kind in (('journal', 'TABLE'), ('trigger', 'TRIGGER')):
        obj = objects.get(capture['resources'][name])
        if not obj or obj['type'] != kind:
            raise SourceCaptureInvalid('CDC捕获对象缺失或类型不匹配')
    ids = {name: objects[resource]['id'] for name, resource in capture['resources'].items()}
    if capture.get('resourceIds', ids) != ids:
        raise SourceCaptureInvalid('CDC捕获对象已被重建，原位点不可继续使用')
    _check_journal(conn, capture)
    row = conn.execute(text('SELECT TABLE_OWNER,TABLE_NAME,STATUS,TRIGGER_BODY FROM ALL_TRIGGERS '
                            'WHERE OWNER=:owner AND TRIGGER_NAME=:name'),
                       {'owner': capture['schema'], 'name': capture['resources']['trigger']}).first()
    # DM reports catalog identifiers folded to upper case even when the quoted
    # source objects were created lower case (this imported schema does that).
    if (not row or str(row[0]).casefold() != capture['schema'].casefold()
            or str(row[1]).casefold() != capture['table'].casefold()
            or str(row[2]).upper() not in ('Y', 'ENABLED', 'VALID') or not _trigger_matches(row[3], capture)):
        raise SourceCaptureInvalid('CDC触发器被禁用、定义变化或目标表不匹配')
    pending, oldest = conn.exec_driver_sql(f'SELECT COUNT(*), MIN("captured_at") FROM {_qualified(capture, "journal")}').one()
    return {'valid': True, 'pendingCount': int(pending), 'oldestCapturedAt': _utc_iso(oldest), 'sourceTimeZoneKnown': True,
            'capture': {**capture, 'resourceIds': ids}, 'mechanism': 'transactional-trigger-dirty-key',
            'sourceCommitPosition': None}


async def probe(kind, cfg):
    def query(conn):
        params = {row[0]: str(row[1]) for row in conn.exec_driver_sql("SELECT PARA_NAME,PARA_VALUE FROM V$DM_INI WHERE PARA_NAME IN ('ARCH_INI','RLOG_APPEND_LOGIC','LOGMNR_PARSE_LOB')")}
        installed = bool(conn.exec_driver_sql("SELECT COUNT(*) FROM ALL_OBJECTS WHERE OWNER='SYS' AND OBJECT_NAME='DBMS_LOGMNR' AND OBJECT_TYPE='PACKAGE' AND STATUS='VALID'").scalar())
        return {'triggerSupported': True, 'nativeLogPackageInstalled': installed,
                'nativeLogConfigured': params.get('ARCH_INI') == '1' and params.get('RLOG_APPEND_LOGIC') in ('1', '2', '3'),
                'archiveEnabled': params.get('ARCH_INI') == '1', 'logicalLogMode': params.get('RLOG_APPEND_LOGIC'),
                'lobLogParsing': params.get('LOGMNR_PARSE_LOB') == '1',
                'ddlPermissionVerified': False, 'mechanism': 'transactional-trigger-dirty-key'}
    return await _run(kind, cfg, query)


async def plan(kind, cfg, capture):
    capture = _capture(capture)
    connector.require_configured_schema(kind, cfg, [capture['schema']])

    def query(conn):
        _check_source(conn, capture)
        sql = _statements(capture)
        return {'capture': capture, 'sql': sql,
                'planDigest': _hash({'sql': sql, 'bootstrapFenceSql': _bootstrap_fence_sql(capture)}),
                'bootstrapFenceSql': _bootstrap_fence_sql(capture),
                'bootstrapFenceRequired': True,
                'mechanism': 'transactional-trigger-dirty-key', 'sourceCommitPosition': None,
                'unsupportedOperations': ['TRUNCATE', 'trigger-bypassing-load', 'administrator-capture-tampering'],
                'requiresExplicitInstall': True, 'sourceTransactionAtomicMirror': False}
    return await _run(kind, cfg, query)


async def install(kind, cfg, capture):
    capture = _capture(capture)
    connector.require_configured_schema(kind, cfg, [capture['schema']])

    def apply(conn):
        _check_source(conn, capture)
        objects = _objects(conn, capture)
        if capture['resources']['metadata'] not in objects:
            if objects:
                raise SourceCaptureInvalid('发现不明同名CDC对象；不会覆盖或删除现有对象')
            conn.exec_driver_sql(_statements(capture)[0])
            conn.commit()  # DM DDL implicitly commits; retries verify persisted ownership.
            objects = _objects(conn, capture)
        _owned(conn, capture, objects)
        for index, name in ((1, 'journal'), (2, 'trigger')):
            if capture['resources'][name] not in objects:
                conn.exec_driver_sql(_statements(capture)[index])
                conn.commit()
                objects = _objects(conn, capture)
            elif name == 'journal':
                if objects[capture['resources'][name]]['type'] != 'TABLE':
                    raise SourceCaptureInvalid('CDC日志名称被其他类型对象占用')
                _check_journal(conn, capture)
        result = _validate_conn(conn, capture)
        # CREATE TRIGGER does not necessarily wait for pre-existing DM writers.
        # Drain those transactions before a later baseline is allowed to start.
        # NOWAIT bounds installation impact; preserve all DDL and retry the same
        # plan when a writer or missing LOCK permission prevents the fence.
        try:
            conn.exec_driver_sql(_bootstrap_fence_sql(capture))
            conn.commit()
        except Exception:
            raise AppError('源表事务栅栏未取得（可能仍有未结束事务或缺少LOCK权限）；捕获资源保留，请处理后重试同一安装计划',
                           code='CDC_BOOTSTRAP_REQUIRED', status_code=409) from None
        result['capture'] = {**result['capture'], 'bootstrapFenced': True,
                             'bootstrapFencedAt': datetime.now(UTC).isoformat()}
        return result
    return await _run(kind, cfg, apply, write=True, stage='源CDC安装（可能已部分完成）')


async def validate(kind, cfg, capture):
    capture = _capture(capture)
    connector.require_configured_schema(kind, cfg, [capture['schema']])
    return await _run(kind, cfg, lambda conn: _validate_conn(conn, capture))


async def pending(kind, cfg, capture):
    """Cheap idle probe: read the private journal only, without re-reflecting the source."""
    capture = _capture(capture)
    connector.require_configured_schema(kind, cfg, [capture['schema']])

    def query(conn):
        count, oldest = conn.exec_driver_sql(
            f'SELECT COUNT(*), MIN("captured_at") FROM {_qualified(capture, "journal")}').one()
        return {'pendingCount': int(count), 'oldestCapturedAt': _utc_iso(oldest),
                'sourceTimeZoneKnown': True}
    return await _run(kind, cfg, query, stage='源CDC变更检查')


def _source_table(capture):
    return table(quoted_name(capture['table'], True),
                 *(column(quoted_name(name, True)) for name in capture['readColumns']),
                 schema=quoted_name(capture['schema'], True))


def _utc_iso(value):
    # Our validated trigger explicitly stores SYS_EXTRACT_UTC, not local time.
    return (value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)).isoformat() if value else None


async def read_changes(kind, cfg, capture, limit=100):
    capture = _capture(capture)
    connector.require_configured_schema(kind, cfg, [capture['schema']])
    if type(limit) is not int or not 1 <= limit <= 1000:
        raise SourceCaptureInvalid('CDC批大小必须在1至1000之间')

    def query(conn):
        status = _validate_conn(conn, capture)
        journal = table(quoted_name(capture['resources']['journal'], True),
                        *(column(quoted_name(name, True)) for name in ('event_id', 'generation', 'operation', 'old_key', 'new_key', 'captured_at')),
                        schema=quoted_name(capture['schema'], True))
        records = conn.execute(select(journal).order_by(journal.c.event_id).limit(limit)).all()
        events, keys = [], set()
        for record in records:
            event_id, generation, operation, old, new, captured_at = record
            if generation != capture['generation'] or operation not in ('I', 'U', 'D'):
                raise SourceCaptureInvalid('CDC变更日志包含未知generation或操作')
            for key in (old, new):
                if key is not None:
                    expected = int if capture['primaryKind'] == 'integer' else str
                    if type(key) is not expected or len(str(key)) > 400:
                        raise SourceCaptureInvalid('CDC变更主键类型或长度不受支持')
                    keys.add(key)
            events.append({'eventId': int(event_id), 'operation': operation, 'oldKey': old, 'newKey': new,
                           'capturedAt': _utc_iso(captured_at)})
        source = _source_table(capture)
        rows = []
        # A PK-changing update can contribute two keys; keep IN lists bounded.
        values = list(keys)
        for offset in range(0, len(values), 500):
            rows.extend(dict(row._mapping) for row in conn.execute(select(source).where(source.c[capture['primaryKey']].in_(values[offset:offset + 500]))))
        return {**status, 'events': events, 'rows': rows}
    return await _run(kind, cfg, query, stage='源CDC变更读取')


async def ack(kind, cfg, capture, event_ids):
    capture = _capture(capture)
    connector.require_configured_schema(kind, cfg, [capture['schema']])
    if (not isinstance(event_ids, list) or len(event_ids) > 1000
            or any(type(value) is not int or value < 1 for value in event_ids)):
        raise SourceCaptureInvalid('CDC确认必须提供本批已持久化的精确event ID列表')
    if not event_ids:
        return 0

    def apply(conn):
        # The batch was fully validated by read_changes before PG committed it.
        # ACK only needs the generation guard: a replaced capture cannot consume
        # or delete another generation's journal rows.
        journal = _qualified(capture, 'journal')
        params = {'generation': capture['generation'], **{f'id{i}': value for i, value in enumerate(event_ids)}}
        placeholders = ','.join(f':id{i}' for i in range(len(event_ids)))
        result = conn.execute(text(f'DELETE FROM {journal} WHERE "generation"=:generation AND "event_id" IN ({placeholders})'), params)
        return result.rowcount
    return await _run(kind, cfg, apply, write=True, stage='源CDC精确确认')
