"""Offline key lifecycle checks: no database, HTTP, real credential or business execution."""

import asyncio
import hashlib
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import Request, Response
from pydantic import ValidationError
from sqlalchemy.dialects import postgresql

from app.common.auth import CurrentUser
from app.core.errors import AppError, Forbidden, Unauthorized, VersionConflict
from app.modules.admin import api_keys as keys

AT = datetime(2026, 10, 31, tzinfo=UTC)


def key_fixture():
    return SimpleNamespace(
        id=10, code="example-key", name="example", env="测试", lock_version=3,
        principal_kind="user", revoked_at=None, expires_at=AT + timedelta(days=30),
        token_hash=hashlib.sha256(b"rg_test_inert_old_fixture").hexdigest(), prefix="masked", last4="only",
        updated_at=AT, scopes=["meta:read", "actions:dry-run", "events:ingest", "functions:invoke"],
        func_snapshot={"rules": "view"}, ontology_ids=[17], ip_allow=["10.0.0.0/8"],
        action_types=["example-a"], event_types=["example-e"], function_types=["example-f"],
    )


def rejected(callback, kind=Forbidden):
    try:
        callback()
    except kind:
        return
    raise AssertionError("Expected rejection")


def check_policy():
    key = key_fixture()
    for field in ("actionTypes", "eventTypes", "functionTypes"):
        for value in (None, ["another"]):
            rejected(lambda field=field, value=value: keys.validate_policy_narrowing(
                key, keys.KeyPolicyIn.model_validate({field: value}), key.scopes, AT,
            ))
        # [] means deny all. It is a valid narrowing and must never mean unrestricted.
        keys.validate_policy_narrowing(key, keys.KeyPolicyIn.model_validate({field: []}), key.scopes, AT)
    key.action_types = None
    keys.validate_policy_narrowing(key, keys.KeyPolicyIn(actionTypes=["example-a"]), key.scopes, AT)
    key.action_types = []
    rejected(lambda: keys.validate_policy_narrowing(
        key, keys.KeyPolicyIn(actionTypes=["example-a"]), key.scopes, AT,
    ))
    keys.validate_policy_narrowing(key, keys.KeyPolicyIn(ipAllow=["10.1.0.1"]), key.scopes, AT)
    for value in ([], ["0.0.0.0/0"], ["2001:db8::/32"]):
        rejected(lambda value=value: keys.validate_policy_narrowing(
            key, keys.KeyPolicyIn(ipAllow=value), key.scopes, AT,
        ))
    keys.validate_policy_narrowing(key, keys.KeyPolicyIn(expiresAt=AT + timedelta(days=1)), key.scopes, AT)
    for expiry in (None, AT + timedelta(days=31)):
        rejected(lambda expiry=expiry: keys.validate_policy_narrowing(
            key, keys.KeyPolicyIn(expiresAt=expiry), key.scopes, AT,
        ))
    rejected(lambda: keys.validate_policy_narrowing(
        key, keys.KeyPolicyIn(expiresAt=AT), key.scopes, AT,
    ), AppError)
    rejected(lambda: keys.KeyPolicyIn.model_validate({"ontologyIds": [99]}), ValidationError)
    rejected(lambda: keys.KeyPolicyIn.model_validate({"name": None}), ValidationError)
    rejected(lambda: keys.KeyRotateIn.model_validate(
        {"version": 3, "scopes": ["meta:read"]},
    ), ValidationError)
    rejected(lambda: keys.KeyPolicyIn.model_validate({"expiresAt": "2026-11-01T10:00:00"}), ValidationError)


async def check_creation():
    body = keys.KeyCreateIn.model_validate({
        "name": "example", "env": "测试", "binding": {"kind": "用户", "userId": "example-user"},
        "scopes": ["actions:dry-run", "events:ingest", "functions:invoke"],
        "actionTypes": ["example-a"], "eventTypes": ["example-event-api"], "functionTypes": ["example-f"],
    })

    class Catalog:
        def __init__(self, missing=False):
            self.info = {"ontology_id": 17}
            self.calls = 0
            self.missing = missing

        async def execute(self, statement):
            self.calls += 1
            assert statement.is_select
            compiled = statement.compile(dialect=postgresql.dialect())
            sql = str(compiled)
            assert "ontology_id =" in sql and 17 in compiled.params.values()
            assert not any(word in sql for word in ("code_text", "payload", "definition", "http_mapping"))
            table = statement.get_final_froms()[0].name
            assert len(statement.selected_columns) == (2 if table == "event_type" else 1)
            rows = {
                "action_type": [("example-a",)],
                "event_type": [("example-event-code", "example-event-api")],
                "function": [("example-f",)],
            }[table]
            return SimpleNamespace(all=lambda: [] if self.missing else rows)

    catalog = Catalog()
    await keys.validate_create_types(catalog, body, 17)
    assert catalog.calls == 3
    for field in ("action_types", "event_types", "function_types"):
        for value in (None, []):
            empty = body.model_copy(update={name: value for name in (
                "action_types", "event_types", "function_types",
            )})
            catalog = Catalog()
            await keys.validate_create_types(catalog, empty, 17)
            assert catalog.calls == 0 and getattr(empty, field) == value
        only = body.model_copy(update={name: None for name in (
            "action_types", "event_types", "function_types",
        ) if name != field})
        try:
            await keys.validate_create_types(Catalog(missing=True), only, 17)
        except AppError as exc:
            assert exc.status_code == 400 and exc.details["fields"][0]["field"].endswith("[0]")
        else:
            raise AssertionError("Unknown / other-workspace identifier accepted")
    catalog = Catalog()
    try:
        await keys.validate_create_types(catalog, body, 18)
    except Forbidden:
        assert catalog.calls == 0
    else:
        raise AssertionError("Untrusted workspace context accepted")


async def check_rotation():
    key = key_fixture()
    before = deepcopy(vars(key))
    user = CurrentUser(id="example-user", name="Example", func={"api-keys": "manage"})
    request = Request({"type": "http", "method": "POST", "path": "/", "headers": []})
    audits = []

    class Session:
        async def flush(self):
            key.lock_version += 1

    async def get_key(session, code, *, lock):
        assert lock is True
        return key

    async def rows(session, values, at):
        return [{"id": key.code, "version": key.lock_version, "prefix": key.prefix, "last4": key.last4}]

    async def audit(session, **kwargs):
        audits.append(kwargs)

    with (
        patch.object(keys, "get_key", get_key), patch.object(keys, "key_rows", rows),
        patch.object(keys, "insert_audit", audit), patch.object(keys, "security_now", lambda: AT),
        patch.object(keys.secrets, "token_urlsafe", lambda size: "inert_new_fixture"),
    ):
        response = Response()
        result = await keys.rotate_key(
            key.code, keys.KeyRotateIn(version=3), Session(), user, request, response,
        )
        assert hashlib.sha256(result["secret"].encode()).hexdigest() == key.token_hash != before["token_hash"]
        assert response.headers["Cache-Control"] == "no-store"
        assert response.headers["Pragma"] == "no-cache"
        changed = {name for name in before if before[name] != getattr(key, name)}
        assert changed <= {"token_hash", "prefix", "last4", "updated_at", "lock_version"}
        assert len(audits) == 1 and "secret" not in str(audits) and "fixture" not in str(audits)
        for version, revoked, expiry, kind in (
            (3, None, before["expires_at"], VersionConflict),
            (4, AT, before["expires_at"], Forbidden),
            (4, None, AT, Forbidden),
        ):
            key.revoked_at, key.expires_at = revoked, expiry
            try:
                await keys.rotate_key(
                    key.code, keys.KeyRotateIn(version=version), Session(), user, request, Response(),
                )
            except kind:
                pass
            else:
                raise AssertionError("Stale/expired/revoked rotation accepted")
        key.revoked_at, key.expires_at, key.principal_kind = None, before["expires_at"], "service"
        try:
            await keys.rotate_key(key.code, keys.KeyRotateIn(version=4), Session(), user, request, Response())
        except Forbidden:
            pass
        else:
            raise AssertionError("Service-key rotation accepted without roles:manage")

    class Lookup:
        async def scalar(self, statement):
            digest = next(iter(statement.compile().params.values()))
            return key if digest == key.token_hash else None

    # The old hash cannot authenticate after rotation, before any principal or workspace is read.
    try:
        await keys.authenticate_key(Lookup(), "rg_test_inert_old_fixture", request, None)
    except Unauthorized:
        pass
    else:
        raise AssertionError("Previous credential still authenticates")


async def main():
    check_policy()
    await check_creation()
    await check_rotation()
    print("API key maintenance checks passed (offline; no credentials printed).")


if __name__ == "__main__":
    asyncio.run(main())
