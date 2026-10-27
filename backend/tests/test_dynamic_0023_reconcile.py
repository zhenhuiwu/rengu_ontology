"""dynamic_0023：把偏离 dynamic_0003 终态的库拉回与新库一致（dims / 守卫 / template_code）。"""

import asyncio
import json

import asyncpg
import pytest
from alembic import command

from tests.helpers.migrations import db_state, temp_database

TAMPER = [
    # 模拟「旧版 0003 + 手工拆守卫回退」等偏离：残留门店两列、dims 带默认值、守卫缺失、模板列缺失
    "DROP TRIGGER trg_rule_hit_append_only ON rule_hit",
    "DROP TRIGGER trg_rule_hit_no_truncate ON rule_hit",
    "ALTER TABLE rule_hit ADD COLUMN store_id varchar(64), ADD COLUMN store_label varchar(200)",
    "ALTER TABLE rule_hit ALTER COLUMN dims SET DEFAULT '[]'::jsonb",
    "ALTER TABLE alert ADD COLUMN store_id varchar(64)",
    "ALTER TABLE scenario DROP COLUMN template_code",
]


def _shape(state):
    return {f: getattr(state, f) for f in ("tables", "columns", "indexes", "triggers", "functions")}


async def test_reconcile_converges_to_fresh_schema():
    up = asyncio.to_thread
    async with temp_database() as (cfg, dsn):
        await up(command.upgrade, cfg, "head")
        fresh = await db_state(dsn)
    async with temp_database() as (cfg, dsn):
        await up(command.upgrade, cfg, "notify_0022")
        conn = await asyncpg.connect(dsn)
        try:
            for sql in TAMPER:
                await conn.execute(sql)
            await conn.execute("SELECT set_config('rengu.ontology_id', '1', false)")
            rule_id = await conn.fetchval(
                'INSERT INTO rule (code, name, "desc", owner, status, obj_kind, level, output,'
                " conds, logic, expr, dedupe, cooldown, trigger, auto_stage, weekly)"
                " VALUES ('R-MIG23', 'probe', '', 'test', 'published', 'product', 'high', 'alert',"
                " '[]', 'and', '', '', '', '{}', '{}', 0) RETURNING id"
            )
            await conn.executemany(
                "INSERT INTO rule_hit (rule_id, seq, at, obj_kind, obj_id, obj_label, store_id, store_label,"
                " value, threshold, result, code) VALUES ($1, $2, now(), 'product', 'P1', 'P', $3, $4,"
                " '1', '2', 'deduped', $5)",
                [
                    (rule_id, 1, "S1", "Shop", "H1"),
                    (rule_id, 2, "S2", None, "H2"),
                    (rule_id, 3, None, None, "H3"),
                ],
            )
            await up(command.upgrade, cfg, "head")
            rows = await conn.fetch("SELECT dims FROM rule_hit ORDER BY seq")
            assert [json.loads(r["dims"]) for r in rows] == [
                [{"objKind": "store", "id": "S1", "label": "Shop"}],
                [{"objKind": "store", "id": "S2", "label": "S2"}],
                [],
            ]
            with pytest.raises(asyncpg.RaiseError, match="UPDATE"):
                await conn.execute("UPDATE rule_hit SET value = 'changed'")
            assert await conn.fetchval("SELECT relforcerowsecurity FROM pg_class WHERE relname = 'rule_hit'")
        finally:
            await conn.close()
        assert _shape(await db_state(dsn)) == _shape(fresh)
        # 已对齐的库再跑一遍：降回 notify_0022 再升，结构不变（幂等）
        await up(command.downgrade, cfg, "notify_0022")
        await up(command.upgrade, cfg, "head")
        assert _shape(await db_state(dsn)) == _shape(fresh)
