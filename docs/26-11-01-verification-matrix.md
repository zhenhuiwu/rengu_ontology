# 使用区、构建区、管理区验证证据矩阵（2026-11-01–11-02）

本文仅盘点已有报告与主会话明确转述，不运行测试、数据库、HTTP、模型、浏览器或服务操作。没有把离线检查、测试数量、代码覆盖范围当作业务完成率。每行的“通过”仅覆盖写明的操作及样本，不能外推为整个模块通过。

最新主会话基线：**11月2日10:04:01正常API-only加载，listener PID52800**，包含第三轮与固定历史版本参数可见性修复；09:50:43/PID67836尚未包含最后历史读取，00:48:02/PID49000及11月1日PID35572/16:26:49、更早22256/29396/49836均为阶段记录。11月2日办公合成联调仅精确追加scope16/EXT-N01的office授权、局部probe与唯一issuer CA文件，保留旧值与证书验证；不沿用早阶段“env完全未改”概括本轮。见[规则/外部验收](26-11-01-rule-external-business-check.md)、[第三轮对照](26-11-02-third-round-summary.md)。本机通过不证明第三方生产部署通过。

最新实际结果由主会话转述补入：ws16标准事件为 **ActionExecuted、ActionUnknown** 共2条；DS-001负责人admin、正式测试连接成功、目录139→3已真实UI复验；FN001新建立即生效，四次平台运行 `{}` / `{"score":null}` / `{"score":90}` / `{"score":89}` 分别为待检/待检/正常/需维护；普通五轮会话刷新、模块/本体隔离，以及既有FN001说明修改模式刷新后preview和未发送文本恢复均已通过。相关deep报告的较早“待主会话”段落保留历史语境，不覆盖这些更新。

随后真实结果：FN001 desc-only保存至v1.0.1，1函数/2版本；取消保留人工输入、放弃后原preview可重采用。OfficeDevice、MaintenanceTask及1FK导入立即生效，主表读写授权及extra映射保存重开通过；设备25条/末页5条、打印机筛选13条回首页、工单37条及task16101→asset16001关系通过。下表记录随后完成的QA分组/三表查询、真实status动作、动作统计、会话URL恢复和P001页面恢复；不是整个模块全部验收。

状态口径：**实际业务通过**指真实页面/接口/受控业务结果已有证据；**部分通过**指明确限定的实际路径通过而其余仍缺；**仅局部/源码**指没有相应真实业务闭环；**当前未实现/保留限制**指不能靠补测宣称具备；**当前不可安全验证**指本轮不触发军事自动求值、既有队列消费或真实外部业务来取证。

## 使用区（use）

| 原问题或流程 | 当前证据状态 | 最少证据及已证实内容 | 仍缺哪一步 |
| --- | --- | --- | --- |
| 问数动作卡无法生成、同对话一次确认执行未跑通 | **实际业务通过：限定本地合成路径** | [重启后验证](26-11-01-restarted-verification.md)：SAR003 新意图得到 ACT-20261101-0001，预校验、stage、一次确认、成功、整页刷新同卡恢复；受限只读核对只有一个新 run，锁版本 2→3 | 其它对象/参数/审批/外部动作不由此覆盖；该对象原已为“搜救中”，不能称完成状态值转换 |
| 预校验失败仍可确认、缺 ontology_id | **部分通过** | [重启后验证](26-11-01-restarted-verification.md)：真实失败后修复可信事务 scope 与前端 failed 卡禁用，加载后 SAR003 通过；SAR001 状态/幂等冲突真实拒绝 | failed 卡禁用有局部检查；没有为每种失败条件重新制造真实卡和逐一点击复验 |
| 暂存复用混用参数、对象或告警；参数摘要误拒 | **部分真实通过：结构化参数及同run复用** | [QA合同](26-10-31-qa-inline-actions.md)、[设备深验](26-11-01-deep-device-verification.md)：S0002原run因摘要score=90与JSON不同误拒，改严格结构化比较后复用同run确认成功，受限只读同targetrun=1 | 对象/告警差异、超时等其它拒绝和竞态仍以局部断言为主，不外推全覆盖 |
| 失败提问消失、原问题丢失 | **实际业务通过：失败回显路径** | [QA 合同修复](26-10-31-qa-inline-actions.md)：两次真实 GUI 失败，后一次确认失败气泡及原问题持续保留 | 无需把失败回显等同动作执行通过；新类型失败的展示可随实际业务继续观察 |
| 对象列表分页、筛选、详情返回及关系 | **部分通过：明确对象与关联路径** | [性能业务检查](26-11-01-performance-business-check.md)：订单1286→渠道225及门店1条；[重启后验证](26-11-01-restarted-verification.md)：ws15镜像101条；本轮设备25/末页5、打印机13及task16101→asset16001通过 | 订单其它关系仍“未接入/暂不可查询”、4动作disabled；设备通过不能覆盖订单这些限制 |
| 镜像业务 id 与物理 rowid 混淆 | **实际业务通过：历史限定对象；当前读取复验通过** | [镜像验收](26-10-31-mirror-live-verification.md)：Task83/Project9 身份映射，大小写/别名受控读取；[重启后验证](26-11-01-restarted-verification.md)：LiveFixture分页后按 RGV0011 读取 SYN0011 | 其它导入类型、其它部署需按各自身份映射验收 |
| 新设备25台/37工单/15补充记录业务链 | **实际业务通过：本轮限定闭环** | [设备深验](26-11-01-deep-device-verification.md)：正式接入/绑定/读取；S0003逐3页核25设备、工单分布1–17各2/18–20各1/21–25各0，总37，15备注/10空，零关联保留；动作与页面见下行 | 日期区间筛选、定义说明再编辑后的数据保持等未实测；不扩大为任意源/任意业务全部通过 |
| QA分组柱状图及刷新恢复 | **实际业务通过：当前全设备分组** | [柱状图截图](evidence/deep-device-qa-chart-2k-20261101.jpg)：S0001 table核13打印机/12投影仪，无日期/状态限定，图表整页恢复通过 | 不称“正常设备”或“近期设备”分组；其它筛选与图表类型未覆盖 |
| 函数绑定本地主表动作、成功后刷新原卡 | **实际业务通过：一次明确确认** | [动作截图](evidence/deep-device-inline-action-2k-20261101.jpg)：recordInspection v1，score:int→FN001 v1.0.1→status；S0002复用ws16原ACT20261101-0001成功，16001待检→正常，lock=2、同targetrun=1，lastinspection/extra不变 | 参数摘要误拒失败已保留；其它动作/审批/外部副作用未验，不与其它空间同编号run混同 |
| 手选会话刷新却回latest | **已修，真实UI通过** | [设备深验](26-11-01-deep-device-verification.md)、[动作截图](evidence/deep-device-inline-action-2k-20261101.jpg)：S0002选择同步URL session=S0002，整页reload仍同成功run且无确认按钮 | 其它导航竞态没有由单会话恢复自动覆盖 |
| 三表查询创建页面PAGE_EXECUTION_UNAVAILABLE | **已修，真实创建/重命名/刷新恢复通过** | [页面截图](evidence/deep-device-page-restored-2k-20261101.jpg)：批准extra joinKey曾被public_columns误拒；仅允许批准ON节点，加载后用原S0003不重问创建P001，重命名“办公设备多表验收”，即时及整页reload同页预览正常、列表1条 | 不是放开任意JOIN列；其它查询结构与页面编辑功能未全面验收 |
| 规则自动求值、军事自动动作、执行报告 | **当前不可安全验证** | [综合复检](26-10-31-recheck-resolution.md)、[重启后验证](26-11-01-restarted-verification.md)均明确没有执行军事链 | 本轮不执行、不改业务阈值/授权制造命中；普通设备纯计算及本地动作的结果不能替代军事验收 |
| 跨关系+固定函数复杂规则正反例 | **实际业务通过：当前数据试跑，触发未启用** | [规则验收](26-11-01-rule-external-business-check.md)、[命中截图](evidence/rule-related-function-hit-2k-20261102.jpg)/[不命中截图](evidence/rule-related-function-no-hit-2k-20261102.jpg)：自身ID+真实FK状态+固定FN002，正例37已求值/1命中/0无法求值，反例37/0/0；v4恢复正常条件、published，trigger disabled | 不是规则自动生成告警/动作，不覆盖其它表达式、军事规则或后台队列 |
| 新办公外部动作确认→HTTPS→对账恢复 | **实际业务通过：本机持久配置与唯一新调用** | [外部验收](26-11-01-rule-external-business-check.md)、[恢复截图](evidence/qa-external-action-reconciled-2k-20261102.jpg)：ACT-20261102-0001经QA确认，call1539真实HTTPS200/attempt1/回执，同幂等键查询同回执；重复dispatch仍postCount1；整页刷新已对账无确认按钮 | 限scope16/EXT-N01；没有生产第三方URL/auth合同，不称生产第三方已接入；独立规则试跑不是自动触发此动作 |
| 第三轮明确FAILED结果/原因/回执/未知清理 | **实际业务通过：限定新run0002主链** | [FAILED恢复截图](evidence/qa-external-failed-reconciled-2k-20261102.jpg)、[第三轮review](26-11-02-third-round-qa-release-events-review.md)：call1540一次HTTPS200/attempt1业务FAILED，原因/code/真实receipt保留、unknown=null；同idem GET未执行同回执、重复dispatch post1；QA整页reload外部未执行/完整原因/回执，无未知说明或确认钮 | 无alert的QA样本不证明规则告警失败联动；不代表生产第三方或全部错误类型 |
| 第三轮明确目标与决策trace | **部分真实通过：16002/score90正向** | S0005-T001 modelProposal/requested/decisionChoice均recordExternalInspection/OfficeDevice16002/score90，decisionTarget相同；[14项对照](26-11-02-third-round-summary.md)记录来源 | 目标/参数矛盾拒绝和歧义仍离线，不保证任意模型自然语言 |
| 动作升版后旧成功run参数被隐藏 | **已修，旧v1真实恢复通过** | [历史读取及QA恢复](26-11-02-third-round-qa-release-events-review.md)：按同scope固定(type,version)批量历史定义+当前权限；root回S0004恢复旧参数/成功同receipt，无误报或确认钮，未重发 | 缺历史快照/当前权限拒绝仍隐藏；额外paramsUnavailable原因分支无真实无权限样本；显示可读不等于可按旧版本执行 |

## 构建区（build）

| 原问题或流程 | 当前证据状态 | 最少证据及已证实内容 | 仍缺哪一步 |
| --- | --- | --- | --- |
| 导入大写/双下划线属性导致说明无法保存 | **实际业务通过** | [导入编辑修复](26-10-31-imported-object-edit-fix.md)：ws15 LiveFixture保留原属性，仅改说明保存至v5；签名不变、无需重同步 | 本轮设备全小写字段不构成该大小写场景的再次验证；其它类型按实际需要观察 |
| 保存定义后概览说明不刷新 | **实际业务通过** | [导入编辑修复](26-10-31-imported-object-edit-fix.md)：同对象v6保存后未换对象/未刷新即显示新说明；[镜像验收](26-10-31-mirror-live-verification.md)核对稳定签名 | 新设备正式编辑后仍需核对定义、映射、绑定与数据保持 |
| 动作执行详情长ID溢出 | **实际业务通过：2K视口** | [导入编辑修复](26-10-31-imported-object-edit-fix.md)、[达梦总体验收](26-10-31-dm-live-verification.md)：真实长ID详情修复后边界在2560×1440视口内 | Chrome102及其它视口没有这次实机证据 |
| 事件嵌套属性行复用、布尔false、非法API提示 | **实际业务通过：建模与模拟载荷** | [事件UI检查](26-10-31-event-ui-check.md)：删除前行后config子字段保留，保存发布v1，中文API就地报错，false模拟载荷校验成功 | 无订阅、无事件/投递创建；不证明事件自动化运行 |
| 新空间缺标准事件 | **实际业务通过：本次空白空间** | [助手深验](26-11-01-deep-assistant-verification.md)及主会话最新转述：ws16自动出现ActionExecuted、ActionUnknown两条 | 首次安装、反复初始化幂等与存量审计仍见管理区边界；旧段落Unknown是早期简称，本次真实API名以ActionUnknown为准 |
| 助手真实生成、多轮保留需求及null处理 | **实际业务通过：限定函数新建与desc-only修改** | [助手深验](26-11-01-deep-assistant-verification.md)：首轮80、二轮85；三轮缺默认值/Is/擅加范围失败保留，修prompt/门禁/warning去重；第五轮阈值90，FN001四次平台输入、desc-only保存v1.0.1且其它字段保持 | 不保证任意需求/模块正确；精确脏表单覆盖及短前置GET时序未实测，模型在途见下行 |
| 助手模型在途clear/route隔离与刷新 | **实际业务通过：B2/C精确时间证据** | [异步验收](26-11-01-assistant-async-business-check.md)：B2 clear早于模型完成，新输入保留、旧结果不复活，reload恢复；C对象路由早于响应，目的桶空，返回原函数无迟到结果，未填对象表单 | A未证明响应晚于form=new；隐式前置GET及丢弃确认更短窗口仍仅离线 |
| 函数真实动作/规则引用显示0 | **已修，真实UI两类来源通过** | [引用修复](26-11-02-function-reference-fix.md)：共享当前定义引用合并旧FnUse，展示及签名guard同用；49000后FN001引用1动作“登记设备巡检”，FN002引用1规则“办公工单关系与函数条件验收” | 不兼容签名保护仅离线，未破坏式保存；不宣称全部历史引用治理 |
| 助手普通会话刷新丢内容、模块/本体串话 | **实际业务通过：五轮普通会话及已做隔离路径** | [助手深验](26-11-01-deep-assistant-verification.md)、主会话最新转述：五轮历史/预览/未发送输入整页恢复，切对象页为空、回函数恢复；切w4同ID不同函数不带旧编辑/历史，回ws16恢复 | 身份切换仍无真实UI证据；不外推任意路由、全部模块自动创建均通过 |
| 跨模块采用时预填被清 | **已修，仅局部检查** | [助手深验](26-11-01-deep-assistant-verification.md)：移除任意会话桶切换清预填，内存路由检查通过 | 真实从建模页生成另一模块、点击采用、目的表单确实有值并可正常保存 |
| 切身份/空间旧editing残留 | **空间切换实际通过；身份切换仅局部** | [助手深验](26-11-01-deep-assistant-verification.md)：watcher修复；ws16→w4同ID priority_label不见旧editing/history，返回ws16点AI编辑恢复desc及未发送文本 | 真实身份切换没有本次证据；弹窗await跨上下文的新发现见下节，未由此空间常规路径覆盖 |
| 取消覆盖脏输入后建议预览消失 | **部分真实通过，精确覆盖路径仍未实测** | [助手深验](26-11-01-deep-assistant-verification.md)：apply保留result；真实人工改title→取消选继续编辑仍保留→明确放弃→原preview再采用→保存v1.0.1通过 | 抽屉遮罩使“表单已脏时同时采用助手，再取消覆盖确认”入口未达；不能用关闭表单的取消路径替代该精确覆盖分支 |
| edit会话刷新自动重建目标 | **已修，真实UI恢复及后续保存通过** | [助手深验](26-11-01-deep-assistant-verification.md)：scope指针重新GET核版本/锁，FN001 desc模式/preview/unsent恢复；后续采用保存v1.0.1 | 版本冲突/拒绝/迟到响应是局部断言；精确脏表单同时覆盖仍未达；未给旧会话补指针 |
| 空白本体建议恢复后不能采用、函数保存不更新本体版本 | **已修；首个函数保存实际通过** | [助手深验](26-11-01-deep-assistant-verification.md)：显式确认空发布context、生成/apply/consume共用门禁、保存同步result.ver；同五轮草稿最终保存FN001，本体checkpoint v2026.11.1 | 真正发布变化后旧空草稿拒绝有页面证据；并发加载/失败等其它分支仅局部，不等同全部版本竞态UI通过 |
| 保存生成示例 | **当前未实现** | [助手深验](26-11-01-deep-assistant-verification.md)：SaveIn/CreateIn无sampleIn/sampleOut可编辑字段；本次四输入是手填真实平台测试 | 不把手填测试称为样例随函数登记，亦不凭结果新增用户未要求的schema |
| 新建连接负责人默认旧演示姓名 | **已修，真实UI通过** | [设备深验](26-11-01-deep-device-verification.md)：Connections.blank改当前auth用户，重开表单admin、DS-001保存负责人admin | ExternalSystems固定OWNERS/默认姓名/人工核对演示文案只是定位，**未修改**；不推断真实API编辑也有同一错误 |
| 源仅可读三表、UI目录却139表 | **已修，真实UI139→3通过** | [设备深验](26-11-01-deep-device-verification.md)：PG按schema USAGE及表/列SELECT共享批量过滤，显式无权表403；新API29396加载后主会话重新测试连接、UI目录实际3表 | 已映射历史目录保留、不新增“不可读”DTO状态，是当前限制；DM/MySQL未改，列级选择权等并非由三表目录验收完全覆盖 |
| 同库源受控主表、补充表与多表问数 | **实际业务通过：本轮三表查询** | [设备深验](26-11-01-deep-device-verification.md)：两对象/1FK、extra id→asset_id及calibration_note持久保存；S0003逐3页核25行、工单总37、15备注/10空、零关联保留；P001同query恢复通过 | 其它1:N结构、任意聚合和补充字段权限变化不由本次单查询覆盖 |
| PG列级UPDATE被整表权限误拦 | **已修，授权与实际动作均通过** | [设备深验](26-11-01-deep-device-verification.md)：原候选为空修逐列权限，API22256后仅status/last_inspected_at，保存重开保留；随后recordInspection只改16001.status、锁=2 | 未扩大GRANT；last_inspected_at虽获授权，本次未写该字段，不称所有写字段执行通过 |
| 新建动作对象引用错误显示门店、旧草稿空态 | **已修，真实对象候选已看** | [设备深验](26-11-01-deep-device-verification.md)及主会话：新建动作真实显示OfficeDevice/MaintenanceTask两项，不再门店；旧草稿空态文案已改 | 空态文案修正不代表旧草稿已清理；本轮没有清旧数据 |
| 动作概览2K被挤压、来源列兼容 | **已修，布局与来源真实通过** | 主会话最后实看source显示“问数”，2K .at right=2548、overview right=2531均小于2560，stats侧栏400px，containment通过 | Chrome102及其它视口未实机；旧审批清理见管理行，不再列未改 |
| 动作概览Descriptions span告警 | **已修，真实UI通过** | [动作统计与布局截图](evidence/deep-action-stats-layout-2k-20261101.jpg)：column=2中原span1+span2不匹配，“写回接口”改span2；主会话重开概览不再warning，定点观察新warn/error为空 | 不外推其它Descriptions或全站控制台全部通过 |
| 执行列表对象引用仅支持固定业务kind | **已修，真实对象跳转通过** | [动作统计与布局截图](evidence/deep-action-stats-layout-2k-20261101.jpg)：ObjRef复用loadMeta/getRow按当前已发布目录与行权限；局部断言后，主会话重载列表点击16001，OfficeDevice:16001抽屉显示办公打印机01/正常/校准备注，关闭再开概览正常 | 其它对象类型与行权限拒绝路径未逐一真实UI验证；不新增绕过当前目录/权限的解析方式 |
| 外部DM直接显式受控绑定、任意表放行 | **当前未实现/保留限制** | [18条对应](26-10-31-deployment-integration-fixes.md)：同库PostgreSQL/RLS显式绑定保留，外部走授权镜像 | 不能通过修改治理边界或后台直改绑定把未支持功能算通过 |
| 手动同步一批即停、取消续传、源删除仍可见 | **实际业务通过：10-31合成源** | [镜像验收](26-10-31-mirror-live-verification.md)：101行、100+1、取消恢复；CRUD后当前集合/完整payload相等，删除退出当前、物理历史保留 | 11-01未重新做源写和同步；部署端独立升级及实际源验证仍缺 |
| 未选列扩权、所选列漂移未阻断、说明使镜像失效 | **实际业务通过：10-31合成源** | [镜像验收](26-10-31-mirror-live-verification.md)：新增未选列不进绑定/payload，类型漂移MIRROR_STALE/409，恢复后完整；说明修改签名不变 | 不能外推全部数据库/DDL类型；当前设备路线不重复证明DM镜像语义 |
| CDC初扫重启、回滚、迟提交低ID、暂停恢复、ACK窗口 | **实际业务通过：10-31唯一合成捕获** | [CDC验收](26-10-31-cdc-live-verification.md)：同run/scan恢复、CRUD及回滚、精确ACK、本地清理失败后正常API接管 | 11-01未重跑；原业务表未安装捕获，不能称生产CDC全面启用 |
| 镜像实时性、CDC任意事务/中间版本/日志缺口 | **当前未实现/保留限制** | [CDC能力边界](26-10-31-cdc-live-verification.md)、[18条对应](26-10-31-deployment-integration-fixes.md)：当前态按键重读，不提供跨多行/表PG原子可见性、每个中间版本；TRUNCATE等不覆盖 | 不将单次秒数当SLA；普通镜像旧值问题不能靠补测宣称源即时一致 |

## 管理区（admin）及事件诊断

| 原问题或流程 | 当前证据状态 | 最少证据及已证实内容 | 仍缺哪一步 |
| --- | --- | --- | --- |
| API密钥跨空间复制配置沿用局部编号 | **实际业务通过：只读表单拒绝** | [综合复检](26-10-31-recheck-resolution.md)：来源11→当前1提示明确，配置未应用、表单及生成禁用；未签发key | 同空间正常复制生成及绑定权限仍不由拒绝表单证明 |
| 新建key空间/身份/默认scope表单 | **实际UI及唯一新key签发通过** | [生命周期](26-11-01-key-lifecycle-business-check.md)：用户明确批准scope16/admin、meta:read+objects:read、loopback、30天；签发workspace16-lifecycle-20261101，实际2/min、100/day | 原2/s口述单位误读已说明，保留更严格2/min；不扩大至120/min |
| 新建/轮换一次性secret直显、确认层级 | **已修，真实UI与安全复制流程通过** | [生命周期](26-11-01-key-lifecycle-business-check.md)：生成/轮换只有mask；获批正规Copy→私有文件→清clipboard，无secret日志；公共confirm层级修复后吊销一次check且详情仍开，精确dangerLabel正确 | 未读取旧key；单位/剩天显示全部边界没有逐项实机，不因复制通过承诺任意AX/内存无secret |
| 密钥维护、收窄、版本冲突、轮换、吊销 | **实际业务通过：同一新key完整主链** | [生命周期](26-11-01-key-lifecycle-business-check.md)：双scope meta200/rows200，维护去objects后meta200/rows403；双页旧version拒绝且新备注未覆盖；rotate同id旧401/新200/跨w4 403；revoke后新材料401；两私有凭据文件删除、clipboard清，key行保留已吊销，原key未动 | 自然到期、网络其它IP/代理、所有quota窗口/计数及高并发未验，不将单生命周期写成全面权限矩阵 |
| 旧审批新动作文案/状态展示/发布入口 | **已清理指定UI，真实新动作与发布页通过** | [生命周期与文案](26-11-01-key-lifecycle-business-check.md)：动作筛选/行旧审批码显示历史待处理，对象动作审批列移除；QA外部confirm旧句已消失并正常确认执行；Releases16正式发布enabled，保存即时生效、正式记录空，无提交审核/审核人/审批按钮 | 历史admin提交/对象历史reviewer及后端guard保留，未点击正式发布；其它历史通知/导出策略不盲删 |
| Bearer读规则元数据403 | **入口已实现/注册；实际读取待验** | [规则机器读取](26-10-31-recheck-metadata-read.md)、[管理深验](26-11-01-deep-admin-verification.md)：definition只读最新发布快照，scope/当前权限/签发快照交集；[重启后验证](26-11-01-restarted-verification.md)确认路由 | 现有规则UI仍读旧详情，尚无正常产品入口消费新definition；未实际验证新端点白名单、Bearer、跨空间拒绝。旧详情能打开不算新端点通过 |
| 演示种子混入统计、系统初始化 | **局部/源码；新空间标准事件局部真实通过** | [种子隔离](26-10-31-recheck-seed-isolation.md)：显式demo、生产/空间保护、迁移默认不灌模板、只读审计；ws16标准两事件见上表 | 存量来源/引用审计、专用空库首次安装及重跑幂等未实际完成；未删除演示数据，不把同名即视为可清理 |
| 投递失败只有错误码 | **部分通过：合成展示** | [综合复检](26-10-31-recheck-resolution.md)、[明细UI](26-10-31-recheck-delivery-ui.md)：结构化/旧记录/仅代码和双主题实际2K合成展示通过；后端errorDetails追加有局部检查 | 当前新后端真实新失败记录的明细持久化→详情→历史全链未重新验证；旧记录缺失信息不补造 |
| 按原因筛选“不支持” | **部分通过：真实空结果** | [重启后验证](26-11-01-restarted-verification.md)：新后端接受“本体发布版本不一致”原因筛选，空列表，无旧提示 | 缺匹配失败样本的精确命中及原响应errorFilter回显验证 |
| 权限/歧义/配置永久错误pending无限重试、等待期限 | **历史服务边界部分通过；当前积压不动** | [事件修复](26-10-31-event-integration-fix.md)、[通用自动化验收](26-10-31-automation-live-verification.md)：特定权限/歧义/到期故障注入真实失败；并非全部HTTP分支 | 15分钟不是实际等待验证；旧积压逐条审计及当前队列消费未做，本轮不启动worker/清队列/批量重放或做队列优化 |
| 入站快照固化、预校验失败终态 | **保留设计；不是待修缺陷** | [综合复检](26-10-31-recheck-resolution.md)、[事件修复](26-10-31-event-integration-fix.md)：保留旧快照、失败守卫与明确人工处置 | 不改快照、不降级为可执行待确认卡；旧业务记录处置需各自真实证据，本轮不做 |
| 对象顺序、小写布尔、嵌套声明字段读取 | **仅局部/源码，事件编辑另有GUI** | [事件修复](26-10-31-event-integration-fix.md)：共享解释器契约检查含保序、迟到隔离、布尔及投影 | 不运行军事规则补验；普通设备如确需事件求值，应另建无外部副作用样本，但本轮不能把字段编辑当执行证明 |
| 通用事件→规则→自动动作→HTTP→对账/机器幂等 | **实际业务通过：10-31独立本机通道** | [通用自动化验收](26-10-31-automation-live-verification.md)：真实回执、机器201/同幂等200同run、无重复发送 | 临时adapter/接收器已恢复关闭；不证明真实外部系统/军事链，不能点旧run复执行补验 |
| 外部授权版本、目录映射、同路径多动作、lockVersion | **诊断已实现；历史服务边界部分通过** | [外部动作修复](26-10-31-external-action-integration-fix.md)、[18条对应](26-10-31-deployment-integration-fixes.md)、[自动化验收](26-10-31-automation-live-verification.md)：版本错误等有限实际边界，其它为源码/局部 | 不改真实授权版本或配置去执行原业务；同路径多映射的完整正常UI保存→发布→执行尚无本矩阵证据 |
| 角色绑定PUT全替换、PATCH增量；限流字段兼容 | **仅局部/源码；部分仍未实现** | [18条对应](26-10-31-deployment-integration-fixes.md)：PUT保原语义，新增PATCH add/remove；dailyLimit兼容dailyCount；perMinute仍不支持 | 真实角色增量及并发持久验证未做；不为验收修改现有用户角色，perMinute不能记通过 |
| 审计日志搜索、游标首屏与导出新词 | **部分通过** | [性能业务检查](26-11-01-performance-business-check.md)：真实搜索/空结果/结果筛选/游标清空通过；导出用当前kw仅源码核对 | 导出按钮未真实点击；不把源码正确写成导出文件内容通过 |
| 动作统计漏in_center、daily沿用seed | **已修，汇总/列表真实通过；daily仅局部** | [设备深验](26-11-01-deep-device-verification.md)及主会话：统一真实ActionRun，list scope=all保留权限；35572加载后UI总计1/100%/0/0，列表同run唯一 | daily图已渲染但每日数未独立读取核对；不能把总成功率100%称系统整体完成率或全部动作通过 |
| 数据库JOIN、列投影、目录查询、前端调度优化 | **结构已改；限定功能通过，收益未测** | [性能业务检查](26-11-01-performance-business-check.md)、[重启后验证](26-11-01-restarted-verification.md)：当前已加载并完成目录/对象/镜像读取；专项局部证据有引用 | 未采SQL数/EXPLAIN/延迟/吞吐/负载，不报提速百分比；军事求值与队列优化本轮不可做 |
| 部署入口、requestId、Chrome102、部署机升级 | **源码/本机部分证据；部署端待验** | [18条对应及部署说明](26-10-31-deployment-integration-fixes.md)：API-only默认、限定worker显式入口、日志合同、本机必要迁移与2K页面 | 不把本机通过当部署机完成；Chrome102、部署机独立迁移/通道未验；本轮不构建打包或启动worker补验 |

## 本轮独立只读源码审查新增边界

已阅读当前BuildAssistant、assistant/releases API、RealFunctions接收/保存路径及HTTP取消边界，没有执行任何检查。恢复指针确实不存源码，恢复重新GET并核对版本/锁；函数保存复用result.ver同步本体checkpoint。以下为原源码发现，随后已由助手agent按root授权修复；不声称实际跨空间写入已经发生：

- `RealFunctions.receiveAssistantDraft` 在消费并校验proposal后，先等待脏输入`confirmDiscard()`，随后才捕获`key=context()`/`captured()`。若等待期间身份、本体或版本改变，确认返回后旧proposal可能被当作新context继续处理；新建分支没有重新校验proposal范围，edit分支同ID/同版本锁也可能通过。需要在await前冻结上下文/表单意图，恢复后重新核验proposal范围及readonly/saving；本次常规切空间、编辑刷新恢复通过不覆盖这个等待窗口。
- `BuildAssistant.generate` 隐式中文编辑入口的前置`getFunction`只比context、fn与route.name，没有比前置requestId或完整路径；等待期间同fn切`form=new`或明确清对话，可能迟到重绑editing并继续生成。底层workspace epoch能拒绝跨空间旧HTTP，但不代替同空间路由/明确清对话的意图检查。该项也仅为源码审查发现，未操作浏览器重现。

两项现为**已修，仅针对内存断言通过，真实时序未复验**。[助手深验](26-11-01-deep-assistant-verification.md)记录：接收方在确认前冻结captured context/完整路径/表单快照及request序号，scope同步变更递增序号，await/GET/路由后重验共享isAssistantPrefillCurrent与readonly/saving；隐式GET冻结requestId/fullPath/key/version，旧finally不清新请求loading。新局部脚本覆盖scope round-trip、真实版本变化、同fn切new后迟到读取等；全部为受控内存API，没有真实时序重现。常规desc-only保存v1.0.1不替代竞态分支验收。

11月2日补充：[异步验收](26-11-01-assistant-async-business-check.md)已真实证明B2模型在途clear及其reload、C模型在途route隔离。它们不替代上段两个更短的前置GET/丢弃确认窗口，故不再泛称“所有异步未验”，也不反向把两个窗口涂为已验。

## 第三轮14项最终状态索引

完整根因、代码和逐项证据在[第三轮14项对照](26-11-02-third-round-summary.md)，不以本表数量推断平台完成率。

| 项 | 当前状态 | 精确界限 |
| --- | --- | --- |
| 1 周期doing/lock恢复 | **已实现，仅离线** | 当前登记周期/队列/白名单+120秒门槛+advisory活归属，官方failed不重放；无真实崩溃/进程竞争/旧jobs处置，部署先停旧workers |
| 2 FAILED事实 | **限定新run真实通过，告警部分离线** | run0002/call1540原因/code/receipt/未执行结论及QA整页恢复通过；无alert样本不覆盖失败告警联动 |
| 3 排队有效期/原条件 | **已实现，仅局部** | 默认0/显式有效期、可信来源、单目标当前条件/指标/关联、attempt0阻断有离线断言；真实超时和条件变化外发拒绝未验 |
| 4 明确目标/参数意图 | **16002/90正向trace真实通过，冲突离线** | 原提案/受控请求/决策一致；歧义/矛盾拒绝不冒称GUI通过 |
| 5 歧义异常干预 | **已实现，仅离线** | 完整授权者非唯一元数据可见、局部权限unique-only；没有拿真实旧歧义事件补投 |
| 6 发布头/formal | **当前正常读取合同通过** | 两空间current头int、空formal正常；旧null来源未补造，不扩大并发发布证据 |
| 7 版本去重 | **已实现，仅离线** | 事件/定时recent-hit限定rule_version，旧记录/原event幂等保留；无真实跨版本新事件样本 |
| 8 订阅公平 | **已实现，仅离线** | 事件按订阅轮次/近期服务+原next_at/id总200，页面每页head+近期服务总100；两槽仅拆外壳，无长压/吞吐实测 |
| 9 旧发布原因 | **已实现，仅离线** | EVENT_RELEASE_CHANGED_REQUIRES_REVIEW及中文/filter、preflight历史与原快照保持；未消费旧事件 |
| 10 终态unknown残留 | **本次FAILED清理真实通过，其它分支离线** | run0002 unknown=null、QA无旧说明；未知/继续查询保留，历史日志不改 |
| 11 canonical告警关联 | **已实现，仅离线** | 同scope/目标、冲突/受控retry/幂等与锁序；不自动回填旧关系，问数无alert样本不代表联动全链 |
| 12 当前值/事件事实 | **来源合同与提示，不是历史快照恢复** | 对象/关联读求值时当前行，事件字段读原持久payload；未提供的发生时属性不补造 |
| 13 源中间态 | **既有限制明确，完整历史未实现** | CDC按键读当前态，源未捕获的中间版本不能重建；需源持久变更事件/审计 |
| 14 循环轮次 | **显式业务键合同，不自动推断轮次** | 生产方提供稳定cycle/inspection键和独立eventId；未清去重/造历史/实跑双轮次自动链 |

root最后统一 `npm run typecheck` exit0，backend/frontend/docs `git diff --check` exit0；没有build/package/pytest/后台全量或真实队列检查。FAILED接收器已核身份停止，8767无监听，API52800/front66608运行；私有材料保留。证据白名单14张，最终manifest待本次文档收口后冻结，不含37个旧docs删除。

## 剩余边界（收口，不扩展任务）

- **实际未改/未实现：** ExternalSystems固定负责人/演示姓名；函数样例可编辑登记；definition规则元数据端点尚无现有UI消费入口；旧外部动作配额perMinute别名不支持（不是APIkey的ratePerMinute）；DM直连受控绑定、CDC跨表原子性及旧订单未接入关系/禁用动作等既有限制。指定旧审批文案/status显示已改，不再列未改。
- **已改但仅局部或精确分支未验：** 助手跨模块采用、真实身份切换、短前置GET/丢弃确认及脏表单同时覆盖；函数不兼容签名破坏式保存；动作daily每日数；规则definition白名单/scope/RLS实际读取；异常原因有样本命中、errorDetails新失败全链、审计导出。B2/C模型隔离与key完整生命周期已有真实证据，不再列待验。
- **限定业务仍未覆盖：** 设备日期区间筛选、定义再编辑保持、last_inspected_at实际写入，其它查询/图表/页面结构；key长期自然到期、其它网络IP/代理、全部quota窗口计数及高负载。当前本机office持久通道HTTPS/回执/查询/防重复通过，但生产第三方无实际URL/auth合同，不称通过；规则触发disabled，未证明规则自动触发此动作。
- **本轮未做：** 军事自动求值、周期队列真实崩溃/负载/旧积压处理、旧数据清理、部署端/Chrome102实机与量化性能测量；恢复/公平代码与其离线证据已在第三轮索引另记，不能仍泛称代码未改。只精确追加office scope16/EXT-N01局部授权/probe/唯一CA，未泛改原配置。没有全量后台、pytest、集成/迁移、全量build或benchmark。本矩阵更新不执行提交、推送或打包，用户新授权的分组提交由root统一处理。

本矩阵各次更新仅改本文，不重跑检查或业务操作、不读取private凭据或改其它agent独占报告；编写者后来另获授权的密钥UI/确认层级/纯文案/函数引用及最终类型修复，分别记录在[生命周期报告](26-11-01-key-lifecycle-business-check.md)和[引用修复](26-11-02-function-reference-fix.md)，不以早期“纯文档盘点”抹去这些新增工作。风险与限制以上表和精确剩余项为准，不以通过数量或单次100%成功率推断整个平台完成率。

上述为原owner各阶段记录；最终第三轮三份汇总由提交准备agent按root明确授权同步，没有覆盖owner独占专项报告，亦未追加业务或测试。
