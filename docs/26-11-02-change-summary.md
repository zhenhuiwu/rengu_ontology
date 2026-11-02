# 11月1–2日更改记录与部署索引

本记录面向接手部署的AI与维护者，汇总本轮新增修复及已取得的限定真实业务证据。工作区还包含此前积累的产品、迁移与文档改动；**提交包含的文件范围不等于本轮全部重新实测通过**。精确证据状态见[验证矩阵](26-11-01-verification-matrix.md)，分组文件边界见[提交准备记录](26-11-02-commit-review.md)。

## 本轮修复与结果

| 范围 | 根因与最小修改 | 已验证结果及限制 |
| --- | --- | --- |
| API密钥确认与秘密表面 | 生成确认前冻结form，确认后拒绝表单/上下文变化、抽屉关闭或busy；生成/轮换模板只显示mask，原secret仅供正规Copy；真实scope动态摘要、剩天按实际剩余时长计算，配额常驻单位label | 同一新key在ws16新建，meta+objects实际读取→去objects后meta200/objects403→轮换旧401/新200→跨w4 403→吊销后401。正规Copy仅存本轮获批私有文件，结束后两文件删除且clipboard清；原key未动，新key行保留已吊销 |
| 密钥版本/确认层级 | 维护传当前version，保留行锁与收窄合同；公共Confirm显式高于drawer，dangerLabel精确描述轮换/吊销影响，保留ack和最终确认 | 两普通页面旧version保存真实冲突拒绝，新备注未覆盖；修复后吊销checkbox一次成功且底层详情仍开。实际2/min、100/day保留，未扩大到口述误读的2/s；自然到期/其它网络IP/全部quota与高负载未验 |
| 规则一跳关系与固定函数 | 当前关系绑定原先误要求可写/物理列口径，补充source_column回退；采用真正只读FK关系source，不把只读关联改成可写；布尔true/false沿正式有限解释器与序列化合同 | 自身ID+关联设备状态+FN002固定版本正例37已求值/1命中/0无法求值，反例37/0/0；v4恢复正常条件、published，trigger仍disabled。证明试跑，不宣称自动告警/自动动作链 |
| 函数真实引用 | FnUse无正常定义写入，实际动作JSON/规则AST引用未进显示与签名保护；共享当前定义引用读取保守合并旧FnUse，展示/签名guard同用，无DB回填 | PID49000加载后FN001显示动作引用1，FN002显示规则引用1；固定旧版本不被重绑。签名不兼容阻断保留并有离线断言，未做破坏式真实保存 |
| 办公本机外部联调 | 精确scope16/EXT-N01的local_probe许可；新旧CA同subject引发证书选错，改办公CA唯一issuer并保留旧bundle与TLS验证；正式UI保存系统目录与动作授权 | EXT-N01两个接口：GET office_health及POST office_receipt；动作recordExternalInspection v1使用目录八字段mapping，本体编辑/副作用零条。实际health200；唯一call1539 HTTPS200/attempt1/真实回执，同幂等键查询同回执，重复dispatch后postCount仍1 |
| QA外部确认与恢复 | 保留结构化参数/原run严格匹配；QA确认是唯一确认入口，已确认run不再去动作中心确认；手选会话同步URL | ACT-20261102-0001问数确认后经精确受控派发，回调对账；整页刷新同run已对账且无确认按钮。没有启动全局worker或消费旧队列；本机接收器不是生产第三方 |
| 助手上下文与版本 | 采用草稿交接/空发布基线/保存同步checkpoint；当前函数编辑恢复重新GET并核id/version/lock；请求序号与scope保护覆盖迟到响应 | 普通五轮/编辑刷新及desc-only保存通过；B2先clear后模型完成，旧结果不复活且reload保新状态；C先跨页后响应，目的桶与返回函数不混入迟到结果。短前置GET/丢弃确认窗口仍只有离线断言 |
| 旧审核展示与最终类型修正 | 旧状态值保留、显示历史待处理；新QA确认/共享CR错误不再指引审批，旧历史reviewer/来源和后端guards不删。ApiKeys共享ref异步窄化与PayloadSchemaEditor数组索引类型限定修正 | 真发布页顶部正式发布可用、保存即时生效、无审核操作按钮；新外部确认旧句已消失并执行。类型修正不重签密钥、不改变schema增删行为 |

关联代码与逐阶段失败记录在专项报告中保留，不把修复前失败删成“一直成功”：

- [密钥生命周期/旧审核UI](26-11-01-key-lifecycle-business-check.md)：字段、scope/IP/版本、轮换吊销、并发冲突、层级与最后两行错误提示。
- [规则/外部业务](26-11-01-rule-external-business-check.md)：FK只读绑定、布尔、正反例、TLS失败根因、正式UI目录、精确受控dispatch/query与回执。
- [函数引用](26-11-02-function-reference-fix.md)：FnUse缺项、当前定义读取、空间约束、签名保护及真实UI。
- [助手异步业务](26-11-01-assistant-async-business-check.md)、[助手与页面深验](26-11-01-deep-assistant-verification.md)：B2/C时序与尚未覆盖窗口；空基线、函数保存/恢复及P001补充JOIN。
- [设备深验](26-11-01-deep-device-verification.md)、[动作深验](26-11-01-deep-action-verification.md)：25设备/37工单/15补充、PG目录139→3、列级UPDATE、三表查询/图表/页面、一次本地status写入及布局。

## 接手升级时必须区分的内容

1. **代码与数据库升级分开核对。** 此次函数真实引用修复不需回填FnUse或新迁移；工作区此前累计变更含迁移。先核对目标库实际revision、备份与依赖，按既有[部署升级说明](26-10-31-deployment-integration-fixes.md)、[迁移操作单](26-10-31-mirror-database-migration.md)、[保留数据操作记录](26-10-31-deployment-integration-operations.md)完成必要前置，不拿开发机dump覆盖、不reset/seed，不因为本轮本机成功跳过目标端独立升级。
2. **正常加载同一版本前后端。** 最后正常API-only加载时间为2026-11-02 10:04:01，listener PID52800，包含第三轮历史版本参数可见性修复；此前09:50:43/PID67836及00:48:02/PID49000是阶段记录。这只是该机器的运行证据，PID不是另一机配置。root最后统一typecheck与backend/frontend/docs diff检查通过，不能把HMR/编译当部署端已加载。真实页面需目标端正常刷新核对。
3. **私有配置不随提交复制。** `.env`、Bearer/HMAC、key-original/key-rotated、私有接收器配置/回执台账、证书私钥及本机临时目录不提交。另一机须按明确授权重新配置自己的系统地址、秘密、证书信任与精确作用域；不要复制报告中的合成回执、PID或临时绝对路径作正式配置。
4. **办公通道精确授权，不扩大默认能力。** 本轮只为scope16/EXT-N01追加本机office路径、局部probe及唯一CA；保留旧值、默认拒绝和证书验证。`local_only/local_probe`不是任意URL放行、不是关闭TLS，也不是生产第三方合同；缺实际生产URL/auth/签名/幂等语义时不能声称生产已接通。
5. **执行不得重复确认或扫旧队列。** 本轮QA已对同一run明确确认，再由精确call身份预检后仅派发call1539；同run在中心只读查看。不要在动作中心二次confirm，不运行通用worker/sweep补证，不重开已解决任务伪造查询记录，不批量重放旧call。
6. **历史资料/数据保留。** 37个旧docs删除不是已证明的归档，不纳本轮提交；保留其工作区状态，不顺手restore或删记录。既有key、旧run、旧审批来源与FnUse账本保守保留，文案清理不授权数据清理。

## 先前累计变更索引与真实验收边界

- [18条部署联调问题处理](26-10-31-deployment-integration-fixes.md)：镜像业务身份、受控映射、手动同步续批/取消恢复、删除对齐、外部动作诊断、事件有界重试、角色PATCH与部署入口等；本轮没有逐条重新运行全部场景。
- [镜像真实验收](26-10-31-mirror-live-verification.md)、[达梦总体验收](26-10-31-dm-live-verification.md)：原业务源只读与独立合成源101行、CRUD/漂移/稳定签名；这些是10月31日限定证据，不写成11月2日又全跑一次。
- [CDC真实验收](26-10-31-cdc-live-verification.md)、[CDC部署AI指引](26-10-31-cdc-deployment-ai-guide.md)：事务触发器日志、初扫恢复、迟提交/回滚、精确ACK及接管。当前态重读不提供跨多行/多表PG原子可见性、所有中间版本或任意管理员日志篡改识别。
- [综合复检处理](26-10-31-recheck-resolution.md)：错误明细、重试、规则元数据definition、种子隔离和密钥维护；definition路由与权限源码已实现，不因本轮key普通元数据200就宣布规则definition全部scope/RLS验收。
- [重启后验证](26-11-01-restarted-verification.md)、[性能功能检查](26-11-01-performance-business-check.md)：限定功能加载与读取证据；不是SQL数量、吞吐或延迟提升证明。

## 检查、提交与仍未覆盖

本轮新增非平凡修复各有针对性离线断言，实际业务按专项报告逐项记录。root最后统一 **`npm run typecheck`通过、`git diff --check`通过**；没有运行后端全量测试、pytest、集成/迁移回归、全量build、package或benchmark。不是以检查数量充当完成率。

仍未覆盖生产第三方、Chrome102实机、军事自动求值、周期队列真实崩溃/长压/旧积压处置、长期到期/所有IP及quota窗口/高负载、全部权限组合与其它模块业务；第三轮已补恢复与公平选择代码，仅有下节离线证据。用户授权分组提交推送不改变这些事实。提交按[分组准备](26-11-02-commit-review.md)的最终manifest完整纳入共享依赖，排除私密材料、生成日志/结果、非批准截图及37个旧文档删除；本记录本身不执行暂存、提交、推送或发布。

前阶段记录完成后曾冻结，随后用户新增第三轮14项并授权必要修复与限定办公FAILED验收，收口事实见下节；不把前一阶段冻结声明当成后续未修改或未验收。

## 第三轮14项与最后FAILED闭环

逐项状态见[第三轮14项对照](26-11-02-third-round-summary.md)，专项来源为[问数/发布/旧事件review](26-11-02-third-round-qa-release-events-review.md)、[动作/规则报告末节](26-11-01-rule-external-business-check.md)和[周期恢复/公平选择](26-11-02-worker-recovery-fairness.md)。不能把下面限定实际结果扩展为14项全部真实通过。

- **#2/#10的限定FAILED主链真实通过。** 新ACT-20261102-0002/call1540在scope16按正常QA确认，recordExternalInspection v2、OfficeDevice16002、score90、八字段及reason/code映射经精确预检后一次HTTPS200/attempt1，业务FAILED。run保留“已对账·外部未执行”、完整拒绝原因、OFFICE_SYNTHETIC_REJECTED与真实回执OFFICE-c322a4c7372c5ae98ec7，unknown_reason=null；同idem GET查到·未执行同回执，重复同call台账postCount仍1。QA同卡与整页reload均有完整原因/回执、无未知说明或确认按钮：[真实FAILED恢复](evidence/qa-external-failed-reconciled-2k-20261102.jpg)。此问数样本无关联告警，#11及告警失败联动不由它证明。
- **#4正向明确目标与trace真实通过，冲突仍离线。** S0005-T001的modelProposal/requested/decisionChoice都是recordExternalInspection、OfficeDevice16002、score90，decisionTarget同16002；公开受控PK/标签、歧义和参数类型冲突/模型前旁路有针对性离线断言，未制造GUI冲突样本。
- **同轮历史读取回归真实修复。** 升到动作v2曾使旧v1成功run按当前定义隐藏参数；现按同scope固定历史(type,version)一批读取，当前权限和执行版本拒绝guard保留。单离线历史可见性脚本通过，root回S0004实际恢复原参数/成功同回执、无误报/确认按钮，没有重发旧动作；无权限参数不可用原因分支未实测。
- **#1/#8已实现，仅离线。** 已登记/队列/固定白名单周期外壳在120秒失联门槛和advisory活归属保护后正式failed，不重放业务；既有CLI兼容，部署须先停全部旧workers，禁止新旧混跑，无新schema。事件2槽只隔离外壳，候选另按订阅轮次/最近服务与原next_at/id分配，页面outbox每页最早头配额/失败头不越过；总批200/100保留。单离线脚本验证状态保护与真实SQL编译，没有真实崩溃、长期负载、全量队列或吞吐证据。
- **#3/#5/#7/#9/#11及其它拒绝分支仍有明确局部边界。** 规则有效期默认0/显式0–86400，claim前可信来源/版本/单目标当前谓词/指标/关联复核；身份歧义完整授权者可见、版本隔离去重、旧发布专用原因、canonical双向关联/重提与锁序均有离线检查。没有执行旧记录、自动修关系、清去重或造无权限样本。#6当前current头int/空formal合同曾由正常会话两空间实读，无需为旧截图null改数据。
- **#12–14为来源与能力合同。** 对象/关联是求值时当前值，事件字段为原持久入站payload；CDC按键读当前态无法恢复读取前已覆盖中间值；循环须生产方提供真实稳定轮次键及独立eventId。操作点提示已补，不新增历史快照、全源中间态或自动轮次推断，更不伪造旧事实。

最后FAILED接收器按身份核对后已停止listener66748/launcher29992，8767无监听；私有配置、证书/凭据和台账保留，API52800与前端66608仍运行，无周期worker。root最后统一 `npm run typecheck` exit0、backend/frontend/docs `git diff --check` exit0，未运行build/package/backend tests。原13张人工审阅图片加本次FAILED图，共14张白名单；不纳未批准图。root已允许在本次文档收口后冻结最终manifest哈希，暂存/提交/推送仍由root执行，不处理37个既有删除。
