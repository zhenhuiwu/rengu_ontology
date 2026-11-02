# 第三轮新增问题：问数意图、发布头与事件前置诊断（2026-11-02）

这是用户新14项截图中第4/6/9/10/11项的当前源码复核与获批局部修复记录，不沿用旧报告“已通过”替代本轮验证。未找到Downloads/docs中的第三轮清单文件；Downloads仅找到第二轮运行清单。本agent未读军事业务数据、未写DB、未运行后台测试或worker；提交保持暂停。

## 第4项：问数目标及参数被决策替换

当前源码原有三个确定原因：

- dynamic_ctx按固定物理code列识别点名对象，把业务code当objId，未使用正式受控公开主键；对id与code不同、source_sql镜像、无code的通用本体不正确。
- decision_target将唯一相关告警放在模型明确目标之前；decision_choice采用规则预填参数，仅补未填项。chain仅检查推荐动作类型相同就返回新决策目标/参数，没有拒绝与本次请求意图冲突。
- decision评估失败统一吞掉后fallback卡，未保留原提案/决策选择及降级类别。已有turn.question保留原问题，但原提案可能已被替换，不能反向重建。

root授权后的修复：

1. 点名/结果对象身份读取复用正式object-search的`explore_api._bind → Bound.stmt(user)/read`，返回公开受控PK，不再固定code；公开code仍可作查询输入别名，但返回canonical id。传入真实user沿用对象读取/行权限及source_sql；标签只从bound.out公开字段取，隐藏name不返回。所有现有调用已更新。
2. 当前问题中可见的唯一受控目标优先，多个候选或模型目标无法从问题确认时返回澄清failed卡；明确proposal目标不再被无关唯一告警替换。决策选择如改变type/kind/id或与本次提案任一给定参数不一致，严格按JSON值/类型拒绝，不将True当1，不调用错误目标dry-run或执行。
3. 已有turn动作卡增加最小intentTrace：原modelProposal、受控解析requested、decisionTarget、decisionChoice/decisionId或有限降级类别；保留实际选择供核查，不输出异常自由文本或新造模型/SQL日志。不新增表或全局审计绕过。
4. qa.router暂存端增加澄清trace拒绝，即使直接调用stage也不能绕过前端failed显示。其它对象/参数/告警/幂等/权限守卫保持。

**同根旁路也已关闭：** chain模型前wants_advice原可直接决策提前return，完全没有原提案，会绕过同一意图门禁。在root“禁止决策替换明确目标/用户参数”的既有授权内，最小删除该提前返回支路，让动作建议统一经过已有模型意图解析和同一一致性检查。部分advice由原零模型改为一次原有模型解析，不增加第二轮模型，普通查询路径不改；已明确通知root这一行为差异。未宣称模型一定正确理解任意自然语言；原问题保留在turn.question，trace标明modelProposal，不能伪称人工结构化输入。

## 第6项：发布头与空formal列表

现树GET `/releases/current`明确返回camelCase **lockVersion=latest.id，无记录为0**，不是OntologyRelease实体的lock_version列；该append-only实体本来没有可变lock_version。`/releases`只列scope=formal，因此存在working checkpoint、formal items=[]可以正常。

规则apply/restore的releaseLockVersion指当前本体发布头令牌，CR自身lockVersion是另一合同；两者不能混用。CurrentVersionOut非空整数字段，前端发布调用使用fetchCurrentAfterWrite的lockVersion，不由空formal列表推导null。

root随后用正常httpx客户端新登录公开默认admin，cookie仅内存保留、没有输出session/token，finally logout200，实际读取两空间：

| 空间 | /releases/current | lockVersion | formalCurrent | /releases条数 |
| --- | --- | --- | --- | --- |
| 16 | v2026.11.11 | 129，int | null | 0 |
| 4 | v2026.10.26 | 36，int | null | 0 |

因此**当前源码及当前正常运行响应均非null**，空formal列表符合working/formal分离合同；没有为了第6项新增迁移、修改发布数据或创建key。截图旧响应的部署/字段来源不能据此补造；这不宣称所有发布并发与空库首次发布已经实测。本agent本人未读cookie/token或发HTTP，以上为root明确反馈的实际结果。

## 第9项：旧发布事件原因精确化

当前树不应一概描述为静默skipped：prepare已有preflight失败历史及aggregate；skipped有订阅disabled/过滤不命中等明确用途。发现的真实缺陷是自动规则resolved_route在latest.id与event.release_id不同时返回空route，随后被泛化为DELIVERY_ROUTE_NOT_CONFIGURED，丢了发布变化原因。worker的返回值也只有扫描/claimed数量，不能当完整成功/失败统计。

本agent仅修resolved_route/prepare版本窄hunk：正式自动规则订阅，以及显式配置的规则/动作route，最新发布与原event.release_id不一致均在preflight抛 **EVENT_RELEASE_CHANGED_REQUIRES_REVIEW**；复用原prepare的终止、append-only历史和aggregate。保留事件release_id、载荷、定义快照，不清错误、不重新绑定、不自动replay，不启动worker。worker统计由另一个负责agent处理，不混称已完成。

root追加要求后同步两处纯展示：`deliveryPresentation.ts`为新EVENT原因显示“事件入站后本体发布版本已变化；原快照保留，须人工核查”；`DeliveryExceptions.vue`原因下拉增加“事件入站发布版本不一致”。原ACTION原因编码、中文提示及筛选继续保留，兼容旧记录。后端既有按error精确过滤合同不变，未新增宽松模糊匹配；这两处纯映射未另外运行检查或冒称已有真实异常样本UI验收。

## 第10/11项交接，未由本agent修改

- 第10项确定当前缺陷：人工reconcile与DynamicReconciliationListener转已对账后未清unknown_reason，序列化仍原样返回队列说明。建议真实终态清当前reason、旧原因留追加日志，不能批量清历史DB掩盖问题。
- 第11项确定当前缺项：ActionService.create_run设run.alert_id，通用QA/规则stage不统一设alert.action_run_id；只有AlertService专用入口设反向指针。成功关联处理还依赖反向指针。已交deep_action_review统一处理canonical run、重试继承、同空间同目标与既有run→alert锁序，不能简单无锁覆盖现有指针。

## 唯一离线检查及待真实复验

唯一新增脚本`backend/scripts/check_qa_intent_release_boundary.py`首次通过：真实Bound调用的内存替身核user/公开PK/隐藏标签，纯函数核目标冻结、歧义、参数值类型冲突；真实events.prepare在内存Session中核版本错→failed、attempt0、专用错误、1条preflight历史、aggregate失败1/跳过0、原release_id不变，并syntax编译当前4个变更文件。随后补充跨类型对象歧义、显式配置route及模型前旁路删除等必要断言，在新代码状态同脚本再运行一次通过；没有重复跑旧suite。导入时既有FieldOut.schema shadow警告仍在，未因此扩改schema。

```text
QA explicit intent, governed identity and stale-event preflight passed (offline only).
```

本agent没有真实API/模型/DB/worker、pytest或后台suite；没有提交/推送。第6项正常会话两空间读取已由root完成。第4/9补丁需root正常加载后用普通办公合成场景补明确目标/参数冲突与trace，事件新样本只能按明确授权精确单条处理，不动旧事件/军事链；尚未取得这些新补丁的真实业务验收。

## 获批办公接收器明确FAILED模式与唯一新调用实际验收

root另授权本agent独占`backend/scripts/local_office_receiver.py`，新增CLI `--outcome FAILED`，默认仍SUCCESS；初次仅代码/离线素材，随后root于API-only 09:50:43正常重启（launcher68324）后，明确授权只启动原loopback接收器，等待新call再派发。

启动阶段，本agent核对原私有目录/所需文件存在、8767未占用，仅以原目录启动FAILED模式：launcher **29992**、listener **66748**，只监听 **127.0.0.1:8767**。stdout显示该地址/ontologyId16/outcome FAILED；两日志为原私有目录`failed-mode-20261102.stdout.log`/`.stderr.log`。未改env/certs/bearer/HMAC、未另登录或启动worker；该阶段没有HTTP或派发。随后获批的唯一新调用见下节，不能把初次“尚未派发”当最终状态。

八字段payload、scope16、recordExternalInspection、OfficeDevice/16002、score整数范围、Bearer、HMAC时间窗/签名与同key不同body冲突全部保留。FAILED是HTTP200里的明确业务status，不伪造网络错误；返回固定合成reason、code=OFFICE_SYNTHETIC_REJECTED及真实生成的本机回执。台账持久保存该key的结论，后续切回默认SUCCESS也不会把旧失败或旧成功翻转；只读query对失败回执返回现有合同“查到·未执行”。

只扩充既有`--self-check`，运行一次通过：默认成功、失败鉴权/签名/目标守卫、失败reason/code/receipt、持久查询、重复同key同结果、模式切换不改旧结论；全部使用临时假凭据文件和内存accept/query，无socket/DB/业务写入。未准备新真实凭据或改现有接收器台账。

已提醒root：原office_receipt响应mapping只读status/receiptId，reason/code路径为空时平台不会自动采用新增字段。root随后正常UI维护响应路径并保存动作v2/对应授权；没有本agent后台改mapping，不改8请求字段/目标16002。

root真实维护响应mapping发现：HttpEndpointSelect提示需重选并保存动作，但下拉绑定change，同项不产生change，旧mapping无法刷新。获准仅改为select，复用原choose读取当前目录httpMapping；已有动作及新建动作表单共用。该单行trivial未新增测试；root允许HMR后实际重选保存v2，后续只读预检确认reason/code两路径均正确，真实失败返回贯通见下节。

### ACT-20261102-0002 / call1540：明确失败、原因与回执、unknown清空及防重

root明确授权仅此scope16新run（recordExternalInspection v2，OfficeDevice16002，score90，EXT-N01/office_receipt，10:04后确认），禁止旧run1/旧队列/worker。首次只读预检因本agent额外假设“run创建也晚于10:04”停止，**没有进入dispatch**。准确读取显示run创建10:02:33、confirm10:04:55.417、call创建10:04:55.485；符合root原话“10:04后confirm”。纠正这项过严的取证时间guard为call+confirm，不放宽其它身份/权限后才继续。

实际只读预检：唯一writecall **1540 / EXEC-10925575c3cd4379881da22ce4609378**，attempt0、QUEUED、HTTP空；run已确认、v2、目标/score及精确POST `https://127.0.0.1:8767/office/receipt`；8请求字段、marker、response reason/code路径、动作0本体编辑/0副作用全部吻合。各读事务设置scope16及READ ONLY，未登录新会话、未输出凭据。

随后按授权一次`dispatch_one(1540,16)`，一次同幂等键真实query，以及再调用同call派发验证不重复。结果：

| 项目 | 实际结果 |
| --- | --- |
| 外部POST | HTTP200、attempt1、业务FAILED，call结果失败 |
| run终态 | 已对账，outcome=外部未执行；不是成功，也不是未知 |
| 原因/代码/回执 | fail_reason=本轮合成接收器明确拒绝办公巡检，未执行业务写入；result_code=OFFICE_SYNTHETIC_REJECTED；ext_ref=OFFICE-c322a4c7372c5ae98ec7 |
| unknown_reason | null，旧排队说明没有作为当前未知原因残留 |
| 同idem真实GET | 查到·未执行，同一回执 |
| 重复同call派发 | 台账仍该同回执、postCount=1，没有第二次POST |
| 问数意图trace | S0005-T001，decisionStatus=recommended，decisionId=DEC-20261102020131-7bd4aa；modelProposal/requested/decisionChoice均recordExternalInspection、OfficeDevice16002、score90，decisionTarget同16002 |

这次普通办公显式16002/90请求的原提案→受控请求→实际决策一致性与trace已有真实证据；未制造目标/参数冲突样本，冲突拒绝仍以离线为主。FAILED运行结果、reason/code/receipt贯通、unknown清空及post防重已实测，root随后负责QA同卡刷新/展示；收到其UI反馈前不提前记最终UI通过。本agent没有执行其它call/规则/worker，没有重启API或改原配置；FAILED接收器此时仍运行，未擅自停止。

### 固定历史版本参数可见性与QA不可用原因

root另发现S0004旧v1 run在当前动作升v2后，参数读取按当前定义错误脱敏，前端又将null误称无参数。实际历史版本定义可读及其权限核对由deep_action_review负责；本agent没有降低执行版本guard或读取旧run秘密。

root已正常切回S0004，真实恢复同一已对账成功run/同receipt，无原“无参数”误报、无确认按钮；没有重发旧动作。在无未保存表单的HMR授权后，本agent仅补QaActionRun.paramsUnavailable可选DTO、rememberAction具体不可用原因优先与确认字段显示；QaConversation原记录摘要同样在该字段存在时显示“参数不可用”，不把权限隐藏/null冒称“无参数”。此额外原因分支未制造真实无权限场景，保留未验；没有新增测试、模型请求或业务执行。

root随后明确这次FAILED事实必须同卡贯通，追加一行QaConversation展示：仅在status=已对账、reconcile=外部未执行且有failReason时显示该原因，既有receipt正常保留；成功旧run不因历史残留reason被渲染为活动失败，未知分支不受影响。后台unknown_reason已真实为空。本agent此后冻结产品，交root最终刷新截图与统一typecheck，不新增检查/模型/业务执行。

### root最终真实UI、整页恢复与接收器停止

root实际打开新S0005并整页reload，仍是ACT-20261102-0002已对账、外部未执行、完整合成拒绝原因及OFFICE-c322a4c7372c5ae98ec7回执；没有未知说明或确认按钮。[FAILED同卡真实恢复截图](evidence/qa-external-failed-reconciled-2k-20261102.jpg)。因此本次明确FAILED的后台结果→QA负向结论/原因/回执→整页恢复闭环已通过；旧S0004的升版后成功同卡恢复也已通过，没有重发旧动作。

第4项本轮显式OfficeDevice16002/score90的正向请求与原提案/决策trace一致已有真实证据；目标/参数矛盾拒绝仅离线，未冒称GUI冲突样本通过。

root截图后明确授权收尾。本agent分别核对listener66748/launcher29992的进程命令含`scripts.local_office_receiver`、本轮原私有目录及`--outcome FAILED`，核对8767唯一监听是127.0.0.1/66748，随后只停止这两个指定进程。结果两进程均不在、8767无监听；private目录与office-receipts.json台账均保留，未删或改凭据/证书/配置。API52800和前端66608进程仍在，未停止或重启；没有再发HTTP/模型/业务、跑worker或后台检查。

产品与本文至此冻结，交root及manifest owner汇总/最终统一检查。这里的进程身份核对是明确授权的关闭前置与结果确认，不是另行扩展验收。
