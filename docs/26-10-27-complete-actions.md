# 完整动作能力 · 2026-10-27

## 边界、存储与接口

动作定义仍使用原 edits/effects 段，经原变更单审核发布；不执行自然语言编辑。新增受控 JSON 映射、真实已发布对象/关系/函数版本候选和表单入口，不创建业务表，不执行旧 seed，不修改历史动作。

复用 `outbox_event` 保存通知/Webhook 副作用，topic 为 `action.effect`，无需数据库迁移。请求内只持久登记；worker 提交一次 claim 后才发送，超时或中断保持未知，不自动再次发送。外部配置复用 ignored 环境中的 `RENGU_INTEGRATION_REAL_CONNECT` 与 SecretStr `RENGU_INTEGRATION_ADAPTERS`，默认关闭。

新增 `GET /action-execution-options` 仅返回真实已发布受控对象、属性、关系、不可变函数版本和事件版本；不返回凭据。只读试运行复用执行预检，检验映射/授权/函数预算，不执行写入、事件或外部调用。

属性修改继续使用既有物理列白名单。显式跨对象目标只可取动作已声明的对应对象引用参数，每个目标独立检查唯一性、对象动作授权及行权限。关联仅可使用已发布关系与实际登记 FK 精确一致的一跳映射，不执行关系 join_sql。新建从有权模板复制安全必需字段与原范围，生成独立新身份；不从说明或缺失字段造值。当前受控表中门店可按此方式创建；订单因必填金额/外键缺少安全创建映射明确拒绝，商品没有可写白名单，其他对象没有本地受控绑定。

本地业务绑定复用对象浏览的唯一 `GovernedTable` 注册和已发布属性 backing_column。`ObjectType.source_table` 是真实上游系统表名（如 `ORG.T_STORE`、`ERP.T_SALE_ORDER`），不拿它当本地表名执行，也不接受用户传表名。

函数值源必须指定真实函数编号和 FnVersion，始终通过既有安全 AST 和预算执行；输入仅来自显式参数/JSON值，结果可用作属性或事件/外部载荷字段。没有 exec/import/网络能力，不用 current 替代缺失版本。

参数的 `def` 只有显式 `{function,version,inputs}` JSON 才用于函数计算，说明文本不作为实际默认值。试运行/提交/执行共用计算器，执行记录保存实际计算参数，人工不能覆盖结果。函数输入/输出按快照源码的受控JSON类型检查；参数编辑提供计算函数、固定版本及每个输入的取值控件。

自定义事件映射固定已发布版本并复用真实 Ingress 校验/版本快照/去重，保存成功仅表示接收，订阅交付由事件模块负责。

## 映射示例与授权

属性赋值沿用旧的 `{"status":{"param":"targetStatus"}}`；显式跨对象使用 `{"target":{"param":"otherOrder"},"values":{"status":{"param":"targetStatus"}}}`。`otherOrder` 必须是声明为订单对象引用的真实参数。原同目标引用必须等于动作目标的守卫继续保留，只有明确用作编辑目标的参数才进入独立逐对象授权路径。

关联的 `obj` 选择真实关系编号，`field` 为 `{"from":{"context":"id"},"to":{"param":"store"}}`。当前真实候选只有 `order_belongs_to_store`，对应订单 `store_id` 到门店 `id` 的实际数据库 FK。关联前后均校验行范围；同一事务内任一业务写入或事件登记失败整笔回滚。角色目录只增加跨对象动作授权候选，不会授予任何角色。

通知/Webhook的 `target` 为已配置 `系统编号:目标键`，`note` 为 `{"payload":{"message":{"param":"reason"}}}`；只支持显式成功后/结果未知时。配置在原适配器中添加 `effectPaths`（例如 `{"notice":"/ontology/notifications"}`），仍要求该系统启用、真实连接总开关开启、动作在 `actions` 白名单且系统支持 `Idempotency-Key`。凭据只来自原 SecretStr 环境配置，不存入定义或outbox。

自定义事件的 `target` 选择事件编号，`note` 为 `{"version":"v1","object":{"context":"id"},"payload":{"orderId":{"param":"order"}}}`；必须与该真实事件的对象类型、必填载荷和当前固定发布版本一致，不能拿标准动作事件冒充其他对象事件。

值源也可为 `{"function":"FN-xxx","version":"v1.0.0","inputs":{"value":{"param":"amount"}}}`。当前真实函数目录为0，因此页面诚实展示空候选，没有为演示新建函数或业务对象。

外部outbox的 `payload.state` 区分queued/unknown/succeeded/failed/rejected。worker提交 `attempts=1` 和unknown后才POST；只有明确业务SUCCESS及真实receiptId记succeeded，HTTP 2xx本身不算交付。CLAIM后中断、超时或无法确认的响应保持unknown，不自动重发；没有新增公开载荷接口。

## 前端入口

`/build/ontology/action-types` 进入既有变更单草稿，在“写回与副作用”选择“新增编辑/编辑”或“新增副作用/编辑”。对象、关系、属性、对象参数、函数/版本和事件/版本来自真实目录；映射错误显示在弹窗内并保留输入。参数编辑可切换“函数计算”；切换动作、身份、历史版本或草稿上下文会关闭旧编辑器。所有改动继续通过原保存草稿/审核/发布链，不直接修改已发布定义。

CR发布写回对新映射执行JSON边界、受控对象/属性、真实关系、固定函数版本/输入、自定义事件版本/载荷字段校验；旧历史记录不重写。保存后的CR试运行会叠加edits/effects，与参数/条件一起真实预检。

## 核验

按用户要求不运行后台测试、pytest、集成或迁移测试、长回归，不发送真实生产通知/Webhook。新增独立小检查文件 `backend/tests/test_complete_action_mappings.py`，按要求未运行。已做12个Python文件AST语法检查和前端 `vue-tsc --noEmit`；只读HTTP确认真实options返回订单/门店、唯一下单门店关系、函数目录0。前端实际填写/取消/错误反馈由协调者统一核验；没有执行新增、关联、业务修改或真实副作用来验证。
