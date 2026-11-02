"""Retained local regression checks; do not need a database, worker or network."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from sqlalchemy import BigInteger, Column, MetaData, Table, literal, select

from app.common.auth import CurrentUser
from app.core.errors import AppError
from app.modules.action.condition_expr import Unavailable
from app.modules.event.delivery import (
    PERMISSION_ERRORS,
    PREFLIGHT_WAIT_ERRORS,
    authorize_object,
    execution_now,
    preflight_failure,
    prepare,
)
from app.modules.event.exceptions import validate_decision
from app.modules.event.simulation import filter_matches


def test_frozen_business_clock_does_not_pause_delivery_deadlines():
    from app.core import clock

    fixed = datetime(2000, 1, 1, tzinfo=UTC)
    with clock.frozen(fixed):
        assert clock.now() == fixed
        actual = execution_now()
        assert actual > fixed + timedelta(days=1)
        row = SimpleNamespace(created_at=actual - timedelta(minutes=16), attempts=0)
        preflight_failure(row, "EVENT_OBJECT_NOT_SYNCED")
        assert row.state == "failed" and row.next_at is None


def test_preflight_wait_is_bounded_and_permissions_never_poll():
    now = datetime(2026, 10, 31, tzinfo=UTC)
    with patch("app.modules.event.delivery.execution_now", return_value=now):
        for error in PERMISSION_ERRORS | PREFLIGHT_WAIT_ERRORS | {"DELIVERY_ROUTE_NOT_CONFIGURED"}:
            row = SimpleNamespace(created_at=now, attempts=0)
            preflight_failure(row, error)
            assert row.attempts == 0 and row.error == error
            if error in PREFLIGHT_WAIT_ERRORS:
                assert row.state == "pending" and row.next_at == now + timedelta(minutes=1)
                row.created_at = now - timedelta(minutes=15)
                preflight_failure(row, error)
            assert row.state == "failed" and row.next_at is None and row.finished_at == now
            validate_decision(row, "retry")


def test_blank_snapshot_filter_matches_all_but_invalid_nonblank_still_fails():
    for text in ("", " \t\n", "全部", " 全部 "):
        assert filter_matches(text, [], {})
    assert not filter_matches("False", [], {})
    for text in ("unknown()", "(", "plain text"):
        try:
            filter_matches(text, [], {})
        except Unavailable:
            pass
        else:
            raise AssertionError("An invalid nonblank filter was accepted")


async def test_legacy_permission_pending_and_expired_wait_never_replay_after_repair():
    now = datetime(2026, 10, 31, tzinfo=UTC)
    for error in PERMISSION_ERRORS | PREFLIGHT_WAIT_ERRORS:
        row = SimpleNamespace(
            id=1, event_id=2, state="pending", error=error, result=None, attempts=0,
            next_at=None, created_at=now - timedelta(minutes=16),
        )
        event = SimpleNamespace(is_test=True, ingested_at=now)
        session = SimpleNamespace(
            get=AsyncMock(side_effect=[row, event]), scalar=AsyncMock(return_value=row),
            scalars=AsyncMock(return_value=[row]), add=Mock(),
        )
        with (
            patch("app.modules.event.delivery.execution_now", return_value=now),
            patch("app.modules.event.delivery.resolved_route", new_callable=AsyncMock) as route,
            patch("app.modules.event.delivery.authorize_object", new_callable=AsyncMock) as authorize,
        ):
            assert not await prepare(session, 1)
        assert row.state == "failed" and row.error == error and row.attempts == 0
        assert row.next_at is None
        route.assert_not_awaited()
        authorize.assert_not_awaited()


async def test_missing_mirror_and_ambiguous_identity_keep_rls_and_workspace_boundaries():
    table = Table(
        "mirror_policy_check", MetaData(),
        Column("internal_row", BigInteger), Column("internal_scope", BigInteger),
    )
    mapping = SimpleNamespace(
        object_type="device", inherit=False, table=table, row_identity="internal_row",
        scope_column="internal_scope", source_sql="unused", columns={}, links={},
    )
    user = CurrentUser(id="owner", name="owner", ontology_id=2, func={"event-types": "manage"})
    event = SimpleNamespace(ontology_id=2, obj_kind="device", object_id="one")
    references = select(literal("one").label("identity"), literal(1).label("row_id"))
    for expr, candidates, expected in (
        (None, [], "EVENT_OBJECT_NOT_SYNCED"),
        (object(), [], "EVENT_OBJECT_FORBIDDEN"),
        (None, [1, 2], "EVENT_OBJECT_AMBIGUOUS"),
        (object(), [1, 2], "EVENT_OBJECT_FORBIDDEN"),
    ):
        session = SimpleNamespace(info={"ontology_id": 2}, scalars=AsyncMock(return_value=candidates))
        with (
            patch("app.common.sql_guard.ensure_governed_tables", new_callable=AsyncMock),
            patch("app.modules.event.delivery.governed_tables", return_value={"device": mapping}),
            patch("app.modules.event.delivery.row_filter", new=AsyncMock(return_value=expr)),
            patch("app.common.object_identity.row_source", return_value=table),
            patch("app.common.object_identity.identity_rows", return_value=references) as identities,
            patch("app.modules.event.delivery.to_sql", return_value=literal(True)),
        ):
            try:
                await authorize_object(session, user, event)
            except AppError as exc:
                assert exc.code == expected
            else:
                raise AssertionError("An absent or ambiguous row was authorized")
        identities.assert_called_once_with(mapping, unique_only=False)
        query = str(session.scalars.call_args.args[0])
        assert "internal_scope" in query and "internal_row" in query
    event.ontology_id = 3
    try:
        await authorize_object(session, user, event)
    except AppError as exc:
        assert exc.code == "EVENT_OBJECT_FORBIDDEN"
    else:
        raise AssertionError("A cross-workspace event was authorized")


def test_only_failed_blank_filter_snapshots_can_be_manually_retried():
    for text in ("", " \t\n"):
        validate_decision(SimpleNamespace(state="failed", error="FILTER_NOT_EXECUTABLE", attempts=0, subscription={"filter": text}), "retry")
    try:
        validate_decision(SimpleNamespace(state="failed", error="FILTER_NOT_EXECUTABLE", attempts=0, subscription={"filter": "unknown()"}), "retry")
    except AppError as exc:
        assert exc.code == "DELIVERY_RETRY_BLOCKED"
    else:
        raise AssertionError("Invalid nonblank historical filter was allowed to retry")
