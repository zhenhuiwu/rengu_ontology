"""data 模块 seed：读 backend/seeds/json/data/（PDE 的 npm run export-mock 生成）。

照 app/modules/versioning/seeds/__init__.py 声明 SEEDS。
"""

from app.seeds import SeedSpec

JSON_MODULE = "data"
SEEDS: list[SeedSpec] = []
