# AI问数内确认与动作执行（2026-10-31）

## 当前结论

本轮已修通问数页复用原run、一次明确确认后调用既有confirm接口的前后端合同与保护逻辑，但**真实“一次确认执行”全链尚未通过验收**。两次实际GUI提问都在生成动作卡前被运行中旧后端的取值索引异常阻断，没有stage、confirm、真实动作执行或对象字段变化。

后端已修复暂存复用内容/告警上下文以及取值索引列名判断；前端已修复请求锁、原run恢复、归属校验和提问失败的持续回显。内存合同检查、SQLAlchemy列集合检查、SFC/VM检查及两项独立review通过，不等于真实执行通过。未改变执行策略、权限、预校验或审批配置，未修改原业务数据。

## 现有合同

| 操作 | 行为 |
| --- | --- |
| 问数链识别 action | 按可用目录过滤动作及参数，dry-run生成建议卡；无可用动作仍拒绝写入。新卡有`stageVersion=1`。 |
| `POST /api/qa/v1/turns/{tid}/action:stage` | 只创建`待确认`记录，来源`问数`、确认人为当前用户；201新建/200复用。保存卡片`runId`。不会调用`apply_policy`或外部提交。 |
| `GET /api/ontology/v1/action-runs/{id}` | 查询同一记录当前真实状态和`can.confirm`；不执行。 |
| `POST /api/ontology/v1/action-runs/{id}:confirm` | 持记录锁，校验权限、执行定义/版本引用、确认人及提交条件；按当前配置提交同一记录。没有独立的第二个execute请求。 |
| `POST /api/ontology/v1/action-runs/{id}:approve` | 兼容历史`审批中`记录，本身进入执行并提交，不再要求execute；本轮问数流程不自动调用。 |

`confirm`只接受`待确认`。同一记录重复确认返回非法状态，不再次提交。请求超时/断线后应读取原runId，不能重新stage、resubmit或以新幂等键重发。`失败`、`结果未知`、`已对账`各自保留真实状态；外部持久排队也属于结果待定，不能显示成功。

策略`只给建议`、不允许AI发起、权限不足、版本引用不可用或提交条件不满足仍阻止动作。历史卡没有`runId`且没有新卡`stageVersion=1`时返回`LEGACY_ACTION_CARD`，不能重新执行历史意图。

## 复用参数修复

此前QA暂存按“同会话+同动作+同对象ID+待确认”查找旧记录，没有比较对象类型和参数；前一轮参数可能被当作本轮参数复用。告警幂等冲突恢复也只检查动作类型。

现在两处都要求目标对象类型、ID、参数及告警上下文相同才复用；告警内容不一致继续返回`IDEMPOTENCY_CONFLICT`，不能误提交旧参数。绑定runId回放同样核对告警上下文。内部比较使用真实`ActionRun.alert_id → Alert.id`外键；没有告警明确要求`IS NULL`，不以同对象替代同告警。

告警卡在任何暂存复用前都按当前行权限读取告警，要求`alerts:manage`、本体owner/editor、本人正在处理，并验证目标对象与告警一致。已关闭、转派、不可见或目标变化的告警不能借旧记录绕过。复用查询不先持告警锁或run锁，避免反转公共确认的锁序。公共详情原本已有公开`alertId`，没有增加payload字段。公共执行服务未修改。

运行中的旧API尚未加载本轮修复。前端还必须对stage/get返回记录的动作、目标、参数与当前卡片核对；不一致时只展示原记录和冲突，不允许确认。参数JSON键顺序可以忽略，数组顺序与值类型必须保持；`null`字符串不等同空对象。服务端参数规范化把无参数、空串及空JSON对象统一为无参数。

上述告警检查限定在stage/复用一致性保护；没有改变公共confirm的告警生命周期策略，也不承诺从stage到confirm全程锁定告警状态。公共confirm继续按原对象权限、执行版本及提交条件授权。

## 前端交付

动作请求锁覆盖stage、确认弹窗到confirm响应全过程；连续点击不会在同一次操作中再次发起。请求保留发起时的本体/身份上下文，确认期间切换空间、会话或进入只读状态会停止提交。

已有runId只GET原记录；刷新、轮询、手动刷新、URL恢复和提交后回显共用归属校验，核对类型、对象类型、ID、参数及告警。缺少告警元数据或内容不匹配时，显示“关联记录（不代表本次动作）”和真实原记录信息，并阻止提交。旧卡没有runId且没有stageVersion时不能重发。网络错误后只查询原run，不能降级为重新stage或自动resubmit。

确认卡明确展示目标、参数、幂等键和复核信息；点击一次“确认并执行”调用既有confirm，需审批时保留审批中，不自动approve。结果按原run的真实状态回显，失败、结果未知均不报成功；取消走原记录撤回。

提问生成失败不再只显示短暂toast：当前会话保留失败气泡与原问题，用户可修改后重新提问。会话或空间/身份切换时清除前一上下文的错误，不把其他会话失败附到当前对话；这里的持续回显不是新增服务端错误记录或跨刷新持久化承诺。

## 审批现状（非本轮策略变更）

当前`dynamic/catalog.py`及Git基线已固定`approver=None`、`approvalConfigured=True`、`approvalRequired=False`，同时保留原`approval`描述文字。因此正常目录下`confirm`直接提交执行。`ActionService.confirm`保留非免审目录的兼容分支，`approve`保留历史审批中记录处理；同一有管理权限的确认人可操作，没有禁止同账号的额外判断。

动作发布及auto上限分流仍以`approval`描述是否精确为`免审`等条件校验。本轮未修改这些配置或兼容分支，没有新增全局免审开关，也没有替任何既有动作改策略。

## 已做局部检查

```text
cd backend
.venv\Scripts\python.exe -m scripts.check_qa_inline_actions
```

通过：QA暂存把runId写回卡片、重复使用绑定记录不新建、历史无绑定卡拒绝、待确认复用SQL包含对象类型/参数、告警参数冲突拒绝、一次confirm仅调用一次内存提交替身、重复confirm拒绝、需审批分支不提交、提交条件失败保持待确认。

补充通过：告警A/B及有/无告警上下文不混用；相同告警可复用；runId回放包含告警外键条件；告警关闭、转派、目标变化或不可见时拒绝暂存复用。这些为内存路由检查，未新增真实告警操作。

检查直接调用内存替身和真实路由/服务函数；没有数据库、HTTP接收端、模型、worker或真实提交器，不等同完整集成验收。未运行pytest/全量suite，未写库、启动worker或重启API。服务正常重启此前已被自动审核阻止，本轮没有尝试绕过，新后端源码尚未加载。

前端局部检查为`node scripts/check-qa-inline-action.mjs`：VM执行真实工作台逻辑的隔离替身，覆盖重复点击、确认期间锁、冻结请求上下文、原run重试、归属不匹配、告警差异/缺元数据、非待确认状态不重发、未知结果不报成功、取消撤回，以及提问失败回显/上下文清理；同时编译Workbench与QaConversation的SFC。该检查已通过，没有真实服务器或数据库写入。两项独立review已通过，覆盖本轮后端与前端改动；不代表整个未提交工作树均已验收。

## 主任务UI证据

主任务在本体4的实际问数页输入：“将合成搜救事件 SYNTHETIC-SAR-001 标记为搜救中，执行动作 markRescueInProgress。”两次请求均在生成动作卡前因同一个`qa_value_index`缺失列名异常失败。第二次仅用于验证新前端的失败气泡与原问题保留，实际UI这两项通过；不是对动作执行链的再次成功验收。

实际DOM视窗为2560×1440，页面无横向或纵向overflow。证据：[2K失败状态截图](evidence/qa-inline-action-blocked-2k-20261031.jpg)。截图证明失败可见、原问题保留及布局边界，不能证明动作已执行。

两次均未生成动作卡，未stage/confirm，未执行真实动作，也没有字段变化。`live_auto_receipt`、DM原业务对象、军事业务动作或生产外部请求均未用于本轮验收。

## 补充：现有本地合成动作只读准备查询

按主任务明确限定，仅连接被忽略配置文件中指向的本机`rengu_ontology`库；连接前检查host为loopback、database精确匹配，连接设置`default_transaction_read_only=on`和语句超时，事务降权到受限角色并只设置本体4范围。未访问其他本体、远程报告数据库或源业务服务，未DML。

只读确认本体4已有动作`markRescueInProgress`（标记搜救中）v1，关联`RescueIncident`，免审、策略缺省confirm。无参数、无提交条件、无副作用或外部系统；唯一编辑为当前目标的`status = 搜救中`。本地合成表为`public.rescue_incident_w4_demo`，身份属性`incident_id`。明确目标`SYNTHETIC-SAR-001`当前状态已是`搜救中`；没有找到状态不同的其他`SYNTHETIC-`行。

该候选在后续实际执行时最多可证明普通合成动作的运行记录与状态，不能用同值写入声称证明字段发生变化。源码的合成表注册及仅`status`写白名单要求运行进程既有`RENGU_RESCUE_DEMO=1`；本轮没有修改环境或重启服务，也没有绕过该要求。当前已进行的GUI尝试均被动作卡生成前的异常阻断。

## 实际QA失败的共享根因与离线修复

主任务在真实GUI、本体4独立会话输入明确的合成动作问题后，得到`INTERNAL_ERROR`，未生成动作卡，也未stage/confirm。只读核对当前API最新stdout的该次堆栈：

```text
qa.router.gen → work → _ask_once
→ qa.chain.answer → qa_value_index
→ SQLAlchemy ColumnCollection.__contains__
sqlalchemy.exc.ArgumentError: __contains__ requires a string argument
```

问题发生在动作意图生成前的通用取值索引：语义维度父binding允许只有object/table，代码却直接把缺失的`column`（None）用于`table.c`成员判断。已在共享`qa_value_index`先验证列名是字符串；只有当前空间唯一受控对象且该列实际存在时才映射表名。没有跳过取值索引、削弱列权限或伪造动作卡。

局部脚本已追加真实SQLAlchemy普通Table及镜像alias ColumnCollection检查，覆盖父binding缺列、None/整数列值、不存在的字符串列、合法列映射；检查和Python编译通过。已rg核对同类调用，QA页面回放路径已有字符串类型保护，未发现另一个相同的直接`.get(...) in table.c`位置。

该修复仍未加载到旧API。两次实际GUI请求均在此处失败；离线修复后未绕过索引或伪造建议来制造成功。

## 本轮文件清单

| 文件 | 本轮交付 |
| --- | --- |
| `backend/app/modules/qa/router.py` | 暂存复用参数/对象/告警上下文匹配，以及复用前告警操作检查。 |
| `backend/app/modules/qa/chain.py` | 取值索引只对字符串列名进行SQLAlchemy列集合成员判断。 |
| `backend/scripts/check_qa_inline_actions.py` | 路由/执行服务内存替身及普通表、镜像别名列集合的局部合同检查。 |
| `frontend/src/api/qa.ts` | 原run的类型/目标/告警元数据与固定请求上下文参数合同。 |
| `frontend/src/pages/qa/Workbench.vue` | 整段请求锁、原run复用、统一归属检查、确认提交及真实状态恢复。 |
| `frontend/src/pages/qa/components/QaConversation.vue` | 动作状态/冲突提示、按钮保护与提问失败持续回显。 |
| `frontend/src/pages/qa/useQaSession.ts` | 保留失败原因及原问题，重提/切换上下文时清理旧错误。 |
| `frontend/scripts/check-qa-inline-action.mjs` | VM交互合同与SFC局部编译检查。 |
| `docs/evidence/qa-inline-action-blocked-2k-20261031.jpg` | 实际2K失败状态截图。 |
| `docs/26-10-31-qa-inline-actions.md` | 本轮结果、证据及未验收边界。 |

本工作区有大量原有未提交成果，上表只说明本轮涉及内容，不代表这些文件的所有工作树差异均由本轮产生。

## 加载与后续验收边界

当前后端仍为旧进程23236，未重启加载本轮router/chain修复。此前一次常规重启请求被自动审批返回`blocked by policy`，没有提供更细原因；命令未执行，本轮没有通过其他方式绕过或重试重启。

仍需按正常受批准的服务管理方式加载当前后端，再在普通合成动作对话中核对动作、目标、参数并明确确认一次，确认原run真实状态及刷新后不重复执行。在完成该验收前，只能报告“源码与局部检查已修复，真实执行待加载后验证”，不能报告“一次确认全链已跑通”。不修改既有动作策略、原数据或生产通道来制造验收成功。

## 补充：动作服务直接复用返回的内容核对

继续核对QA与公共动作服务的边界后，发现`ActionService.stage`可能直接返回幂等旧记录而不抛出`IdempotencyConflict`；其告警幂等匹配未比较对象类型。此前QA只在异常恢复分支核对完整内容，直接复用返回可能把同告警、同对象ID但不同对象类型的旧run绑定到当前卡片。

已在QA路由把动作类型、对象类型、对象ID、参数及告警外键的核对移到两条返回路径的共同出口。内容不同返回409且不绑定；相同旧记录原样返回200，不把规则或其他会话来源改成本轮新建。没有修改公共动作服务、确认权限、策略或绑定runId的会话限制。当前Workbench调用`stageTurnAction`只传空对象，已有runId只GET公共详情；本轮未为理论上的跨会话重复stage新增兼容语义或有效参数存储。

`backend/scripts/check_qa_inline_actions.py`已补最小内存断言：动作类型/对象类型/对象ID/参数/告警差异拒绝，同告警同ID但不同对象类型的直接复用拒绝，合法同告警规则run保持原来源并返回200。新增断言先复现旧代码失败，修复后完整局部脚本通过；限定文件`git diff --check`通过。检查无数据库、HTTP、模型、worker或真实提交器，未运行pytest或集成suite。

当前仍是未加载这些源码的旧后端PID23236。本次没有尝试重启、热加载、更换端口或绕过先前的自动审核拒绝；真实“一次确认执行”仍未验收通过。
