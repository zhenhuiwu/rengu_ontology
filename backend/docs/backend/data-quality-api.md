# 我的质量检查：真实定义及手动运行

接口前缀 `/api/data/v1`，功能权限读取实际角色的 `data-quality:view/manage`，预览同时校验有效预览身份的读取权限。检查定义和运行按 `created_by` 的稳定用户 code 隔离，预览读取 `previewAs` 用户的记录，写请求由统一历史/预览只读守卫拒绝。没有演示种子或随机历史。

- `GET /quality-checks/meta` → `{objects:[{key,name,kinds,rules}],automaticScheduling:false}`。对象名称来自 object_type 表，未映射对象 kinds 为空。
- `GET /quality-checks?typeKey=&kind=&result=&page=&size=` → 分页 QCheck + `dataCutoff` + `summary{total,passed,failed,unrun,lastRun}`；summary 针对我的生效未退休检查。初始空库 `items=[]`、各计数为0、dataCutoff为空，不伪报健康。创建后状态“未运行”，history为空，不算通过。失败项优先。
- 定义CRUD全部走CR：`POST /quality-checks` 新建、`PUT /quality-checks/{code}` 编辑，body `{name,typeKey,kind,threshold,schedule,crId?,lockVersion?,title?,reason?}`；`DELETE /quality-checks/{code}`（同义POST `/{code}:retire`）只接变更选项。响应 `{id,crId,lockVersion,status:"草稿"}`。不直接新增/修改/删除生效定义；新建编号由后台分配。加入CR必须传旧lockVersion，且仅能加入当前创建者自己的草稿；已有pending必须继续原CR。错误或冲突保留输入，不能补演示成功。
- 发布hook登记 `quality/def`，严格复用原字段及受控检查映射，校验已发布定义基线锁；审核发布前运行仍用已发布定义。删除发布只写retired_at，不物理删除QualityCheck/QualityRun；退休后禁止执行或编辑，仍可读历史。未发布新检查不能执行。
- 列表行附 `hasPublished,retired,pending?`；pending包含 `{crId,lockVersion,status,isNew,definition,retire}`，定义是实际保存的CR字段，可刷新恢复编辑。现有检查字段保持已发布值，待发布新检查显示草稿字段且无运行结果。summary仅计算生效未退休检查。列表与运行历史SQL COUNT+LIMIT/OFFSET分页，前端full分页默认10，最新运行/连续失败起点只批量读取本页检查。
- `qualityChecks`快照节仅定义、ownerId与退休标记；不复制运行。历史ver按该版本快照与发布时间截止运行查询，缺节的旧历史为空；整版回滚恢复定义及退休状态，保留所有运行。`quality_cr_0011`在event_0010后新增nullable retired_at；downgrade遇已退休定义拒绝丢列。
- `GET /quality-checks/{code}/runs?page=&size=` → 真实运行分页，倒序；`time`检测开始时刻、`dataCutoff`数据库查询快照时刻、`sourceTime`业务源时间（仅新鲜度MAX下单时间）。definition保留当时名称、对象、类型、规则、阈值、调度快照。
- `POST /quality-checks/{code}:run` → `{jobId,completed:true,run}`；`POST /quality-checks:run-all` → `{jobId,completed:true,count,estimate:"已完成"}`。**本轮有界同步执行**，最多100项；不伪称异步排队。jobId写入对应quality_run，可关联同批记录。全部运行事务一致，失败不会留下半批完成提示。
- 调度只存配置，当前没有自动执行器。页面新建固定手动，调度选项禁用，不承诺周期运行。

执行白名单（固定现有 ORM 映射，权限沿订单/门店/商品受控入口；订单行继承订单范围）：

| 对象 | 新鲜度 | 空值率 | 唯一性 | 引用完整性 |
|---|---|---|---|---|
| order | MAX(created_at_biz)距检测时刻小时数 | store_id | code | store_id→store.id |
| order_line | 不可用（无业务时间字段） | product_id | id | product_id→product.id |
| store | 不可用 | big_region | code | 不可用（无大区实体表） |
| product | 不可用 | category | code | 不可用（无品类实体表） |

阈值只解析 `≤ N 小时`、`≤ N%`、`重复 N 条`、`找不到 N 条`（也支持<=），数值非负，百分比不超过100。重复计数=记录数−不同业务键数量；引用检查对物理目标表判断存在性，不将目标权限裁剪误报为孤儿引用。没有可见记录返回“无数据”，不会判为通过。新鲜度未来时间同样不通过。

存储：`quality_check` 使用标准可修改列；`quality_run` 使用标准只追加列并有数据库禁止修改/删除触发器。迁移 `data_quality_0006` 接 `admin_0005`，只建空表，保留开发库已有数据。

总览可复用 `app.modules.data.quality.checks_query(owner_code)` 与 `latest_runs_query(owner_code)`：后者按每check最大run.id取最后真实结果。创建而未运行的定义没有运行行；质量无数据/未运行不能计入通过或健康0。

验证：隔离库 `tests/test_data_quality.py`；前端实际SFC行为 `node scripts/check-quality-integration.mjs`。没有启动worker、外部服务或定时器。


专项回归：`tests/test_quality_cr.py` 验证真实审核/发布CRUD、旧锁、owner/preview/history、退休/整版回滚不删运行、23定义/31运行分页；`tests/test_data_quality.py` 保留受控真实执行与文本边界。前端 `check-quality-integration.mjs` 验证实际SFC的ctx迟到、真实目录、草稿恢复、409输入保留与退休仅CR。
