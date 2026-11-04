"""Bounded real CDC checks for workspace 15 / LiveFixture only; never installs capture.

Default --phase observe only reads. Mutating phases require --allow-fixture-mutations
and reject every source table except the already authorized synthetic fixture.
No HTTP login, new workspace, source DDL, broad journal ACK or pytest is involved.
"""
import argparse
import asyncio
import json
import secrets
import sys
import time
import uuid
import zlib
from datetime import timedelta
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
from dotenv import load_dotenv
load_dotenv(BACKEND / '.env')

from sqlalchemy import column, delete, func, insert, or_, select, table, text, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import quoted_name

from app.common.ontology import set_ontology_scope
import app.db.models  # Register scoped FK targets before the isolated worker writes its lease.
from app.db.session import dispose_engine, get_engine, get_sessionmaker
from app.core.errors import AppError
from app.modules.data import connector
from app.modules.data.external_cdc import cfg_for, key_json
from app.modules.data.external_mirror import scalar
from app.modules.data.models import (Datasource, ExternalCdcSubscription, ExternalMirrorRow,
    ExternalMirrorRun, ExternalMirrorState, ExternalReadBinding)

WORKSPACE = 15
OBJECT = 'LiveFixture'
FIXTURE = 'rgv_1031_fc38f7783a'


async def saved_context(require_installed=True, pin_run=False, paused=False):
    async with get_sessionmaker()() as session:
        await set_ontology_scope(session, WORKSPACE)
        binding = await session.scalar(select(ExternalReadBinding).where(ExternalReadBinding.object_code == OBJECT))
        sub = await session.scalar(select(ExternalCdcSubscription).where(ExternalCdcSubscription.object_code == OBJECT))
        assert binding and binding.table_name == FIXTURE, 'refusing any source except authorized synthetic fixture'
        assert sub and sub.capture['table'] == FIXTURE, 'capture plan must target the explicit fixture'
        assert not require_installed or sub.installed, 'capture must already be explicitly installed'
        assert sub.capture['schema'] == binding.schema_name and sub.binding_version == binding.lock_version
        source = await session.scalar(select(Datasource).where(Datasource.code == binding.datasource_code))
        state = await session.scalar(select(ExternalMirrorState).where(ExternalMirrorState.object_code == OBJECT))
        if pin_run:
            assert state and state.complete and (sub.cursor_value or {}).get('snapshotComplete'), 'wait for complete CDC baseline'
            assert sub.phase == ('paused' if paused else 'streaming'), 'root must select the expected settled CDC phase before fixture DML'
            assert bool(sub.enabled) is not paused, 'CDC enable flag does not match the requested phase'
            assert state.active_run_id == (sub.cursor_value or {}).get('snapshotRunId'), 'snapshot run ownership differs'
        cfg = cfg_for(source)
        pinned = connector._approved_ip(cfg)
        assert pinned, 'source must remain on explicit host allowlist'
        return {'capture': dict(sub.capture), 'signature': sub.binding_signature,
                'kind': source.type.value, 'cfg': {**cfg, 'host': pinned}, 'enabled': sub.enabled,
                'pinRun': pin_run, 'runId': state.active_run_id if state else None,
                'scanId': (state.cursor_value or {}).get('scanId') if state else None}


async def local_rows(ctx, keys=None):
    async with get_sessionmaker()() as session:
        await set_ontology_scope(session, WORKSPACE)
        query = select(ExternalMirrorRow.source_key, ExternalMirrorRow.payload).where(
            ExternalMirrorRow.object_code == OBJECT, ExternalMirrorRow.binding_signature == ctx['signature'],
            ExternalMirrorRow.is_current.is_(True))
        if keys is not None:
            query = query.where(ExternalMirrorRow.source_key.in_([key_json(key) for key in keys]))
        rows = {key: payload for key, payload in (await session.execute(query)).all()}
        sub = await session.scalar(select(ExternalCdcSubscription).where(ExternalCdcSubscription.object_code == OBJECT))
        state = await session.scalar(select(ExternalMirrorState).where(ExternalMirrorState.object_code == OBJECT))
        if ctx.get('pinRun'):
            assert state.active_run_id == ctx['runId'], 'unexpected new full-scan run during CDC delivery check'
            assert (state.cursor_value or {}).get('scanId') == ctx['scanId'], 'unexpected new full-scan identity during CDC delivery check'
            assert (sub.cursor_value or {}).get('snapshotRunId') == ctx['runId'], 'CDC subscription changed its baseline run'
            assert sub.capture['generation'] == ctx['capture']['generation'], 'CDC generation changed during delivery check'
        return rows, {'phase': sub.phase, 'enabled': sub.enabled, 'pendingAckCount': len(sub.pending_ack or []),
                      'installed': sub.installed,
                      'runId': state.active_run_id if state else None, 'scanId': (state.cursor_value or {}).get('scanId') if state else None,
                      'pendingCount': sub.pending_count, 'appliedCount': sub.applied_count,
                      'restartRequired': sub.restart_required, 'errorCode': sub.error_code}


def source_table(ctx):
    capture = ctx['capture']
    return table(quoted_name(capture['table'], True), *(column(quoted_name(c, True)) for c in capture['readColumns']),
                 schema=quoted_name(capture['schema'], True))


def require_transactional(conn):
    value = getattr(conn.connection.driver_connection, 'autoCommit', None)
    assert type(value) in (bool, int) and not value, 'DM autoCommit must be verified false before fixture DML'


def source_rows(engine, src, primary, keys=None):
    with engine.connect() as conn:
        query = select(src)
        if keys is not None:
            query = query.where(src.c[primary].in_(keys))
        return {key_json(row._mapping[primary]): {c: scalar(row._mapping[c]) for c in src.c.keys()}
                for row in conn.execute(query)}


async def await_keys(ctx, engine, src, keys, timeout=40):
    expected = await asyncio.to_thread(source_rows, engine, src, ctx['capture']['primaryKey'], keys)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        actual, state = await local_rows(ctx, keys)
        if actual == expected and state['pendingAckCount'] == 0:
            return state
        if state['restartRequired']:
            raise AssertionError('CDC failed closed: ' + str(state['errorCode']))
        await asyncio.sleep(0.3)
    raise AssertionError('CDC fixture delivery timed out; no payload/key values emitted')


def template_and_keys(engine, src, primary, primary_kind):
    with engine.connect() as conn:
        row = conn.execute(select(src).order_by(src.c[primary]).limit(1)).first()
        assert row, 'fixture needs its existing baseline'
        template = dict(row._mapping)
        expected = int if primary_kind == 'integer' else str
        assert type(template[primary]) is expected, 'fixture primary value differs from approved capture type'
        if primary_kind == 'integer':
            base = -1_000_000_000 - secrets.randbelow(100_000_000)
            keys = [base + offset for offset in range(8)]
        else:
            prefix = 'z' + secrets.token_hex(3)
            keys = [prefix + str(offset) for offset in range(8)]
        assert not conn.execute(select(src.c[primary]).where(src.c[primary].in_(keys))).first(), 'test-key collision'
        return template, keys


def row_for(template, primary, key):
    row = {**template, primary: key}
    # This is the known synthetic field, kept short enough for its existing VARCHAR.
    if 'code' in row and primary != 'code':
        row['code'] = 'cdc' + (str(abs(key)) if type(key) is int else key)
    return row


async def crud(ctx, engine, src, template, keys):
    primary = ctx['capture']['primaryKey']
    key, moved = keys[:2]
    with engine.begin() as conn:
        require_transactional(conn)
        conn.execute(insert(src).values(row_for(template, primary, key)))
    started = time.monotonic()
    await await_keys(ctx, engine, src, [key])
    print(f'PASS real INSERT delivered; observedAfterCommitSeconds={time.monotonic() - started:.3f}', flush=True)
    field = next((name for name in ('State', 'MixedCaseValue') if name in src.c), None)
    assert field, 'known synthetic update field is not approved'
    with engine.begin() as conn:
        require_transactional(conn)
        conn.execute(update(src).where(src.c[primary] == key).values({field: 'cdcU'}))
    started = time.monotonic()
    await await_keys(ctx, engine, src, [key])
    print(f'PASS real UPDATE delivered; observedAfterCommitSeconds={time.monotonic() - started:.3f}', flush=True)
    with engine.begin() as conn:
        require_transactional(conn)
        conn.execute(update(src).where(src.c[primary] == key).values({primary: moved}))
    started = time.monotonic()
    await await_keys(ctx, engine, src, [key, moved])
    print(f'PASS real primary-key change removed old key and delivered new key; observedAfterCommitSeconds={time.monotonic() - started:.3f}', flush=True)
    with engine.begin() as conn:
        require_transactional(conn)
        conn.execute(delete(src).where(src.c[primary] == moved))
    started = time.monotonic()
    await await_keys(ctx, engine, src, [key, moved])
    print(f'PASS real DELETE aligned current visibility; observedAfterCommitSeconds={time.monotonic() - started:.3f}', flush=True)


async def rollback(ctx, engine, src, template, keys):
    primary, key = ctx['capture']['primaryKey'], keys[2]
    with engine.connect() as conn:
        require_transactional(conn)
        transaction = conn.begin()
        conn.execute(insert(src).values(row_for(template, primary, key)))
        assert not await asyncio.to_thread(source_rows, engine, src, primary, [key]), 'uncommitted row visible on independent source connection'
        for _ in range(5):
            rows, _ = await local_rows(ctx, [key])
            assert not rows, 'uncommitted source change became visible in mirror'
            await asyncio.sleep(0.4)
        transaction.rollback()
    await asyncio.sleep(1.5)
    await await_keys(ctx, engine, src, [key])
    print('PASS uncommitted INSERT invisible and full rollback never delivered', flush=True)
    # A savepoint rollback must also remove that statement\'s transactional journal row.
    with engine.connect() as conn:
        require_transactional(conn)
        transaction = conn.begin()
        conn.exec_driver_sql('SAVEPOINT CDC_FIXTURE_CHECK')
        conn.execute(insert(src).values(row_for(template, primary, keys[3])))
        conn.exec_driver_sql('ROLLBACK TO SAVEPOINT CDC_FIXTURE_CHECK')
        transaction.commit()
    await asyncio.sleep(1.5)
    await await_keys(ctx, engine, src, [keys[3]])
    print('PASS savepoint rollback never delivered', flush=True)


def own_event_id(conn, ctx, key):
    capture = ctx['capture']
    journal = table(quoted_name(capture['resources']['journal'], True),
                    *(column(quoted_name(name, True)) for name in ('event_id', 'new_key')),
                    schema=quoted_name(capture['schema'], True))
    return int(conn.execute(select(journal.c.event_id).where(journal.c.new_key == key)).scalar_one())


async def reverse_commit(ctx, engine, src, template, keys):
    primary, late, early = ctx['capture']['primaryKey'], keys[4], keys[5]
    with engine.connect() as first, engine.connect() as second:
        require_transactional(first)
        require_transactional(second)
        first_tx, second_tx = first.begin(), second.begin()
        first.execute(insert(src).values(row_for(template, primary, late)))
        low = own_event_id(first, ctx, late)
        assert not await asyncio.to_thread(source_rows, engine, src, primary, [late]), 'first transaction already visible before commit'
        second.execute(insert(src).values(row_for(template, primary, early)))
        high = own_event_id(second, ctx, early)
        assert not await asyncio.to_thread(source_rows, engine, src, primary, [early]), 'second transaction already visible before commit'
        assert low < high, 'fixture did not allocate lower ID to the uncommitted transaction'
        second_tx.commit()
        await await_keys(ctx, engine, src, [early])
        rows, _ = await local_rows(ctx, [late])
        assert not rows, 'late source transaction became visible before commit'
        first_tx.commit()
    await await_keys(ctx, engine, src, [late, early])
    print('PASS high event ID committed/applied first; late lower ID still delivered', flush=True)


def pending_for_key(engine, ctx, key):
    capture = ctx['capture']
    journal = table(quoted_name(capture['resources']['journal'], True),
        *(column(quoted_name(name, True)) for name in ('old_key', 'new_key')),
        schema=quoted_name(capture['schema'], True))
    with engine.connect() as conn:
        return int(conn.execute(select(func.count()).select_from(journal).where(
            or_(journal.c.old_key == key, journal.c.new_key == key))).scalar_one())


async def paused_backlog(ctx, engine, src, template, keys):
    primary, key = ctx['capture']['primaryKey'], keys[0]
    before, state = await local_rows(ctx)
    assert not state['enabled'] and state['phase'] == 'paused'
    field = next((name for name in ('State', 'MixedCaseValue') if name in src.c), None)
    assert field, 'known synthetic update field is not approved'
    with engine.begin() as conn:
        require_transactional(conn)
        conn.execute(insert(src).values(row_for(template, primary, key)))
        conn.execute(update(src).where(src.c[primary] == key).values({field: 'cdcP'}))
    await asyncio.sleep(2)
    after, state = await local_rows(ctx)
    assert not state['enabled'] and state['phase'] == 'paused', 'root must leave consumption paused until backlog is observed'
    assert before == after, 'mirror changed while CDC was explicitly paused'
    pending = await asyncio.to_thread(pending_for_key, engine, ctx, key)
    assert pending == 2, 'expected exactly the owned INSERT and UPDATE in the private pending journal'
    print(json.dumps({'event': 'paused_backlog_ready_for_GUI_resume', 'privatePendingEvents': pending,
        'mirrorUnchanged': True, 'runId': state['runId'], 'scanId': state['scanId']}), flush=True)
    deadline = time.monotonic() + 150
    while time.monotonic() < deadline:
        _, state = await local_rows(ctx, [key])
        if state['enabled']:
            await await_keys(ctx, engine, src, [key])
            print('PASS GUI resume caught retained private journal with unchanged baseline run/scan', flush=True)
            return
        await asyncio.sleep(0.5)
    raise AssertionError('GUI resume was not observed within the bounded pause test')


def pending_event_ids(engine, ctx, key=None):
    capture = ctx['capture']
    journal = table(quoted_name(capture['resources']['journal'], True),
        *(column(quoted_name(name, True)) for name in ('event_id', 'old_key', 'new_key')),
        schema=quoted_name(capture['schema'], True))
    query = select(journal.c.event_id).order_by(journal.c.event_id)
    if key is not None:
        query = query.where(or_(journal.c.old_key == key, journal.c.new_key == key))
    with engine.connect() as conn:
        return [int(row[0]) for row in conn.execute(query)]


async def ack_cleanup_failure(ctx, engine, src, template, keys):
    """One isolated-process fault after exact source ACK, before PG clear commit.

    The real API remains running. Its existing object lock excludes it only for
    the controlled batch; after release it reclaims the durable token and ACKs
    the already-deleted IDs again. No production hook or database fault is added.
    """
    from app.modules.data import dm_cdc_source, external_cdc as cdc
    primary, key = ctx['capture']['primaryKey'], keys[6]
    owner = uuid.uuid4().hex
    lock_key = zlib.crc32(f'qa.eval.mirror.{WORKSPACE}.{OBJECT}'.encode())
    _, before = await local_rows(ctx)
    async with get_engine().connect() as connection:
        await connection.execute(text('SELECT pg_advisory_lock(:k)'), {'k': lock_key})
        await connection.commit()
        try:
            assert not await asyncio.to_thread(pending_event_ids, engine, ctx), 'private journal is not empty; refusing to touch unknown pending work'
            async with get_sessionmaker()() as session:
                await set_ontology_scope(session, WORKSPACE)
                sub = await cdc.subscription(session, OBJECT, lock=True)
                assert sub.enabled and sub.installed and not sub.pending_ack and not sub.restart_required
                assert sub.capture['generation'] == ctx['capture']['generation']
                sub.lease_owner, sub.lease_until = owner, cdc.now() + timedelta(seconds=cdc.LEASE_SECONDS)
                sub.heartbeat_at = cdc.now()
                await session.commit()
            with engine.begin() as conn:
                require_transactional(conn)
                conn.execute(insert(src).values(row_for(template, primary, key)))
            owned_ids = await asyncio.to_thread(pending_event_ids, engine, ctx, key)
            all_ids = await asyncio.to_thread(pending_event_ids, engine, ctx)
            assert len(owned_ids) == 1 and owned_ids == all_ids, 'unexpected private pending work; exact ACK fault injection refused'
            original_ack, original_commit = dm_cdc_source.ack, AsyncSession.commit
            flags = {'sourceAckCommitted': False, 'faultFired': False}

            async def tracked_ack(kind, cfg, capture, event_ids):
                assert capture['generation'] == ctx['capture']['generation'] and event_ids == owned_ids, 'refusing ACK outside this single owned event'
                count = await original_ack(kind, cfg, capture, event_ids)
                assert count == 1, 'expected one exact source journal deletion'
                flags['sourceAckCommitted'] = True
                return count

            async def fail_one_cleanup_commit(session):
                clearing = any(isinstance(item, ExternalCdcSubscription) and item.object_code == OBJECT
                               and item.lease_owner == owner and item.pending_ack == []
                               for item in session.identity_map.values())
                if flags['sourceAckCommitted'] and not flags['faultFired'] and clearing:
                    flags['faultFired'] = True
                    raise AppError('isolated synthetic CDC checkpoint cleanup failure',
                                   code='CDC_FIXTURE_ACK_CLEANUP_FAILURE', status_code=409)
                return await original_commit(session)

            dm_cdc_source.ack, AsyncSession.commit = tracked_ack, fail_one_cleanup_commit
            try:
                try:
                    await cdc._consume(WORKSPACE, OBJECT, owner)
                except AppError as exc:
                    assert exc.code == 'CDC_FIXTURE_ACK_CLEANUP_FAILURE', 'unexpected consumer failure before the controlled window'
                else:
                    raise AssertionError('controlled PG cleanup commit failure did not fire')
            finally:
                dm_cdc_source.ack, AsyncSession.commit = original_ack, original_commit
            assert flags['sourceAckCommitted'] and flags['faultFired']
            assert not await asyncio.to_thread(pending_event_ids, engine, ctx), 'source exact ACK did not persist'
            expected = await asyncio.to_thread(source_rows, engine, src, primary, [key])
            mirrored, observed = await local_rows(ctx, [key])
            assert mirrored == expected, 'mirror apply was not durable before source ACK'
            async with get_sessionmaker()() as session:
                await set_ontology_scope(session, WORKSPACE)
                sub = await cdc.subscription(session, OBJECT)
                assert sub.pending_ack == owned_ids, 'PG cleanup transaction did not roll back to durable pending ACK'
            print(json.dumps({'event': 'source_ACK_committed_PG_cleanup_rolled_back', 'sourcePrivatePending': 0,
                'localPendingAck': len(owned_ids), 'mirrorApplyDurable': True,
                'runId': observed['runId'], 'scanId': observed['scanId']}), flush=True)
        finally:
            try:
                await connection.rollback()
                await connection.execute(text('SELECT pg_advisory_unlock(:k)'), {'k': lock_key})
                await connection.commit()
            except BaseException:
                await connection.invalidate()
                raise
    # The ordinary API is the only consumer after releasing the per-object lock.
    observed = await await_keys(ctx, engine, src, [key])
    assert observed['appliedCount'] == before['appliedCount'] + 1, 'replayed ACK must not reapply or recount the event'
    async with get_sessionmaker()() as session:
        await set_ontology_scope(session, WORKSPACE)
        try:
            await cdc.assert_lease(session, OBJECT, owner)
        except AppError as exc:
            assert exc.code == 'CDC_LEASE_LOST'
        else:
            raise AssertionError('old isolated consumer token was not fenced after API recovery')
    print('PASS normal API reclaimed the old token, safely repeated exact ACK=0, and cleared checkpoint without reapplying', flush=True)


async def main(arguments):
    pin_run = arguments.phase in ('crud', 'rollback', 'reverse-commit', 'all', 'pause-backlog', 'ack-cleanup-failure')
    ctx = await saved_context(require_installed=arguments.phase != 'observe', pin_run=pin_run,
                              paused=arguments.phase == 'pause-backlog')
    if arguments.generation:
        assert ctx['capture']['generation'] == arguments.generation, 'capture generation changed; refusing test'
    if arguments.expected_run_id is not None:
        assert ctx['runId'] == arguments.expected_run_id, 'baseline run changed before the check began'
    if arguments.expected_scan_id is not None:
        assert ctx['scanId'] == arguments.expected_scan_id, 'baseline scan changed before the check began'
    if arguments.phase == 'watch-snapshot':
        deadline = time.monotonic() + arguments.timeout
        try:
            while time.monotonic() < deadline:
                async with get_sessionmaker()() as session:
                    await set_ontology_scope(session, WORKSPACE)
                    state = await session.scalar(select(ExternalMirrorState).where(ExternalMirrorState.object_code == OBJECT))
                    run = await session.get(ExternalMirrorRun, state.active_run_id) if state and state.active_run_id else None
                    sub = await session.scalar(select(ExternalCdcSubscription).where(ExternalCdcSubscription.object_code == OBJECT))
                    assert sub.capture['generation'] == ctx['capture']['generation'], 'generation changed during watch'
                    if (sub.enabled and run and run.trigger == 'cdc_snapshot' and state.status == 'running'
                        and state.processed > 0 and not state.complete):
                        print(json.dumps({'event': 'snapshot_running_with_committed_prefix', 'runId': run.id,
                            'scanId': (state.cursor_value or {}).get('scanId'), 'processed': state.processed,
                            'batchSize': state.batch_size, 'status': state.status, 'complete': state.complete}), flush=True)
                        return
                await asyncio.sleep(0.08)
            raise AssertionError('no live CDC snapshot prefix observed within bounded watch')
        finally:
            await dispose_engine()
        return
    engine = connector._engine(ctx['kind'], ctx['cfg'])
    src, keys = source_table(ctx), []
    primary = ctx['capture']['primaryKey']
    try:
        if arguments.phase == 'observe':
            source = await asyncio.to_thread(source_rows, engine, src, primary)
            mirror, state = await local_rows(ctx)
            print(json.dumps({'selectedPayloadsEqual': source == mirror, 'sourceRows': len(source), 'mirrorRows': len(mirror),
                'sourceOnlyCount': len(source.keys() - mirror.keys()), 'mirrorOnlyCount': len(mirror.keys() - source.keys()),
                'changedPayloadCount': sum(source[key] != mirror[key] for key in source.keys() & mirror.keys()), **state}))
            if arguments.require_equal:
                assert source == mirror, 'fixture source/mirror selected payloads differ'
            return
        assert arguments.allow_fixture_mutations, 'mutating phase requires explicit --allow-fixture-mutations'
        assert ctx['enabled'] or arguments.phase == 'pause-backlog', 'root must explicitly enable the installed fixture subscription first'
        template, keys = await asyncio.to_thread(template_and_keys, engine, src, primary, ctx['capture']['primaryKind'])
        operations = {'crud': crud, 'rollback': rollback, 'reverse-commit': reverse_commit,
                      'pause-backlog': paused_backlog, 'ack-cleanup-failure': ack_cleanup_failure}
        phases = ('crud', 'rollback', 'reverse-commit') if arguments.phase == 'all' else (arguments.phase,)
        for phase in phases:
            print('PHASE START ' + phase, flush=True)
            await operations[phase](ctx, engine, src, template, keys)
            _, state = await local_rows(ctx, keys)
            print(f'PHASE COMPLETE {phase}; unchangedRunId={state["runId"]} unchangedScanId={state["scanId"]}', flush=True)
    finally:
        if keys:
            # Only this invocation\'s collision-checked synthetic keys; never clears unknown pending work.
            with engine.begin() as conn:
                require_transactional(conn)
                conn.execute(delete(src).where(src.c[primary].in_(keys)))
            await await_keys(ctx, engine, src, keys)
        engine.dispose()
        await dispose_engine()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--phase', choices=('observe', 'watch-snapshot', 'crud', 'rollback', 'reverse-commit', 'all', 'pause-backlog', 'ack-cleanup-failure'), default='observe')
    parser.add_argument('--allow-fixture-mutations', action='store_true')
    parser.add_argument('--generation', help='optionally pin the exact generation approved in the normal UI')
    parser.add_argument('--timeout', type=int, default=120, help='bounded read-only snapshot watch timeout in seconds')
    parser.add_argument('--require-equal', action='store_true', help='require full source/mirror equality for an observed settled stream')
    parser.add_argument('--expected-run-id', type=int)
    parser.add_argument('--expected-scan-id', type=int)
    try:
        asyncio.run(main(parser.parse_args()))
    except Exception as exc:
        # Driver errors may contain SQL or source values. Emit only bounded classification.
        detail = str(exc) if isinstance(exc, AssertionError) else str(getattr(exc, 'code', 'CHECK_FAILED'))
        print('CDC fixture check failed: ' + type(exc).__name__ + ' / ' + detail, file=sys.stderr)
        sys.exit(1)
