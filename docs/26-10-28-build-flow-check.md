# 2026-10-28 构建区实际流程复验

本轮以用户手册主线逐步使用前端，复用隔离库 `rengu_test_use_qa_20261027` 的 `scope2 / wf-retail-20261027`；没有清库、seed、迁移、模型请求或真实外部投递。旧 scope1、空对照 scope3、前轮问数与页面记录保留。主库仅做只读快照，`auto_ontology` 未访问。

## 环境与实际范围

- 前端 `127.0.0.1:5282` 代理独立 API `127.0.0.1:8001`。先启动 API44352/session94348、前端6296/session60066；经 ROOT 授权在三组源码安全点重载为31268/session27022。管理组最后兼容补丁冻结、双方无在途后再次仅重载本组 API 为34280/session50939。两次启动均明确 `modelEnabled=false, workers=0`，readyz 为 `ok / db ok`。
- 重载前后原模型预算文件 SHA256 一致；TEMP 数据源密钥保留，未清除、重建或展示。启动不执行准备脚本；重载时原39页、4条动作运行仍保留。
- 测试提交人在 `127.0.0.1` 操作；独立审核人使用测试专用 `localhost:5282` cookie。每次审核均核对实际页面身份，两者保持独立会话；不切换使用组的会话。
- 主8000/5280及四个原 worker 均未由本组停止。原约33张截图删除和既有未跟踪文件未恢复、覆盖或清理。

## 按手册主线的实际结果

| 步骤 | 本轮实际操作与结果 | 仍有的边界 |
| --- | --- | --- |
| 工作空间与权限 | UI确认当前隔离验收空间scope2、测试提交人身份、发布版本起始v2026.10.4。 | 未重建工作空间、复制角色或修改成员。 |
| 准备源表 | 复用前轮同隔离库真实零售副本，不重新复制业务记录。 | 未连接新ERP或其它外部源。 |
| 连接与库表 | 实际点“测试连接”，成功47ms；库表显示 public.biz_order 1286行/19字段、store 95行/19字段，均映射正确。 | 测试只经过受控本地只读连接。 |
| 对象定义 | UI核订单PK=id、标题为展示订单号；门店PK=code，两者已发布，来源WF-RETAIL-SOURCE。 | 本轮没有重新导入/覆盖对象定义。 |
| 真实数据关联 | 订单关系页实际显示store_id=store.id、N:1、反向“门店订单”。 | 使用区实例/关联核对由使用组报告，不把定义显示当完整实例验收。 |
| 字典 | UI新增唯一独立码表`uicheck_20261028_order_status`及`UICHECK_ONLY / 隔离流程测试值`，保存CR-0005，共13处改动。 | 独立审核人的测试角色不具备该类审核资格；该单保留草稿，未扩大权限、未假称发布。 |
| 指标 | 原新指标`wf_order_paid_total`显示SUM(order.amount)、order.created_at_biz、默认最近7天；实际7日试算显示10/22–28且数据截至10/25。改为1日实际返回10/28无可见数据，没有补0。 | 主库旧15项业务口径未改，未调用模型。 |
| 查询与隔离 | UI切scope3，事件、字典、变更均为空且本体未发布，不继承scope2新事件/CR5草稿；切回scope2恢复。 | 未扩大为所有跨空间API/调度/密钥分支已验证。 |

规则页实际为空，新建入口先要求对象并进入变更单流程；打开后取消，没有新增规则、调度任务、告警或运行结果。

## 新定义保存、独立审核与发布

1. CR-0006“UICHECK-20261028-独立事件发布复验”：UI新增`uicheck_20261028_store_event`，来源动作、对象store、checked必填布尔、无订阅。实际校验0阻断/1警告/1通过/15不适用（警告为checked无描述）；独立审核人真实审核，测试提交人发布v2026.10.5。CR5未被合并发布。
2. CR-0007：UI创建唯一订单事件`uicheck_order_identity_event_20261028`，来源动作、对象order、checked必填布尔、无订阅；独立审核并发布v2026.10.6，事件自身v1。
3. CR-0008：UI创建`uicheck_order_identity_20261028`（名称与API相同），order/人工/免审，参数、条件、本体编辑、外部系统均空；唯一效果为上面的内生订单事件。发现事件去重窗口缺省问题后，真实撤回已通过审核结果，把本轮订单事件加入原单，UI补“事件ID / 24 小时”，再校验、复审、发布v2026.10.7。共11处改动，新动作自身v1、订单事件自身v2。
4. CR-0009：原受控效果编辑器实际显示目标事件v2，确定后仅将固定版本v1→v2存到草稿（1处改动）；再次真实校验、独立审核人审核、测试提交人发布v2026.10.8。最终测试动作自身v2，唯一效果绑定订单事件v2。两批之间没有执行该动作，没有降低版本检查。

CR8/9的通用校验页实际为1通过、16不适用，未执行项仍按页面保留，不能称所有发布检查均执行通过。窗口合法性另由保存/发布写回边界验证。

## 本轮发现与最小修复

- 审核人候选多而不能搜索：`Releases.vue`原Select增加Ant原生搜索，按label过滤，支持名称或用户标识检索。真实UI搜索找到独立审核人、保存并走完审核；CR5搜索同一审核人仍“暂无数据”，没有绕过审核资格检查。
- 新事件默认空去重窗口可发布，但真实接收必报`EVENT_DEDUP_CONFIG_INVALID`：新建表单初始值改24小时；新建/显式修改窗口时，草稿保存和发布复用原`ingress.dedup_window`合同，拒绝空、0天、无效文字，指出dedupWindow字段。没有批量改写无关旧定义。
- 事件详情没有修改去重设置的入口：增加只含去重键/窗口的草稿表单，复用`useStaticCr`保留其它定义、载荷与订阅。实际UI保存到原CR8、整页刷新恢复；重载后输入“0 天”点保存被拒，0天输入和弹窗保留，已保存24小时不变。恢复24小时后继续正常发布。
- 标准ActionExecuted的“订阅投递”误当业务对象拒绝：只读授权严格识别平台内部完成事件，依照原有页面与动作记录读取权限检查同本体及runId绑定，再复用动作记录原`scoped_instances`行范围。生产外发仍走原业务授权，不因为平台事件放行。隔离READ ONLY真实调用证实有权限的测试提交人可读；无对象行范围、跨工作空间均404。使用组重载后UI复验原事件投递页为真实空态、不再误403。
- 业务事件投递沿旧code匹配而误拒numeric主键：`delivery.py`和`exceptions.py`复用使用组的`identity_rows`，保留公开主键与唯一旧code合同、RLS及歧义拒绝。没有在事件模块复制另一套身份转换。

角色保存原API/id兼容、未授权测试动作的禁用及订单动作PK执行链由管理/使用组负责代码，具体证据见对应报告。

## QA角色精确授权：当前状态

ROOT明确授权仅为既有测试角色追加本轮唯一订单测试动作，不增加读取/导出范围、用户或其它功能。测试profile仅接受当前版本的精确差异，并核最终已发布动作v2只有一个固定内生事件v2、无业务编辑或外联；请求继续经过原产品鉴权、校验、审计与RLS。

真实UI保留原授权，仅勾选新测试动作。首次保存被产品拒绝“动作不属于该对象类型”（不是profile拒绝），角色未保存。根因为既有失效授权妨碍无关的最小变更。本组停止重试，没有删除旧授权或后台代改；管理组补充兼容检查，仅允许原封保留既有失效值，新增未知或跨对象授权仍拒绝。

管理组修复冻结后，本组先恢复页面原值并确认无未保存输入，按ROOT授权只重载8001为34280。随后通过真实UI重新勾选唯一新测试动作，保存成功；整页刷新与正常只读详情请求均证实旧授权保留、新动作持久化，原有功能及数据范围没有扩大。后台的无权限值规范化不增加有效授权。没有直接数据库修改权限；已通知使用组进行两笔订单UI执行，本组不代执行或伪造成功。

## 页面订阅精确处理

使用组从真实历史SQL结果新建P002并保存本人站内订阅、手动刷新后，ROOT授权仅处理本次`scope2/P002`的afterRefresh事件。先只读核唯一待处理topic条目及接收者为测试提交人，再调用原`dispatch_workspace(limit=1)`提交：该条目已dispatched且error为空，新增通知指向P002；未启全局worker或处理其它topic/空间。

首轮辅助脚本沿用test固定时钟，due=0断言失败并回滚；改为与UI服务相同实际时钟后通过。这是辅助核查脚本问题，不是产品缺陷。铃铛查看、跳转及已读状态由使用组实际UI验收。

## 验证与证据

- 本组无DB定点：`python -m pytest --noconftest tests/test_event_completion_visibility.py tests/test_event_dedup_validation.py tests/test_retail_order_permission_profile.py -q --tb=short`：4 passed；覆盖平台读取与manage分离、权限拒绝、新/改窗口、旧窗口不改写、精确单动作差异。
- `node scripts/check-event-dedup-settings.mjs`通过：执行真实保存处理器，仅存变更def标量、不碰payload/subs、失败保留输入、切空间后迟到成功不写回缓存。
- `node scripts/check-platform-event-context.mjs`通过：平台候选有限、陈旧选择清除及新建默认24小时。
- Ruff F/I与`git diff --check`通过。前端typecheck早轮曾因使用组并行中的ObjectSearch类型报错，其修复后已通过；最后完整`npm run build`（包含typecheck）成功，4490模块、Vite阶段19.50秒。
- 未运行全量pytest、迁移往返、付费评测、外部业务投递或全站截图。

关键截图含内部身份及权限信息，以下原始文件仅本地留存，不纳入Git或公开上传：

- CR6真实发布：`01-event-cr6-published.jpg`（仅本地留存）
- 发布载荷合同：`02-published-event-payload.jpg`（仅本地留存）
- 空空间没有变更单：`03-empty-scope3-releases.jpg`（仅本地留存）
- 去重草稿刷新恢复：`04-dedup-draft-restored.jpg`（仅本地留存）
- 0天窗口保存被拒：`05-zero-day-rejected.jpg`（仅本地留存）
- CR9真实发布：`06-cr9-published.jpg`（仅本地留存）
- 角色首次保存实际阻断：`07-role-save-rejected.jpg`（仅本地留存）
- 测试角色新增动作真实保存成功：`08-role-grant-saved.jpg`（仅本地留存）
- 测试角色整页刷新仍保留：`09-role-grant-restored.jpg`（仅本地留存）

## 主环境只读快照与保留边界

`backend/testlogs/build-flow-main-before-20261028.json`记录主库`rengu_ontology`的READ ONLY一致快照：schema dynamic_0030，16表数量与上轮一致（空间2、对象18、属性211、关系27、指标15、订单1286、明细3905、门店95、商品12、动作1991、事件1243、告警154、CR19、通知0、页面33、APIkey0）。未分发outbox0、真实queued/claimed写回0、event_delivery0、对账仅74manual无querying、作业仅succeeded，无todo/doing；11条executing全部为scope1历史seed展示记录，未更改。

原主API32704/launcher34368；integration16764/15084、event23072/49524、data27704/24732、dynamic31944/18620。此为只读快照和进程归属，不代表已重启；主服务重载须由ROOT在最终冻结安全点统一处理。

本轮新增/修改数据仅隔离QA：CR5草稿、CR6–9发布定义、连接测试元数据、P002通知处理、既有QA角色的一项订单测试动作授权；旧记录和业务源均保留。ERP/Kafka/预测模型继续后置，外部投递没有被本地状态代替。

## 最后实际结果与主服务加载

使用组在最终v2026.10.8和QA角色授权后，通过真实UI对同展示订单号下的184867、185056分别完成校验、暂存、本人确认：ACT-20261028-0002/0003成功、INTERNAL、动作v2；两条自定义事件分别保存各自numeric对象ID、checked=true，标准事件另各一条，均pending/订阅方0，WritebackCall=0。使用组独立只读核对95门店/1286订单全部行SHA前后一致；这部分执行与源数据证据来自使用组，本组没有代执行、后台造成功或把pending写成已投递。

ROOT确认全部产品源码冻结、使用组验收tab已关闭及无在途后，授权将修复安全加载到主项目。01:48及最终停止前再次以READ ONLY核主库：真实queued/claimed写回、processing投递、querying对账、未分发outbox、todo/doing作业、非seed executing均0；规则/质量调度游标0、页面next_run非空0、启用站内订阅0。实际配置integration_real_connect=false、event_delivery_http_enabled=false，不启模型评测或额外外呼。

精确核对原进程完整命令及父子归属后，仅停止原主API与四worker的runtime/launcher对并各启动一次；5280始终保留。新主进程如下：

| 服务 | runtime / launcher | 受管session |
| --- | --- | --- |
| API8000 | 51484 / 40488 | 66123 |
| integration | 37152 / 25548 | 17418 |
| event | 20044 / 35724 | 3988 |
| data | 21372 / 30868 | 18362 |
| dynamic | 37292 / 5816 | 18189 |

healthz返回ok、readyz返回ok/db ok，五个runtime均存活；主前端5280仍为29132。01:51后置READ ONLY快照确认上列16张主表全部数量不变、schema仍dynamic_0030，全部真实在途与队列仍0。只读快照位于backend/testlogs/build-flow-main-deploy-before/after-20261028.json，属于本机诊断日志，不纳入产品提交。

主日志已重定向至`%TEMP%/rengu-ontology/{main,integration,event,data,dynamic}-build-flow-20261028.{stdout,stderr}.log`。本次检查时10个文件均仍为0字节，因此只能确认没有可见错误，**不能据此声称已审阅完整启动日志或全部日志正常**；未为填充日志造请求或重复重启。

使用组确认其真实验收tab已关闭；本组尝试清理自己临时tab时浏览器绑定已不可用，未接管用户主tab或外站。随后精确核对并关闭自建QA API34280/session50939与QA前端6296/session60066。最终8001/5282无监听，主8000/5280保留；QA库、TEMP数据源密钥与原预算账本均保留，账本及密钥SHA256仍与启动前一致。没有迁移、seed、Git提交或推送。

## 本组文件清单

- 产品前端：`frontend/src/pages/ontology/{Releases,EventTypes}.vue`、`parts/NewActionEventForm.vue`。
- 产品后端：`backend/app/modules/event/{dedup_validation,writeback,platform_context,delivery,exceptions}.py`，以及`backend/app/modules/static/cr_write.py`现有事件保存分支。
- 验收profile：`backend/scripts/retail_workflow_ui_server.py`，精确单角色单动作白名单，不改变产品鉴权。
- 轻量检查：`frontend/scripts/check-event-dedup-settings.mjs`及既有`check-platform-event-context.mjs`；`backend/tests/test_event_completion_visibility.py`、`test_event_dedup_validation.py`、`test_retail_order_permission_profile.py`；既有`test_event_platform_publish.py`与`test_action_event_writeback.py`仅补新窗口合同的输入。
- 本报告可纳入提交；只读主快照JSON及上述9张本轮截图仅本地保留，不纳入Git。未提交、未推送、未清理历史附件。
