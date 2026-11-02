"""Offline CDC guard: late commits stay pending and ACK uses exact IDs only."""
import asyncio
import json
from decimal import Decimal
from unittest.mock import patch

from app.modules.data import dm_cdc_source as source


async def main():
    capture = source._capture({'generation': 'a' * 32, 'schema': 'fixture', 'table': 'rows',
                               'primaryKey': 'id', 'primaryKind': 'string', 'readColumns': ['id'],
                               'structureSignature': 'b' * 64, 'bindingSignature': 'c' * 64})
    other = source._capture({**capture, 'generation': 'd' * 32, 'resources': {
        key: name.replace('a' * 32, 'd' * 32) for key, name in capture['resources'].items()}})
    assert set(capture['resources'].values()).isdisjoint(other['resources'].values())
    assert source._tokens('"key with space"') != source._tokens('"keywithspace"')
    assert source._quote('x";DROP TABLE y;--') == '"x"";DROP TABLE y;--"'
    assert source._trigger_matches(source._statements(capture)[2].replace('CREATE TRIGGER', 'CREATE OR REPLACE TRIGGER', 1), capture)
    assert source._trigger_matches(source._trigger_body(capture), capture)
    assert not source._trigger_matches(source._trigger_body(capture).replace("'I'", "'D'", 1), capture)

    class CatalogConnection:
        def execute(self, statement, parameters):
            return [(capture['resources']['journal'], 'TABLE', Decimal(42), 'VALID')]

    catalog = source._objects(CatalogConnection(), capture)
    assert type(catalog[capture['resources']['journal']]['id']) is int
    json.dumps(catalog)  # DM catalog NUMBER values must be safe in PG capture JSON.

    class Row:
        _mapping = {'id': 'late'}

    class Result:
        rowcount = 1

        def __init__(self, rows=()):
            self.rows = rows

        def all(self):
            return self.rows

        def __iter__(self):
            return iter(self.rows)

    class Connection:
        pending = [(2, capture['generation'], 'I', None, 'late', None)]

        def execute(self, statement, params=None):
            if params is not None:
                sql = str(statement)
                assert ' IN (:id0)' in sql and '>' not in sql and '<' not in sql
                assert params == {'generation': capture['generation'], 'id0': 2}
                self.pending = [event for event in self.pending if event[0] != 2]
                return Result()
            if statement.get_final_froms()[0].name == capture['resources']['journal']:
                assert statement.whereclause is None, 'A sequence lower bound can lose late commits'
                return Result(self.pending[:statement._limit_clause.value])
            return Result([Row()])

    conn = Connection()

    async def run(kind, cfg, operation, **kwargs):
        return operation(conn)

    status = {'valid': True, 'pendingCount': 1, 'oldestCapturedAt': None, 'capture': capture}
    with patch.object(source, '_run', run), patch.object(source, '_validate_conn', return_value=status):
        batch = await source.read_changes(source.KIND, {}, capture)
        assert [event['eventId'] for event in batch['events']] == [2]
        assert await source.ack(source.KIND, {}, capture, [2]) == 1
        # Event 1 was allocated earlier but becomes visible after event 2 is ACKed.
        conn.pending.append((1, capture['generation'], 'U', 'late', 'late', None))
        batch = await source.read_changes(source.KIND, {}, capture)
        assert [event['eventId'] for event in batch['events']] == [1]
        assert batch['rows'] == [{'id': 'late'}]
    print('CDC offline guard passed; no databases were opened')


if __name__ == '__main__':
    asyncio.run(main())
