"""跨模块接口契约（团队已对齐，改动需要相关负责人一起评审）。

- versioning.py：静态本体 → 版本层（本体小白）。发布时生成整版快照。
- actions.py：动态本体 ↔ 动作执行/对账（Palantir专家）。
- static.py：静态本体 → 基础层（行级范围取值映射 CATEGORY_SCOPE_ALIAS，静态本体负责人）。

只放类型和 Protocol，不放实现；实现方在自己的模块里提供，并在 tests 里做 isinstance 契约检查。
"""
