"""seed 模块注册表。顺序 = 加载顺序（被依赖的放前面）。新模块在这里加一行。

- versioning 在 static 之前：版本行只读导出 JSON（快照由各模块登记的 seed_section 按 JSON 组装，不读表），
  静态的 change_request.release_id 等外键要按 ver 查到 ontology_release.id，所以版本行必须先写；
- action、event 在 dynamic 之前：规则、生命周期迁移、执行记录的外键指向 action_type.id / event_type.id；
- admin 最先：sys_user 吸收动态的 PEOPLE（转派候选人），其它模块的人名都对得上用户。
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
    "qa",
]
