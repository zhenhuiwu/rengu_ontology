"""seed 模块注册表。顺序 = 加载顺序（被依赖的放前面）。新模块在这里加一行。

- versioning 在 static 之前：版本行只读导出 JSON（快照由各模块登记的 seed_section 按 JSON 组装，不读表），
  静态的 change_request.release_id 等外键要按 ver 查到 ontology_release.id，所以版本行必须先写；
- action、event 在 dynamic 之前：规则、生命周期迁移、执行记录的外键指向 action_type.id / event_type.id；
- admin 最先：sys_user 吸收动态的 PEOPLE（转派候选人），其它模块的人名都对得上用户；
- integration 分两段：`integration` 只写 ext_system（被动作 / 数据源引用，排前面）；
  `integration_runs` 写 writeback_call、reconcile_task
  （外键指向 dynamic / action 的执行记录，排在 dynamic 之后）。

列表里的是**阶段名**。阶段名就是模块名时读 `app.modules.<模块>.seeds`；
不是模块名的阶段在 SEED_STAGES 里登记「负责模块 + seeds 包」。
"""

SEED_MODULES: list[str] = [
    "admin",
    "data",
    "integration",
    "biz",
    "versioning",
    "static",
    "action",
    "event",
    "dynamic",
    "integration_runs",
    "qa",
]

# 阶段名 → (负责模块, seeds 包)。只登记阶段名不等于模块名的阶段。
SEED_STAGES: dict[str, tuple[str, str]] = {
    "integration_runs": ("integration", "app.modules.integration.seeds.runs"),
}


def stage_module(stage: str) -> str:
    """阶段的负责模块（决定默认 export 组、报告里的归属）。"""
    return SEED_STAGES[stage][0] if stage in SEED_STAGES else stage


def stage_package(stage: str) -> str:
    return SEED_STAGES[stage][1] if stage in SEED_STAGES else f"app.modules.{stage}.seeds"
