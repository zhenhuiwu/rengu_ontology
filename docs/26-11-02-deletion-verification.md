# 定义及运行记录删除能力

本轮新增服务与受控删除入口；主会话已用新建合成定义与两条终态运行样本完成限定真实 UI 删除验收，其他既有业务记录未删除。公共问数组件保持冻结；旧命名空间仍按登录、功能管理权限、本体成员和只读历史守卫校验。

## 接口

对象类型与维度/码表已有 `:delete-check`、`:delete`，本轮复用维度原有合同。新增八类统一 `GET /{resource}/{id}:delete-check` 返回工作版 `lockVersion`、定义 `targetVersion` 和实际 `references`；`POST ...:delete` 只接受两个版本，成功返回 `{ver}`。资源为 ontology/v1 下 link-types、action-types、functions、metrics、knowledge、event-types、rules，以及 data/v1 下 ext-systems。冒号路由挂在既有 `/{id}` 读取路由之前，避免被当成普通编号。

定义删除沿现有发布锁、目标锁、人工工作空间编辑权限及 releases/manage，按工作版与行版本同时核对。阻断当前变更单、真实结构依赖、当前规则/动作/事件/函数引用、显式 JSON 身份和实际数据库外键；未知外键/不确定级联拒绝，不删除其他模块对象。409 保留具体来源及 references，冲突必须刷新，不静默覆盖。

无依赖关系、指标、知识和外部系统从当前定义表移除（知识只连同自身 applies 子项）；动作/事件复用已有 retired_release_id，函数仅使用新的内部 deleted 状态并从当前列表总数/详情/版本入口排除，原 disabled 状态不变；规则清空当前 version、关闭触发/自动暂存并排除当前列表与 by-id。FnVersion、RuleVersion 和其他只追加版本不删。新工作版快照明确移除指定定义，其他定义和旧发布快照保留。存在执行、告警或其他引用时拒绝删除，删除运行记录的可见性不清除此类历史审计依赖。

## 动作中心与告警

新增 `DELETE /api/ontology/v1/action-runs/{id}?lockVersion=N` 和 `/alerts/{id}?lockVersion=N`，成功 204。响应增加 lockVersion，详情 can.delete 与 can.reasons.delete 表示当前资格，执行时仍重新核验。

动作仅成功、失败、已撤回、已对账终态可删；排队/已领取外部调用、未解决对账或未完成副作用投递均阻断。告警须已解决/已忽略，且没有关联在途动作或未完调用。行权限仍通过原 scoped_instances；不是人工管理者、非当前工作空间 owner/editor 或 API key/服务账号不能删。告警删除使用原暂存互斥，按 run→alert 锁序，防并发暂存关联动作。

迁移 `dynamic_0050_record_deletion` 只为 action_run/alert 新增 nullable deleted_at/deleted_by，没有默认、回填或历史行删除。主会话已审阅并在完整备份后应用该增量；本子任务未执行迁移。删除为真实持久标记，无恢复入口，不移除执行事实、canonical 指针、外部 call/reconcile、幂等键或只追加日志，另追加删除审计。内部原始幂等/命中查询仍读取 tombstone，旧键不会再次外发。

当前列表、详情、状态计数/分面、对象行为、动作每日统计、概览及动态摘要不返回已删记录；命中读取不再生成已删告警的当前链接。保留原 RuleHit/调度/事件幂等历史，删除显示记录不会清空冷却窗口或重放原事件。历史审计 JSON 和版本快照仍保留原事实，不伪造取消或执行结果。

## 验证边界

一个新增离线 `backend/scripts/check_deletion_boundaries.py` 使用内存替身与 SQL 编译覆盖八类删除模式、双版本冲突、引用阻断、历史保留/当前读取过滤、未知外键、终态/排队/对账/副作用拒绝、删除审计、原键不能重放，以及冒号路由注册顺序。该脚本没有数据库连接、HTTP、队列、真实业务删除、pytest 或迁移测试；不能把离线断言写成全部分支真实通过。

## 本机真实 UI 验收

| 范围 | 主会话已取得的实际结果 |
| --- | --- |
| 九个定义页面 | 外部系统、函数、知识、关系、动作、事件、指标、维度、规则均用合成定义完成真实删除。维度/码表共用页面本轮使用维度样本，独立码表类型未另建样本 |
| 规则保存与删除 | 首次保存发现的 CR 锁缺项已修为同一响应返回并缓存 `crLockVersion`，未放松版本校验。正常 API 与 UI 重载后，R001 `raw1 == 0`、关闭触发可保存并生效，随后删除成功；立即导航函数页保持函数页。[规则确认截图](evidence/2026-11-02-deletion/rule-delete-confirm-2560.png) |
| 动作记录 | 失败样本 `ACT-TEMP-DELETE-20261102` 在执行记录精确 ID 筛选下勾选删除确认，行隐藏；重载、同 ID 筛选仍暂无数据且详情清空。[重载截图](evidence/2026-11-02-deletion/action-deleted-after-reload-2560.png) |
| 告警记录 | 已解决样本 `AL-TEMP-DELETE-20261102` 在已关闭精确 ID 筛选下勾选删除确认，行隐藏；重载、同 ID 筛选仍暂无数据。[重载截图](evidence/2026-11-02-deletion/alert-deleted-after-reload-2560.png) |
| 禁用入口 | 既有待确认动作 `ACT20261028-0006` 的删除按钮实际禁用；未执行删除 |

运行样本只在本体 4 新增两行，标记 `TEMP_DELETE`，关联既有 `RescueIncident / SYNTHETIC-SAR-001`；没有修改业务对象、执行器、call 或队列，也没有执行业务动作。持久 tombstone 与审计保留，不是物理清除。最终统一 typecheck exit 0。

尚未逐项真实 GUI 覆盖独立码表类型、所有引用/队列/claim/待对账/副作用拒绝分支、末页与并发删除组合；部分已由受控离线检查覆盖，边界不混同。没有运行后端 tests/pytest、全量测试、build/package、降级或恢复测试，也没有清理其他既有记录。
