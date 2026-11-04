"""One offline contract check: binlog decoding, view keys and atomic source write receipts.

No database connection, HTTP request, worker, migration execution or real source write.
"""

import asyncio
import inspect
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import sqlalchemy as sa
from sqlalchemy.dialects import mysql
from sqlalchemy.sql.dml import Insert, Update
from sqlalchemy.sql.elements import TextClause

from app.contracts.actions import ExtStatus, SubmitStatus
from app.core.errors import AppError
from app.modules.data import connector, external_mirror
from app.modules.data import mysql_cdc_source as cdc
from app.modules.integration import source_database as db


def binlog():
    import pymysqlreplication
    import pymysqlreplication.event as event_module
    import pymysqlreplication.row_event as rows_module

    signature = inspect.signature(pymysqlreplication.BinLogStreamReader)
    classes = {
        name: type(name, (), {})
        for name in (
            "GtidEvent",
            "QueryEvent",
            "RotateEvent",
            "XidEvent",
            "WriteRowsEvent",
            "UpdateRowsEvent",
            "DeleteRowsEvent",
        )
    }

    def event(name, position, **kwargs):
        item = classes[name]()
        item.position, item.timestamp = position, 1700000000
        for key, value in dict(schema="office_demo", table="device", **kwargs).items():
            setattr(item, key, value)
        return item

    events = [
        event("GtidEvent", 10, gtid="uuid:1"),
        event(
            "UpdateRowsEvent",
            20,
            rows=[
                {
                    "before_values": {"id": 1, "status": "before"},
                    "after_values": {"id": 2, "status": "after", "secret": "not projected"},
                }
            ],
        ),
        event("XidEvent", 30),
        event("DeleteRowsEvent", 40, rows=[{"values": {"id": 2, "status": "after"}}]),
        event("XidEvent", 50),
        event("WriteRowsEvent", 60, rows=[{"values": {"id": 3, "status": "final"}}]),
        event("XidEvent", 70),
        event("GtidEvent", 80, gtid="uuid:2"),
        event("WriteRowsEvent", 90, rows=[{"values": {"id": 99, "status": "uncommitted"}}]),
    ]
    source_events = [events]
    closed = []

    class Stream:
        def __init__(self, **kwargs):
            assert set(kwargs) <= set(signature.parameters), "client constructor contract changed"
            assert kwargs["resume_stream"] and kwargs["verify_checksum"] and not kwargs["enable_logging"]
            self.log_file, self.log_pos = kwargs["log_file"], kwargs["log_pos"]

        def __iter__(self):
            for item in source_events[0]:
                self.log_pos = item.position
                yield item

        def close(self):
            closed.append(True)

    capture = dict(
        schema="office_demo", table="device", serverUuid="uuid", serverId=160081, readColumns=["id", "status"]
    )
    status = dict(serverUuid="uuid", files=["bin.000001"], file="bin.000001", position=100)
    with (
        patch.object(pymysqlreplication, "BinLogStreamReader", Stream),
        patch.object(cdc, "_probe", return_value=status),
        patch.object(cdc, "connection_settings", return_value={}),
    ):
        patches = [
            patch.object(
                event_module
                if name in ("GtidEvent", "QueryEvent", "RotateEvent", "XidEvent")
                else rows_module,
                name,
                cls,
            )
            for name, cls in classes.items()
        ]
        for item in patches:
            item.start()
        try:
            result = cdc._read_changes({}, capture, {"file": "bin.000001", "position": 4})
            assert len(result["events"]) == 3 and result["position"]["position"] == 70
            assert len({row["eventKey"] for row in result["events"]}) == 3
            assert all("secret" not in (row["after"] or {}) for row in result["events"])
            mirror = {1: {"id": 1, "status": "snapshot-overlap"}}
            for row in result["events"]:
                if row["before"] is not None:
                    mirror.pop(row["before"]["id"], None)
                if row["after"] is not None:
                    mirror[row["after"]["id"]] = row["after"]
            assert mirror == {3: {"id": 3, "status": "final"}}  # DELETE, PK move and uncommitted tail.
            source_events[0] = [event("QueryEvent", 20, query="ALTER TABLE device ADD COLUMN x INT")]
            try:
                cdc._read_changes({}, capture, {"file": "bin.000001", "position": 4})
            except AppError as exc:
                assert exc.code == "CDC_STRUCTURE_CHANGED"
            else:
                raise AssertionError("DDL must stop before publication")
            source_events[0] = events
            with patch.object(cdc, "MAX_TRANSACTION_ROWS", 0):
                try:
                    cdc._read_changes({}, capture, {"file": "bin.000001", "position": 4})
                except AppError as exc:
                    assert exc.code == "CDC_TRANSACTION_TOO_LARGE"
                else:
                    raise AssertionError("cannot publish part of a transaction")
            status["files"] = []
            try:
                cdc._read_changes({}, capture, {"file": "bin.000001", "position": 4})
            except AppError as exc:
                assert exc.code == "CDC_BINLOG_GAP"
            else:
                raise AssertionError("cannot skip purged logs")
        finally:
            for item in reversed(patches):
                item.stop()
    assert len(closed) == 3
    assert cdc.position_reached(
        {"file": "bin.000002", "position": 4}, {"file": "bin.000001", "position": 100}
    )
    assert not cdc.position_reached(
        {"file": "bin.000001", "position": 4}, {"file": "bin.000001", "position": 100}
    )
    for flag in ("binlogCatchupPending", "viewValidationPending"):
        state = SimpleNamespace(cursor_value={flag: True, "scanId": 1}, complete=False)
        assert external_mirror.current_rows_sql(state) == "FALSE"


def view_keys():
    class Read:
        def __init__(self, values):
            self.values = iter(values)
            self.calls = 0

        def execute(self, stmt):
            self.calls += 1
            assert stmt.is_select
            value = next(self.values)
            return SimpleNamespace(first=lambda: value)

    good = Read([None, None])
    connector.assert_unique_key(good, "office_demo", "device_view", "id")
    assert good.calls == 2
    for values in ([(None,)], [None, (1,)]):
        try:
            connector.assert_unique_key(Read(values), "office_demo", "device_view", "id")
        except ValueError:
            pass
        else:
            raise AssertionError("NULL or duplicate declared key accepted")


def receipts():
    target = sa.Table(
        "device",
        sa.MetaData(),
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("status", sa.String),
        schema="office_demo",
    )
    receipt = sa.Table(
        "receipt",
        sa.MetaData(),
        sa.Column("receipt_id", sa.String, primary_key=True),
        *[
            sa.Column(name, sa.String)
            for name in ("run_id", "request_hash", "outcome", "result_code", "reason")
        ],
        schema="office_demo",
    )
    plan = db.Plan(
        {},
        "MySQL",
        "office_demo",
        "device",
        "id",
        "receipt",
        "a" * 64,
        "b" * 64,
        "ACT_FIXTURE",
        "1",
        {"status": "bound ' value"},
        "database://FIXTURE/1/1",
        idem_key="fixture-key",
    )

    class Connection:
        dialect = mysql.dialect()

        def __init__(self):
            self.receipt, self.updates, self.inserts = None, 0, 0
            self.exists = True

        def execute(self, statement, params=None):
            if isinstance(statement, TextClause):
                return [("device", "InnoDB"), ("receipt", "InnoDB")]
            if isinstance(statement, Insert):
                self.inserts += 1
                self.receipt = statement.compile().params
                return None
            if isinstance(statement, Update):
                assert plan.values["status"] not in str(statement.compile(dialect=self.dialect))
                self.updates += 1
                return None
            if statement.get_final_froms()[0] is receipt:
                assert statement._for_update_arg is None  # SELECT/INSERT-only receipt account.
                return SimpleNamespace(mappings=lambda: SimpleNamespace(one_or_none=lambda: self.receipt))
            assert statement._for_update_arg is not None  # Actual target still locked before update.
            return [(1,)] if self.exists else []

    conn = Connection()
    reflected = []

    def table(name, *args, **kwargs):
        reflected.append(name)
        return receipt if name == "receipt" else target

    with patch.object(db, "Table", table):
        assert db.operation(conn, plan)["outcome"] == "SUCCESS"
        assert conn.inserts == conn.updates == 1
        assert db.operation(conn, plan)["outcome"] == "SUCCESS"
        assert conn.inserts == conn.updates == 1
        try:
            db.operation(conn, replace(plan, request_hash="changed"))
        except AppError as exc:
            assert exc.code == "IDEMPOTENCY_CONFLICT"
        else:
            raise AssertionError("idempotency payload changed")
        reflected.clear()
        assert db.operation(conn, plan, read_only=True)["outcome"] == "SUCCESS"
        assert reflected == ["receipt"]  # Reconcile never needs the current target table.
        conn.receipt = None
        assert db.operation(conn, plan, read_only=True) is None
        assert conn.updates == 1
        conn.exists = False
        assert db.operation(conn, plan)["outcome"] == "FAILED"
        assert conn.updates == 1
    return plan


async def transaction_failures(plan):
    plan.progress = {"phase": "receipt_metadata", "commitStarted": False}
    with patch.object(db, "perform", AsyncMock(side_effect=TimeoutError())) as perform:
        result = await db.submit(plan)
        assert result.status == SubmitStatus.FAILED and perform.await_count == 1
        assert "phase=receipt_metadata" in result.reason

    async def uncertain(_plan):
        _plan.progress = {"phase": "commit", "commitStarted": True}
        raise ConnectionError("SECRET_SQL_AND_VALUE")

    with patch.object(db, "perform", uncertain):
        result = await db.submit(plan)
        assert result.status == SubmitStatus.UNKNOWN and "SECRET" not in result.reason
    with patch.object(db, "perform", AsyncMock(side_effect=ValueError("SECRET_SQL_AND_VALUE"))):
        try:
            await db.ReceiptAdapter(plan).query_by_key("fixture-key")
        except AppError as error:
            assert error.code == "SOURCE_DB_QUERY_FAILED" and "SECRET" not in error.message
        else:
            raise AssertionError("failed receipt query was treated as a result")
    with patch.object(db, "perform", AsyncMock(return_value=None)) as perform:
        result = await db.ReceiptAdapter(plan).query_by_key("fixture-key")
        assert result.ext_status == ExtStatus.not_found and perform.await_count == 1


async def receipt_conflicts(plan):
    from sqlalchemy.exc import IntegrityError

    receipt = {
        "outcome": "SUCCESS",
        "receipt_id": plan.receipt_id,
        "reason": "canonical",
        "result_code": "SOURCE_UPDATED",
    }
    for answer, expected in (
        (receipt, SubmitStatus.SUCCESS),
        (None, SubmitStatus.UNKNOWN),
        (db.unavailable("mismatch", "IDEMPOTENCY_CONFLICT"), SubmitStatus.FAILED),
        (ConnectionError("SECRET"), SubmitStatus.UNKNOWN),
    ):
        plan.progress = {"phase": "receipt_insert", "commitStarted": False}
        with patch.object(
            db, "perform", AsyncMock(side_effect=[IntegrityError(None, None, Exception("SECRET")), answer])
        ) as perform:
            result = await db.submit(plan)
            assert result.status == expected and perform.await_count == 2
            assert perform.await_args_list[1].kwargs == {"read_only": True}
            assert "SECRET" not in (result.reason or "")


async def main():
    binlog()
    view_keys()
    plan = receipts()
    await transaction_failures(plan)
    await receipt_conflicts(plan)
    print(
        "Source channels offline check passed: committed binlog, gaps/DDL/limits, "
        "view key validation, receipt idempotency/unknown."
    )


if __name__ == "__main__":
    asyncio.run(main())
