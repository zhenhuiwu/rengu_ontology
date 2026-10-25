"""模块 integration —— 占位。负责人：Palantir专家。

范围：外部系统（ext_system）、回写调用记录（writeback_call）、对账（reconcile）。
表名沿用 writeback_call / ext_system（团队约定，不加前缀；不要新建 ext_call）。
测试连接走 app/modules/data/adapters.py 的 ConnectionTester（dev/test 用 mock）
表名：writeback_call、ext_system、reconcile_task（不加前缀）
接口：只在 /api/data/v1 下（/ext-systems、/writeback-calls），不在 /api/ontology/v1 下开路由
接口前缀：/api/data/v1（外部系统属于 data 分组，在 app/api.py 挂载）
照 app/modules/versioning 的结构填充；规范见 docs/backend/CONVENTIONS.md。
"""
