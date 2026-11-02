# 2026-11-01 构建助手、函数编辑与草稿上下文深验

本文件由助手源码核对 agent 独占维护。当前结论是：**已完成源码合同核对，并最小修复多个确定的上下文/草稿与生成门禁缺陷；本 agent 没有执行真实模型或页面验收，不能把内存断言写成模型理解正确或业务保存成功。** 主会话拥有官方 CUA，可在纯合成普通设备维护 workspace 中逐轮补充页面证据。

最初范围限于构建助手、普通纯计算函数及草稿交接；后续主会话明确扩展至纯合成 workspace16 的 QA 页面保存故障，并授权两个严格限定的只读诊断。助手阶段未访问数据库/环境文件；后续诊断按授权仅从 backend/.env 在内存读取正常数据库配置，校验 localhost:5433/rengu_ontology，以正常 app session scope16、只读事务执行必要 SELECT 并 rollback，未打印配置/连接/凭据。全程未操作军事业务、自动行为求值、调度或部署，未启停服务，未运行后台全量检查、pytest、全量构建或真实模型/API。主会话真实模型页面事实与本 agent 的局部证据分别注明。

## 已确认的源码合同

| 入口或行为 | 当前实现及边界 |
| --- | --- |
| 页面模块识别 | `BuildAssistant.vue` 由 `route.name` 设置默认 target，中文明确需求可选择另一模块；已有结果绑定目标，随后要求另一模块会提示新建对话。函数、对象、关系、动作、指标、维度、字典、知识、事件、规则均有目标映射。维度页默认 dimension，明确“字典/码表”再选 dictionary；非上述页面才可能走后端 auto 模型识别 |
| 真实模型上下文 | 后端 `auto_ontology/assistant.py::generate` 读取当前用户所属本体，且仅带当前用户有 view/manage 权限的建模候选。前端 target 选择、后端格式校验本身不证明模型理解或多轮需求保持正确 |
| 函数编辑入口 | 函数详情“AI 辅助修改”发送 targetId、lockVersion、version、name 以及当前 title/desc/timeoutS/code；详情必须属于当前 API context，非只读、非保存中。也支持在已选函数时用“修改当前函数”等有限中文正则转入编辑 |
| 编辑源定义可信性 | API 请求仅带目标 id 与版本锁，不发送前端 base。后端重新读取当前本体已发布函数及对应 `FnVersion.snapshot`，比较已发布源定义与快照，拒绝缺失、版本冲突、超过助手长度限制及不一致定义；模型仅获当前函数，返回 patch 不得改 name |
| 函数建议格式 | 编辑 patch 合并源定义后经过 `SaveIn`、签名与受限源码验证；生成期间再核对身份权限及函数版本。这里只检查定义格式与边界，不运行生成代码。纯计算结果仍须人工审阅与平台测试 |
| 对话保存 | 标签页 `sessionStorage`，键为 `ontology:assistant:v1:<身份>:<本体>:<route.name>`；函数编辑额外加目标 id、version、lockVersion。保存 prompt、history、result、resultScope，不写服务器对话表 |
| 容量与敏感文本 | 最多 20 个新会话桶、单桶 120000 字符、历史最多 20 条；疑似 api key、bearer、password、密码或密钥文本不持久保存。不能把该启发式当作完整敏感信息识别 |
| 刷新恢复 | 普通页面桶恢复未发送内容、历史与建议。后续新增安全编辑指针后，“打开助手”可经可信函数回读恢复同版本 edit 桶；既有旧桶没有指针时不臆造补填，须先重新点击 AI 辅助修改建立指针。checkbox 选择不持久保存 |
| 过期与只读保护 | 生成响应要与 request id、activeKey、完整路径、本体版本一致才展示；apply 要与生成时完整路径及本体版本一致，拒绝未知版本、历史版本和身份预览；prefill 接收方另核本体、身份、当前版本 |
| 新建与修改 | 无 `edit` 的函数草稿进入新建表单；有 `edit` 才重新 GET 目标函数并比较 id、version、lockVersion，再进入原目标 edit 表单。只将用户勾选的 title/desc/code/timeoutS 复制进去；不改函数 name，不自动保存 |
| 现有输入保护 | 函数表单脏输入先经过丢弃确认，取消不覆盖原表单；只读/正在保存时不接收。其他模块使用既有 `useAssistantDraft` 的 blocked guard；最终提交仍走各页正常保存合同 |

## 本轮修复的确定缺陷

### 跨模块采用草稿时交接数据被清掉

旧实现：`apply` 先 `putAssistantPrefill`，随后 `router.push` 改变页面；`sessionKey` 的同步 watcher 在 route.name 变化时一律 `clearAssistantPrefill`，目标页因而可能读不到刚确认的建议。

修复：移除“切换任意会话桶即清预填”，改为身份或本体改变才同步清预填。目的页既有本体、身份、版本及只读校验继续生效。

### 切身份/本体时旧函数编辑绑定仍存在

旧实现只在离开函数页或 query.fn 改变时清 `editing`。同一函数路由下切换本体/身份可能沿用旧 targetId/version/lockVersion、旧名称与比较基准；后端仍有本体/版本校验，不能据此声称发生越权或错误写入。

修复：增加身份/本体同步 watcher 清除 `editing` 与待交接预填。对话保存仍按原旧桶执行，返回后可通过原函数编辑入口再取回原版本编辑对话。

### 取消覆盖未保存输入后建议预览消失

旧实现：`receiveAssistantDraft` 是异步 listener，先消耗 prefill，再等待丢弃确认；`BuildAssistant.apply` 不等待 listener 结束便清 result。取消后原输入仍受保护，但“可从助手重新采用建议”的提示缺少可重试的建议预览。

修复：采用动作只关闭面板，不清唯一 result。原页面会话仍保留建议，未确认接收时可再次采用；跨页应用也不再清除目的页已加载的独立预览。未增加事件 ACK 或绕过接收方保护。若成功进入表单改变完整路径，原预览仍会被现有 resultScope 校验限制，不能当成新页面建议随意再次应用。

变更仅为 `frontend/src/components/BuildAssistant.vue`，另增加一个局部内存检查脚本；没有更改后端助手或函数保存逻辑。

## 唯一局部离线检查结果

在 `frontend` 运行 `node scripts/check-assistant-context.mjs`。第一次检查前两个修复通过；主会话随后明确要求修复取消覆盖后预览丢失，追加一个接收方拒绝/重试断言后，同一局部脚本再运行一次通过。两次均返回：

```text
assistant context contracts passed (in-memory only; no model/API/DB)
```

该脚本用真实组件 script、Vue reactive/watch 和受控内存路由/存储，断言跨模块导航保留交接草稿、返回源页面恢复未发送输入与历史、切本体同步移除旧编辑绑定且旧输入仍在旧桶、切身份同步移除旧编辑绑定，并检查受控接收方拒绝后预览保留且可再次交接。最后一项模拟接收方拒绝，不声称已点击真实丢弃确认弹窗。脚本明确禁止 API 与模型入口；它不证明浏览器 DOM 时序、真实模型正确性、实际表单保存、版本发布或服务端存储。

该阶段没有运行其它离线测试、typecheck、全量 build、pytest 或任何后台测试。后续针对主会话真实首轮模型输出，只增加下节记录的一个局部解释器断言。

## 主会话待真实验收路径

以下均为待办，不是已通过结果。使用新建纯合成设备维护 workspace；代码仅纯计算，不调用系统、网络、数据库或外部服务。

1. 在函数页打开助手，输入“创建设备状态标签函数 `device_status_label(status: str) -> str`，正常返回可用，其余返回待检”；第二轮修改其余返回待维护，同时要求保留正常分支。核对建议代码与两轮历史，再进入表单、人工核对、保存、平台测试正常与其它两个输入。
2. 新建函数后点“AI 辅助修改”。确认面板标题含原函数及版本，描述“只改其它状态返回待保养，正常仍返回可用，不改中文名和说明”。核对预览只涉及明确需求、源值来自当前版本，选择要采用的字段后进入原函数 edit 表单，保存后核对同 id 的新版本及原中文名/说明未变。
3. 普通函数页会话已由下节主会话真实反馈通过：留未发送输入和已生成建议，完整刷新后打开助手，原历史、预览、未发送输入都恢复。函数 edit 会话的刷新恢复仍待实测；源码未自动重建 editing，不可把普通桶通过写成编辑绑定完整自动恢复。
4. 从一个建模页明确生成另一模块的草稿，点击采用，核对确实进入目的页并填入新建表单。这是本轮跨模块交接修复的真实页面验收。
5. 在函数 edit 会话保留未发送输入，切换到另一个合成 workspace；即使路由残留相同 fn 参数，也不应继续显示旧“修改函数”绑定。返回原 workspace、打开原函数并点 AI 编辑，核对旧输入只属于原桶。切另一模块也应恢复该页自己的会话。
6. 人工编辑函数表单形成脏输入，再采用助手建议；在丢弃确认中取消，核对原输入未被覆盖，并记录助手建议是否仍可再次采用。当前存在下节列明的静态缺口。

## 尚未修复或未覆盖的边界

- 编辑会话刷新缺口后经主会话真实复现，已按文末安全指针方案实施；建立新指针后的真实自动恢复仍待主会话复验。旧桶无指针不会臆造补填。
- 取消脏表单覆盖后预览丢失已按上述最小方式修复，真实丢弃确认弹窗及再次采用仍待主会话 CUA 验证。
- 没有真实模型多轮结果、实际 UI 刷新/导航证据、真实函数保存/版本历史及平台运行结果，因此尚不能宣布助手功能全面验收。

## 主会话真实结果

主会话反馈：已新建纯合成 workspace 16“设备维护业务验收20261101”；标准事件新建即有 ActionExecuted、Unknown 两条，事件事实由主会话另行记录。

函数助手真实首轮输入要求：JSON 参数 score、80 及以上返回“正常”、其它返回“需维护”，函数标识 inspection_label、中文名“巡检结果标签”、timeout 5 秒，示例 `{score:80} -> 正常`。主会话实际看到预览字段正确，代码为：

```python
def inspection_label(score: int) -> str:
    if score >= 80:
        return "正常"
    else:
        return "需维护"
```

收到该反馈时尚未采用或保存。本 agent 源码核对确认该 `def` 是平台的正常输入格式：

- `runtime.definition` 用 `ast.parse` 取唯一同名 FunctionDef；`signature` 从该函数提取参数 score/int 和返回 str。不是把整段源码当作一个裸表达式，也不执行 Python 字节码。
- `service.save` 和 `sections.publish_definition` 从源码重新推导签名；本段代码可走该签名合同。保存本身不等于已试运行，源码中保存/发布只调用 signature，而受限函数体检查在测试的 validate_source 中执行。
- `runtime.validate_source` 明确允许函数体内 If、Compare/GtE、Return 及字符串常量；`runtime.run` 调用该检查，再用 `Interpreter` 对 AST 做有限步数/时间纯计算。未放宽执行器，也没有 compile/exec 任意生成代码。
- **真实测试页的输入应为 `{"score":80}`，不包 input/params/context 根。** HTTP 接口 `TestIn` 外层为 `{input: {...}}`，但前端 `testFunction` 已代包装这一层；真正送进受限解释器的字典键须对应声明形参。若函数声明的是 `input: dict`，才会使用 `{"input": {...}}` 作为测试页输入，本次 score 参数没有此包装。
- 当前助手 function FIELDS、前端 FunctionSave 和后端 SaveIn/CreateIn **均无 sampleIn/sampleOut 可编辑字段**；service 新建时写 sampleIn={}、sampleOut=None，更新时保留旧样例。故本次示例不能被声称已登记随函数保存，必须在测试页手动填写核验。未悄然增加样例字段，也未把空登记示例当成执行结果。

本 agent 为该真实生成源码运行一次 `backend/scripts/check_assistant_function_contract.py`，仅导入错误类型和受限解释器，不导入应用/lifespan，不读取 DB/环境文件，不调用 API/模型。它验证签名、validate_source、80→正常、79→需维护，以及错误 input/params/context 根均被 FUNCTION_INPUT_INVALID 拒绝；另 AST 检查 SaveIn/CreateIn 无样例字段。返回：

```text
inspection-label contract passed (bounded interpreter only; no API/DB/model)
```

这是对主会话真实首轮源码的局部受限执行结果，**不能替代该 workspace 的真实保存、平台试运行、版本历史、多轮修改或刷新恢复**。未因误认为 def 不支持而修改助手 prompt、保存 schema 或执行器；主会话仍继续实际功能验收。

主会话随后真实完整刷新并重新打开构建助手，确认历史、上述“待校验”预览，以及未发送的“只改阈值85...”输入均在。**普通函数页面会话刷新恢复已实测通过。** 这次会话尚未进入既有函数 editing 模式，不能外推为编辑目标绑定已自动恢复。主会话下一步继续多轮、页面/本体隔离与实际函数保存/试运行；本轮不新增横幅、实现说明或样例 schema。

### 第三轮真实输出不达标及必要修复

主会话第三轮真实要求“score 缺失或 null 返回待检，保持阈值 85、API/中文名/timeout5”。实际建议源码为：

```python
def inspection_label(score: int) -> str:
    if score is None or score < 0 or score > 100:
        return '待检'
    if score >= 85:
        return '正常'
    else:
        return '需维护'
```

该轮**不能记为正确生成**，也尚未采用/保存：

1. runtime.run 仅给签名中明确声明的默认值补值，再拒绝 missing；缺少 `= None`，故 `{}` 会直接返回 FUNCTION_INPUT_INVALID，不会进入待检分支。
2. 执行器正式允许的比较是 Eq/NotEq/Lt/LtE/Gt/GtE，没有 Is/IsNot；`score is None` 在 validate_source 即被 FUNCTION_UNSUPPORTED 拒绝。因此代码甚至不能通过正式平台受限测试的静态语法门禁，不能只看自然语言解释或预览“待校验”便采用。
3. 模型擅加 0–100 范围分支，超出本轮要求；该含义偏差已记录，不为它扩大本轮 schema 或放宽运行器。
4. 同一固定“函数代码未经运行验证，请审阅并使用平台受控测试。”提示在预览出现两条。模型可复述历史 warnings，后端又无去重追加同句，是可确定的重复原因。

按主会话授权，后端仅修改 `auto_ontology/assistant.py`：

- system 明确测试 JSON 键直接绑定声明形参，可缺失参数必须在签名声明默认值；null 用 `== None` / `!= None`；只支持六类正式比较；不得擅自加条件。该 prompt 是对后续模型输出的约束，不是模型正确性的证明。
- 固定 warnings 与模型 warnings 通过标准库 `dict.fromkeys` 保序去重，仍最多 10 条。
- 新建函数草稿同时具备非空 name/code 时，复用正式 `signature` 与 `validate_source` 作静态门禁；若不支持则抛正式 AppError，不返回可采用 result。不完整/仅说明的新建草稿不因缺 name/code 误拒。已有 edit 分支继续先合并可信源定义，再验证 SaveIn、signature、validate_source，不误把 desc-only patch 当作完整源码。

没有自动运行模型代码、没有改解释器或保存 schema；业务输出含义仍需要人工核对及平台受控测试。完整新建草稿静态门禁拒绝 Is，也不保证它满足“缺失参数默认值”“阈值保持”等用户需求。

同一局部脚本追加验证：必填参数 `{}` 拒绝；固定受控源码 `score: int = None` 配 `score == None` 对 `{}` / null / 85 / 84 分别得到待检/待检/正常/需维护；Is 被拒；prompt 合同存在；真实固定 warning 赋值语句对重复输入保序去重；新增函数实际 gate 对完整受支持 def 放行，对 desc-only/name-only/code-only 不误拦，对 Is 拒绝。脚本只执行可信源码中截取的局部警告/gate 语句，模型代码只送正式受限 AST 解释器，不 exec 生成源码。

追加检查第一次因检查脚本错误假定 warning 节点位于 generate 最外层倒数第二项而失败，改为精确定位嵌套 AST 语句后通过；随后新增静态门禁后再跑同一局部脚本通过。均为本次新增修复的必要局部检查，没有启动后台 suite/服务，也没有 API、DB 或模型调用。

本 agent 已通知主会话补丁就绪，等待其安排一次正常 API-only 重启加载，再以真实第四轮纠正和保存/测试结果验收。没有自行重启，也未将提示词或局部断言写成第四轮模型已经成功。

### 第五轮真实建议恢复后无法采用：空发布基线修复

主会话反馈，第五轮真实提案已包含 `score: int = None`、`== None`、阈值 90，且保留函数 name/title/timeout5。随后同标签页完整导航至 workspace16 对象页，助手为空；再完整返回函数页，五轮历史和建议完整恢复。**跨模块会话隔离与返回恢复已有真实页面证据。** 点击唯一“确认并进入表单”却出现“页面或本体版本已变化，请重新生成建议”，URL 仍为函数页，没有打开表单。主会话保留该错误与原五轮草稿，未靠重新生成绕过。

本 agent 没有读取浏览器隐藏状态、sessionStorage 或实际接口。源码确认 resultScope 只保存完整路径和 app.ontologyVersion，**不含 HTTP epoch**。更直接的确定缺陷是：新 workspace 无发布时 `loadReleases` 返回 empty，router 只设置 next，app.ontologyVersion 仍为 `—`；旧 apply 和 consume 又无条件拒绝 `—`。因此该流程能够生成和恢复预览，却不能采用首个本体组件。原错误未区分版本未知、真实发布变更、页面变更与合法零发布。

加载合同核对：`enterWorkspace` 先清旧 version/next，workspaceLoading 覆盖随后 await loadReleases；初次发布读取失败时 next 保持空。另有发布页刷新 loadReleases 调用，不总先清 next，故没有仅以“next非空”猜测已确认空本体。

修复保持原五轮 scope.version=`—`，不回写成新 scope 或新版本，也不去掉真实版本比较：

- `api/releases.ts` 新增仅内存的“已确认空发布 context”标记；每次 loadReleases 开始即清，只有当前 actor/ontology/history/preview context 下列表与 current 两个读取都成功、current=null、正式列表也空，才标记。读取失败不会把旧 next 当作确认；并发读取使用递增 id 拒绝旧读取覆盖。
- 列表语义已核：后端 `/releases` 仅返回 `ReleaseRepo.formal()`；`/releases/current.current` 来自 latest，包含 working checkpoint。首个组件保存若产生非正式 checkpoint，current 会有真实版本，即使正式列表仍空也走普通非空版本合同，不要求正式发布才能创建下一组件。
- `api/assistant.ts::assistantVersionReady` 为生成、apply 和 prefill consume 的同一入口：加载中/工作空间错误/缺版本均拒绝；`—` 仅在上述当前 context 已确认空发布时允许。真实非空版本仍逐字与 resultScope 比较。
- BuildAssistant 在预览内提前显示具体不可采用原因并禁用按钮。若旧空基线遇到真实版本，明确显示“建议基于尚无发布的本体，当前为 v...”，不等用户点击后才给笼统错误。用户尚未确认的输入与草稿保持。
- 原请求 requestId、activeKey、完整路径、生成时版本和底层 HTTP epoch 保护继续存在；edit 接收方仍重新 GET 并比较 targetId/version/lockVersion。恢复建议仅匹配逻辑上下文，不持久化 HTTP epoch。

同一局部前端脚本在本次新修复后运行一次通过，加载真实 component、assistant API 模块与 releases 模块的脚本，用内存路由/存储/发布响应验证：stale-next 不冒充确认；完整确认空基线后恢复旧 scope=`—` 能经过真实 consume；读取失败使原空标记失效并拒绝；loading 拒绝；真实非空发布使旧空建议拒绝且不改 scope；异步请求切空间再切回同逻辑 context 仍丢弃旧响应。没有真实模型、API、数据库或浏览器隐藏状态读取。

本 agent 已通知主会话修复就绪。主会话安排的正常 API-only 重启反馈为 PID29396、14:36:58、readyz成功；该后端进程事实来自主会话，本 agent 没有启停服务。前端空基线修复待主会话完整正常刷新后直接采用同一五轮原草稿，不能提前宣称已成功保存。

随后主会话授权一并修复函数调用端同步：`RealFunctions.persist` 成功后原本没有像其它建模页调用 `invalidateStaticAfterWrite(result.ver)`，本标签页 app 版本可能到完整刷新才更新。已核对 `functions.service.save` 的 result.ver 来自 `PublishService.publish(save_mode=True)`，其返回 `VersioningService.create_version` 的本体 checkpoint；函数 semver 则另在详情 `version` 中返回。因此在保存响应状态为“已发布”、原 context/intent 仍有效之后，最小增加 `invalidateStaticAfterWrite(result.ver)`，复用既有即时更新本体版本及 current 回读，无需手刷才让助手获知首个保存版本。

该修复仅新增一次轻量局部检查 `node scripts/check-function-write-version.mjs`：编译 BuildAssistant/RealFunctions 两个 SFC，执行真实 persist 的一次受控成功保存，断言既有失效入口恰收到本体版本 v2026.11.1，未误用函数 v1.0.0。结果通过，没有 API/DB/模型或原大检查 suite。真实保存及顶部版本、同页 AI 编辑仍由主会话执行。

本轮源码变更最终范围：

| 文件 | 本轮最小职责 |
| --- | --- |
| `frontend/src/components/BuildAssistant.vue` | 上下文切换、跨页预填、采用后保留建议、生成/采用的版本门禁及具体不可采用原因 |
| `frontend/src/api/assistant.ts` | 统一 assistantVersionReady 与 prefill 接收门禁 |
| `frontend/src/api/releases.ts` | 当前 context 的显式已确认空发布标记、并发加载旧响应保护 |
| `frontend/src/pages/functions/RealFunctions.vue` | 保存成功后复用本体 checkpoint 失效/同步入口 |
| `backend/app/modules/auto_ontology/assistant.py` | 函数 prompt 合同、warnings 去重、完整新建函数建议的正式静态门禁 |

没有新增源码 helper 文件、依赖或 schema。局部检查脚本为 `frontend/scripts/check-assistant-context.mjs`、`frontend/scripts/check-function-write-version.mjs`、`backend/scripts/check_assistant_function_contract.py`；每次因新增修复而追加必要断言的执行已在上文按阶段注明，不是后台全量测试。

## 编辑会话自动恢复的最小安全方案与实施

保留现有 edit 会话桶与内容格式；额外使用一个按 actor、ontology、route 分桶的“最后活动编辑”小指针，仅保存 targetId、version、lockVersion，不保存源码或前端 base。进入该精确函数路由时，若指针目标与 query.fn 一致且不是历史/预览，先用当前可信 API context GET 函数；只有 actor/ontology/route/目标在请求前后完全一致、仍已发布且 version/lockVersion 与指针一致，才从 GET 的 name/title/desc/timeoutS/code 重建 editing，并由现有 sessionKey 载入精确 edit 桶。过期、拒绝、404 或上下文变化则不恢复、不带旧 base、不将草稿变为新建；无需扫描猜测“最近”的其它函数桶。

此方案不能只从保存结果中的 edit 猜目标：未发送草稿可能没有 result，且多个 edit 桶并存时没有可靠的最后活动选择。用户主动退出修改模式/开启普通函数会话须清除该指针，避免普通会话被旧编辑绑定覆盖。以下是在真实缺口确认后的实施，不是猜测迁移旧数据。

主会话随后新增真实事实：

- 当前纯合成 inspection_label 函数 v1.0.0 已保存；平台真实输入 `{}` 与 `{"score":null}` 均返回“待检”，`{"score":90}` 返回“正常”，`{"score":89}` 返回“需维护”。这是四次实际平台运行，不是本 agent 的局部解释器模拟。
- 首个函数保存后本体 working checkpoint 当前 v2026.11.1 正确；不需把函数 semver 当作本体版本。
- “AI 辅助修改”仅要求 desc 时，实际只预览一项 desc。未勾选采用出现“请选择要采用的修改”，未写入。
- SPA 切至 w4，同 ID 函数是 priority_label，助手没有旧 editing/history；返回 ws16、同函数测试页，重新点 AI 辅助修改，desc 预览和未发送文本恢复。跨空间隔离已有真实证据。
- 完整刷新再直接点“打开助手”，却回到旧普通五轮新建会话；旧空基线草稿被正确识别为 `— -> v2026.11.1` 不可采用，但最新函数编辑会话没有自动恢复。此为真实确认的编辑恢复缺口。

按主会话后续明确授权，现只在 BuildAssistant 实施小指针恢复：

1. 指针键为 `ontology:assistant-edit:v1:<actor>:<ontology>:functions`，只保存 targetId/version/lockVersion，不保存 source/base；点击真实 AI 编辑入口或明确中文编辑入口后建立。它不算原 20 个会话桶，避免容量提示被指针干扰。
2. “打开构建助手”仅在同函数路由、非 new 表单、非历史/预览且当前不在生成时读取当前 scope 指针；没有指针不猜，目标不符不请求旧函数。随后用现有 getFunction 获取当前可信定义，要求前后 actor/ontology/完整路径/requestId 未变，目标、已发布状态、version/lockVersion 都匹配，才重建 editing 并加载原 edit 桶。比较 base 来自新 GET，不来自指针。
3. 恢复中隐藏普通旧草稿；失败/权限拒绝/版本冲突会明确阻断、隐藏普通旧草稿并禁止发送和采用，不静默转成新建。不删除已有会话或改标旧结果版本。异步作用域切走再切回仍因 requestId 变化拒绝旧响应。
4. 明确普通新会话、新建表单、选其它函数或离开已有编辑目标会清该指针；跨本体/身份切换只卸下当前绑定，各 scope 的会话仍隔离保存。已处于编辑模式时“新建对话”继续只清该编辑对话，保持原模式，不强行变为新建函数。
5. RealFunctions 的版本历史 tab 仅移除数字后缀，显示“版本历史”；未修改 section 中的“被引用”计数。

新增一次必要局部检查 `node scripts/check-assistant-edit-restore.mjs` 通过：实际组件 script 与 SFC 编译；受控当前源恢复 desc 预览与未发送输入；比较 base 来自源；指针不含源码；同 ID 跨本体/身份不发旧查询；锁冲突拒绝；异步切走再切回拒绝；明确普通新会话只清指针而不删 edit 桶。没有真实模型/API/DB。

主会话重新点击 AI 辅助修改建立新指针后，**真实完整刷新恢复编辑已通过**，截图：[编辑会话真实恢复](evidence/deep-assistant-edit-restored-2k-20261101.jpg)。此结果基于新指针，未声称给历史旧会话补出指针。此前纯计算函数平台运行截图：[函数真实运行](evidence/deep-function-run-2k-20261101.jpg)。

主会话随后继续实际操作 desc-only 建议：选择该唯一字段后重新采用进入编辑表单，name/title/code/timeout5 保持原值。人工临时修改 title，取消后选择“继续编辑”时该临时值保留；随后明确放弃临时输入返回列表，原助手预览仍在，可以再次采用。最终保存成功，同一函数从 v1.0.0 到 v1.0.1，列表只有 1 个函数，name/title/code/timeout5 未变；版本历史 2 条，作者 admin。**desc-only 编辑与取消/放弃/重采用/保存的新版本闭环有真实证据。**

该闭环不等于“表单已经脏时同时采用助手，再取消覆盖确认”的路径通过：编辑抽屉遮罩下浮窗入口被遮挡，主会话没有从该入口完成同时覆盖测试。该项明确保留为未实测，不能拿上述取消表单和重新采用替代。

## 独立只读 review 的异步竞态及局部修复

matrix agent 的独立只读 review 随后指出两个具体风险；主会话授权本 agent 最小修复，没有把代码推断写成已发生跨空间错误写入：

1. `RealFunctions.receiveAssistantDraft` 在 consume 预填后，原本先 await confirmDiscard，再捕获当前 context。若确认期间切身份/本体/版本，旧 proposal 可能按新 context 继续读源及填表。现先捕获 request id、可信 context、完整路径和表单快照；API context 变化同步递增 request id，切走再切回也使旧确认失效。确认后、源 GET 后及路由应用后各复核当前身份/本体/真实版本、只读/保存状态；不得把旧建议套进新表单。
2. BuildAssistant 的隐式“修改当前函数”先 GET 源，但原本只比当前 context/fn/route.name，没有 request id/fullPath。相同 fn 在等待时切到 form=new，迟到结果可能重绑 editing。现读源前捕获 request id、activeKey、完整路径、当前本体版本；迟到结果/错误不写入新上下文，旧 finally 也不清新生成请求的 loading。

`api/assistant.ts` 复用并导出同一 `isAssistantPrefillCurrent` 判定，供 consume 与函数接收方复核，避免两套身份/本体/版本条件漂移；未放宽现有 edit lockVersion/version 比较，未修改后端或重启。

本次仅新增一个针对性脚本 `node scripts/check-assistant-async-boundaries.mjs`，SFC 编译与局部断言一次通过：丢弃确认期间切空间再切回，或本体版本变化，旧新建/编辑建议均不导航、不填表；隐式源 GET 等待时同 fn 切 new 并开始新生成，旧 GET 不重绑 editing、不写指针、不清新请求 loading。全部为受控内存 API/确认 Promise，不发真实 API/模型/DB，也不跑后台大套件。

最短待主会话后续回测为上述两个竞态的真实 UI 时序：脏表单丢弃确认期间切上下文后再确认，应不套旧建议；隐式函数读源期间同 fn 切新建，应不重绑旧编辑。当前遮罩导致同时覆盖入口未可达，主会话已继续数据对象导入，不要求重跑已通过的四次纯计算输入、编辑刷新或 desc-only 保存。

## 后续扩展：真实 QA 多表结果保存页面被拒

主会话在 workspace16 的真实新会话 S0003 提问：“列出当前全部办公设备的Id、Name、校准备注，以及每台设备关联的维护工单数量。没有工单或没有校准备注的设备也保留，不限制时间和状态。”实际返回 25 行，并逐页核对工单数量 2/1/0 与 15 条备注、10 条空备注，数据正确。随即“把会话整理成页面”，名称“办公设备多表验收”却报 PAGE_EXECUTION_UNAVAILABLE：“会话没有与当前对象定义一致的真实查询，请重新提问”。这是主会话真实失败；未通过重问掩盖。

### 一次限定只读诊断与确定根因

经主会话明确授权，本 agent 在 app 正常 session、scope16、只读事务 (`transaction_read_only=on`) 定向读取 S0003 最新成功轮次的必要列、其引用 release 与当前 release 的必要元数据；没有读取其他工作空间内容，没有运行原 SQL 或查询业务结果，也没有写表。正常治理绑定加载仅用于核对当前已批准来源，不修改绑定。

| 依据 | 实际读到的限定结果 |
| --- | --- |
| 源轮次 | S0003-T001，id85，answer/done，is_seed=false |
| 原版本 | release_id123、answered_ver v2026.11.5 |
| 当前版本 | 同 id123、v2026.11.5、scope working |
| 执行 proof | guarded-sql-v1，SQL 与 turn.sql_text 一致，releaseId123/releaseVer v2026.11.5 一致 |
| SQL 来源 | deep_device_w16_asset a，LEFT JOIN deep_device_w16_extra e ON a.id=e.asset_id，再 LEFT JOIN deep_device_w16_task t ON a.id=t.asset_id；按设备与备注分组并 COUNT(t.id) |
| 已发布定义 | OfficeDevice 的公开 calibration_note 来源为 calibration_note；MaintenanceTask 有公开 asset_id；设备本身没有补充表连接键 asset_id 的公开属性 |
| 正规补充绑定 | OfficeDevice 主表 asset，补充表 extra，mainKey=id、joinKey=asset_id、fields={calibration_note:calibration_note}；当前 child projection 含 asset_id 与 calibration_note，继承设备主表权限 |
| 失败点 | `public_columns` 返回 PAGE_EXECUTION_UNAVAILABLE“原查询引用未公开、敏感或无法确认的字段” |

因此不是“QA 查询后又改了定义”，也不是缺真实查询 proof；原版 `public_columns` 把 `ON` 中补充表专用 `e.asset_id` 当作必须公开的对象属性，错误拒绝已获准连接条件。`pages_api` 吞掉逐卡 PAGE_EXECUTION_UNAVAILABLE 后再返回笼统“没有一致真实查询”，掩盖了这个实际原因。working 组件 checkpoint 没有 formal objectBindings 并不意味着公开属性或当前受控补充关联未批准；不能靠要求整体正式发布或公开连接键来绕过问题。

### 最小修复与仍保留的保护

只修改 `backend/app/modules/qa/page_execution.py::public_columns`：复用当前正规加载的 supplementary tuple，要求同对象继承、当前 child 实例一致、同 SQL scope 唯一配置主表，table/mainKey/joinKey 完全一致；只给恰好批准的 `JOIN ON` 等值项中 child key **那个 AST 节点**连接用途许可。不把 key 加入公开字段集合，投影、WHERE、非批准关联、OR 扩大连接、系统列、隐藏/敏感属性仍拒绝。

真实成功轮次校验、SQL/proof/原发布版本一致性、跨版本兼容检查、当前用户权限、run_guarded 的补充 JOIN/RLS 检查和只读重算均保留。没有复制旧结果缓存充当执行，没有生成/修复 SQL，没有修改数据绑定、Workbench.vue 或其它 agent 文件。

本次新增一个纯内存脚本 `backend/scripts/check_page_supplementary_join.py`，运行一次通过：实际 public_columns 可验证该三表 LEFT JOIN；投影连接键、WHERE 读键、错误键、OR、多个候选主表、系统列、隐藏/敏感备注和缺批准绑定全部继续拒绝。脚本无 DB/API/model 调用，不跑后台 suite。随后主会话正常加载补丁并完成下节真实重试；局部检查与真实结果分别记录，未为更新报告重复执行。

### 原会话创建页面与刷新已真实通过

主会话反馈正常 API-only 重启于16:26:49，launch PID23364、listener PID35572，`.env` 未改、readyz成功。本 agent 没有启停服务或追加健康/数据库请求。

加载补丁后，主会话**直接使用原 S0003 成功查询，未重新问模型**，整理为页面 P001 成功，实际预览内容正确；重命名为“办公设备多表验收”后点击立即刷新，提示“按当前权限刷新”，数据截至时间更新至16:28。主会话亦 review 了补充键修复的 diff，确认许可只作用于受控 ON 节点，而非公开该连接键。

主会话最终完成整页 reload：列表仅1条且状态正常，`page_id=P001` 抽屉预览的校准备注与工单数量正常，持久名称仍为“办公设备多表验收”。因此原错误的“原 S0003 成功查询 → 不重新问模型直接整理页面 → 正确预览 → 重命名 → 按当前权限立即刷新 → 整页 reload 持久恢复”闭环已真实通过。此阶段仅更新文档，没有新代码检查、后台测试、模型调用或额外数据库请求。

### 另一次追加授权的动作结果只读摘要

主会话报告同卡明确确认执行 `recordInspection(score=90)`，绑定函数 inspection_label v1.0.1，将设备由待检改为正常。本 agent 另按追加授权仅查询 scope16 设备16001指定列、ACT-20261101-0001指定列，以及同动作同目标 run 数，未重复 S0003 查询：

- 设备16001当前 status=正常、lock_version=2；last_inspected_at=2026-10-01 02:00:00Z，updated_at=2026-11-01 08:14:45.507480Z（北京时间16:14:45.507480）。
- ACT-20261101-0001 为成功，params={score:90}，action_type_code=recordInspection、type_version=v1，目标 OfficeDevice/16001；同 scope、动作类型和目标的 run 计数恰为 1。
- 此次只读摘要证明当前状态和唯一 run；锁版本“推进”需与主会话已掌握的原版本配对，不能单凭后态2捏造前态。last_inspected_at 未被本次摘要证明发生变化。动作确认与写入均由主会话 UI 执行，本 agent 只读事务 rollback，没有触发动作或写数据库。
