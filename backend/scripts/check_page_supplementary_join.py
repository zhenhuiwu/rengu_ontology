"""One pure page-plan check for approved supplementary JOIN keys; no DB/API/model."""
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy import Column, Integer, MetaData, String, Table

from app.common.sql_guard import GovernedTable, InheritVia
from app.core.errors import AppError
from app.modules.qa import page_execution as replay

metadata = MetaData()
asset = Table('deep_device_w16_asset', metadata, Column('id', Integer), Column('code', String), Column('name', String))
extra = Table('deep_device_w16_extra', metadata, Column('asset_id', Integer), Column('calibration_note', String), Column('ontology_id', Integer))
task = Table('deep_device_w16_task', metadata, Column('id', Integer), Column('asset_id', Integer))
main = GovernedTable(asset, 'OfficeDevice', {}, ontology_id=16, projection=frozenset({'id', 'code', 'name'}))
child = GovernedTable(extra, 'OfficeDevice', {}, ontology_id=16,
                     inherit=InheritVia(extra.c.asset_id, main, asset.c.id),
                     projection=frozenset({'asset_id', 'calibration_note'}))
tasks = GovernedTable(task, 'MaintenanceTask', {}, ontology_id=16, projection=frozenset({'id', 'asset_id'}))
bindings = {row.name: row for row in (main, child, tasks)}
spec = {'table': extra.name, 'mainKey': 'id', 'joinKey': 'asset_id', 'fields': {'calibration_note': 'calibration_note'}}
session = SimpleNamespace(info={'ontology_id': 16, 'supplementary_bindings': {'OfficeDevice': (spec, child)}})
snapshot = {'objectTypes': {code: {'status': '已发布'} for code in ('OfficeDevice', 'MaintenanceTask')},
            'properties': {'OfficeDevice': {name: {'type': 'integer' if name == 'id' else 'string', 'source': name} for name in ('id', 'code', 'name', 'calibration_note')},
                           'MaintenanceTask': {name: {'type': 'integer', 'source': name} for name in ('id', 'asset_id')}}}
release = SimpleNamespace(ontology_id=16, snapshot=snapshot)
sql = '''SELECT a.code, a.id AS "Id", a.name AS "Name", e.calibration_note AS "Note", COUNT(t.id) AS "Tasks"
FROM deep_device_w16_asset a LEFT JOIN deep_device_w16_extra e ON a.id=e.asset_id
LEFT JOIN deep_device_w16_task t ON a.id=t.asset_id
GROUP BY a.id,a.name,e.calibration_note,a.code ORDER BY a.id'''

def rejected(query, source=release):
    try:
        replay.public_columns(query, source, session)
    except AppError as error:
        assert error.code == 'PAGE_EXECUTION_UNAVAILABLE'
    else:
        raise AssertionError('Unpublished fields or unapproved supplementary key use must remain unavailable')


with patch.object(replay, 'governed_tables', return_value=bindings):
    replay.public_columns(sql, release, session)
    rejected(sql.replace('a.code, a.id', 'e.asset_id, a.code, a.id'))
    rejected(sql.replace('GROUP BY', 'WHERE e.asset_id > 0 GROUP BY'))
    rejected(sql.replace('ON a.id=e.asset_id', 'ON a.code=e.asset_id'))
    rejected(sql.replace('ON a.id=e.asset_id', 'ON a.id=e.asset_id OR a.id>0'))
    rejected(sql.replace('LEFT JOIN deep_device_w16_extra', 'LEFT JOIN deep_device_w16_asset b ON a.id=b.id LEFT JOIN deep_device_w16_extra'))
    rejected(sql.replace('e.calibration_note AS "Note"', 'e.ontology_id AS "Note"'))
    for flag, value in (('visibility', 'hidden'), ('sensitive', True)):
        changed = deepcopy(snapshot)
        changed['properties']['OfficeDevice']['calibration_note'][flag] = value
        rejected(sql, SimpleNamespace(ontology_id=16, snapshot=changed))
    session.info['supplementary_bindings'] = {}
    rejected(sql)
print('page supplementary join-only field contract passed (no DB/API/model)')
