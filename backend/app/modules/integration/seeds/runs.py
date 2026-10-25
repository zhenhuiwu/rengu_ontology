"""integration 的第二段 seed（阶段 `integration_runs`，见 app/seeds/registry.py）。负责人：Palantir。

写 writeback_call、reconcile_task：它们的外键指向 dynamic / action 的执行记录，
所以排在 dynamic 之后、qa 之前。
ext_system 仍由 `integration` 阶段（app/modules/integration/seeds/__init__.py）写，排在前面。

占位：表还没建，SEEDS 为空。照 app/modules/versioning/seeds/__init__.py 声明。
"""

from app.seeds import SeedSpec

JSON_MODULE = "system"
SEEDS: list[SeedSpec] = []
