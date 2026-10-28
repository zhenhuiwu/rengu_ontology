"""Migration keeps previously saved external systems unchanged and unmarked."""

import asyncio
import json

import asyncpg
import pytest
from alembic import command

from tests.helpers.migrations import temp_database


async def test_local_only_migration_preserves_old_rows():
    async with temp_database() as (config, dsn):
        await asyncio.to_thread(command.upgrade, config, "qa_0007")
        connection = await asyncpg.connect(dsn)
        try:
            for code, environment in (("OLD-PROD", "生产"), ("OLD-TEST", "测试")):
                await connection.execute(
                    "INSERT INTO ext_system (ontology_id, code, name, vendor, category, endpoint, auth_type, "
                    "auth_detail, idempotent, idem_header, timeout_ms, max_retry, retry_text, rate_limit, "
                    "owner, status, env) VALUES (1, $1, $1, 'legacy', 'Webhook', 'https://legacy.example', "
                    "'', '', false, '-', 1000, 0, '', '', '', 'ok', $2)",
                    code,
                    environment,
                )
            before = [
                json.loads(row[0])
                for row in await connection.fetch(
                    "SELECT row_to_json(e)::text FROM ext_system e ORDER BY code"
                )
            ]
        finally:
            await connection.close()
        await asyncio.to_thread(command.upgrade, config, "integration_0031")
        connection = await asyncpg.connect(dsn)
        try:
            after = [
                json.loads(row[0])
                for row in await connection.fetch(
                    "SELECT row_to_json(e)::text FROM ext_system e ORDER BY code"
                )
            ]
            assert all(row.pop("local_only") is False for row in after)
            assert before == after
        finally:
            await connection.close()
        with pytest.raises(RuntimeError, match="本机演示标记不可丢弃"):
            await asyncio.to_thread(command.downgrade, config, "qa_0007")
