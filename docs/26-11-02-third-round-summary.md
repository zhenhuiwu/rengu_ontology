# 第三轮14项对照与提交准备（2026-11-02）

这是本轮14项截图对应的收口清单，已补主会话最后 FAILED 实际业务及整页恢复事实；root已确认最后统一检查并允许据此冻结提交清单。代码实现、离线断言、正常读取、真实业务和原有能力边界分别记录；不是14项全部实际通过的声明。未在指定 Downloads/docs 候选路径找到第三轮 Markdown 原单，编号依据主会话转述的截图和各owner报告。

主会话最终正常 API-only 于10:04:01加载，listener PID52800，没有周期worker；此前09:50:43/PID67836阶段还未包含历史版本参数可见性修复。root最后已明确反馈 `npm run typecheck` exit0、backend/frontend/docs `git diff --check` exit0。PID与时间仅为本机加载证据，不是部署配置。本汇总agent没有启停服务、HTTP、数据库、队列消费、pytest、全量build或打包，未暂存、提交或推送。

## 14项精确对应

| 项 | 问题与最终处理 | 当前证据状态与不能外推的部分 |
| --- | --- | --- |
| 1 | 周期worker崩溃遗留doing/lock：共享 `jobs/recovery.py::ReliableApp` 通过公共middleware/生命周期保护。只检查当前已登记周期、消费队列与固定白名单的交集；120秒心跳/可信started门槛，事务重读job/worker，再以jobId归属advisory锁保护活任务。正式finish为failed、保留历史、不重放当次业务。六个builder接入，无schema变更。 | **仅离线通过。** 状态选择、心跳更新竞态、活归属、未知/具体业务参数/非doing/未来心跳保护有内存断言；没有真实崩溃、跨进程锁竞争或旧队列处置。升级前必须先正常停旧版workers，不能新旧混跑。 |
| 2 | 外部明确FAILED原因/编号/告警事实：提交及负向查询沿共享detail传reason/code/已有真实receipt；run保留fail_reason/ext_ref，状态“已对账”继续表示已核实终态，明确展示“外部未执行”；canonical告警追加失败事实，不自动关闭或重发。缺编号不补造。 | **限定FAILED主链真实通过。** 新run0002/call1540一次HTTPS200业务FAILED、reason/code/receipt、负向GET、重复派发post1及QA整页恢复均通过；样本没有关联告警，告警失败联动仍仅离线，不能用问数样本替代。 |
| 3 | 自动规则动作有效期及当前条件：`validitySeconds` 整数0–86400，默认0不额外设限；在服务端冻结origin/规则版本/命中/目标/持久事件或调度来源与expiresAt，重提不延长。外发claim前复核单目标当前predicate/指标/关联及截止时间，拒绝时failed、attempt0、无HTTP；旧记录无可信origin不猜测来源。 | **仅离线及3个SFC编译通过。** expiry、source/version、条件不符、目标唯一性、指标身份和关联scope/LIMIT2已有断言。真实过期/条件变化UI分支未验，触发关闭的既有规则试跑不覆盖自动外发guard。 |
| 4 | 问数明确目标/参数被决策替换：通过正式Bound按当前user读取公开受控PK/标签，明确唯一目标优先，歧义澄清；决策目标或给定参数值/类型冲突拒绝。持久intentTrace保留原提案/受控解析/选择或有限降级类别；移除模型前advice旁路，统一一次意图解析，stage端也拒绝澄清trace。 | **明确16002/score90正向与trace真实通过；冲突拒绝仍离线。** modelProposal/requested/decisionChoice与实际目标均为同一动作、OfficeDevice16002、score90；内存另核公开PK/隐藏标签、True不等1、歧义与旁路。不保证任意自然语言或GUI冲突样本均正确。 |
| 5 | 身份歧义failed/attempt0无法在异常区干预：只有完整对象行授权者可用非唯一身份投影看见该异常；局部行权限仍unique-only，避免泄漏隐藏碰撞对象。修复身份后仍走原审计人工retry及快照/路由/身份重核。 | **仅离线通过。** attempts0仍准确表示未发送，未把它改成HTTP已尝试；没有在真实歧义旧记录上试retry或放行。 |
| 6 | current.lockVersion与空formal列表：当前合同为latest发布头id，空库0；formal列表只列正式发布，working存在时formal为空正常。发布头令牌不同于CR lockVersion，append-only实体没有可变lock_version。 | **当前正常响应实读通过，无需为截图旧null改数据/schema。** root正常会话实读ws16 current v2026.11.11/lock129:int、w4 v2026.10.26/lock36:int，两者formalCurrent=null/list0；finally logout200。不能据此重建旧截图部署来源或声称全部并发发布已验。 |
| 7 | 规则升版沿用旧去重窗口：事件与定时两条recent-hit查询均按当前rule_version限定；新版本使用自己的窗口，旧命中保留。原event+rule已有命中幂等与入站发布锚点不变。 | **仅离线SQL/合同通过。** 未通过改原eventId、删除去重表或重执旧事件补证；真实跨版本新事件/新时段仍待验。 |
| 8 | 高频订阅占批与外壳互堵：事件worker固定2槽保留两个任务各自全局锁；真正事件候选按(type,subscriber)轮次、最近attempt开始时间、原next_at/id顺序，批总200。页面站内outbox每page最早未投递头一条、按最近服务排序，批总100；未来重试头不被同页后续越过，保留SKIPLOCKED与业务claim/ordering/权限。 | **仅离线查询编译/关系例通过。** 2槽仅隔离两类外壳，不冒称订阅公平；没有长压、吞吐或多进程真实公平投递。历史聚合成本未量化，保留量大时需再评估持久游标。 |
| 9 | 旧发布事件诊断被泛化：自动规则和显式规则/动作route发现原event.release_id与latest不一致，明确 `EVENT_RELEASE_CHANGED_REQUIRES_REVIEW`，复用preflight追加历史与aggregate。前端原因文案/筛选新增EVENT编码，保留旧ACTION编码。 | **仅离线通过。** 实际prepare内存场景failed1/skipped0/attempt0/历史1，原release_id不变；没有处理旧事件或新发布错配真实样本，不改快照/清错/自动replay。 |
| 10 | 已核实终态仍残留排队/unknown原因：仅明确SUCCESS/FAILED、人工或自动已核实对账及外发前明确失败终态清active unknown_reason；仍不确定、排队、继续查询保留。历史日志不删，重复结论回调不重复追加。 | **本次FAILED终态真实通过，其它分支仍离线。** run0002 unknown_reason=null，QA整页恢复无旧未知说明；不批量改历史DB，不把“已对账·外部未执行”冒称成功。 |
| 11 | run→alert单向关联/重试覆盖：统一create_run与stage replay建立canonical双向关联；严格核同scope/目标/规则，拒绝覆盖不同canonical及未绑定历史冲突；只允许明确父canonical失败记录的retry迁移。沿run→alert锁序与NO KEY UPDATE避免FK互锁。 | **仅离线通过。** 跨scope/目标、冲突、受控retry、幂等和锁SQL有断言；未自动回填旧关系，也未证明真实并发重试/规则告警联动全链。当前问数FAILED样本若无alert，不覆盖这条告警路径。 |
| 12 | 对象当前值与事件入站事实混淆：操作点明确“对象当前属性/关联对象当前属性/事件入站载荷”；已有evaluationMode/evaluatedAt保留。对象与关联读取求值时当前授权行，事件字段来自已持久payload。 | **合同与来源事实边界已明确，提示SFC编译通过。** 没有完整历史对象/关联快照；生产方未提供发生时字段时，平台不能用当前值补造事件时刻事实，不宣称历史回放恢复。 |
| 13 | CDC读取前已覆盖的中间态：连接配置提示按键通知后读取源当前态，完整转换历史须接保存每次变化的源事件/outbox或审计日志。独立事件API已收到的payload可以持久保留，不能混称所有事件都不可留存。 | **能力边界说明，完整源中间版本历史仍未实现。** 旧101行CDC恢复证据不覆盖任意中间值、全源历史或跨表事务原子性；本轮没有重建、补写或伪造历史状态。 |
| 14 | 同对象循环业务没有轮次：明确当前规则版本与所选去重键的永久/窗口语义；已有声明payload标量可纳入键，循环业务应由生产方提供稳定cycle_id/inspection_id及独立eventId，同轮幂等、异轮不同键。 | **既有合同及提示说明，不是自动推断轮次的新能力。** 没有新增业务参数、清空旧去重记录、随机改原事件ID，也没有实跑两个轮次并证明自动动作主链。 |

## 专项证据与唯一检查边界

- [周期恢复与公平选择](26-11-02-worker-recovery-fairness.md)：单份 `check_periodic_recovery.py` 一次通过，实际恢复函数+内存connector、活归属/心跳竞态、真实查询编译与关系选择例；不执行SQL、真实worker或旧jobs。
- [动作/规则/外联报告](26-11-01-rule-external-business-check.md)：`check_action_outcome_links.py` 内存/SQL合同及3个SFC；`check_rule_dispatch_guard.py` expiry/source/current单目标断言及3个SFC。新增分支的状态以报告末尾后续授权实现为准，不沿用其早期“待授权”段落。
- [问数/发布/事件review](26-11-02-third-round-qa-release-events-review.md)：同一 `check_qa_intent_release_boundary.py` 首次通过后，因新增跨类型/显式route/旁路修复再追加必要断言通过；没有重跑旧suite。FAILED接收器既有 `--self-check` 新分支一次通过，无socket；同项重选的HttpEndpointSelect改用select是单行修改，无新增测试，正式UI保存结果待root。

以上来源均为各owner明确报告及其已落盘专项文档，汇总未新增检查或私密读取。当前root统一typecheck通过不替代真实执行；本轮没有pytest、全量后台、build、package、benchmark或部署端验证。

## 最后真实 FAILED 主链与历史成功run恢复

root通过正常UI同项重选接口、刷新response mapping并保存动作v2及对应授权，新run `ACT-20261102-0002` / call1540在10:04:55确认。固定scope16/EXT-N01、recordExternalInspection v2、OfficeDevice16002、score90、八字段、reason/code路径及0本体编辑/0副作用，经精确只读预检后仅派发这一个新call。

实际一次HTTPS POST200、attempt1、业务FAILED；run已对账·外部未执行，完整原因“本轮合成接收器明确拒绝办公巡检，未执行业务写入”、代码 `OFFICE_SYNTHETIC_REJECTED`、真实回执 `OFFICE-c322a4c7372c5ae98ec7` 保留，unknown_reason=null。同幂等键真实GET“查到·未执行”且同回执，再派同call后台台账postCount仍1。QA同卡及整页reload保持外部未执行、完整原因/回执，无未知说明和确认按钮：[FAILED整页恢复](evidence/qa-external-failed-reconciled-2k-20261102.jpg)。

S0005-T001的 modelProposal/requested/decisionChoice 均为recordExternalInspection、OfficeDevice16002、score90，decisionTarget相同，正向意图trace实际一致；没有制造GUI目标/参数冲突，拒绝分支仍离线。问数样本无alert，不证明规则告警失败联动全部通过。

同轮root发现升版后旧成功run的参数被当前版本可见性逻辑隐藏。owner改为按固定(type,version)批量读取同scope历史快照，保留当前权限与执行版本guard；`check_historical_run_visibility.py` 内存/SQL断言通过。QA增加paramsUnavailable明确原因，隐藏参数不冒称无参数。root真实返回S0004，旧v1成功run在当前v2下恢复原参数/同一成功回执，无误报或确认按钮，没有重发旧动作；额外无权限原因分支未制造真实样本。

截图后owner按明确授权核对并停止本轮FAILED接收器listener66748/launcher29992，8767无监听，私有配置/证书/凭据和台账保留；API52800、前端66608仍运行，没有周期worker。没有把先前SUCCESS、静态mapping或接收器离线材料冒充本轮FAILED结果。

## 提交路径准备与排除

root最后统一检查通过并明确允许冻结后，`backend/.local/commit-path-manifest-20261102.json` 按本次文档收口状态记录最终路径/哈希；实际暂存、提交和推送仍仅由root执行。5组为后端产品/迁移依赖、前端产品资源、离线合同与测试源码、运维部署工具、正式文档及明确批准的证据；本轮新恢复/公平/规则guard、动作结果/QA边界/历史可见性脚本及专项文档均纳入。

37个旧docs删除继续排除，不restore或伪称已归档。`_fetch`、testlogs、E2E结果、新frontend/shots、docs/images、非批准evidence及三个真实fixture/故障注入脚本继续排除；不会将私有配置、证书、token/HMAC、原始台账、备份或临时manifest提交。root已批准上述FAILED截图，证据总白名单14张。公开默认admin/admin123按用户强制要求保留，README明确首次登录后改密；这不授权提交其他真实秘密。
