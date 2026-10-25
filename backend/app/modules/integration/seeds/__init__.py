"""integration 模块 seed：读 backend/seeds/json/integration/（PDE 的 npm run export-mock 生成）。

本阶段（`integration`，排在 versioning 之前）只写 ext_system；writeback_call、reconcile_task
在后面的 `integration_runs` 阶段写（app/modules/integration/seeds/runs.py，排在 dynamic 之后）。

照 app/modules/versioning/seeds/__init__.py 声明 SEEDS。
"""

from app.seeds import SeedSpec

JSON_MODULE = "system"
SEEDS: list[SeedSpec] = []
