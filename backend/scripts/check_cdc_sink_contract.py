"""Offline CDC trust-boundary check; no database, HTTP login, pytest or source DDL.

Run from backend: .venv/Scripts/python.exe scripts/check_cdc_sink_contract.py
Real DM delivery/rollback/restart verification is a separate, explicitly armed check.
"""
import sys
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.main import app
from app.modules.data.external_cdc import _lease_valid, key_json, now
from app.modules.data.models import ExternalCdcSubscription


def main():
    paths = app.openapi()['paths']
    prefix = '/api/data/v1/external-read-bindings/{code}/cdc'
    for suffix in ('', '/probe', '/plan', '/install', '/enable', '/pause', '/resume'):
        assert prefix + suffix in paths
    table = ExternalCdcSubscription.__table__
    assert 'ontology_id' in table.c
    assert any(set(c.name for c in constraint.columns) == {'ontology_id', 'object_code'}
               for constraint in table.constraints)
    assert key_json(1) != key_json('1')  # Business key types must never alias a platform identity.
    for unsupported in (True, None, 1.5, 'x' * 401):
        try:
            key_json(unsupported)
        except Exception as exc:
            assert getattr(exc, 'code', None) == 'CDC_PRIMARY_UNSUPPORTED'
        else:
            raise AssertionError('unsupported source key accepted')
    sub = SimpleNamespace(enabled=True, lease_owner='current', lease_until=now() + timedelta(seconds=10))
    assert _lease_valid(sub, 'current')
    assert not _lease_valid(sub, 'old')
    sub.enabled = False
    assert not _lease_valid(sub, 'current')
    sub.enabled, sub.lease_until = True, now() - timedelta(seconds=1)
    assert not _lease_valid(sub, 'current')
    print('CDC route, workspace uniqueness, business-key and lease guards passed (offline only)')


if __name__ == '__main__':
    main()
