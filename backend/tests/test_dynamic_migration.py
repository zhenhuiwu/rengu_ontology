"""dynamic 模块迁移往返（CONVENTIONS §16）：用骨架 tests/helpers/migrations.py::assert_module_round_trip，
按 ScriptDirectory 找 dynamic 第一个迁移的父迁移，不假定 head；只列 dynamic 自己的四张表。
枚举走 enum_column（varchar + CHECK），不建 PG 类型，所以 own_types 为空。
"""

from tests.helpers.migrations import assert_module_round_trip

OWN_TABLES = {
    "alert",
    "alert_log",
    "action_run",
    "action_run_log",
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
