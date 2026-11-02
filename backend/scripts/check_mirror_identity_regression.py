"""Offline mirror regression checks; intentionally not run during the integration repair.

Run manually from backend after approval: python -m scripts.check_mirror_identity_regression
All source/database entry points used by these checks are replaced with in-memory mocks.
"""
import asyncio
from contextlib import ExitStack, asynccontextmanager
from copy import deepcopy
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch

from sqlalchemy.dialects import postgresql

from app.common.object_identity import identity_rows, row_identity_column, row_source
from app.modules.data import external_mirror as mirror
from app.modules.static.models import ObjectStatus


def metadata():
    def prop(name, primary=False):
        return NS(api_name=name, source_column=name, backing_column='', data_type='string',
                  is_sensitive=False, visibility='visible', transform='', lock_version=1,
                  display_name=name, is_pk=primary)
    props = [prop('id', True), prop('code'), prop('ontology_id'), prop('__mirror_row_id'),
             prop('__mirror_ontology_id'), prop('ID_ALIAS')]
    props[-1].source_column = 'id'  # Several public properties can share one source column.
    obj = NS(code='OfflineRows', display_name='Offline rows', pk_property='id',
             datasource_name='offline', source_table='MixedSchema.Rows', properties=props,
             status=ObjectStatus.published, lock_version=1)
    source = NS(id=1, code='offline', name='Offline source', type=NS(value='offline'),
                host='example.invalid', port=1, database='MixedSchema', username='offline',
                ssl=False, password_enc='offline-placeholder')
    binding = NS(datasource_code=source.code, schema_name='MixedSchema', table_name='Rows',
                 read_columns=list(dict.fromkeys(p.source_column for p in props)), lock_version=1,
                 object_code=obj.code)
    return obj, source, binding


def signatures():
    obj, source, binding = metadata()
    original = mirror.signature(binding, source, obj)
    legacy = mirror._legacy_signature(binding, source, obj)
    upgrading = NS(binding_signature=original, complete=False,
                   cursor_value={'previousBindingSignature': legacy})
    assert mirror.preserved_binding_signature(upgrading, binding, source, obj, original) == legacy
    obj.display_name, obj.lock_version = 'Changed display only', 50
    binding.lock_version = 50
    source.name, source.password_enc = 'Renamed display', 'rotated-offline-placeholder'
    for prop in obj.properties:
        prop.display_name, prop.lock_version = 'Display only', 50
    assert mirror.signature(binding, source, obj) == original
    assert mirror.preserved_binding_signature(upgrading, binding, source, obj, original) == legacy
    changed = deepcopy(source)
    changed.host = 'other.invalid'
    assert mirror.signature(binding, changed, obj) != original
    assert mirror.preserved_binding_signature(upgrading, binding, changed, obj,
                                               mirror.signature(binding, changed, obj)) is None
    upgrading.complete = True
    assert mirror.preserved_binding_signature(upgrading, binding, source, obj, original) is None
    obj.pk_property = 'code'
    assert mirror.signature(binding, source, obj) != original
    obj.pk_property = 'id'
    obj.properties[0].is_sensitive = True
    assert mirror.signature(binding, source, obj) != original


def session():
    result = AsyncMock()
    result.info = {'ontology_id': 1}
    result.__aenter__.return_value = result
    return result


async def governed_identity():
    obj, source, binding = metadata()
    state = NS(object_code=obj.code, status='succeeded', complete=True, processed=1,
               cursor_value=None, binding_signature=mirror.signature(binding, source, obj))
    db = session()
    db.scalars.side_effect = [[state], [binding]]
    db.scalar.side_effect = [obj, binding, source]
    tables, gaps = await mirror.governed_mirrors(db, 1, set(), set())
    assert not gaps
    governed = next(iter(tables.values()))
    assert len(governed.table.c) == len(binding.read_columns) + 2
    assert governed.row_identity not in governed.projection
    assert governed.scope_column not in governed.projection
    assert {'id', 'code', 'ontology_id'} <= governed.projection
    assert row_identity_column(governed, row_source(governed)).name != 'id'
    sql = str(identity_rows(governed).compile(dialect=postgresql.dialect()))
    assert governed.row_identity in sql and 'ontology_id = 1' in sql


async def batch_progress():
    obj, source, binding = metadata()
    user = NS(id='offline', ontology_id=1)
    state = NS(object_code=obj.code, active_run_id=7, status='queued', cursor_value=None,
               processed=0, complete=False, batch_size=100,
               binding_signature=mirror.signature(binding, source, obj))
    run = NS(id=7, status='queued', read_count=0, write_count=0, complete=False, finished_at=None)
    def pair():
        first, second = session(), session()
        first.scalar.return_value, first.get.return_value = state, run
        second.scalar.side_effect, second.get.return_value = [state, source.id, binding], run
        return first, second
    cancelled, superseded = session(), session()
    cancelled.scalar.return_value = superseded.scalar.return_value = state
    sessions = [*pair(), cancelled, *pair(), superseded, *pair()]
    queue = iter(sessions)
    reflected = NS(name='Rows', pk=['id'], columns=[NS(name=c, kind='string') for c in binding.read_columns])
    def rows(start, end):
        return [{c: str(index) for c in binding.read_columns} for index in range(start, end)]
    lock = NS(xact_lock=AsyncMock())
    with ExitStack() as stack:
        stack.enter_context(patch.object(mirror, 'get_sessionmaker', return_value=lambda: next(queue)))
        stack.enter_context(patch.object(mirror, 'set_ontology_scope', AsyncMock()))
        stack.enter_context(patch.object(mirror, 'get_adapter', return_value=lock))
        stack.enter_context(patch.object(mirror, 'resolve_user', AsyncMock(return_value=user)))
        stack.enter_context(patch.object(mirror, 'authorize'))
        stack.enter_context(patch.object(mirror, 'external_binding_context', AsyncMock(
            return_value=(obj, source, 'MixedSchema', 'Rows', set(binding.read_columns), 'id'))))
        stack.enter_context(patch.object(mirror, 'decrypt_password', return_value='offline-placeholder'))
        stack.enter_context(patch.object(mirror, '_require_real'))
        stack.enter_context(patch.object(mirror.connector, 'inspect_tables', AsyncMock(return_value=[reflected])))
        read = stack.enter_context(patch.object(mirror.connector, '_read', AsyncMock(side_effect=[rows(1, 102), rows(101, 151)])))
        assert await mirror._run_batch(1, obj.code, user, False, run.id) is True
        assert state.status == 'queued' and not state.complete and state.processed == 100
        # Cancellation keeps the cursor/count; a resumed worker continues the same bounded scan.
        cursor = deepcopy(state.cursor_value)
        state.status = 'cancelled'
        assert await mirror._run_batch(1, obj.code, user, False, run.id) is False
        assert state.cursor_value == cursor and state.processed == 100 and read.await_count == 1
        state.status = 'queued'
        assert await mirror._run_batch(1, obj.code, user, False, run.id) is False
        assert state.complete and state.status == 'succeeded' and state.processed == 150
        assert run.read_count == run.write_count == 150 and run.complete
        assert state.cursor_value['scanId'] == 7 and state.cursor_value['deletionsAligned'] is True
        publish = sessions[4].execute.await_args_list[-1].args[0]
        sql = str(publish.compile(dialect=postgresql.dialect(), compile_kwargs={'literal_binds': True}))
        assert 'last_seen_scan = 7' in sql and 'IS DISTINCT FROM' in sql and 'ontology_id = 1' in sql
        assert 'last_seen_scan' not in str(mirror.current_rows(state))
        state.complete = False
        assert 'last_seen_scan' in str(mirror.current_rows(state))
        state.complete = True
        # A superseded worker must never consume a newly queued run.
        state.status, state.active_run_id = 'queued', 8
        assert await mirror._run_batch(1, obj.code, user, False, run.id) is False
        assert read.await_count == 2
        # Exactly one full 100-row page is complete; the extra probe is not counted/written.
        state.active_run_id, state.processed, state.complete, state.cursor_value = 7, 0, False, None
        run.read_count = run.write_count = 0
        read.side_effect = [rows(1, 101)]
        assert await mirror._run_batch(1, obj.code, user, False, run.id) is False
        assert state.status == 'succeeded' and state.complete and state.processed == 100
        assert run.read_count == run.write_count == 100


async def manual_continuation():
    @asynccontextmanager
    async def context():
        yield None
    with ExitStack() as stack:
        stack.enter_context(patch.object(mirror, 'get_engine', return_value=NS(connect=context)))
        stack.enter_context(patch.object(mirror, 'AsyncSession', return_value=NS()))
        stack.enter_context(patch.object(mirror, 'get_adapter', return_value=NS(session_lock=lambda *_: context())))
        batch = stack.enter_context(patch.object(mirror, '_run_batch', AsyncMock(side_effect=[True, False])))
        await mirror.run_batch(1, 'OfflineRows', NS(id='offline'), run_id=7)
        assert batch.await_count == 2
        assert all(call.args[-1] == 7 for call in batch.await_args_list)


async def main():
    signatures()
    with patch.object(mirror, 'scan_schema_ready', AsyncMock(return_value=True)):
        await governed_identity()
        await batch_progress()
        await manual_continuation()
    print('Offline mirror regression checks passed.')


if __name__ == '__main__':
    asyncio.run(main())
