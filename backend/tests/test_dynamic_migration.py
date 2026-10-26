"""dynamic 模块迁移往返（CONVENTIONS §16）：用骨架 tests/helpers/migrations.py::assert_module_round_trip，
按 ScriptDirectory 找 dynamic 第一个迁移的父迁移，不假定 head；只列 dynamic 自己的四张表。
枚举走 enum_column（varchar + CHECK），不建 PG 类型，所以 own_types 为空。
"""

import asyncio
import json

import asyncpg
import pytest
from alembic import command
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.modules.dynamic.models import AlertLevel
from app.modules.dynamic.rules_models import Rule, RuleOutput, RuleStatus
from tests.helpers.migrations import assert_module_round_trip, temp_database

OWN_TABLES = {
    "alert",
    "alert_log",
    "action_run",
    "action_run_log",
    "action_run_daily_counter",
    # dynamic_0002：规则 / 情景推演 / 生命周期
    "rule",
    "rule_version",
    "rule_draft",
    "rule_hit",
    "scenario",
    "scenario_result",
    "scenario_impact",
    "lifecycle",
    "lifecycle_version",
    "lifecycle_state",
    "lifecycle_transition",
    "lifecycle_change",
    "lifecycle_draft",
}


async def test_dynamic_module_round_trip():
    await assert_module_round_trip("dynamic", OWN_TABLES)


async def test_dims_migration_preserves_existing_append_only_hits():
    async with temp_database() as (cfg, dsn):
        await asyncio.to_thread(command.upgrade, cfg, "static_0003")
        engine = create_async_engine(make_url(dsn).set(drivername="postgresql+asyncpg"))
        try:
            async with AsyncSession(engine, expire_on_commit=False) as session:
                rule = Rule(
                    code="R-MIG",
                    name="Migration probe",
                    owner="test",
                    status=RuleStatus.published,
                    obj_kind="product",
                    level=AlertLevel.high,
                    output=RuleOutput.alert,
                    conds=[],
                    trigger={},
                    auto_stage={},
                )
                session.add(rule)
                await session.commit()
                rule_id = rule.id
        finally:
            await engine.dispose()
        conn = await asyncpg.connect(dsn)
        try:
            await conn.executemany(
                "INSERT INTO rule_hit (rule_id, seq, at, obj_kind, obj_id, obj_label, "
                "store_id, store_label, value, threshold, result, code) "
                "VALUES ($1, $2, now(), 'product', 'P1', 'Product', $3, $4, '1', '2', 'deduped', $5)",
                [
                    (rule_id, 1, "S1", "Shop", "H1"),
                    (rule_id, 2, "S2", None, "H2"),
                    (rule_id, 3, None, None, "H3"),
                ],
            )
            await asyncio.to_thread(command.upgrade, cfg, "dynamic_0003")
            dims = await conn.fetch("SELECT dims FROM rule_hit ORDER BY seq")
            assert [json.loads(r["dims"]) for r in dims] == [
                [{"objKind": "store", "id": "S1", "label": "Shop"}],
                [{"objKind": "store", "id": "S2", "label": "S2"}],
                [],
            ]
            with pytest.raises(asyncpg.RaiseError, match="UPDATE"):
                await conn.execute("UPDATE rule_hit SET value = 'changed'")
            await asyncio.to_thread(command.downgrade, cfg, "static_0003")
            rows = await conn.fetch("SELECT store_id, store_label FROM rule_hit ORDER BY seq")
            assert [tuple(r) for r in rows] == [("S1", "Shop"), ("S2", "S2"), (None, None)]
            with pytest.raises(asyncpg.RaiseError, match="DELETE"):
                await conn.execute("DELETE FROM rule_hit")
            await asyncio.to_thread(command.upgrade, cfg, "head")
            assert await conn.fetchval("SELECT count(*) FROM rule_hit") == 3
        finally:
            await conn.close()
