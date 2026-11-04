"""Read committed MySQL ROW/FULL binlog transactions; never install source objects.

Snapshot overlap protocol: record P0 before a single consistent read transaction,
copy that snapshot, then replay complete row images from P0 through P1 captured
after the copy. The mirror stays unpublished until replay reaches P1. Crashes
restart an incomplete snapshot rather than resume a lost source transaction.
"""

import asyncio
import hashlib
import json
import re
import ssl
import time
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import text

from app.core.errors import AppError
from app.modules.data import connector

MAX_TRANSACTION_ROWS = 10000
MAX_TRANSACTION_BYTES = 8 * 1024 * 1024


def error(message, code="CDC_SOURCE_OPERATION_FAILED"):
    return AppError(message, code=code, status_code=409)


def connection_settings(cfg):
    if not connector.permitted("MySQL", cfg):
        raise error("MySQL源地址未获部署授权", "CDC_SOURCE_NOT_ALLOWED")
    pinned = connector._approved_ip(cfg) if connector.real_connect_enabled() else cfg["host"]
    if not pinned:
        raise error("MySQL源地址未获部署授权", "CDC_SOURCE_NOT_ALLOWED")
    if cfg.get("ssl") and str(cfg["host"]) != str(pinned):
        raise error("MySQL域名TLS固定地址身份校验尚不可用", "CDC_SOURCE_UNSUPPORTED")
    settings = dict(
        host=pinned,
        port=int(cfg["port"]),
        user=cfg["user"],
        password=cfg["password"],
        charset="utf8mb4",
        connect_timeout=10,
        read_timeout=5,
        write_timeout=5,
    )
    if cfg.get("ssl"):
        settings["ssl"] = ssl.create_default_context()
    return settings


def _connect(cfg):
    try:
        import pymysql

        return pymysql.connect(**connection_settings(cfg), cursorclass=pymysql.cursors.DictCursor)
    except AppError:
        raise
    except ImportError:
        raise error("MySQL binlog依赖mysql-replication/PyMySQL未安装", "CDC_DRIVER_UNAVAILABLE") from None
    except Exception:
        raise error("MySQL CDC连接失败，请核对专用复制账号与TLS") from None


def _position(cursor):
    try:
        cursor.execute("SHOW BINARY LOG STATUS")
    except Exception as exc:
        if getattr(exc, "args", [None])[0] != 1064:
            raise
        cursor.execute("SHOW MASTER STATUS")
    row = cursor.fetchone()
    if not row or not row.get("File") or not row.get("Position"):
        raise error("MySQL未提供binlog位置", "CDC_BINLOG_UNAVAILABLE")
    return {"file": row["File"], "position": int(row["Position"])}


def _probe(cfg):
    connection = _connect(cfg)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT @@server_uuid AS uuid, @@server_id AS server_id, @@log_bin AS log_bin, "
                "@@binlog_format AS format, @@binlog_row_image AS image, "
                "@@binlog_row_metadata AS metadata, @@binlog_transaction_compression AS compressed"
            )
            config = cursor.fetchone()
            if (
                not config
                or not config["log_bin"]
                or config["format"] != "ROW"
                or config["image"] != "FULL"
                or config["metadata"] != "FULL"
                or config["compressed"]
            ):
                raise error(
                    "MySQL CDC要求log_bin开启、ROW/FULL/FULL及关闭事务压缩", "CDC_BINLOG_CONFIG_INVALID"
                )
            position = _position(cursor)
            cursor.execute("SHOW BINARY LOGS")
            files = [row["Log_name"] for row in cursor.fetchall()]
            return {
                "serverUuid": config["uuid"],
                "sourceServerId": int(config["server_id"]),
                **position,
                "files": files,
            }
    except AppError:
        raise
    except Exception:
        raise error("无法读取MySQL binlog配置或位置，请核对复制权限") from None
    finally:
        connection.close()


async def probe(kind, cfg):
    if kind != "MySQL":
        raise error("此捕获器只支持MySQL", "CDC_SOURCE_UNSUPPORTED")
    # Check the parser as well as the SQL driver, without opening a replication stream.
    try:
        import pymysqlreplication  # noqa: F401
    except ImportError:
        raise error("请安装mysql-replication以启用binlog通道", "CDC_DRIVER_UNAVAILABLE") from None
    result = await asyncio.to_thread(_probe, cfg)
    return {"binlogSupported": True, "nativeLogConfigured": True, "serverUuid": result["serverUuid"]}


def digest(capture):
    stable = {key: value for key, value in capture.items() if key != "snapshotProtocol"}
    return hashlib.sha256(json.dumps(stable, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


async def plan(kind, cfg, capture):
    await probe(kind, cfg)
    if type(capture.get("serverId")) is not int or not 1 <= capture["serverId"] <= 4294967295:
        raise error("须配置此订阅独有的MySQL复制server-id", "CDC_SERVER_ID_REQUIRED")
    status = await asyncio.to_thread(_probe, cfg)
    if capture["serverId"] == status["sourceServerId"]:
        raise error("复制server-id不能与源服务器相同", "CDC_SERVER_ID_REQUIRED")
    engine = await connector._read(
        kind,
        cfg,
        lambda conn: conn.execute(
            text(
                "SELECT ENGINE FROM information_schema.tables "
                "WHERE TABLE_SCHEMA=:schema AND TABLE_NAME=:table"
            ),
            {"schema": capture["schema"], "table": capture["table"]},
        ).scalar(),
        stage="验证事务表",
    )
    if engine != "InnoDB":
        raise error("MySQL CDC仅支持有真实主键的InnoDB表；视图和非事务表不可订阅", "CDC_SOURCE_UNSUPPORTED")
    capture = {**capture, "mode": "binlog", "serverUuid": status["serverUuid"]}
    return {
        "capture": capture,
        "planDigest": digest(capture),
        "sql": [],
        "requiresSourceWrite": False,
        "message": "使用已启用的ROW/FULL binlog，不安装源表触发器；启用后建立一致快照并回放交叠日志",
    }


async def validate(kind, cfg, capture):
    status = await asyncio.to_thread(_probe, cfg)
    if status["serverUuid"] != capture.get("serverUuid"):
        raise error("MySQL server UUID已变化，须重新建立基线", "CDC_BINDING_CHANGED")
    return status


async def install(kind, cfg, capture):
    await validate(kind, cfg, capture)
    return {"capture": {**capture, "snapshotProtocol": "overlap-row-image"}, "pendingCount": None}


def position_reached(position, target):
    if position["file"] == target["file"]:
        return position["position"] >= target["position"]
    left, right = re.fullmatch(r"(.+)\.(\d+)", position["file"]), re.fullmatch(r"(.+)\.(\d+)", target["file"])
    if not left or not right or left[1] != right[1]:
        raise error("binlog文件序列已变化，须重建基线", "CDC_BINLOG_GAP")
    return int(left[2]) > int(right[2])


@asynccontextmanager
async def snapshot(cfg, capture):
    """Keep a single InnoDB consistent source transaction alive across all copied pages."""
    from app.modules.data.external_mirror import scalar

    settings = connection_settings(cfg)
    engine = connector._engine("MySQL", {**cfg, "host": settings["host"]})
    try:
        async with engine.connect() as conn:
            status = await asyncio.to_thread(_probe, cfg)
            if status["serverUuid"] != capture["serverUuid"]:
                raise error("MySQL源身份变化", "CDC_BINDING_CHANGED")

            def begin(sync):
                sync.exec_driver_sql("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
                sync.exec_driver_sql("START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY")
                return {"file": status["file"], "position": status["position"]}

            start = await conn.run_sync(begin)
            preparer = conn.dialect.identifier_preparer
            qualified = ".".join(preparer.quote(name) for name in (capture["schema"], capture["table"]))
            columns = ",".join(preparer.quote(name) for name in capture["readColumns"])
            result = await conn.stream(text(f"SELECT {columns} FROM {qualified}"))

            async def pages(size):
                async for batch in result.partitions(size):
                    yield [{key: scalar(value) for key, value in row._mapping.items()} for row in batch]

            yield start, pages
            await result.close()
    finally:
        await engine.dispose()


async def current_position(cfg, capture):
    return await validate("MySQL", cfg, capture)


def _read_changes(cfg, capture, cursor):
    from app.modules.data.external_mirror import scalar

    try:
        from pymysqlreplication import BinLogStreamReader
        from pymysqlreplication.event import GtidEvent, QueryEvent, RotateEvent, XidEvent
        from pymysqlreplication.row_event import DeleteRowsEvent, UpdateRowsEvent, WriteRowsEvent
    except ImportError:
        raise error("MySQL binlog依赖未安装", "CDC_DRIVER_UNAVAILABLE") from None
    status = _probe(cfg)
    if status["serverUuid"] != capture["serverUuid"] or cursor["file"] not in status["files"]:
        raise error("MySQL源身份改变或所需binlog已清理，须重建基线", "CDC_BINLOG_GAP")
    stream = BinLogStreamReader(
        connection_settings=connection_settings(cfg),
        server_id=capture["serverId"],
        resume_stream=True,
        log_file=cursor["file"],
        log_pos=cursor["position"],
        blocking=False,
        only_events=[
            GtidEvent,
            QueryEvent,
            RotateEvent,
            XidEvent,
            WriteRowsEvent,
            UpdateRowsEvent,
            DeleteRowsEvent,
        ],
        only_schemas=[capture["schema"]],
        only_tables=[capture["table"]],
        verify_checksum=True,
        enable_logging=False,
    )
    pending, committed, position, gtid = [], [], dict(cursor), None
    active = False
    transaction_bytes = 0
    started = time.monotonic()
    try:
        for event in stream:
            if isinstance(event, GtidEvent):
                gtid = str(event.gtid)
                active = True
            elif isinstance(event, QueryEvent):
                query = event.query.strip().upper()
                if query == "ROLLBACK":
                    pending = []
                    transaction_bytes = 0
                    active = False
                elif query == "BEGIN":
                    active = True
                elif query not in ("BEGIN", "COMMIT") and re.match(
                    r"^(ALTER|CREATE|DROP|RENAME|TRUNCATE)\b", query
                ):
                    schema = event.schema.decode() if isinstance(event.schema, bytes) else event.schema
                    if schema and schema != capture["schema"]:
                        active = False
                        position = {"file": stream.log_file, "position": int(stream.log_pos), "gtid": gtid}
                        continue
                    # Unknown DDL can invalidate column ordinals; stop rather than guess.
                    raise error("MySQL源发生DDL变化，须核对结构并重建基线", "CDC_STRUCTURE_CHANGED")
            elif isinstance(event, (WriteRowsEvent, UpdateRowsEvent, DeleteRowsEvent)):
                if event.schema != capture["schema"] or event.table != capture["table"]:
                    continue
                for row in event.rows:
                    before = (
                        row.get("before_values")
                        if isinstance(event, UpdateRowsEvent)
                        else row.get("values")
                        if isinstance(event, DeleteRowsEvent)
                        else None
                    )
                    after = (
                        row.get("after_values")
                        if isinstance(event, UpdateRowsEvent)
                        else row.get("values")
                        if isinstance(event, WriteRowsEvent)
                        else None
                    )

                    def image(value):
                        if value is None:
                            return None
                        if not set(capture["readColumns"]) <= value.keys():
                            raise error("binlog缺少完整授权列row-image", "CDC_PAYLOAD_INVALID")
                        projected = {column: scalar(value[column]) for column in capture["readColumns"]}
                        if len(json.dumps(projected, ensure_ascii=False).encode()) > 262144:
                            raise error("源行镜像超过大小上限", "CDC_PAYLOAD_INVALID")
                        return projected

                    pending.append(
                        {
                            "op": "update"
                            if before is not None and after is not None
                            else "delete"
                            if after is None
                            else "insert",
                            "before": image(before),
                            "after": image(after),
                            "decimalPaths": [
                                f"{side}.{column}"
                                for side, value in (("before", before), ("after", after))
                                if value is not None
                                for column in capture["readColumns"]
                                if isinstance(value.get(column), Decimal)
                            ],
                            "at": datetime.fromtimestamp(event.timestamp, UTC).isoformat(),
                        }
                    )
                    if len(pending) > MAX_TRANSACTION_ROWS:
                        raise error(
                            "MySQL单事务超出受控批次上限，未提交部分事务", "CDC_TRANSACTION_TOO_LARGE"
                        )
                    transaction_bytes += len(json.dumps(pending[-1], ensure_ascii=False).encode())
                    if transaction_bytes > MAX_TRANSACTION_BYTES:
                        raise error(
                            "MySQL事务镜像超过受控大小上限，未发布部分事务", "CDC_TRANSACTION_TOO_LARGE"
                        )
            if isinstance(event, XidEvent) or (
                isinstance(event, QueryEvent) and event.query.strip().upper() == "COMMIT"
            ):
                position = {
                    "file": stream.log_file,
                    "position": int(stream.log_pos),
                    "gtid": gtid,
                    "committedAt": datetime.fromtimestamp(event.timestamp, UTC).isoformat(),
                }
                for index, row in enumerate(pending):
                    row["eventKey"] = (
                        f"{capture['serverUuid']}:{position['file']}:{position['position']}:{index}"
                    )
                    decimal_paths = row.pop("decimalPaths")
                    row["position"] = {
                        **position,
                        **({"decimalPaths": decimal_paths} if decimal_paths else {}),
                    }
                committed.extend(pending)
                pending = []
                transaction_bytes = 0
                active = False
                if len(committed) >= 100 or time.monotonic() - started >= 2:
                    break
        if not active:
            position = {**position, "file": stream.log_file, "position": int(stream.log_pos)}
        return {"events": committed, "position": position, "head": status}
    except AppError:
        raise
    except Exception as exc:
        if getattr(exc, "args", [None])[0] == 1236:
            raise error(
                "MySQL拒绝续读binlog，可能日志已清理或复制server-id冲突；请核对后重建基线", "CDC_BINLOG_GAP"
            ) from None
        raise error("MySQL binlog读取失败；保留已提交游标，不跳过变更") from None
    finally:
        stream.close()


async def read_changes(cfg, capture, cursor):
    return await asyncio.to_thread(_read_changes, cfg, capture, cursor)
