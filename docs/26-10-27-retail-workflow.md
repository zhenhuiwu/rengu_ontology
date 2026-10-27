# 从零零售本体流程：隔离操作环境与证据（2026-10-27）

用户确认零售真实库主线与空本体隔离；只补平台，不改15项旧业务口径。主库 `rengu_ontology` 只读，`auto_ontology` 不使用。本文区分环境准备与 ROOT 实际前端操作，不把辅助脚本当成流程已完成。

## 最终状态（10月28日；下文保留分阶段证据）

- 真实前端已走完：空白测试本体→连接测试与内省导入→对象/属性草稿→独立身份审核发布→95门店/1286订单分页查询→发现展示订单号重复并经CR纠正唯一主键→关系/实付汇总指标→最近7天真实试算→标准/自定义事件→纯本地动作暂存及确认成功；共实际发布v2026.10.1–4。所有定义和状态由ROOT真实UI保存、审核、发布，助手没有后台填造成功记录。
- 固定10题已串行从UI执行；预算组核对provider8、pending0、halt=false、预留3.60元。第10题空白本体产品DATA_NOT_CONFIGURED、0provider；逐题结果与实际usage计价见[模型报告](26-10-27-model-smoke.md)，不宣称真实模型评测全过或旧15指标均可运行。
- Q6真实SQL结果固定为P001；本人站内订阅真实保存→手动刷新→outbox8→仅该页原consumer→通知7→铃铛点击、已读并跳回P001已完成。动作的两条事件仅持久化待投递，订阅方0，不冒称已投递；动作外部调用0，源事实业务哈希不变。
- 两处窄屏详情布局及共享变更弹窗/草稿恢复/校验假通过/标准平台事件/手动刷新订阅遗漏均按真实堵点最小修复。规则告警自动运行、业务源写回、外部通知/生产接口、本轮未实际操作的其它模块不算验收通过。空本体没有借用零售定义或实例。
- 最终主API32704与四worker已受控重载，health/ready均200，16表计数不变，主库schema dynamic_0030，无迁移/seed；隔离API38000在验收结束后已按ROOT授权关闭，预算账本和测试数据保留。下文“尚未”等文字如出现在早期步骤，描述当时阶段，最终结果以本节和末尾为准。

## 环境与初始状态

- 复用 `rengu_test_use_qa_20261027`，没有全量重seed、清库或迁移往返。启动时原37页、3条动作运行保留。
- 独立 API `127.0.0.1:8001`，前端由 ROOT 启动在5282；主8000/5280及四个worker不动。
- 本轮隔离进程使用实际当前日期/时区，不沿用旧测试固定10/25时钟；旧记录时间不修改。起初模型依赖明确503，后台worker数量0，真实模型只有管理组预算封装装载后才能调用。
- ROOT 已通过真实前端创建 `wf-retail-20261027 / 零售流程验收`，测试本体ID2，初始对象、关系、指标、发布均0；此ID2不是主库设备运维ID2。
- 允许 ROOT 另创建 `wf-empty-20261027 / 设备隔离验收` 作为真正空白对照。后续ROOT明确授权仅本人第10题所需问数历史写入：新建一个会话、固定问题“当前空间有哪些可查询对象？”（无引用）、本人会话标题更新；不允许定义、实例、动作、权限或绑定写入。真实空源守卫可以0模型调用拒绝，不能用profile的403代替产品隔离验证。

第1题后阶段隔离 API PID35564、launcher21424，启动日志 `%TEMP%/rengu-ontology/retail-workflow-20261028-003128.stdout.log` / `.stderr.log`，readyz实际返回`status=ok,db=ok`。ROOT与预算组确认第1题完成、无在途后精确重载，已加载建模组PK标记同步及标准事件合同；账本重载前后SHA一致、真实provider1次/预留0.45元/halt=false/pending=0，不重发第1题、无worker。没有重启主服务。

## 真实数据副本与只读边界

仅从主库本体1以一个只读一致快照读取两张实际表，追加到测试库 `public.store/public.biz_order` 的 `ontology_id=2` 分区，不是私有外库或另一份不一致的数据。主QA只读连接和显式对象绑定都指向同一测试库、同两张表、同一scope2；读取仍须通过RLS、当前对象权限和公开字段约束。

| 表 | scope2追加真实源行 | 业务字段SHA256 |
| --- | ---: | --- |
| store | 95 | `70b1f4b493a536577353c5e37bd40dc234fff39cfe3ff691749cf47c01998d4c` |
| biz_order | 1286 | `7ec3b3211b9ae9f3d9e27c5630484a59b8a0d8c364ad04f53066c2b0dd822e10` |

源/副本业务号、金额、日期等选定字段哈希一致。门店物理id重新分配，订单store_id全部重映射到新scope2门店，1286个关联均通过同本体核对。手机号与运单仅在副本清空，不复制会员个人事实；这些隐私字段和技术id不纳入上述业务哈希。真实主记录仍是本机现有数据，不声称来自新ERP同步。

测试旧scope1原门店98、订单1289（含此前隔离测试追加）在复制后均不变，旧查询不因新副本翻倍。复制没有添加任何本体定义、变更项、审核/发布状态、问数答案、指标结果或动作结果。

## 技术前置（不能冒称用户已走通）

- `WF-RETAIL-SOURCE / WF真实零售只读副本` 连接登记只服务本地QA库；初始状态为未测试，未伪写“连接成功”。ROOT 可以在真实界面测试连接、导入结构。
- 仅该隔离进程启用本地DS真实内省；连接器核对固定本地host/5433/测试库/登记凭据，强制READ ONLY、降权到现有受限role和本体2。其它目标连接拒绝，主服务配置与生产外呼开关没有改变。
- 内省导入只允许public的store、biz_order，取值样本关闭；导入预览与生成CR均走真实产品接口。数据源密码仅保存测试库密文，临时随机密钥和绑定文件位于用户TEMP目录，未输出或写入仓库。
- 对象显式只读绑定仅本体2的store/order；空对照和旧scope1不借用此绑定。
- 技术验收角色 `WF-RETAIL-ROLE` 仅在测试库新空间绑定对象权限，并配置现有测试副本用户U010001、U010002；U010002为新空间editor。普通产品鉴权、范围裁剪、自审禁止继续有效，profile只允许U010002在新空间执行审核通过/驳回，不允许修改旧空间。
- 已只读调用当前CR-0001的真实审核人目录，确认U010002为陈立军、确实可选；未替用户提交或审核。
- 现有前端`?as=`只有admin/viewer，不支持U010002。隔离profile提供同源`/api/_workflow/identity/reviewer`及`/owner`入口，用HttpOnly本地cookie选择实际X-Demo-As身份，随后仍由原CurrentUser、成员和审核权限校验；不是readonly preview，也没有改生产登录或主库权限。ROOT须先本人提交，再切陈立军审核，回本人发布。

代码入口：`backend/scripts/retail_workflow_ui_server.py`、`retail_workflow_prepare.py`；复用`qa_use_test_server.application`的可选workflow写入守卫，原profile不变。准备脚本第一次因空异步生成器断言错误中止于事务内，修复后重跑成功；没有留下半份业务数据或虚构流程状态。

## ROOT 当前可操作的顺序

1. 当前scope2取消尚未保存的空对象表单，选择“从数据源导入”。连接选WF真实零售只读副本，schema public，表store、biz_order，保持取值样本关闭。
2. 对象API/稳定ID分别取小写`store`、`order`。来源实际列为：门店`id/code/store_no/name`；订单`id/code/store_id/created_at_biz/amount/status`。不要把`ordered_at/pay_amount`当物理列名。
3. 在真实草稿表单核对公开属性；门店主键选唯一`code`，订单必须选唯一`id`（下文实际发现订单展示码重复）。保留订单code作标题/展示、store_id作关联依据，唯一id须能作为主键输出。定义及字段映射由前端保存CR，助手未代填。
4. U010001提交，选择独立审核人U010002；切换实际测试身份审核，再回U010001整版发布。不能让提交人审核自己。
5. 定义实际发布后再在UI建关系与新客观指标，指标建议“验收订单实付金额汇总”和“验收订单数”，仅SUM实际amount、COUNT订单；不重定义销售净额或旧15项口径。范围默认复用构建组已批准的`rolling_days:7`，仅发布后生效。
6. 对象查询、实际试算与QA用同一scope2源对数。正常受控SQL表格问数有真实计划时可固定页面；专用来源裸指标直接汇总如果返回`canPin=false`，应明确不可固定，不能造SQL或计划伪装支持。
7. 后续页面刷新/站内订阅与本地事件动作只在该隔离空间实际操作。预算模型、规则/动作触发、通知消费需要分别记录真实结果；目前未宣布这些步骤通过。

## 当前未执行边界

ROOT 已实际点“测试连接”成功，显示正常/41ms；产品自动内省后的库表目录为public.biz_order1286行19字段、store95行19字段，均尚未映射。随后通过真实导入预览，在scope2保存 CR-0001“接入真实零售门店与订单”，54处改动、order/store各19属性，没有勾选FK关系；尚未发布，正在真实草稿调整主键与公开字段。不是助手后台填定义或成功状态。

随后ROOT已实际保存两对象草稿：PK均code；门店name标题/store_no展示；订单code标题、主要时间金额字段分组/筛选/列表；个人和技术字段标敏感。此时仍未提交/审核/发布，不能宣称对象已可查询。

ROOT随后实际完成独立审核：王敏选陈立军后提交，发起人界面无审核按钮；切真实陈立军后审核通过，再回王敏。首次发布前发现校验误报，暂停发布，等待修复后的重新校验/审核；没有绕过自审。

### 校验状态误报的最小修复

真实UI初次校验显示0阻断/1警告/11通过/5跳过，V14“各目标库EXPLAIN通过”为空说明且页面硬编码DM8/KES。源码确认未执行的检查从统一fallback获得假通过；没有目标库EXPLAIN执行器，指标试跑也不能冒充EXPLAIN。现fallback统一“不适用/当前校验器未执行此项”，V14明确未配置执行器/未执行；V7、V11、V15仅按实际本轮已检查条件给结果，V3/V4/V12已有执行结果保留，旧种子阻断不洗掉。页面删除硬编码目标库，改为逐项说明。

定点命令：`python -m pytest --noconftest tests/test_cr_check_execution.py -q --tb=short`，2 passed/0.86s；无DB、无外库、无provider。覆盖未执行V2/V14跳过、实际V12通过、原有V14阻断保留；Ruff F/I通过。旧指标试跑用例只调整其V14期望为未执行EXPLAIN（本轮未重跑该PG用例）；真实试跑不可用仍在detail中保留，未伪造成功。V1/V2等跳过项不能称外源主键/唯一性已执行验证。

本次仅重启隔离8001加载该修复，确认旧预算进程退出且0调用后清旧lease，预算JSON未重置；重载后仍0provider/0questions/halted=false。原APPROVED单按真实状态契约须撤回审核结果后重校验/复审，不能宣称历史校验已更新。

ROOT实际撤回并重新校验后看到0阻断/1警告/2通过/14跳过，V14明确未执行EXPLAIN，硬编码目标库已消失；正在按真实流程重新提审/复审。后续用户要求补齐可复用的V1/V5真实结构校验，`cr_write.py`已交建模组独占该两分支，我方fallback保持冻结，未中途再次重载API。

发布确认中原“V1–V17通过”仅表示各单无阻断，易误读为17项均执行；前端改成“各单无阻断（未执行项见单内校验）”，不改公共检查编号、合并校验结果或发布门槛。

### 首次发布与读取实际证据

ROOT重新提审/独立复审后，真实整版发布v2026.10.1成功；页面显示首个发布版本、无上一版可回滚，没有发模型问题。发布后本地只读事务核对：仅order/store两个published定义，PK均code；RLS下门店95、订单1286。构建原问数上下文约12176字节，0provider；指标/关系说明仍0。受治理登记还包括随order继承的order_line物理表，scope2没有复制其事实/定义，不把它当已建第三对象或关系。

对象页首次只读搜索被测试profile错误按POST拦截，非产品权限拒绝；已精确允许新scope2的store/order搜索POST及scoped-ask只读聚合，关联GET原可读，空对照与旧scope不扩大。仅8001重载后，原产品搜索实际返回store total95/order total1286、均page1/size10/items10/bound=true，未输出私有行内容或写业务数据。此为受控接口只读核对，ROOT前端重新查看结果另记，不将profile修复当产品业务bug。

题1/3/4/5/7的客观事实核使用同scope2降权只读事务：上海时区10/20–27含起止日共1286订单，原amount字段SUM=387245.43；门店95，全部is_test=false。订单按日10/22=328、10/23=380、10/24=347、10/25=231；按状态cancelled136、delivered243、pending_payment140、pending_shipment144、picking132、shipped491。未另加状态过滤，不将该技术字段汇总当作新销售净额或修改旧15项业务定义。首次辅助SQL因day别名触发语法错误，改day_bucket后只读核对成功；这是辅助检查错误，不是产品bug或模型结果。没有把事实核写成问数答案或指标结果。

门店主键核对：scope2的code/store_no分别95个唯一值且无空；例如稳定code为SH0031，展示门店编码store_no为SH-PD-0031。保留两者原业务值，code作为对象PK、store_no作为门店编码展示。显式来源读取仍以用户实际发布的主键属性解析，并非辅助脚本代设定义。

### 订单展示码不唯一（实际流程发现，待真实CR纠正）

ROOT前端看到订单号分面有两组计数2，暂停建关系/指标并要求核对。scope2降权只读结果：1286行、1284个不同code、1286个不同id，code/id均无NULL，重复code组2；门店95个code仍唯一。不是跨本体分面污染，复制保留了真实源业务值。助手此前只核门店后建议订单也用code作PK过早，现已纠正；不改业务号、不删重复行、不改金额/日期，ROOT应通过真实CR把order.pk改id，code保留标题展示。

对象读取适配器支持按发布PK解析；现动态/动作执行引用仍按受治理表code核唯一性，歧义code会拒绝，不静默任选记录。因此本轮本地动作闭环应选code唯一的store；order的通用id动作运行契约还不能冒称已接通。此为平台技术边界，不需用户另定业务口径，不影响COUNT/SUM对1286行的客观汇总。

建模组V2真抽样校验加载后，ROOT完整前端正负复验：将已发布order加入同CR0002，PK暂保留code实际校验得到“授权样本1286、重复2组、空值0”阻断，截图名`05-duplicate-pk-blocked`；随后真实UI把order.pk改id并保存，同单重校验V2通过“1286授权样本内无空/重复，仅样本不代表全源唯一”，V1结构通过。此时只剩尚未填写的L-0001关系V3阻断，订单PK修改仍在草稿未发布；源事实、金额、日期不变。不是助手改定义或删除重复行消除阻断。

### 关系草稿交互定位

ROOT通过真实入口登记新关系L-0001到CR-0002（0改动），未保存关系定义。填写部分字段后复用旧AX/index点击，出现另一“发起变更”弹窗；源码核对源对象select仅切换属性，没有ensureDraft/发起变更事件。最初仅把它归为工具误定位证据不充分：随后HMR后实际出现“关系类型不存在”、表单消失，属于必须继续处理的草稿恢复问题。ROOT改用fresh DOM语义定位，未重复进入草稿或重建CR。为可访问性及稳定定位，给四个原select增加“关系源对象/关系目标对象/关系源端属性/关系目标端属性”aria-label，SFC模板编译与标签检查通过，无视觉、权限或流程改动。

只读真实API确认CR0002仍草稿/0changes，目标L-0001/isNew=true，draft-targets目录也完整。共享确定缺陷在`useDraftView.resetDraftState`：清缓存后只监听身份/本体字符串，同上下文重置不触发目录重载，useStaticCr失去CR header后便可能读取尚未发布的定义。最小修复用响应式resetEpoch触发持久目录重载，保留原页面初始加载guard；不恢复尚未保存字段、不代写定义。`node scripts/check-draft-registry-reset.mjs`直接执行两个真实共享helper，验证0items新target恢复、同上下文重置恢复、切空本体不继承旧CR、切回恢复，全部通过，无HTTP/DB写。ROOT随后真实刷新L-0001已恢复“未命名关系/CR0002”，无404，点击编辑定义成功，正重新填写；此部分恢复已有实际UI证据。

四个aria-label在实际AntSelect中落在外层div，combobox输入仍无名；只读核库支持Select.id→input.id及FormItem.htmlFor→label.for后，待关系实际保存安全点才补四组html-for/id关联并去掉无效外层aria-label。SFC编译与四组关联检查通过；真实输入可访问名应为源对象/目标对象/源端属性/目标端属性，最终DOM命名待ROOT复核，不以模板检查冒充实际可访问性已验证。

ROOT随后在实际1172宽工具视口发现关系定义两面板布局遮挡：右侧固定440px预览将编辑表单压缩到约150px，目标下拉被覆盖。此为实际CSS问题，不是工具定位问题。仅热更新样式：定义面板auto-fit、最小440px不足时堆叠，字段最小280px自适应单列，右侧取消固定宽并限制intrinsic溢出，编辑体可纵向滚动；未改模板/脚本，保留当前未保存字段。CSS编译通过，实际布局恢复待ROOT后续核对，不冒称1920/2560全尺寸已验证。

ROOT随后真实确认CSS堆叠后目标输入约165px、可点击且无遮挡，当前输入保留，关系保存到原CR0002成功：L-0001“订单所属门店”、API wf_order_store、order.store_id→store.id、N:1、反向“门店订单”，同单共16处改动；界面V3/描述/单路径通过、无error。未重新创建CR，尚不能把草稿关系称为已发布可用关系。此时才进行上述模板a11y补丁，避免清空未保存输入。

### 新指标真实草稿

ROOT继续在同CR0002保存wf_order_paid_total（验收订单实付金额汇总）：分组流程验收、owner王敏、元/v1，公式SUM(order.amount)，时间order.created_at_biz，默认过滤空，defaultRange={kind:rolling_days,days:7}。详情实际显示公式/时间/最近7天，试算按钮可用；CR2共27处改动（订单PK、关系、指标），尚未发布。现有手动试算UI请求固定近30日，和问数默认7日不是同一合同；已明确提醒ROOT不能因本批数据都落10/22–25、汇总相同而把30日验收写成7日。未代发试算或保存结果。

新PK一般查询仍使用原ObjectSource/Bound：发布后由pk_property解析id，numeric id_value及原row predicate/RLS继续生效；A V2正向抽样已通过同一Bound.read/stmt读取草稿id。当前一般搜索仍按已发布v1定义，不能提前把未发布id当生效；待CR2发布后再核唯一行id/详情。动作运行仍保留上文code契约限制。

ROOT随后真实独立审并发布CR2=v2026.10.2，订单PK=id/关系/新指标生效。只读核对live+snapshot均pk=id；原对象搜索两页各10、20个numeric行ID唯一，总数1286；真实Property的展示订单号名称/说明已更新、敏感字段仍敏感，metric live+snapshot为v1/SUM(order.amount)/order.created_at_biz/default7/空filter。另发现旧Property.code.is_pk仍true、id亦true的派生标记不一致，已交建模组补canonical同步；当前Source明确pk_property=id所以实际读取无歧义。同步补丁已加载，但不后台洗现记录，等ROOT真实CR3保存对象说明并发布后再验证标记。第1题门店总数不受此项影响。

建模组随后补手动试算days=1..93的严格API/UI合同（兼容旧range30d），ROOT确认安全点后已重载8001；现在可以实际选择7日试算，但尚未把接口/组件定点测试当成ROOT UI已通过。实际时钟跨到10/28，默认7天为10/22–28；固定题3–7仍是10/20–27。本轮空间code/题单/预算JSON沿20261027路径保留，不另开或清零账本。

ROOT随后真实7日试算通过：输入初始7，请求成功显示“已按真实数据完成试算”；caption为10/22至10/28、时间order.created_at_biz、草稿CR2、数据截至10/25，图为4日实际折线，截图`08-real-seven-day-trial`。没有为取结果补零/改值/调用模型。此前完整前端build（含tsc）27.02s、4490模块通过。

同次实际1172宽视口又发现MetricsBody的固定440px图表把左定义挤到约145px。无可安全单处修改的共享定义面板样式，因此仅本页scoped CSS复用Link auto-fit/min440堆叠，右图min0并限制intrinsic溢出、标题/toolbar可换行、长公式可断行；没有更改A的TS/default/trial/template，也未重算已成功结果。CSS编译通过，最终实际布局待ROOT复核，不重复全构建或后台测试。

ROOT另通过UI创建wf-empty-20261027“设备隔离验收”，真实本体ID3，空总览各定义0、未发布；实际对象搜索无error/0行，明确当前本体暂无可查询对象，截图`07-empty-workspace-isolation.png`。随后只读权限核对owner001成员role=owner、qa-workbench=manage、objectGrants为空、本体绑定角色0、本人会话0；无需补权，没有创建会话或发题10，也未添加源/业务数据。本人固定题10历史例外仍交原产品空源守卫，不用profile403替代隔离验证。

## 后半段合同与实施进度

最初提出的本地闭环合同如下，后续实际完成证据另见文末；选code唯一的store，不改业务源、不外发HTTP、不启全空间worker：

1. 经真实UI/CR发布wf_store_reviewed事件：来源动作、对象store、v1，checked布尔必填，事件ID去重、24小时窗口。
2. 经真实UI/CR发布wf_record_review动作（与技术预备角色的确切操作授权一致）：对象store、owner王敏、审批免审、策略`{"mode":"confirm","callers":["人"],"overflow":"stage"}`，无参数、条件或业务编辑，外部系统留空。只加事件效果：

```json
{"kind":"事件","target":"wf_store_reviewed","when":"执行成功后","note":"{\"version\":\"v1\",\"object\":{\"context\":\"id\"},\"payload\":{\"checked\":{\"value\":true}}}"}
```

3. 动作中心选择该动作及真实门店、参数`{}`，原校验→暂存→本人确认路径。现DatabaseActionSubmitter对sysId空的本地定义走INTERNAL，原emitter同事务记录成功事件及自定义事件；无需调用生产外部通道。预期须由实际记录证明succeeded/INTERNAL、事件持久化和WritebackCall=0，不预写成功状态。无订阅时事件仍可待分发，不冒称已投递。

已确认的技术前置缺口：原emitter无条件经receive写ActionExecuted，要求当前本体有已发布的标准事件、objectApi=ActionRun；新空间不存在该定义，现新建事件UI仅列业务ObjectType，不能配置平台ActionRun。旧测试helper曾直接准备标准事件，不等于从零UI可完成。建议只补受控平台对象/标准事件发布合同，仍由ROOT真实CR创建、审核发布，不借旧本体或后台seed填成功；未新增执行器。

ROOT授权的最小基座补丁现已实现并加载：event/platform_context.py固定`platform:ActionRun`，仅ActionExecuted+来源动作允许；NewActionEventForm在这两个条件成立时才增加“动作执行记录（平台）”候选；保存hook与原event def/payload发布hook复用同一校验。持久objectApi=ActionRun、objKind空，与既有emitter合同一致，没有创建业务ObjectType、开放内部表查询或后台生成事件。载荷仅允许runId/actionType/targetId/initiator/confirmer/writeback六个字符串键，runId必填，其他可选；推荐真实UI声明六键。原receive只检查已声明字段，不拒额外键，因此只声明runId也不会把真emitter另外五键误拒；自定义事件的额外键拒绝仍保留。

针对性验证分轮：`pytest --noconftest tests/test_event_platform_context.py -q --tb=short`，7 passed/1.38s，无DB；未知平台对象、错误API/来源、缺runId/错误类型、无event-manage权限均拒绝。`scripts/run_use_tests.py --db rengu_fix_qa_20261027 -- tests/test_event_platform_publish.py -q --tb=short`，初轮1 passed/0.59s，加入原HTTP来源防伪断言后同一case复测1 passed/0.65s（不相加），实际CR保存不提前建定义，原发布hook持久化ActionRun上下文，不增加ObjectType或Event结果；HTTP伪造标准完成事件仍EVENT_SOURCE_NOT_EXTERNAL403。仅独立fix库事务记录，未清旧数据/改主库；未跑迁移往返。`node scripts/check-platform-event-context.mjs`及SFC编译通过，含候选仅标准动作事件出现、外部来源/其他API不可选、改条件清掉陈旧选择。首次脚本因同一tick内切两次条件导致watch未触发，按实际UI分tick修正后通过，未为测试改产品授权。

ROOT第1题已从真实前端发送并返回95门店，预算组只读确认1次provider、HTTP200、usage3492输入/21输出/0推理，预留0.45元；没有SSE/W6重复。新进程载回完全相同账本而不是新开额度。这是第1题完成时的记录；随后模型题目继续串行进行，最终题目结果及预算由预算组单独记录。两事件同CR3、动作随后CR4经真实UI发布，见下文。

本人站内通知当前真实支持CR审核与页面订阅，模型CheckConstraint仅cr/page两源；动作事件→本人inbox桥尚无实现。效果“通知”实际走外部受控投递，不可当站内信。可另以真实页面刷新/本人站内订阅验证现有闭环，仅处理newscope2新page相关outbox；本轮不为动作通知新增结构或伪投递。本段描述能力边界；后续已完成本地动作与事件记录，页面通知尚待当前修复加载后实际验收。

另精确放行retail scope本人与独立审核人的原通知标已读接口，仍由产品核当前recipient；不开放空对照/旧scope，不把profile例外当新增通知授权。该项已随本次安全重载生效，尚无实际通知读写验证。

首次导入Schema空不是内省权限错误：组件GET的是持久库表目录，技术DS尚未测试所以返回空。按真实流程先测试连接后目录有数据。补了空目录普通提示和前往数据连接入口，不自动外连；`check-object-import-empty.mjs` 已验证提示、导航scope/版本保留及未保存保护。测试连接后详情正常、侧栏仍旧状态的问题已最小同步同id列表项，`check-data-connection-state.mjs` 覆盖正常同步和上下文迟到不覆盖。

对象、关系、新指标和两事件及动作已真实审核发布，新指标7日试算、本地动作已完成；模型题目按独立预算进行。没有真实生产外部HTTP写入、生产动作或后台评测；模型10题/最多1失败重试/10元预算由管理组独占封装。普通HTTP出口关闭，只有预算专用官方出口例外。没有运行全量pytest、迁移往返或后台长回归。

隔离server已接可选`--enable-model`入口：只有显式带该开关、TEMP中固定十题文件存在且预算helper核验通过，才在生命周期内装载预算客户端并在关闭时释放；普通启动仍明确503。ROOT授权的本次重载已装载固定十题和预算，题8为“验收订单实付金额汇总”、题10为“当前空间有哪些可查询对象？”。最初启动时只读账本providerCalls=0/questionRequests=0/halted=false，后来已实际发题，不能把该启动计数当最终计数；预算账本不重置、无usage按管理组封装立即停机。不能把接线视作十题执行通过。

空对照profile守卫做了9个纯本地受控检查，覆盖固定问题/本人标题允许，以及不同问题、非空跨域引用、非本人会话、重复新建、范围修改和动作路径拒绝；使用替身仅检查测试profile自身，无DB/HTTP/provider请求，不声称产品问数或真实鉴权已通过。预算组另只读确认顶层导入不提前缓存主库配置，生命周期接线无确定阻断问题。

## 10月28日真实发布与本地动作证据

ROOT在CR3通过UI保存标准ActionExecuted六个字符串载荷键（runId必填）和自定义wf_store_reviewed（checked必填布尔），并保存store/order描述改动。独立陈立军审核后，owner发布v2026.10.3；没有后台代建定义或修改源记录。新建表单缺省Boolean props被Vue转为false，使StartChangeModal成功后仍留屏，引出下一次新事件空id/422以及对象重复弹窗；共享组件显式默认closeOnOk=true修复，保留调用者显式false异步流程。实际组件检查通过，ROOT随后自定义事件保存关闭正常，无VALIDATION_FAILED。新事件草稿未发布前仍不加入已发布目录，可从createType/createCr恢复，不宣称目录已展示草稿。

ROOT随后CR4通过UI保存、独立审核并发布v2026.10.4：wf_record_review、store、owner王敏、人工发起、免审，无参数和外部系统，事件效果引用已发布wf_store_reviewed。新建表单尚无策略段，未持久化策略，运行侧沿既有安全默认confirm/stage，不假称显式配置了callers限制；配置sysId=null、endpoint=—、idemKey空是纯本地动作，不填伪外部地址。

独立只读事务分别核live与最新v2026.10.4快照：order.pk=id、store.pk=code；live Property的is_pk分别仅[id]和[code]，CR3正常发布已应用canonical同步。标准事件ActionRun/objKind空/动作来源/六字段，自定义store/动作来源/一字段；快照对象与事件上下文一致。主库旧15项业务定义未写入；测试库旧scope1已有16指标（含旧测试记录），不把它误称本轮新增或主库15项。

ROOT在动作中心实际选择store，校验、暂存、本人确认，ACT-20261028-0001成功，界面“本体内部动作已完成”。只读数据库确认该run succeeded/INTERNAL/manual；标准ActionExecuted一条（source=dynamic）、自定义事件一条（source=action.effect），两者pending、订阅方0；WritebackCall总数0。未启worker或执行外部请求，也不将待投递记录冒称投递成功。再次读取scope2的95门店/1286订单，选定业务字段SHA与原复制manifest均一致。

实际截图`11-local-action-success.png`、`12-real-action-event.png`暴露1172宽布局问题：动作页44px工具栏不换行使radio页签被筛选挤成竖排；事件标题被超长ID挤压。仅局部CSS使工具栏与筛选换行、页签不收缩，抽屉标题/状态不收缩、ID可省略换行。两SFC/CSS编译通过；ROOT最终settled截图`13-local-action-layout-fixed.png`、`14-event-drawer-layout-fixed.png`确认横排、完整关闭按钮、文档无横向溢出，不把动画中间帧当新故障。

## 手动刷新后的站内订阅遗漏修复与实际通知

ROOT已从第6题真实SQL结果创建scope2页面P001“验收订单明细页面”，一张卡、仅自己；真实UI保存手动刷新、站内信默认owner、每次刷新后、不要求变化。未将第8题无SQL计划的专用指标回答固定为页面。

源码确认原定时refresh_page_once会发送afterRefresh事务outbox，手动POST :refresh只更新缓存/审计，导致同一订阅配置无法由用户立即刷新触发。最小抽取emit_refresh_subscription供两条入口复用，仅成功、启用站内信、刷新后、满足onlyChanged才emit；失败保留旧缓存/时间，禁用/无订阅/未变或旧邮件配置不产生通知。缓存、审计和outbox仍共用调用者事务，不内部commit；outbox失败向外抛出而非假成功。未修改共享页面当前身份/对象权限、卡片执行器、只读SQL或外部投递配置，也不在pin/create/template/view等其它执行调用者顺带发通知。

定点无DB测试实际调用manual handler：初轮6 passed/3.23s；补outbox失败透传后同文件7 passed/1.95s（覆盖前轮，不累加），命令`python -m pytest --noconftest tests/test_page_manual_subscription.py -q --tb=short`。Ruff F/I通过。以上定点检查完成后，按ROOT与预算组确认无在途请求的安全点重载8001，随后ROOT实际触发刷新，再仅处理P001新outbox，见下文；没有启动全局worker扫描旧记录。

预算组完成十题后确认pending0/provider8/halt=false/预留3.60元（实际usage计价及逐题结果见模型组报告）。经ROOT授权仅停止核验过的35564/21424，确认旧PID全退出后清其残留lease，预算JSON/题单/DS密钥未改；账本SHA重载前后一致。新隔离API38000/launcher33580、readyz=ok/dbok，日志`%TEMP%/rengu-ontology/retail-workflow-20261028-005809.stdout.log`/stderr；不带enable-model，后续模型依赖503封账，不修改halt/计数伪造预算状态，后台worker0。

ROOT在P001实际点击一次立即刷新，UI提示已按当前权限刷新、数据截至10/28 00:58。只读确认scope2待处理页面订阅恰一条outbox ID8，pageId=P001、channel=inbox、to仅owner U010001；随后在仅该范围内调用原dispatch_workspace(limit=1)，真实提交后outbox已dispatched/error空，新Notification ID7/source_kind=page/target=P001/read=false。未动其它topic、旧scope1或空scope3，不代替ROOT点刷新/铃铛；通知仅通用更新提示，接收方重新按当前权限查看页面，不转发owner缓存摘要。ROOT随后实际看到铃铛1条通知，点开跳转page_id=P001&ptab=preview、badge清零；独立只读确认通知7的read_at非空。截图`15-page-subscription-notification.png`。

该截图同时暴露MyPages只有1条数据时未挂条件paged-scroll类，表格无滚动边界而撑宽document。先给已有mp-table一行CSS固定min-width:0/overflow:auto，ROOT确认document宽1172与viewport一致，但名称列仍0宽：其它固定列合计1420px，唯一无宽的名称列被固定布局挤空。再补名称列240px与原Ant表格scroll.x=1660（240+1420），由内置内容区横向滚动，不改列表/权限/分页/订阅逻辑。SFC/template/CSS编译通过。ROOT最终复核document=viewport=1172px、名称th=240px、Ant content client899/scroll1660/overflowauto，页面名称恢复可见；截图`16-page-table-layout-fixed.png`已覆盖为最终状态，通知已读无badge仍保留记录。

## 主服务最终安全重载

ROOT授权后只读核对main本体1/2无queued/claimed外部调用、processing事件投递、querying对账或未分发outbox，Procrastinate无todo/doing；仅本体1有11条10/23–25历史is_seed=true的executing展示记录，未篡改它们。精确校验原主API/四worker子进程与launcher命令后一次重载，保留5280/5282/8001，未停止其它agent进程。

新主API32704/launcher34368；integration16764/15084、event23072/49524、data27704/24732、dynamic31944/18620。API无reload，healthz/readyz均200，日志`%TEMP%/rengu-ontology/{backend,integration,event,data,dynamic}-retail-workflow-20261028-005845.stdout.log`及stderr；APIstartupcomplete，四worker存活，仅既有Windows信号处理/顶层实例化提示，无错误traceback。既有外联开关均false，不启QA-eval/每日模型/新开关，无迁移或seed，schema仍dynamic_0030。

重载前后16表计数完全一致：空间2、对象18、属性211、关系27、指标15、订单1286、明细3905、门店95、商品12、动作1991、事件1243、告警154、CR19、通知0、页面33、APIkey0。主库没有导入本轮验收工作空间、业务副本、定义或执行结果。

验收完成后经ROOT授权，精确核对并关闭隔离API runtime38000/launcher33580，8001监听0；QA数据库、TEMP DS密钥、预算账本与题单保留，没有清库/重置计费。主API32704及四worker仍存活，前端5280保留；临时5282由ROOT另行关闭。本文产品、针对性检查、独立只读证据均已冻结，无额外全站检查。
