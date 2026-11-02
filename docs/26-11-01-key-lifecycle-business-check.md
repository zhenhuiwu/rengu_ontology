# API密钥生命周期与旧审核UI核对（2026-11-01–11-02）

本文件包含源码合同及主会话分阶段实际事实，**唯一新key的限定生命周期主链已完成**，不代表所有凭证安全边界全面验收。上轮仅看过新建表单；本轮用户随后具体批准workspace16、meta:read+objects:read、loopback、30天，以及维护收窄/轮换/吊销。主会话真实签发唯一新key **workspace16-lifecycle-20261101**，生成面只有掩码，正规Copy保存current-user+SYSTEM私有ACL临时文件；验收后已吊销且按授权删除本轮两份凭据文件、清空剪贴板。原始凭据不写本报告，本agent从未读取这些文件或代发请求。

**已通过：新建→metadata/objects读取→同id维护收窄→双页面旧版本冲突拒绝→轮换→旧材料401/新材料200→跨本体403→吊销→轮换材料401→专用凭据清理。** 修复后的吊销确认checkbox一次成功且底层详情仍开，最终新key行保留为已吊销验收记录，原有key未动。root正做最后reload留证，本文不提前声称该次reload已完成。实际限流为**2/min、100/day**；先前2/s为缺显式单位label时的口述误读，保留更严格2/min，不扩大为120/min。下表是签发前合同指导，不能据其建议名/meta-only再创建第二key。

本轮沿用ponytail最小改动：只补新建确认的可变表单守卫、清理动作页面的误导审批展示，保留权限、兼容状态码和历史记录。之前DOM掩码修复及复制闭包说明见[管理深验](26-11-01-deep-admin-verification.md)。

## 正式UI入口与新建表单

正常产品入口为“管理 → 系统 → API密钥”，路由 `/admin/system/api-keys?ontology=16`。先在工作空间选择器确认16/设备维护业务验收，当前账号admin；URL不能代替后台成员校验。页面“新建密钥”打开 `form=create`；不要从旧key复制作为本轮样本。

| 字段/动作 | 本轮最小取值与源码合同 | 需要真实记录 |
| --- | --- | --- |
| 名称 | 独立新名，例如 `device-meta-lifecycle-20261101`；小写字母/数字/短横线3–40字符，服务端未吊销同名冲突拒绝 | 名称、独立新编号；不记录secret |
| 环境 | 测试，生成`rg_test_`前缀；这只是标签/前缀，不另开沙箱 | 测试标签 |
| 绑定本体 | 16，创建固定`ontology_ids=[16]`；不能在此改成全部本体 | 页面16及详情绑定16 |
| 绑定身份 | 当前账号：admin当前真实userId由meta给出，不能手造编号；后端拒绝绑定别人并校验空间成员关系 | 当前身份文字 |
| scope | **仅元数据读取 `meta:read`**；不勾objects/functions/actions/events/dynamic | 仅一项scope |
| 白名单 | 动作/事件/函数开关均关。meta-only没有对象编号白名单，能读本体16当前身份允许的全部对应元数据 | 不误称只读单设备或只读FN001 |
| 有效期 | 30天，创建最短有限选项；不选永不过期 | 实际到期时间 |
| IP | `127.0.0.1`，后台规范为`127.0.0.1/32`；认证比`request.client.host`，不信任任意X-Forwarded-For | 详情归一化IP；代理后实际来源需真实请求才知道 |
| 配额 | 可选每分钟10、每日100（足够有限核验）；范围1–100000/1–100000000，留空不限 | 实际保存值 |
| 备注 | 明确“仅本轮设备元数据生命周期验收” | 保存后持久 |
| 生成 | 按“生成密钥”后统一确认卡，再按“生成密钥”；会创建持久有效凭证 | 主会话按工具confirmation policy在该action-time单独获取用户确认，不替代为本文件授权 |

确认卡目前显示名称、scope、绑定身份、有效期；本体/IP/配额在前置表单，主会话确认前应将它们一并提供给用户审阅。生成201后抽屉标题“密钥已生成”，输入框只绑定`mask(secret.key)`；原secret仅在内存ref供复制按钮使用，模板不绑定原值。

真实Bearer调用须事先有明确秘密保存/调用方式。本轮用户已授权root对唯一新key使用正式Copy→剪贴板→私有临时文件，完成后清剪贴板；这项明确授权不由本报告禁止。没有保存目的地时仍不能勾“已妥善保存”，可“完成 → 仍然关闭”诚实退出。不得扩展为读Vue内存、响应body或旧key；本agent不读取该剪贴板或私有凭据文件，不把掩码当Bearer。仅UI生命周期不能证明旧Bearer失效。

## 同一个新key的维护、轮换、吊销

| 阶段 | UI入口和正常操作 | 服务端合同/可观察结果 |
| --- | --- | --- |
| 维护 | 列表点新key名称→详情底部“维护配置”；只改新名/备注，并可将到期提前至当前时间以后、原到期以前；IP保持127.0.0.1/32，scope仍仅meta:read | `PATCH /api/admin/v1/api-keys/{id}`携带当前`version`；行锁读取，旧version报冲突；返回新version；刷新/重开后配置持久。同一key编号不变 |
| 收窄拒绝 | 如需要拒绝路径，只在该新key维护表单将IP试改为127.0.0.0/8，保存预期403并保留原范围 | 已有限IP只能子网收窄，不能清空/扩网；scope不能新增；白名单有限集合不能扩大；到期不能延长、清空或恢复过期key。不要把拒绝操作记作成功维护 |
| 配额 | 同维护表单的“限流与配额” | 配额可调高/低/取消，不是与授权等价的“只能收窄”；名字/备注也可维护。权限、本体、身份、签发快照不能原位替换 |
| 轮换 | 新key详情“轮换密钥”→“轮换并停用旧密钥”明确确认 | `POST /api/admin/v1/api-keys/{id}:rotate` body `{version}`必填；仅有效/即将到期入口，后端拒绝过期/吊销；保留编号、身份、授权、本体、到期，只换hash/prefix/last4/更新时间。提交后旧材料失效；UI仅能证明掩码/版本变化，Bearer失效需实际受控调用才证明 |
| 新凭证表面 | 轮换成功复用新建一次性掩码抽屉 | 同样不能从DOM取得原secret；妥善保存与关闭语义同新建。请求中断先核对状态，不能自动再轮换 |
| 吊销 | 仅本轮新key详情或列表“吊销”→确认卡“吊销” | `POST /api/admin/v1/api-keys/{id}:revoke`无version body，后端行锁、幂等保留已吊销状态；不可恢复。刷新详情/列表核对一致。后续认证401为源码合同，若没有真实Bearer调用就不记认证结果通过 |

所有管理读需`api-keys:view`；新建/维护/轮换/吊销需`api-keys:manage`。服务账号另涉及`roles:manage`，本轮不创建服务账号、不改角色、不操作现存key。`KeyPolicyIn.version`在后端可省略以兼容旧调用，**正式UI维护始终传当前version**；rotate必须传version。不要为了验收把兼容参数改成强制而破坏旧调用。

## 本体16最小元数据读取合同

以下是接口地址和调用合同，**本agent没有发送**。业务key必须经`Authorization: Bearer <新测试凭证>`，并明确`X-Ontology-Id: 16`。后端默认缺头空间1，URL `?ontology=16`不能替代header。基址为本机现有API `http://127.0.0.1:8000`；UI/Vite路径是`/api/...`，不要把key管理入口误写在ontology前缀下。

| GET地址（基址后） | 范围与前置 |
| --- | --- |
| `/api/ontology/v1/object-types` | 列当前本体当前身份有view的对象元数据；先从此正常返回取对象id，不能猜OfficeDevice的内部编号 |
| `/api/ontology/v1/object-types/{id}` | 对上步返回的OfficeDevice或MaintenanceTask id读定义；不要加`full`，token显式拒绝包含运行记录的完整定义 |
| `/api/ontology/v1/object-types/{id}/properties` | 同对象公开元数据属性；不等于读取25设备业务行 |
| `/api/ontology/v1/rules/{rule_id}/definition` | 需`meta:read`且签发快照∩当前权限含rules:view；只读当前空间最新发布快照的白名单规则，缺发布/缺规则404。ws16若尚无可用发布规则，不为这项修改原规则或用旧混合详情替代 |

meta:read还列出object-types/brief、metrics、dimensions与knowledge的显式GET；不含函数调用、动作执行、事件写入、设备数据读取，也不能用该key管理key自身。机器权限是scope携带权限∩签发快照∩身份当前权限及空间/行权限；测试前缀不消除这些要求。当前对象/规则UI用cookie能打开，不能冒充Bearer验证。

## 旧审核入口实际盘点及本轮最小清理

`rg`读取了pages/components全部Vue命中，按入口确认后只处理下表动作误导展示。没有删除后端审核/权限守卫、历史数据库记录或兼容码，没有把字符串命中一律认作应删功能。

| 实际入口 | 源码证据与性质 | 本轮处理 |
| --- | --- | --- |
| 构建→动作类型→执行记录 | `ActionTypes.vue`的RUN_FILTERS含`审批中`，segmented及badge直接展示旧码；是真模式可见入口 | 状态值/查询参数仍`审批中`，label和badge改“历史待处理”，保持历史可检索 |
| 构建→对象类型→行为→动作 | `ObjBehaviorActions.vue`动作类型表含“审批”列；执行筛选/badge直接旧码 | 删除误导的审批展示列，执行状态同样显示“历史待处理”；不删数据字段或权限校验 |
| 使用→动作中心→执行记录 | `ops/Actions.vue`已把`审批中`phase/filter显示历史待处理，详情明确旧流程停用；mock重提日志仍“沿用原审批” | 无需重复改。真实resubmit分支先返回，旧mock日志不代表真人正在审批 |
| 对象行为动作回退限制GUARDS | 固定correct_order_status“负责人审批”文案只在`!real`mock展示 | 保留，不当作当前真实业务审批入口 |
| 构建→变更与发布 | `Releases.vue`已有statusLabel：审核中/已通过/已驳回映射历史；仍保留submit/approve/reviewer函数及modal代码，是否可操作须看页面实际can与后端能力 | 未删；不能因文案清理绕开真正权限、审核人与自审保护。根任务可只读查看当前可见入口，不能把未调用分支说成UI已验 |
| 对象历史/指标/自动化 | `HistoryTab.vue` reviewer列/status；`MetricsTab.vue`审核中mock推导；`ObjBehaviorAutomation.vue`真displayStatus与mock兼容 | 只定位，未扩改真实历史/规则状态合同 |
| 构建引导、问数草稿、指标/知识/规则/本体概览 | BuildGuideDrawer、QaApplyDrawer、QaConversation、QaObjectPanel、Workbench、MetricsBody、GlossaryBody、Rules、ObjectTypesBody、OntologyOverview、qa-tuning/CrTag与EvalTab有审核提示/兼容分支 | 列为残余入口，未经当前可达性/合同核对不批量替换 |
| 管理→通知/安全设置 | NotificationSettings的cr-review显示“变更单待审核”；Settings的exportApproval“导出需审批”选项/标签 | 未改。这些是不同通知/导出策略字段，不等同动作旧审批。不能仅删UI后仍暗中改变策略 |
| 外部系统/告警/推演 | ExternalSystems、Alerts、Scenarios亦有审核字符串 | 本agent不动其它负责agent的联调/规则/外部操作文件 |

后端`action_options`仍核验配置中的approver权限/行权限，`policy.auto_decision`在非免审时拒绝自动执行；这些真实guard本轮保留。隐藏一个显示列不是把所有动作改为免审。

## 本轮修复与唯一离线验证

`ApiKeys.create()`原先await确认后直接读取可变form，用户等待期间改变scope/配置可能造成实际签发与确认内容不一致。现在确认前记录form快照，确认返回后要求form完全不变、抽屉仍开、非busy、原身份本体context不变及现有canCreate/readonly均通过，再调用createApiKey。没有新增自动签发或取消确认。

只新增并运行一次 `frontend/scripts/check-key-confirmation-boundary.mjs`：提取真实create函数到隔离VM，以内存确认Promise/假API断言改变form、关抽屉、busy后均不发起生成，未变表单仅一次；同时编译ApiKeys/ActionTypes/ObjBehaviorActions三个实际SFC及检查历史label。返回：

```text
Key confirmation and legacy status display contracts passed (in-memory only).
```

初次修改仅3个前端产品文件、该一个局部脚本及本文。没有重跑以前通过的脚本、pytest、后台/集成/迁移测试、typecheck、全量build或benchmark。本agent无UI/DB/HTTP/进程操作、未创建或读出实际凭据，也没有提交、推送或打包。主会话已获action-time确认并真实新建的事实见开头；其余阶段不以本agent源码检查冒充通过。

## 已签发双scope后的只读行接口与拒绝检查

下列均为源码指导，**本agent没有发送请求**；仅使用本次获批新key、`X-Ontology-Id:16`和本机API。正式行接口是**只读语义POST**，不是猜测的GET /objects：

```text
POST http://127.0.0.1:8000/api/ontology/v1/machine/objects:query
Authorization: Bearer <本次新凭据>
X-Ontology-Id: 16
Content-Type: application/json

{"objectType":"<元数据列表返回的OfficeDevice真实id/code>","page":1,"size":1}
```

`ObjectsQueryIn`继承ExploreIn，withFacets默认false；`_bind`实际按ObjectType.code精确查，必须从允许的元数据取真实id/code，本轮实际返回OfficeDevice/MaintenanceTask。代码复用受控绑定、对象读权限、行权限，返回items/total/page/size/bound/boundProps；`size:1`不触发模型或动作。本轮真实查询200/total25/size1已通过；未另行报告bound字段取值，不补造。

无需试任何业务写入即可得到最小deny证据：

1. 原双scope新key读同一POST，把本体头改4，预期空间允许列表/成员合同拒绝403；不读取空间4对象明细。
2. 相同key `GET /api/admin/v1/api-keys`预期403：元数据/对象scope不含key管理入口；只记录拒绝状态，不获取旧key列表内容。
3. 维护把同一新key收窄为仅meta:read后，原空间16/原objectType/原POST应因缺objects:read拒绝403，正常元数据GET仍可读取。这是最小scope收窄前后对照，不能重新扩回objects:read；轮换继续保留收窄后的授权。

这些建议检查的实际覆盖见末节：跨本体与收窄后objects已通过，admin列表deny没有本轮记录。应控制请求间隔满足实际ratePerMinute，429不能冒充scope拒绝。不要用函数invoke、action dryrun/execute、events写入或改原数据来做deny检查。

## 新建真实观察后的两个显示修复

主会话发现双scope生成确认卡binding仍显示“仅元数据”。根因是meta端点提供的默认binding摘要为meta-only，前端`realBinding()`原样复用它。现在只在新建当前账号摘要按实际`form.scopes`生成中文范围，再接“创建时授权与当前真实角色权限的交集”。不改scope、身份、签发快照或后端权限。维护/详情原本根据key真实scope输出摘要，未改。

“30天有效但剩31天”是独立显示错误：原daysLeft用当前UTC日期字符串的零点作为基准，再round，签发当天的时分也被算进剩天。真模式改成实际到期时间减`Date.now()`、向上取整且不低于0，解析后端本机显示日期时明确T分隔；mock保留固定日期口径。后端`status_expr`及`key_rows`均使用`expires_at <= at+30days`为即将过期，因此新建30天立即该状态符合当前明确阈值合同，本轮没有改状态阈值或持久到期时间。

新加`frontend/scripts/check-key-scope-expiry-display.mjs`：第一次编译实际SFC、抽取真实两显示表达式，以内存meta/form/固定当前时钟断言双scope摘要无“仅元数据”、meta-only摘要准确，30天/不足一天/已到期分别30/1/0天。输出`Key scope and remaining duration display passed (offline expressions only).`

root随后确认单位误读来自未填值时才显示的placeholder，填写后AX只有spin数值。按授权，新建与维护两个表单都增加**常驻“每分钟请求数/每日请求数”label**，分别通过for/id关联，并在对应input-number增加相同aria-label；不改v-model、范围或ratePerMinute/quotaPerDay合同。因新增这项可访问名称要求，在同一局部脚本追加四组for/id/aria断言及SFC编译后再运行一次通过，这是新代码状态的必要检查，未重跑旧套件。

本agent这两阶段均无HTTP、DB、浏览器、凭据读取或进程操作。新增摘要/剩天/单位label的表达式与可访问名称检查通过；未另收到逐项真实显示结果，保留该限定。后续确认卡层级与危险文案的真实吊销验收见末节，已不属待验。

## 主会话真实生命周期进展与确认卡层级缺陷

以下均由root实际UI及专用CLI受控调用反馈，本agent没有读取凭据或代发请求：

| 操作 | 真实结果及范围 |
| --- | --- |
| 新建 | 唯一key `AK-f7cad194a46169139d0f36ae`，原名workspace16-lifecycle-20261101，meta:read+objects:read；生成只有mask，正规Copy保存私有临时文件并清剪贴板，无secret日志 |
| 初始读取 | meta200，total2且id为OfficeDevice/MaintenanceTask；正式objects:query 200、total25、size1 |
| 同id维护 | 改名workspace16-meta-lifecycle-20261101、更新备注、移除objects:read保存；meta仍200、原objects查询403，证明scope确实收窄 |
| 同id轮换 | 正常UI轮换成功，新mask；保留12月1日到期、本体16/IP loopback/2min/100day及收窄后scope，正规Copy保存新材料，无secret日志 |
| 原材料/新材料 | 原材料401，新材料元数据读取200；新材料X-Ontology-Id:4跨本体403。不是仅凭UI掩码变化推断失效 |
| 旧维护版本冲突 | 两正常页面同version打开维护，A先保存备注，B旧表单保存报“数据已被他人修改，请刷新后重试”，A备注未被覆盖；[真实冲突截图](evidence/key-maintenance-conflict-2k-20261101.jpg) |
| 修复后确认卡 | 最后吊销确认checkbox一次check成功，detailsStillOpen=true、URL key仍在，dangerLabel准确；保留勾选与最终确认，没有自动吊销 |
| 吊销与认证 | 明确确认后该唯一key状态已吊销，轮换材料再GET返回401；不操作原有key |
| 清理与留存 | 按用户批准仅删除本轮key-original/key-rotated专用临时凭据文件，两者test-path=false，剪贴板清；不删除新key行，它作为已吊销验收记录保留。root最后reload留证仍在进行，未报告完成前不写完成 |

真实轮换暴露层级故障：详情drawer中弹公共confirm，第一次点危险checkbox落到下层并关闭详情，query.key消失且checkbox未变；底层drawer关闭后再点文本才成功勾选并完成轮换。不能把第二次完成掩盖第一次失败。

源码根因：公共ConfirmActionModal与APIkey drawer都不传zIndex，当前安装Ant Design Vue的Modal/Drawer样式同用zIndexPopupBase；全局公共卡挂在AppLayout、页面drawer后挂，同层覆盖与真实误落下层现象一致。按root授权，在全局确认卡**一处**加`:z-index="1100"`，高于默认drawer及助手1000；未改任何drawer或用第二次轮换制造验收。ConfirmRequest新增可选dangerLabel，旧调用默认原文；仅密钥rotate/revoke分别传“旧密钥将立即失效，轮换提交后不能撤销”/“密钥将立即吊销且不能恢复”，保留irreversible、ack勾选、最终确认及权限检查。

新单个`frontend/scripts/check-confirm-layer-label.mjs`编译两个实际SFC，断言zIndex、label fallback、密钥精确影响文案及ack/最终确认仍在，一次通过：`Confirmation layer and key impact labels passed (offline SFC only).` 此检查不验证真实DOM堆叠命中；修复后可在同key最终吊销确认卡观察，不要求再轮换。本agent已告知root这次ApiKeys script HMR可能重载，须在并发表单冲突检查前重新准备表单；之后不再改这些产品文件。

上述“可在最终吊销观察”是补丁交付时的指导，现已由root真实一次勾选、详情仍开、最终吊销成功取代。原轮换首次误落下层的失败仍保留，不因后续成功删掉。

## 最终未覆盖与本轮边界

- 未等待30天测试自然到期自动失效；状态阈值、提前到期及真实时间显示的全部边界没有逐一UI验收。
- 本轮是本机loopback与跨本体拒绝，不是不同网络来源/真实代理链IP的实机白名单矩阵。
- 2/min、100/day配置保留有证据；每种限流窗口、每日耗尽/重置、所有429分支、并发高负载与吞吐未验。不能把401/403错误当配额测试，也不扩大限流来方便测试。
- 没有测试所有角色/服务账号/类型白名单、全部元数据端点、admin列表拒绝或任意权限变更组合。旧审批残余清理只覆盖本文明确动作显示入口，其它入口没有被整体删除。
- 本agent本次收尾仅改本文，没有新增代码或运行检查；本轮没有后台全量、pytest、集成/迁移、全量build或benchmark，也没有提交、推送或打包。

## 11月2日有限新动作文案清理与发布入口只读定位

root在真实问数动作确认卡看到“确认后按已发布策略提交执行；需要审批时显示审批中。结果会显示在当前对话中。”。本轮仅将`Workbench.vue`这一impact改为“确认后按已发布策略提交执行。…”；内部动作保留“结果会显示在当前对话中”，外部动作保留“外部结果未知时需先对账”。这是新发起动作可见的过时说明，不是旧run事实；没有改submit/confirm、策略、scope、权限、幂等或run状态。root授权纯文案不运行测试，本agent没有新增测试或执行检查。

相关只读定位：`actionReview.ts`只整理“需人工复核/外部系统声明效果”字段，不包含旧审批承诺；公共`ConfirmActionModal`按req展示impact和dangerLabel，并保留不可逆ack及阻断。本轮不改这些文件；Workbench里描述旧兼容状态的源码注释也未被当成当前可见UI删除。

`Releases.vue`当前源码可见性进一步核准如下，取代前表“是否可操作须看页面can”的不确定表述：

- **没有提交审核/通过/驳回/设置审核人的模板按钮调用入口。** `submit/approve/unapprove/openReviewer`及reviewer modal仍留在script/模板作为旧合同残余，但rg模板`@click`没有这些调用；不能因为函数存在就报告真人能进入审核。reviewerOpen只在openReviewer中置true，没有其它可见触发。
- 真模式顶部有“正式发布”，`v-if=realMode`，按钮disabled为`readonly || !isAdmin`；isAdmin来自loadMyProfile返回的`capabilities.publish === true`，不是admin姓名猜测。是否当前身份实际显示启用及确认卡内容，仍需root只读实看，不点击发布补证。
- 旧变更单头部保留草稿“删除草稿/编辑”，受isOwner/crFallback/editTo限制；不涉及新审核授权。`直接发布`按钮外层是`v-if=!realMode`，真模式不显示，不能把workspace.directPublishEnabled推断为按钮已可见。
- 已发布记录可有“回滚”，受真实`capabilities.rollback`、最近版本、非首个版本等门禁；这是发布管理操作，不属于审批旧文案清理，未点击、未改。
- 历史状态筛选仍保留审核中/已通过/已驳回值，但显示历史待处理/历史已通过/历史已驳回；对象HistoryTab审核人列和旧记录来源照旧保留。本轮没有删除历史数据或后端自审/审核人/权限守卫。

本轮仅改Workbench这处可见纯文案与本文标题/新增记录；不改其它agent负责的问数行为、助手、规则或外部联调逻辑。没有UI/HTTP/DB/进程操作、没有新增许可或策略变更。

### 后续真实入口复验（11月2日）

root在ws16真模式发布页实际看到顶部“正式发布”启用，旁边明确“保存的修改立即生效”；正式发布记录为空。CR历史表没有提交审核、审核人或审批操作按钮。历史“admin提交”来源文案保留，不删除历史记录，不因旧字段存在恢复审核流程，也没有点击正式发布来补证。

root随后真实QA外部动作确认卡中，旧“需要审批时显示审批中”句已消失，并经该问数确认正常执行。这证明本轮新动作可见文案的修改已被真实加载和使用，不再仅为源码检查；不改变旧run历史状态或后端策略守卫。本agent本次只补文档，不再改产品或运行检查。

### 最终类型检查发现后的限定修正

root统一typecheck发现ApiKeys详情读取在先将remoteDetail置null后，TypeScript不会推断await期间其它写操作会更新共享ref，因而将后续可选id访问收窄到never。改为在await后读取既有detail computed的新快照，再按原id/version比较；真模式下detail仍返回同一remoteDetail，不移除防迟到旧响应覆盖守卫，不重新签发或重复验收生命周期。

另按root接管的单文件范围，PayloadSchemaEditor原node:any导致模板v-for把字段视作对象而给index推断string|number；现在用同一schema.fields数组的typed computed及PayloadField行类型进行迭代/重复API校验，index自然为number；新增/删除/schema watcher行为不变。只修改这两个文件及本文，未运行全量或局部检查，交root最后统一typecheck一次。

### shared错误提示最后两行清理

root审diff指出`dynamic.ts::crErrText`的CR_BLOCKED仍写“不能提交审核”；该mapper由Rules统一fail及对象生命周期save失败路径实际调用，属于当前保存/发布阻断可见提示，不能仅归入历史死码。本轮仅把CR_BLOCKED改为“合并校验有阻断项，无法保存并生效或发布”，原blocked列表继续显示；CR_NO_REVIEWER改为“此变更单仍受历史流程约束，无法直接发布；请核查当前发布配置”，不再指引单人产品去设置审核人。CR_EMPTY既有“没有新改动，修改后再保存”保持。

只替换两行字符串，无分支/错误码/LOCKS/后端guard变化，历史状态未删。ActionTypes/EventTypes/NewActionEventForm使用action.ts中独立crErrText，不把同名函数误记为dynamic mapper调用；本次未改它们。按root冻结例外，纯文案未再运行检查，不触发新许可或策略变更。
