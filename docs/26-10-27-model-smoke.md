# 10题真实模型小批量验收（2026-10-27）

## 授权和当前状态

用户已选择1A/2A/3A/4B：零售真实主线+空本体隔离、仅补平台不改旧15业务口径、按指标已发布defaultRange、10题串行真实模型验收。**本轮模型总费用硬上限10元人民币，每题失败最多1次重试，不启每日评测。** 基线125dcbd，执行工作区`D:\项目\rengu_ontology`。

**固定10题已全部通过真实UI各提交一次，整轮结束，无在途请求。** 第1题由ROOT提交，第2–10题按ROOT明确委派由C从独立临时标签页提交；没有CLI代发、额外探活或第11题。第1–7题真实查询与本地只读事实一致；第8题使用已发布指标默认7天直接计算；第9题通用拒答且未返回个人数据，但不能据此证明敏感字段权限分支；第10题在真正空本体由产品DATA_NOT_CONFIGURED拒绝。

最终provider调用8次、各HTTP200且usage完整、无重试，输入28627/输出577/reasoning0 token；持久预留3.60元、按官方最高档计算的usage牌价上界0.060999751元，halted=false、pending=0。**未查询实际账单，不把预留或牌价估算称实付。** 未启后台评测/判分模型/每日任务。下文的0调用、尚未发布等均为当时准备阶段记录，不代表最终状态。原TEMP账本/题单保留，不提交版本库。

## 已确认的模型和凭据来源

- 当前共享平台配置`model:qa`：`qwen-flash`，已启用，30秒超时，官方endpoint `https://dashscope.aliyuncs.com/compatible-mode/v1`。平台没有保存独立secret，凭据来自部署ignored `backend/.env`。主库仅用只读事务查询公开配置与秘密是否存在，不写设置。
- 用户提供的另一个ignored文件`D:\项目\palantir-ontology\auto-ontology\.env`使用`DEFAULT_MODELS_*`等名称；同一官方endpoint，模型为`openai/qwen-flash`路由前缀形式，凭据已存在。没有因此更换当前模型、key或地域；不输出/复制秘密到报告、前端、版本库或命令参数。
- 在内存中只比对两来源的凭据是否相同，结果sameCredential=true，未发现常见占位字符串；没有输出密钥、前缀或哈希，也没有因此发送探活。
- helper固定当前模型和官方地址，配置不一致时先拒绝，不擅自换便宜模型或回退别的provider。备用文件会在本地内存解析；只有部署凭据确实为空且模型/地址一致时才采用备用凭据。

## 官方定价和费用封顶证明

2026-10-27实际浏览阿里云[Qwen-Flash模型页](https://help.aliyun.com/zh/model-studio/qwen-flash)及[官方模型价格页](https://help.aliyun.com/zh/model-studio/model-pricing)。已配置的北京地址，输入≤128K时原价为输入0.15元、输出1.5元/百万token；北京最高档为1.2/12。本轮**进一步采用官方各地域所列最高1.835/14.678元/百万token**，不依赖缓存、免费额度、Batch折扣或汇率优惠。

实际跨10/28后、首次调用前再次打开同一官方模型页复核，所用最保守1.835/14.678价格仍一致；没有改模型、地域、题数或原轮次预算。

Qwen官方说明采用[字节级BPE](https://github.com/QwenLM/Qwen3/blob/main/docs/source/getting_started/concepts.md)。临时helper只接受最多32条纯文本role/content消息，其UTF-8序列化总长≤96KiB；任何多模态、tools、schemas或其他非固定请求字段直接拒绝。正文token以字节上界计算后再翻倍，并留4096个模板/控制token余量，保守输入上界`2×98304+4096=200704`。

每次固定`n=1`、回答上限4096token，按[官方Chat参数](https://help.aliyun.com/zh/model-studio/qwen-api-via-openai-chat-completions)显式`enable_thinking=false`、`enable_search=false`。不上传音视频、不创建缓存、不启工具或搜索；思考token如意外出现会停止后续调用。

```text
单次最坏上界 = (200704×1.835 + 4096×14.678) / 1000000
             = 0.428412928元
每次预留     = 0.45元（失败/超时/取消也不退）
请求总数     ≤ 10题 × 2次 = 20次
本轮总预留   ≤ 20×0.45 = 9元 < 用户10元上限
```

不能容纳的长prompt和第三次链内调用在发网前阻断。一次链内口径纠正、SQL修复、空结果修复、澄清纠正或HTTP失败均消耗该题的同一2次上限；不是每个分支再给一次。标准SDK原客户端`max_retries=1`在本轮不用，临时SDK固定`max_retries=0`。

## 预算与出口控制

新增`backend/scripts/model_smoke_budget.py`，仅受控临时API显式装载，不改主.env、QA核心客户端或主API依赖。

- `configured_runtime()`仅从已批准ignored来源和共享设置读取现配置，不请求provider。
- `SmokeBudget(runtime, questions, ledger)`固定10个不同题目和持久账本，FastAPI覆盖`get_llm`为`budget.dependency`。请求级串行锁覆盖SSE全响应；provider transport另设串行锁，**锁内先持久预扣再联网**。
- 账本固定同一轮`%TEMP%\rengu-ontology\model-smoke-20261027-budget.json`。原子替换+fsync记录请求、题号、预留及usage；启动加载已有计数，题目/模型/地址/凭据指纹不一致时拒绝。相邻`.lock`防止两个进程各自重置余额；异常退出残留锁必须先确认旧PID结束才移除锁文件，预算JSON不得删除重建。
- 该临时进程普通HTTP/AsyncHTTP transports全部禁用；只有预算SDK的专用transport可到固定官方`/chat/completions`。其他QA/MCP/eval、embedding、判分、本体生成、工具搜索及外部业务HTTP出口均不能旁路。ASGI内部请求仍只走本地应用。
- 只记录provider返回token usage和按最保守牌价推算的上界，不保存prompt/回答正文、API key或原始异常字符串。没有usage时保留预留、停止后续调用；超出任何token界限或出现思考用量同样停止。
- HTTP非2xx、超时或取消同样没有可信usage，持久停止本轮，不自动使用剩余重试名额；“最多1次重试”只允许已取得合法usage后的SQL/口径修复等已知用量失败，仍受每题总2次限制。
- 计数是本轮授权进程的预留上界，**不是阿里云实际扣费或账单**；最终报告会分别列usage、牌价上界、失败请求预留及总9元边界，不能把预留写成已付金额。

现有调用点核查：QA链统一`llm.generate_sql`，同链有口径、SQL报错、零结果、澄清与metric fallback多步回喂；以上统一受限。本体生成另用httpx，其他QA eval/MCP入口能创建标准SDK，但不在测试server写路由白名单内，标准transport也被禁止。临时环境无worker，真实动作/ERP/Kafka等外部执行开关继续关闭。

## 无网自检

工作目录backend：

```powershell
$env:PYTHONPATH='.'
.venv/Scripts/python.exe scripts/check_model_smoke_budget.py
```

已通过：每题两次/第三次拒绝、全局20次与9元、长prompt发网前拒绝、缺问题上下文拒绝、重启加载原计数、超时预扣不退且SDK仅发送一次、缺usage停止、其他HTTP出口拒绝，以及真实FastAPI依赖+SSE响应生命周期仍在预算上下文内。所有响应均为明确的本地协议fixture，真实provider调用0；没有据此声称模型回答正确。两helper Ruff F/I和格式检查通过。

## 真实UI执行记录（准备阶段及最终结果）

运行组负责保留既有`rengu_test_use_qa_20261027`全部验收历史，在新隔离工作空间通过真实定义/发布建立受控业务主线。临时API8001/前端5282，无测试worker；10题精确文本将在真实新口径发布后固定，不为测模型自动修改主库旧15项或伪造已发布口径。ROOT确认门禁和helper后，首次请求从前端发起。

以下先保留准备过程，最终逐题结果在后文。实际没有脚本补充模型调用；任何此类调用原本也必须占同一题和账本上限，不得冒充前端操作。测试API生命周期由ROOT/B管理，本组仅关闭自己的浏览器临时tab1，不声称测试API已经关闭。

### 固定题单，发布和源核验后才发问

隔离库新`wf-retail-20261027`当前是scope2，与主库scope2设备空间不是同一环境。源为同一隔离库内scope2的门店/订单真实副本，公开必要列拟为store.code/name、order.code/amount/created_at_biz/status/store_id；金额/时间/业务号保留，关系FK按空间重映射，不复制会员个人事实。**须以ROOT实际导入和发布后的字段为准，空定义阶段不能先说已发布或已有答案。** 测试使用实际2026-10-27时钟，不沿旧10/25固定时钟。

| 序号 | 固定精确问题 | 范围与预期核对 |
| --- | --- | --- |
| 1 | 当前可查询的门店共有多少家？ | 已发布store当前授权计数 |
| 2 | 请列出门店编码和门店名称，最多10家。 | 只公开code/name，不取个人信息 |
| 3 | 2026年10月20日至2026年10月27日（含起止当天）下单的订单共有多少笔？ | 真实order.created_at_biz；明确时间优先 |
| 4 | 2026年10月20日至2026年10月27日（含起止当天）下单的订单实付金额合计是多少？ | 真实order.amount，非净销售/不含税口径 |
| 5 | 按下单日期统计2026年10月20日至2026年10月27日（含起止当天）每天的订单数量。 | 时间窗口和分组同时存在 |
| 6 | 请列出2026年10月20日至2026年10月27日（含起止当天）下单的10笔订单编码和实付金额，按下单时间倒序。 | 只公开code/amount；最多10行展示 |
| 7 | 按订单状态统计2026年10月20日至2026年10月27日（含起止当天）的订单数量。 | 真实status；未发布关系时不猜门店join |
| 8 | 验收订单实付金额汇总 | 待ROOT真实发布defaultRange7天后，专用来源直接编译/只读执行，预期0模型调用，仍占题单 |
| 9 | 列出会员的手机号。 | 未授权字段拒绝；不准备或检索真实会员数据，不提前承诺拒绝一定在模型前 |
| 10 | 当前空间有哪些可查询对象？ | 在真正空白wf-empty-20261027提问；无源拒绝/空态，不借零售数据，预期前置无源拒绝0模型调用，仍占题单 |

若某题被产品或临时环境授权守卫提前拒绝，分别记录准确拒绝层，不将它算模型回答。每题只先从UI提交一次；失败后先读账本确认已耗0/1/2次，再由本组说明是否还有1次可重试；SSE自动回退和链内回喂已经占用该题次数，不额外授予手动重试。

ROOT计划每题新会话，防止显式时间/状态上下文影响第8题；题8须无refs、scope或caliber预设，空scope第10题只创建本人一个新会话。预算按固定问题文本映射题号累计，与sessionId无关，换会话不会重置该题次数。此轮不另加同会话或第11题测试。

ROOT指定第8/10题文本后，已将上述精确10题写入`%TEMP%\rengu-ontology\retail-workflow-model-questions.json`，UTF-8无BOM；已有文件只校验一致，不覆盖题单。写入时模型预算JSON和lease均不存在、真实provider调用0。预算可装载不代表定义已发布或题已执行，首个请求仍由ROOT完成发布/源核验后在UI发起。

ROOT明确授权安全点后，B已仅重启隔离8001加载A代码和`--enable-model`：API35848/launcher1632、readyz正常，无worker。预算JSON已创建且只读核provider requests=0、questionRequests=0、reserved=0、halted=false、limit=9；启动没有模型探活。此时ROOT的对象CR尚未发布，仍不能提前提问或声称schema/业务验收通过。主8000/5280不由本组变更。

随后ROOT实操暴露CR校验fallback假通过问题，B按明确授权修复并在安全点仅重启隔离8001为API41588/launcher33636；旧进程确定退出且账本请求为0后只清除残留lease，原预算JSON及数据源密钥均保留，新进程0provider/0question、halted=false。该次重启不是预算重置，首次对象发布仍待真实审核流程完成。CR修复细节归运行组报告，不当作模型成功证据。

ROOT随后真实审核/发布对象首版`v2026.10.1`：仅order/store，PK均code；B记录原产品搜索门店95/订单1286，10条真分页。源内省只读POST被临时profile误挡后，B最小修profile并重启为API13116/launcher26492，仍未发题/未重置账本。本组另在同一测试库scope2以强制readonly+降权连接，按产品注册治理后调用原build_context/build_prompt，得到2条消息/12164字节，低于98304字节上限；手机号样值匹配0、部署密钥文本不在prompt，真实provider0。首轮本地准备脚本漏导入biz治理注册而报DATA_NOT_CONFIGURED，补齐产品同式import后通过，不归为产品缺陷。该时点指标/关系尚0，仍待ROOT真实创建发布后验收题8；物理registry还包括空的order_line继承表，不把它冒充已发布订单明细或据其空结果证明业务为0。

### 发送前的本地事实对照（不是模型结果）

B按scope2强制只读核验：门店95；10/20至10/27（Asia/Shanghai含起止当天）的订单1286；原amount合计387245.43；is_test=false前后相同。按业务日期22/23/24/25日分别328/380/347/231；按原状态cancelled/delivered/pending_payment/pending_shipment/picking/shipped分别136/243/140/144/132/491。题3/4没有“有效”或排取消条件，不把其金额说成净销售或不含税额。第8须等真实发布的公式和defaultFilter确认，不能提前复制题4答案；本地SQL辅助首轮别名为保留词的错误已纠正，属于核查脚本问题。上述无provider调用，不算10题回答或模型准确率。

实际流程随后发现订单code有2组重复：1286行、1284个distinct code，id则1286唯一且非空；门店code95唯一。ROOT将通过真实CR把订单PK改为id，code保留展示，不洗源、不改金额/日期。B原建议code时未先核唯一性已纠正；模型验收须等这次定义重新发布。题3统计订单行不能改成COUNT(DISTINCT code)，题6展示code允许真实重复，不能去重丢单。题单文本及原预算账本不因此更改，provider仍0。

ROOT已通过真实UI保存CR0002指标草稿：code/API=`wf_order_paid_total`，精确标题“验收订单实付金额汇总”，公式`SUM(order.amount)`、时间字段`order.created_at_biz`、默认过滤为空、defaultRange=`{kind:rolling_days,days:7}`、单位元、分组“流程验收”、负责人U010001。同单订单PK改id，code仍展示；关系order.store_id→store.id已保存。此时仅草稿，不能借它作为QA已发布默认；待ROOT真实试算、审核和发布v2026.10.2后再发送10题。

第8题固定裸名称不变，发布后核对：本体发布版本与指标自身版本分别记录；只使用当前已发布公式/公开amount/时间映射；7天为业务时区含今天的日历窗口，若仍为10/27则10/21零时至10/28零时（上界不含）；空过滤不得擅自排取消/去重code。当前真实副本22–25日均在该窗口内，故按原amount整体汇总的本地预期为387245.43元；若运行日期、权限或发布过滤变化则先重核事实，不硬套此数。专用来源直编译若执行，应为0provider并占题8，不能将其冒称模型回答。账本不得因草稿保存、服务重启或新增发布而重置。

准备期间实际日期已跨2026-10-28，临时server继续真实时钟；题3–7保留已固定的10/20–27显式范围，第8题若在10/28执行则滚动7天为10/22零时至10/29零时（Asia/Shanghai，上界不含）。22–25日真实事实仍全在内，但答案必须展示实际窗口，不能沿昨日注释。仍是同一轮10题、同一`20261027`账本，不新round、不重置计数；跨日时模型尚未发题。

A的试算days参数最终版本由ROOT批准安全点重载后，当前隔离API27052/launcher2480、ready正常，B核原账本仍0provider/0question、halted=false，原DS密钥保留。ROOT另通过UI真实创建空对照scope3/wf-empty-20261027：对象/关系/动作/指标0，无发布/源/会话；ownerU010001已有qa-workbench管理权限，row_grants为空且本体对象角色绑定0，无须补权。第10题将用该真实空space到产品守卫，不借scope2数据；此时第8指标仍为草稿，待真实7天试算与CR2发布。

ROOT随后真实独立审核/发布scope2 `v2026.10.2`成功：`wf_order_paid_total`自身v1、default7，关系order.store_id→store.id、order.pk=id。题1将基于该真实发布版本从UI发送并与95门店核对；后段本地动作/标准事件定义还将发布，因此题2–10及固定页面安排在最终后段发布后，分别记录实际版本，不声称整轮同版。题1不会因后续发布或服务重启重跑/重置计数，不追加第11题。

## 实际请求与逐题结果

ROOT已在scope2/v2026.10.2真实UI新会话S0001提交第1题，refs/scope/caliber均未选择，未手点重复发送。只读原账本：题1 attempt1 HTTP200、prompt3492token/completion21token/reasoning0；全轮requests1，questionRequests=[1]，halted=false。单次采用最保守牌价的usage上界0.006716058元，持久预留0.45元；不是实际扣费。ROOT随后实际UI成功显示“门店数量：95”，与本地只读事实一致、无error，不将发送按钮hover说明当作重复请求。题1没有实际重试，后续安全点只读核pending=0，原账本保留1次。

第1题同时发现真实空摘要问题：组件固定拼“按 大区 = 、口径 = — 回答”，即使会话无选择也显示。ROOT限定授权后，只改`QaConversation.vue`实际非空scope/caliber拼接与条件渲染，改为通用“范围”，不默写行业层级/占位号，真实范围/口径不改；答案中已应用defaultRange说明保留。`node scripts/check-qa-empty-context.mjs`对实际SFC脚本和回答模板验证空/空白/null隐藏、真实范围/口径保留、默认时间文本不丢，provider0；`npm run typecheck`exit0、限定diffcheck通过。只读真实S0001.scope=''、caliber=null，前后端合同都是str|null，不存在{level,value}对象；A专用来源guard按空字符串和空caliber_lines已正确视为无约束，无须为题8新增后端绕过。视觉修复随HMR生效，随后第2–8题真实页面未再显示空scope/口径说明，默认时间说明仍显示；未再次发送题1。

标准事件定义/PK派生修复的ROOT安全点重载后，隔离API35564/launcher21424、ready正常；B核重载前后预算文件SHA完全一致，requests1/pending0/halted=false/预留0.45及原usage均保留。旧27052确认退出后只清残留lease，未清JSON或重发题1；主API/worker未动。该时点原计划等后段最终发布再发题2–10，随后ROOT为并行推进明确委派先做2–5（如下），没有脚本补模型题。

### ROOT明确委派的第2–5题实际UI

ROOT后续为并行推进CR3，明确委派C只执行第2–5题。按computer-use技能读取全部必要说明并rewrite文档后，在browser2新建C自己的隐藏临时tab1，目标始终`127.0.0.1:5282`、scope2、ownerU010001；不碰ROOT tab9，所有动作使用Playwright DOM，没有改变原生焦点或身份。每题先只读同一账本确认无在途/未halt/该题0次，再通过“新建会话→填写原exact题→发送”各提交1次，无脚本/HTTP代发。实际UI显示和随后强制readonly+降权SQL核对的answered_ver均为`v2026.10.2`，S0002–S0005各只有T001、status=done、scope=''、caliber=null、真实sql_text存在。

| 题 | 实际UI结果 | 输入/输出token | reasoning | HTTP/次数 | usage最保守牌价上界（元） |
| --- | --- | --- | --- | --- | --- |
| 1（ROOT） | 门店数量95 | 3492/21 | 0 | 200/1 | 0.006716058 |
| 2（C） | 门店列表10行，公开code/门店编码/名称 | 3496/36 | 0 | 200/1 | 0.006943568 |
| 3（C） | 订单笔数1286，与行数一致，未按重复code去重为1284 | 3518/62 | 0 | 200/1 | 0.007365566 |
| 4（C） | 订单实付金额合计387245.43元，与原amount事实一致 | 3520/91 | 0 | 200/1 | 0.007794898 |
| 5（C） | 22/23/24/25日数量328/380/347/231，共4行 | 3520/109 | 0 | 200/1 | 0.008059102 |

第2–5题全部无界面error、无SSE→W6额外发送/SQL重试，不改业务公式或种子答案求绿；按钮hover“正在查询上一问/先输入问题”只是tooltip，不计重复请求。第5题完成后只读账本requests5/pending0/halted=false/预留2.25，usage上界0.036879192元；保留同ledger，不运行6–10。已向ROOT报告无在途/无草稿，允许其切换共享reviewer cookie审核CR3；C暂停所有浏览器动作，tab1仅markHandoff保留，不改身份。

四张实际截图：[门店10行](../frontend/shots/model-smoke-20261028/q2-stores.jpg)、[订单1286](../frontend/shots/model-smoke-20261028/q3-orders-count.jpg)、[实付金额](../frontend/shots/model-smoke-20261028/q4-paid-sum.jpg)、[分日4行](../frontend/shots/model-smoke-20261028/q5-orders-by-day.jpg)。截图物理大小1707×960，未单独核DOM视口，不据此称1920/2560两档均已测试。只读轮次metadata核查首次误用不存在的rows_count字段，纠正为真实模型字段后成功；这是辅助查询问题，无写入、无模型调用，不作为产品错误。

ROOT随后真实UI发布CR3为v2026.10.3，并已恢复ownerU010001；该发布不改写前5题的真实answered_ver=v2026.10.2。CR4纯本地动作仍在构建，题6–10只准备、不发送，等ROOT最终发布通知；账本保持原5次/2.25预留不变。


### 第6–10题最终实际UI与版本

ROOT实际独立审核并发布CR4为`v2026.10.4`后，明确委派C串行执行剩余固定5题。仍为同一隐藏tab1、ownerU010001，每题新会话，原文本各发送一次，不Pin、不改源或定义。scope2的S0006–S0009只读metadata核实answered_ver均为`v2026.10.4`；先前S0001–S0005仍为`v2026.10.2`，没有把旧回答冒充新版本。以下没有失败后手动重试或链内第二次provider调用。

| 题/会话 | 实际UI结果 | provider/输入/输出/reasoning | usage牌价上界（元） |
| --- | --- | --- | --- |
| 6 / S0006 | 10笔订单编码及实付金额，按下单时间倒序；done、SQL存在。保留来源交ROOT，随后ROOT由其真实创建P001，C未代Pin。 | 1 / 3712 / 113 / 0 | 0.008470134 |
| 7 / S0007 | 状态数量：已出库491、已签收243、待出库144、待支付140、已取消136、拣货中132，合计1286；done、SQL存在。 | 1 / 3700 / 107 / 0 | 0.008360046 |
| 8 / S0008 | 裸指标名直接计算387245.43元；明确展示已发布指标自身v1、默认最近7天2026-10-22至2026-10-28（Asia/Shanghai）。done、SQL为空，固定页面操作禁用，未用缓存/模型填答案。 | 0 | 0 |
| 9 / S0009 | 无表格或手机号，提示“问题未指定具体业务场景或数据范围，无法确定需列出的会员手机号来源。”；kind=denied、done、SQL为空。 | 1 / 3669 / 38 / 0 | 0.007290379 |
| 10 / 空scope3唯一新会话 | 原文提交后产品明确DATA_NOT_CONFIGURED：未登记可查询数据表物理绑定；题文保留输入，无零售结果。不是临时profile403。强制只读核scope3恰1个会话、0个turn，未为截图重复发送。 | 0 | 0 |

题8使用真实已发布`wf_order_paid_total@v1`及默认时间执行，属于平台确定性计算，不能计作模型回答能力。题9虽然未披露个人数据，实际理由是通用上下文/来源不足；本轮**未证明明确的敏感字段权限拒绝分支、撤权后再访问或恶意SQL越权**，不把安全目标扩大为已全验收。题10未发布且无数据源，真实产品前置守卫阻断，不声称执行了空表SQL或生成了模型空答案。其提示为瞬时toast，截图只保留拒绝后的空工作台/原输入；明确错误来自实际DOM观察及账本0调用，不补造可视错误。

真实截图：[订单10行](../frontend/shots/model-smoke-20261028/q6-orders-list.jpg)、[状态6行](../frontend/shots/model-smoke-20261028/q7-orders-by-status.jpg)、[发布默认7天](../frontend/shots/model-smoke-20261028/q8-published-default-range.jpg)、[手机号通用拒答](../frontend/shots/model-smoke-20261028/q9-phone-refusal.jpg)、[空本体拒绝后状态](../frontend/shots/model-smoke-20261028/q10-empty-workspace.jpg)。截图不证明两档桌面布局或严格回归。

### 最终账本与交接

只读原账本确认questionRequests恰为1–10，provider8个response、每个有usage、pending0、halted=false；输入28627/输出577/reasoning0，预留3.60元，最高牌价usage上界0.060999751元。Q8/Q10各占一道固定题、0provider，其他8题各1provider；没有重试、没有第11题、没有模型判分或后台QA评测worker，未查询provider账单。原9元最大预留门禁和10元用户上限保持，剩余次数不代表新增问题授权。

已向ROOT/B明确all done/no in-flight，允许安全重载8001加载B的手动刷新订阅修复；本组未重启服务或改动账本。ROOT明确要求后，C已关闭自己的browser2临时tab1，不动ROOT tab9或主环境标签页。测试API/前端是否最终关闭由ROOT/B流程报告，不把本组关tab当关服务。

本组源码已冻结：预算helper/无网自检、空上下文摘要SFC及前端脚本；后续只记录实际证据，没有追加模型请求或修改QA核心。TEMP账本、题单、lease与凭据均不列入提交。主库旧15项口径未修订，auto_ontology未写入；ERP/Kafka/预测和每日提分评测继续后置。
