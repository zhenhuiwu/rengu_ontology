# 动作执行接线 · 2026-10-27

本次实现接既有动作中心暂存、确认、审批、重新提交、结果未知和对账流程。真实外部写入默认关闭，没有发送外部生产请求，没有修改现有业务对象记录来演示。

## 执行边界

- `dynamic.ports.get_submitter` 接真实 `DatabaseActionSubmitter`。无外部系统的内部动作在同一请求事务执行受控属性修改、动作状态和内部成功事件；任一步失败整笔回滚。
- 外部动作确认后只原子登记新的 `writeback_call`，`msg=ACTION_DISPATCH_QUEUED/attempt=0`；动作中心显示“结果未知”，原因和时间线明确“已持久排队，尚未调用外部系统”。缺执行配置、白名单、行权限、参数、定义版本时拒绝排队，不回假成功。
- `action.dispatch` 每分钟消费新QUEUED记录。worker先锁动作和call、重新读取真实用户权限、核验版本/参数/目标/路径，提交CLAIMED/attempt=1后才发一次POST。原有seed和历史结果未知记录从不作为POST队列。worker崩溃后CLAIMED不重发，只走既有幂等查询和人工对账。
- 响应成功必须有真实receiptId。非2xx、超时、无效响应都保持结果未知，不猜作失败或成功。外部回执和调用证据独立于本地成功回调保留；本地属性/事件失败时转人工，不丢掉外部成功证据。
- worker已提供真实结论后沿用对账回调，动作显示“已对账 · 外部已成功/外部未执行”。未发出的排队记录不能人工提前标记对账结论。
- 确认、审批、重新提交现在锁住ActionRun；同记录不重复排队。HTTP只发送同一稳定Idempotency-Key，不自动重试写入。依赖外部系统真实实现该幂等协议。
- 历史执行版本与当前已发布动作版本不相同、缺版本或已下线时明确拒绝；不借用当前定义。完整历史版本执行不在本轮支持范围。

## 属性修改

只支持“修改”动作绑定的唯一真实门店或订单对象。属性名必须是已发布静态属性API，且有真实backing_column、受控表映射和硬编码可写白名单；不接受任意表、列、SQL或表达式。对象身份/外键、金额、个人信息和范围列不允许写入。

- 门店白名单物理列：`name/status/is_test`。
- 订单白名单物理列：`status/warehouse/shipped_at/is_test`。
- `field`必须是有界JSON赋值，例 `{"status":{"param":"status"},"warehouse":{"value":"WH-01"}}`。左侧用该对象真实属性API；`param`必须是已声明且提供的实际动作参数。
- 真实数据库类型、长度、可空、枚举、时区日期时间会校验；目标按真实用户行权限过滤并加行锁，重复业务编号拒绝。一次动作的全部属性变更与状态、事件保持同事务，更新对象lock_version。
- 外部成功应用属性还必须有实际写入或幂等查询成功证据及原始授权执行用户；单纯人工填写“外部已成功”不能触发业务属性更新。角色撤权或映射变化导致回调不能应用时保留人工状态。
- 自然语言存量edits不推断赋值；新建、关联、跨对象、通知/Webhook及自定义事件副作用没有执行映射时明确失败。只支持已有ActionExecuted/ActionOutcomeUnknown标准事件，事件投递由事件模块负责。
- 本体定义仍只经CR发布，执行器不改对象定义表。

## 显式外部配置

复用现有 `RENGU_INTEGRATION_REAL_CONNECT=false` 默认值及SecretStr `RENGU_INTEGRATION_ADAPTERS` JSON。只有取得目标环境业务写入授权后才能本地启用。示例仅为格式说明，未写入本地密钥配置：

```dotenv
RENGU_INTEGRATION_REAL_CONNECT=true
RENGU_INTEGRATION_ADAPTERS={"EXT-ERP":{"queryPath":"/ontology/idempotency","probePath":"/health","submitPath":"/ontology/actions","actions":["explicit-approved-action-code"],"token":"local-secret"}}
```

系统必须声明支持幂等且 `idemHeader=Idempotency-Key`，动作编码在actions白名单内。端点取外部系统登记的HTTPS origin；执行路径只取submitPath，禁止跨源路径、凭据URL、重定向和环境代理。参数不允许控制请求地址。配置和token不会输出到日志。

排队call.path保存完整HTTPS origin+路径，受现有255字符上限约束，执行时完整比对；排队期间更换域名或路径会拒绝旧队列请求，避免向新环境误发旧业务。

POST JSON为 `{actionRunId,actionType,typeVersion,idemKey,objKind,objId,params}`；头为 `Idempotency-Key`，可选Bearer鉴权。2xx响应必须为 `{status:"SUCCESS|FAILED|UNKNOWN",receiptId?,reason?,code?}`；SUCCESS须有真实receiptId，不能用HTTP状态码伪造业务回执。幂等GET查询沿用既有契约。

## worker与前端

独立 `python -m app.modules.integration.worker` 和统一jobs入口都注册同名 `action.dispatch/reconcile队列/每分钟`，部署选择一个worker入口，不同时启动重复周期入口。已有 `action.reconcile` 每15分钟仅GET查询，未发出的QUEUED记录不消费查询次数。本次没有新增数据库迁移。

前端 `/use/ops/actions` 右侧审批中动作详情底部新增“审批并执行”，仅后台can.approve允许的真实审批人可见，沿用确认卡。只读历史/预览入口继续禁写。动作类型 `/build/ontology/action-types` 既有“试运行”只校验真实参数与条件，不排队、不改业务对象。

事件订阅公开入口 `app.contracts.actions.stage_event_action(session,user,*,action_type_code,event_id,params,obj_kind=None,obj_id=None)` 返回 `{id,status,idemKey}`，只生成待确认动作，不隐式审批或执行。执行身份须从明确授权的订阅配置resolve_user，不能由事件载荷冒充；同事件+动作幂等，改参数或身份409，目标必须匹配已发布动作。测试模拟应只调用只读checker，不能调用stage入口。

## 检查记录

前端实际检查发现的动作概览固定日期已移除：真实统计接口附带同一次聚合使用的服务器clock截止时间asOf，真实页只展示该返回值；加载或失败时不借用客户端/演示时间。引用规则列表在详情或引用仍加载时显示加载状态，避免先展示“暂无规则使用”。

动作类型只读试运行现在以可手动关闭的结果弹窗展示服务器返回的通过状态、检查项数、全部缺参数/缺上下文原因或请求错误，并在同一动作上下文保留“查看校验结果”入口；不再只显示短暂toast。切换动作、身份或定义视图清除旧结果，没有增加执行参数表单。

只执行了Python AST语法静态解析；留下一份无DB/无网络的类型和配置拒绝条件检查文件 `backend/tests/test_action_runtime_values.py`，按用户要求未运行。没有pytest、后台测试、集成/迁移测试、全量回归或真实外部请求；前端实际操作由协调者核验，本文不宣称已经通过。
