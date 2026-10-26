# 构建引导、定义表单与静态草稿恢复

2026-10-26：用户已确认 `docs/26-10-26-build-forms-review.html` 四项推荐方案，JSON状态为 `approved`。这是现有 Vue/Ant Design 构建区的增量实现；不改 mock 数值，不新增执行器或数据库表。

## 已接线

- 本体总览增加普通“构建引导”按钮，6步抽屉按实际接口显示登记数量、未配置、无权限、失败与跳转。连接登记不等同连通检查通过，存在定义不等同外部通道验收。历史规则配置未接入时明确不提供历史自动化汇总。mock引导不请求API。
- 对象、指标、维度新建先收集既有CR选择，再填写定义；点击保存才创建/加入目标。新目标默认 `id=api`、`isNew=true`，不会以当前选中对象代替。既有定义只在可编辑草稿中修改，API名只读。
- 对象字段采用 object/def 和 properties；属性按API键增改删，支持主键、标题、可分组、敏感及已有显示/检索标记。来源映射仅为定义，不扩大受控SQL查询范围。
- 指标字段采用 metric/def 的 name/api/group/unit/owner/version/formula/dims/variants；公式只保存口径。显式变体沿用 `[{code,label,default,api,version}]`，编辑仅提交 code/label/default，api/version读取真实指标且由服务端推导。最多32行，非空恰好一个默认，编码及标签分别唯一。
- 维度字段采用 dimension/def，object必填，attr/column只从该对象真实属性中选择。层级编辑 name/api/source/note，最多32行，API及名称分别唯一，顺序由数组决定，不提交运行count。保存后沿用已有取值、说法和问数设置页签。
- 负责人选项为 `/build/owners?kind=object|metric|dimension` 的启用用户。选择提交稳定用户code，后端按现有合同规范化为真实姓名，不迁移姓名列语义。只使用数据库候选，旧姓名仅在候选唯一匹配时用于恢复选择；未匹配不允许静默保存未知负责人。
- 源表导入使用真实连接、单schema多表及只读内省预览。`POST /datasources/{id}/import-preview` 后只编辑生成对象的名称/API/负责人；API默认也作为新业务编号。外键建议逐条勾选，初始全不选。`import-to-cr`仅提交预览fingerprint、table/id/name/api/owner、linkIds和CR说明/锁，不提交任意属性、SQL或关系内容。后端确认时重新内省；结构变化409保留输入。无单列主键要求人工建模。
- 数据连接库表“映射对象类型”列读取GET真实映射清单；编辑选择实际对象并走PUT mapping→新建/加入CR（带旧lock）。成功只提示待发布CR，绝不直接更新objectType显示标记。后端重新验证主键/映射字段，409保留对象选择。真实连接池、超时与网络白名单没有返回字段时显示未提供，mock原文保留。
- CR详情既有审核人字段旁增加“设置审核人”：仅草稿提交人且非历史/预览可用。候选来自公开真实权限接口（排除owner），PATCH提交稳定用户code与读取的旧lockVersion。空候选要求管理员先分配实际角色，不自动授权；403/409保留选择，加载失败禁止保存。真实当前身份取公开/admin/v1/me的id/name，优先用CR ownerId/reviewerId判断权限，只有无ID旧seed兼容姓名，不以PERSONAS姓名判断owner。
- 总览“发起变更”真实模式改为真实创建CR并打开CR页；原mock本地登记分支保留。

## 刷新与并发

`useStaticCr` 按模式/身份/preview/ver、CR和target读取单头再读取items，固定请求header/query。旧代成功或失败不写入新代数据或loading/error。数据库header.changes覆盖本地演示计数。

Object/Link/Dimension/Knowledge Body在真实模式从已发布基线重新叠加实际section、op和after；切已发布、放弃、切CR不会残留上一单行。新增关系/知识目标可由实际CR target恢复。真实新建关系/知识通过CR内targets:allocate在事务内分配并立即登记编号（L-/K-），失败保留CR可重试，不再前端本地取号；显式mock原编号行为保留。新建对象/指标/维度也恢复新target并显示草稿。

发布/回滚成功使当前上下文静态缓存失效并递增代号，返回Body时重拉，旧pending不能盖回旧定义；失败不清已加载缓存。

共享 `crSave` 先取得旧lock再读section，带旧lock执行整段PUT；竞争写返回409。全部子请求固定身份/预览/版本，中途切上下文停止后续写。删除本单新增行直接移除add行。多section表单顺序写入现有CR接口，**不宣称跨section原子提交**；失败保留输入及CR号，重试沿用原目标。

表单加载失败不能保存；保存失败不重载覆盖输入；历史/预览函数入口与按钮均只读。移除已有属性/层级保存前确认。长属性/内省预览列表分页，滚动只在现有Drawer主体。

对象概览改读已有真实detail/meta/brief/db、usage metric、动作类型、规则及创建者可见质量检查；各列表完整分页，权限或错误单独显示未知。真实模式不显示mock质量记录、同步成功时间或虚构最近修改。没有后端同步运行记录时明确未提供。维度自动同义词行没有DB命中计数时显示—，显式DB mapping.hits保留。业务知识试解析已接 POST /knowledge:resolve，真实初始无结果，用户触发后按后台真实定义及实际日历预览词义，不执行SQL；上下文/目标/CR锁变化清空结果，失败不回演示。mock原解析保留。

## 真实当前身份与发布能力

`GET /api/admin/v1/me` 提供实际id/name/roleNames和后端计算的publish/rollback能力。Releases按能力控制按钮与函数入口，不再用姓名白名单；当前用户未加载/失败时能力为false。TopBar与app.store真态初始/切身份/失败清空姓名角色，加载实际姓名与真实角色标签，旧身份迟到响应不覆盖新身份，显式mock保留PERSONAS行为。该接线只显示服务端能力，不自动推导或授予角色权限。

## 真实CR读取隔离

Releases真实记录、detail和events只保存在本页，不调用registerChange，不合并mock/上一用户CR_DETAILS；缺字段用空数组/空时间，未加载或ctx不匹配时立即隐藏。对象/关系/指标/知识的工作流状态、驳回信息只读按模式/身份/preview/ver固定的真实CR记录，历史版本不混当前工作流；失败为空且不补全局CHANGE_REQUESTS。发起、提交、放弃后重新读取真实状态；非草稿header禁止编辑/提交。显式mock分支保持原来的登记和显示。

## 其他静态真实读取收尾

维度使用情况改读 `/dimensions/{id}/usage` 全量分页，真态不再用mock指标推导引用。知识适用目录从真实已发布对象/指标目录完整分页加载，固定身份/预览/版本；已存失效/不可见引用保留并标明不可选，只在用户明确移除时删除，加载失败禁止保存，绝不filter掉未知引用。冲突处理策略只读后端配置；没有真实配置时返回空rule并显示未提供。

## 专项验证

- `node scripts/check-qa-page-cleanup.mjs`：完整brief分页、真实pin标识、ctx迟到、模板结构预览、真实当地日期及mock日期不变。
- `node scripts/check-static-draft-hydration.mjs`：真实loader迟到/403/409、4个Body真实叠加callback和已发布基线重建、mock无请求。
- `node scripts/check-pde-integration.mjs`：登记changes、锁/竞争、403/404事件、discard失败保留及删除新增草稿行。
- `node scripts/check-definition-forms.mjs`：真实选项完整分页/上下文、新旧目标编号、CR重试、实际表单读取/变体/409输入/只读、引导实际数量及mock无请求、导入受控payload和逐条关系选择。
- `node scripts/check-current-profile.mjs`：真实profile固定上下文、store/mock边界、TopBar迟到及失败、发布/回滚真实capability与函数只读。
- `npm run typecheck` 专项执行；全量测试、最终构建和1920×1080/2560×1440截图由协调者统一执行。专项VM不替代浏览器截图验收。


## 真实只读试算与历史/回滚收尾

维度匹配接 POST `/dimensions:match`，当前/历史/preview读取实际启用取值及已保存映射；指定CR仅叠加选中维度，不借同单其它维度改动。包含匹配最多显示6项并给出剩余数量；未知自动同义词命中数后端返回null，显式DB mapping.hits保留。空输入EMPTY_INPUT，NUL/无效Unicode/超长统一422；不写运行数据。

对象历史改用 `/object-types/{id}/usage?kind=cr` 完整分页的 `{cr,parts}`，历史截止由后台处理，不再从全局CR总表猜对象关系。加载失败不补mock修订；旧上下文成功/失败不覆盖当前。

指标版本回滚仅允许后台 `canRollback` 的真实定义，旧seed没有snapshot时显示不可用原因。沿既有确认卡与StartChangeModal，POST `/metrics/{id}:rollback` 生成metric/def CR，加入CR先读旧lock，失败保留版本/标题/说明/加入选择；成功才关闭并切实际草稿，不改变已发布定义。基线说明为变更前定义、时间为实际记录时间，显式mock行为保持。

专项：`tests/test_dimension_match.py` 11pass（独立rengu_agent_static_trials_test）；前端两份静态VM脚本覆盖试算/知识/对象历史迟到、403、完整201条历史分页、rollback冲突与只读。全量测试/截图仍交协调者统一。
