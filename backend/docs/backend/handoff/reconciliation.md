# 事件模拟与持久对账

`POST /api/ontology/v1/event-types/{id}:simulate` 已接真实定义与草稿载荷/订阅行。body仍为`{payload?}`，默认按sample解析数值/布尔并校验类型。仅执行受限过滤表达式，无法解释的条件返回`matched:null`与原因，不算匹配。`testEventId:null`、`deliveredTo:[]`、`payloadValid/checks/message`明确表示未创建事件、未投递。`ver/previewAs`统一403 READONLY；未保存前端草稿须先保存。没有新增事件测试通道或假测试ID。

迁移`integration_0013`接`static_0012`，新增`reconcile_task`。现有结果未知记录保持人工处理，不自动改变历史业务结果；新未知记录由真实动态事件发射器原子建任务，worker也会发现重启后遗漏记录。seed始终`manual/tries=3/next_at=null`；不支持幂等查询的系统始终manual。

既有`action.reconcile`任务、`reconcile`队列与`*/15 * * * *`周期已接真实实现。部署先升级Alembic，再运行：

```powershell
uv run python -m app.modules.integration.worker --install-schema
uv run python -m app.modules.integration.worker
```

独立worker仅注册对账业务任务，与既有统一jobs入口使用相同任务名、队列及periodic_id；部署时选择一个入口。worker每轮最多处理200条，后续周期继续处理积压；每次查询仅GET，从不重发业务写入。每条执行记录先锁ActionRun，再写task，防止重复worker和人工对账竞态。

每轮最多3次真实查询（查无也计次），间隔15分钟；未配置适配器不计查询、不伪装查无，任务转manual并保存`RECONCILE_UNAVAILABLE`。实际HTTP失败/超时或响应结构无效记录真实查询失败；3次仍无结论转人工。配置修复后，动作中心“仍无法确认”开启下一轮。此操作缺配置时明确503，轮次和计数保持原值。

查询有结论时，经`DynamicReconciliationListener`在同一事务更新执行记录为“已对账”；相同回调可重入，不生成第二条事件。成功回调通过已有入站校验持久化`pending` ActionExecuted事件，事件定义缺失/载荷无效则回调回滚，task转manual记录原因。pending只代表待投递；订阅方投递执行器仍未配置。人工完成对账同事务将task收敛resolved。

真实外部调用没有默认实现或默认成功。只有获得外部环境授权后，在被忽略的`backend/.env`中配置（示例中的token是占位符）：

```dotenv
RENGU_INTEGRATION_REAL_CONNECT=true
RENGU_INTEGRATION_ADAPTERS={"EXT-ERP":{"queryPath":"/ontology/idempotency","probePath":"/health","token":"replace-in-local-env"}}
```

系统地址来自`ext_system.endpoint`；只允许HTTPS同源绝对路径，不跟随重定向、不使用系统代理，配置token仅通过Bearer头发送。queryPath使用GET并传`idemKey`参数；外部响应必须为`{"extStatus":"查到·成功|查到·未执行|查无","extRef":"可选真实回执"}`，HTTP 404不能推断为查无。查询配置只对声明支持幂等的系统生效。probePath为明确配置的只读GET探测，200系列才表示连通；不代表业务写回执行成功。

外部系统页测试调用已接真实探测并落writeback_call；未启用/未配probePath返回503 EXT_SYSTEM_TEST_UNAVAILABLE且不创建记录。已发出的成功或失败探测均保存真实状态/耗时；页面按结果显示成功或失败。mock模式不发请求。

2026-10-27增量：动作执行已接持久QUEUED队列和独立action.dispatch任务，只消费新明确排队记录；实际HTTPS写入仍需显式配置及业务授权。受控JSON属性修改已实现，要求真实映射/白名单/行范围/版本/锁和外部成功证据；自然语言edits及无映射副作用继续明确拒绝。执行版本与当前定义不一致时不借当前定义补发。订阅投递已新增独立event-delivery worker及event_0014快照/记录，旧seed不回填投递。配置和限制详见仓库docs中的2026-10-27动作/事件运行说明。本轮未运行后台测试或真实外部写入。
