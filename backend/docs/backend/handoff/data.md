# 数据连接当前实现（2026-10-26）

- 真模式列表、详情、库表和测试结果来自数据库/API；前端读失败不改演示模式，写失败不落本地数组，跨身份/查看上下文的迟到结果丢弃。按实际分页读取超过200条的列表及库表。
- 驱动配置：PostgreSQL/金仓 PG 协议使用 `postgresql+asyncpg`，MySQL 使用可选 `mysql+asyncmy`，达梦使用可选 `dm+dmPython`（同步官方驱动在线程执行）。本轮不安装驱动，不连接外部数据库；可选驱动缺失时测试返回 `ok=false` 和“驱动未安装”，内省返回 `INTROSPECT_FAILED`，不回退模拟结果。ERP 仍返回 `REAL_CONNECT_UNAVAILABLE`。达梦线程超时不能取消底层驱动调用，生产部署需配置驱动/网络超时；非 PG 行数暂为0（未提供估算）。
- 不启动同步/通用任务worker，不改用户业务数据。真实连接仍需 `RENGU_DS_REAL_CONNECT=1` 和有效密钥、已保存密码。
- 对象页原“重新采集元数据”调用 `POST /api/data/v1/object-types/{id}/metadata-diff`：从已发布对象的已保存数据源/表映射重新内省，以属性 `map_column`（否则 `source_column`）比较，返回 `{objectId,datasourceId,schema,table,added:[{name,type,nullable,comment}],deleted:[列名]}`。仅显示新增/删除列，不自动写定义、草稿或刷新映射。要求 connections/object-types 的 manage 权限，ver/previewAs 仍403 READONLY；无映射、无连接、读取失败明确报错。mock 按钮行为和数值不变。
- 2026-10-26 增量验证：独立 `rengu_test_data_finish` 本地 PG 库中 `test_datasource_ontology_import.py` + `test_datasources_real.py` 32项通过，含真实加列/删列/删表和重新采集不写定义、只读拒绝、可选驱动缺失无网络测试；`vue-tsc --noEmit`通过。
- 验证：`test_datasources.py`、`test_datasources_real.py`、`test_ds_secret_check.py` 25项通过；`frontend/scripts/check-data-real.mjs` 验证全量分页、上下文、空/错不回mock及写失败。
- API合同详见 `frontend/docs/backend/datasources-api.md`。共享外部系统测试入口另在 integration 模块收口，data/adapters.py 不再是数据源真API的隐式回退。
- 外部系统测试不再按状态伪造 HTTP 200/502 和耗时，也不写虚假 `writeback_call`；缺真实执行器503 `EXT_SYSTEM_TEST_UNAVAILABLE`，停用409保留。前端明确置灰、未保存原始报文显示缺失，不承诺自动对账轮询。既有开发库记录不删除。
