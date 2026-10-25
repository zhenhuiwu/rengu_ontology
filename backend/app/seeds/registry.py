"""seed 模块注册表。顺序 = 加载顺序（有外键依赖的放后面）。新模块在这里加一行。"""

SEED_MODULES: list[str] = [
    "versioning",
    "admin",
    "data",
    "integration",
    "biz",
    "static",
    "dynamic",
    "action",
    "event",
    "qa",
]
