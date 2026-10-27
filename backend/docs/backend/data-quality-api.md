# 我的质量检查：真实定义、手动运行与发布后调度

接口前缀 `/api/data/v1`，功能权限读取实际角色的 `data-quality:view/manage`，预览同时校验有效预览身份的读取权限。检查定义和运行按 `created_by` 的稳定用户 code 隔离，预览读取 `previewAs` 用户的记录，写请求由统一历史/预览只读守卫拒绝。没有演示种子或随机历史。

- `GET /quality-checks/meta` → `{objects:[{key,name,kinds,rules}],automaticScheduling:true,schedulingTimezone}`。对象名称来自当前本体的 object_type 表，未映射对象 kinds 为空；现有真实业务绑定仅本体1可用，不借用其他本体的数据。
- `GET /quality-checks?typeKey=&kind=&result=&page=&size=` → 分页 QCheck + `dataCutoff` + `summary{total,passed,failed,unrun,lastRun}`；summary 针对我的生效未退休检查。初始空库 `items=[]`、各计数为0、dataCutoff为空，不伪报健康。创建后状态“未运行”，history为空，不算通过。失败项优先。
- 定义CRUD全部走CR：`POST /quality-checks` 新建、`PUT /quality-checks/{code}` 编辑，body `{name,typeKey,kind,threshold,schedule,crId?,lockVersion?,title?,reason?}`；`DELETE /quality-checks/{code}`（同义POST `/{code}:retire`）只接变更选项。响应 `{id,crId,lockVersion,status:"草稿"}`。不直接新增/修改/删除生效定义；新建编号由后台分配。加入CR必须传旧lockVersion，且仅能加入当前创建者自己的草稿；已有pending必须继续原CR。错误或冲突保留输入，不能补演示成功。
- 发布hook登记 `quality/def`，严格复用原字段及受控检查映射，校验已发布定义基线锁；审核发布前运行仍用已发布定义。删除发布只写retired_at，不物理删除QualityCheck/QualityRun；退休后禁止执行或编辑，仍可读历史。未发布新检查不能执行。
- 列表行附 `hasPublished,retired,pending?`；pending包含 `{crId,lockVersion,status,isNew,definition,retire}`，定义是实际保存的CR字段，可刷新恢复编辑。现有检查字段保持已发布值，待发布新检查显示草稿字段且无运行结果。summary仅计算生效未退休检查。列表与运行历史SQL COUNT+LIMIT/OFFSET分页，前端full分页默认10，最新运行/连续失败起点只批量读取本页检查。
- `qualityChecks`快照节仅定义、ownerId与退休标记；不复制运行。历史ver按该版本快照与发布时间截止运行查询，缺节的旧历史为空；整版回滚恢复定义及退休状态，保留所有运行。`quality_cr_0011`在event_0010后新增nullable retired_at；downgrade遇已退休定义拒绝丢列。
- `GET /quality-checks/{code}/runs?page=&size=` → 真实运行分页，倒序；`time`检测开始时刻、`dataCutoff`数据库查询快照时刻、`sourceTime`业务源时间（仅新鲜度MAX下单时间）。definition保留当时名称、对象、类型、规则、阈值、调度快照。
- `POST /quality-checks/{code}:run` → `{jobId,completed:true,run}`；`POST /quality-checks:run-all` → `{jobId,completed:true,count,estimate:"已完成"}`。**本轮有界同步执行**，最多100项；不伪称异步排队。jobId写入对应quality_run，可关联同批记录。全部运行事务一致，失败不会留下半批完成提示。
- 调度支持“手动”“每 2 小时”“每天 23:30”“每周一 06:00”，按业务时区计算。只有本次显式整版发布的定时定义才建立计划；旧定义即使已有定时文字也不会自动启用。列表返回 `scheduleStatus,nextRun,scheduleError`；定时执行成功写入的真实运行附 `trigger:"scheduled",scheduledAt`。切回手动、退休会移除计划；整版回滚清除当前计划，恢复的历史定时定义需重新审核发布后启用。

## 定时执行边界（2026-10-27）

`quality_0020` 接 `admin_0019`，只新增空的 `quality_schedule_cursor` 表，含本体RLS、同本体检查引用校验、唯一游标及最小运行角色授权。游标与检查定义分表，推进运行时点不会改变定义的乐观锁，避免影响已保存的CR基线。现有定义和运行不复制、不删除。

部署迁移后由专用 `python -m app.modules.data.worker` 消费 `quality` 队列，每分钟扫描各本体独立Session中的到期游标，每空间每轮最多100项。实际执行身份始终是发布检查的真实创建者；重新读取其成员关系、owner/editor权限、data-quality:manage和对象数据权限，并核对发布时权限指纹、检查版本及当前发布快照。没有隐式以本体owner或管理员代跑。权限或定义变化会暂停并显示错误，重新发布才恢复。

到期游标在既有发布锁及行锁中执行本地受控检查，结果和下次时点同事务提交；幂等jobId含本体、检查、定义版本和计划时点。停机错过的时段仅合并为最近一次对当前记录的检查，不还原或生成历史结果。数据库执行设置15秒限制，失败不写虚假通过/失败记录，只保留调度错误并在下个时段重试；其他不可执行错误暂停。单项或单空间异常不会阻塞后续空间。不会连接外部源、发送通知或启用ERP/Kafka。

本轮只做AST、Ruff及前端类型检查；没有运行后台测试、pytest、调度自检、迁移测试、集成回归或实际检查任务。`scripts/check_quality_schedule.py` 留作明确允许时的纯时间/权限边界自检，当前未执行。迁移应用、worker启动及实际页面读取由主任务单独记录，不能据代码存在宣称周期检查已实测通过。

### 本轮实际部署与页面证据

- 主任务先备份 `rengu_ontology` 至 `%TEMP%\rengu-ontology\before-quality-0020-a1b396fa.dump`（1,732,712字节），随后增量应用 `quality_0020` 并启动质量worker（launcher 43492）；未清理已有记录。启动日志只有已知的Windows信号通知，未观察到异常；启动成功不等于检查任务已实际执行。
- 实际空间2质量页为空，页面仅显示一处“暂无数据”，“全部运行”禁用。空间1当前用户的“我的检查”同样为0，本轮没有看到既有检查项或运行历史，不据此推断其他用户的数据数量。
- 空间1的新建检查表单加载真实订单候选；调度可展开“手动”“每 2 小时”“每天 23:30”“每周一 06:00”，主任务实际选择“每天 23:30”成功。取消时触发未保存保护，随后放弃修改。没有保存CR、审核发布或执行检查，没有生成质量结果。
- 页面截图：`frontend/shots/admin-build-20261027/quality-schedule-form.png`。本轮没有运行后台测试或调度自检，也没有验证真实周期触发、重启补偿、权限变化暂停或重复任务消费；这些仍属于未实测边界。

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
