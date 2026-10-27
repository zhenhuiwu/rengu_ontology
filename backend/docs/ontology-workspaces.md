# 本体工作空间

一个平台账号可加入多个独立本体。用户、组织、功能角色、系统配置、API 密钥和平台审计共享；领域定义、版本、变更单、数据连接、实例、问数会话、任务、事件与 outbox 均包含 `ontology_id`。

## 请求和权限

- 领域 API 使用 `X-Ontology-Id` 数字代理主键。旧调用省略时只兼容既有零售空间 `1`；显式非法值返回 422，不存在返回 404，没有成员授权返回 403，不回退。
- `GET /api/ontology/v1/workspaces` 为真实 SQL 分页，默认 `page=1&size=10`；`/workspaces/catalog` 是当前用户已授权空间目录。创建与目录接口、平台 admin 接口不依赖当前选中空间是否合法。
- `GET /workspaces/current` 返回当前已授权空间。`POST /workspaces` 需要真实 `releases:manage` 功能权限；创建后仅创建者为 owner，领域表为空，不复制零售数据或角色对象绑定。
- owner/editor/viewer 与用户真实功能权限取交集；viewer 默认拒绝非 GET/HEAD/OPTIONS 请求，仅显式核对的对象搜索、范围只读问答、试算和 dry-run POST 可用。不会把允许历史预览的写接口一起放行。owner 可通过成员接口添加成员，并显式绑定共享角色的对象权限；角色成员关系仍需真实存在。新空间没有对象角色绑定时，实例访问默认拒绝。
- API 密钥持久化签发时的 `ontology_ids`，与绑定用户当前成员关系取交集。旧密钥仅可访问空间 1，新密钥固定为签发请求头选择的空间；后续用户加入新空间不会扩大旧密钥。
- 非零售空间的密钥调用必须显式选择空间。例如，绑定空间 2 的密钥读取对象目录时，请求使用 `GET /api/ontology/v1/object-types`，同时发送 `Authorization: Bearer <密钥>` 与 `X-Ontology-Id: 2`。省略本体请求头仍按旧接口兼容空间 1，因此该密钥返回 403，不根据密钥猜测空间；显式非法或未授权空间同样拒绝，不回退。
- `PUT /workspaces/{id}/members` 输入 `{userId,role}`；`PUT /workspaces/{id}/role-bindings` 输入 `{roleCodes}`。二者必须由目标空间 owner 执行，不接受客户端自报身份或角色。
- `GET /workspaces/{id}/members` 和 `member-options` 均后台分页，默认 10 条；候选用户接口只返回编号与姓名。`GET /workspaces/{id}/role-bindings` 返回当前绑定及角色候选，均要求 owner 和真实 `releases:manage` 权限。

## 数据库边界

`ontology_0018` 无损地将全部存量领域记录归入空间 1，原主键和外键不变。code、api_name、版本、幂等键等唯一约束与部分唯一索引均包含 ontology_id；各空间可以独立使用相同业务编号和发布版本号。领域外键由数据库触发器核验同空间引用，已有记录禁止通过 UPDATE 移入其他空间。

所有领域表启用并强制 PostgreSQL RLS。应用的每个 Session 事务首先 `SET LOCAL ROLE rengu_ontology_scoped`；该角色必须 NOLOGIN、NOSUPERUSER、NOCREATEDB、NOCREATEROLE、NOBYPASSRLS。即使连接账号原来为 postgres，实际领域查询仍在降权角色执行。事务内 `rengu.ontology_id` 无值时领域查询返回空，写入被拒绝；ORM、Core 和 native SQL 采用同一策略。提交/回滚重置 LOCAL 设置，池连接不会保留选中的空间。迁移连接仍使用管理员，不通过应用 Session。

后台任务须先读取共享空间目录，为每个空间建立独立 Session，再 `await set_ontology_scope(session, id)`。一个 Session 不得切换空间。`require_ontology_id(session)` 缺少上下文直接拒绝。不要使用客户端或 LLM 提供的 SQL 设置角色或本体上下文；业务只读网关另有只读连接和领域绑定检查。

平台审计仍以真实 audit 功能权限全局查询。新领域审计记录本体 ID，旧日志保持 NULL，不猜造来源；可用 `ontologyId` 过滤或导出，CSV 包含本体列。平台操作没有领域上下文时来源为空。

## API 密钥权限范围与服务账号（admin_0025 / admin_0026）

- 权限范围是对外契约编码（中文名仅显示）：`meta:read` 元数据读取、`objects:read` 对象读取、`functions:invoke` 函数调用、`actions:dry-run` 动作试运行、`actions:execute` 动作执行、`events:ingest` 事件写入、`dynamic:read` 动态读取。旧标签“元数据读取”输入仍接受并存为 `meta:read`。`GET /api/admin/v1/api-keys/meta` 的 `scopeOptions[{value,label,pages}]` 给出每个范围可携带的功能页。
- 运行时权限 = 签发快照 ∩ 主体当前授权 ∩ 范围可携带页面（`SCOPE_PAGES`，永不高于主体）。`dynamic:read` 只携带 `alerts`、`rules`、`object-types`（生命周期接口按 object-types view 校验）、`action-center` 的 view，不带 manage。
- 令牌只能到达声明了 `Depends(require_api_scope("<scope>"))` 的路由（元数据白名单除外）；未声明的路由一律 403。机器路由写法：`dependencies=[Depends(require_api_scope("dynamic:read")), Depends(require_func("alerts", "view"))]`。
- 白名单：`actionTypes` / `eventTypes`（null = 不限制，[] = 全拒），需要对应范围（否则 422）；路径参数用 `action_type="{type_id}"`，请求体内的类型在解析后调用 `enforce_api_allowlist(request, event_type=...)`。
- 限流配额：`ratePerMinute` / `quotaPerDay`，请求事务内原子计数（被拒或回滚的请求不计数），超出 429 `RATE_LIMITED`。PATCH `/api-keys/{id}` 只能收窄范围（扩大 403），可改白名单与配额。
- 服务账号（平台共享身份）：`/api-keys/service-accounts` 增改吊销，需 api-keys + roles manage；绑定一个服务角色（sys_role），密钥权限 = 该角色在当前本体的功能/对象/行级权限 ∩ 密钥范围；角色必须经 `ontology_role_binding` 绑定到当前本体才有行级授权。吊销服务账号同时吊销其全部密钥。审计记录 `principal_kind / service_account_code / api_key_prefix`。
- 删除角色：成员、行级范围、本体绑定（所有本体，FK ON DELETE CASCADE，admin_0026）一并删除；仍有成员只有这个角色或有服务账号以它为服务角色时 409 `ROLE_IN_USE`（`details.users` / `details.serviceAccounts`）。

## 行级范围按本体（admin_0025）

- `sys_role_scope` 是本体领域表（FORCE RLS），同一角色在不同本体各有范围；存量记录归空间 1。范围以 `object_type_id`（稳定引用）+ `object_type_code`（快照）定位对象，`obj_type` 仅显示；引用的对象类型被删除时该范围拒绝全部行（fail-closed）。前端添加范围时发送 `objectTypeCode`。
- `sql_guard.governed_tables(ontology_id)` 按当前本体的对象类型绑定解析受控表：事务开始与 `set_ontology_scope` 时加载，对象类型增删改 flush 后重载，30s TTL；继承表跟随父表。未加载的本体回退旧规则（1 → 全部、其他 → 无）；解析为空时 `row_filters_for` 抛 SqlRejected，不放行。

## 外部集成接入点（Palantir）

- `POST /internal/v1/events` 由 palantir-t2 接入 `events:ingest` 密钥 + 事件类型白名单（e97bc61），本模块只提供 `require_api_scope` / `enforce_api_allowlist` / 调用方审计字段。
- 机器动作执行使用 `actions:execute` + 动作类型白名单；动态摘要使用 `dynamic:read`（由动态模块接入）。

## 部署和核对

由具备创建角色权限的迁移连接执行 `alembic upgrade head`，再启动新后端。迁移不清库、不重置种子，不支持有损 downgrade；后续新增表需同步授予运行角色权限。零售 seed 命令仅显式绑定空间 1，不用于初始化新空间。

`python -m scripts.check_ontology_metadata` 是不连接数据库的元数据一致性检查，核对领域表清单、所有唯一键和 ORM 映射。当前按用户约束没有运行后台测试、pytest、迁移测试或集成回归；实际页面验收由主任务完成。

当前真实物理数据绑定仅零售空间支持。新领域未配置绑定时应显示不可执行，不能借用零售数据。ERP 连接器与 Kafka 事件通道继续延后。

## 站内通知

`notify_0022` 接 `static_0021`，新增空的本体领域表 `notification`，沿用强制RLS、同本体CR及提交事件引用校验；接收人为真实用户代理主键。同一提交事件、接收人和本体的组合唯一。迁移不回填旧变更单，不生成示例消息，不增加通知worker或外部投递器。

目前只有一个真实来源：变更单显式提交审核。提交钩子在原CR及CrEvent刷新后，重新确认审核人的稳定编号、启用状态、本体owner/editor成员关系及全部目标审核权限，再同事务写入通知；失败使提交一起回滚。姓名缺少唯一对应身份或审核人不再有权时拒绝提交，不向猜测的用户发送。后续撤回重提使用新的提交事件，不会因重复消费同一事件创建重复通知。

接口挂在 `/api/ontology/v1`：`GET /notifications?page=1&size=10&unreadOnly=false` 返回本人分页及本人未读数；`GET /notifications/unread` 仅返回本人未读数；`POST /notifications/{notification_id}:read` 和 `POST /notifications:read-all` 仅修改本人的已读状态。所有入口再次验证当前身份是本体的启用成员，查询和更新固定同时使用本体及接收人条件；不能传入其他接收人。API密钥元数据白名单没有增加通知权限，服务层也拒绝API密钥和服务账号。

viewer只对上述两条精确路径的POST放行个人已读标记，不扩大领域写权限。通知始终属于当前本人及当前本体，不切换为previewAs身份，也不回放历史版本的收件状态。真实前端支持无数字的“全部/未读”筛选、后台10条分页、刷新及本人已读；切换本体或身份立即清理旧收件状态，迟到响应不能覆盖新上下文，无mock回退。管理区通知渠道页通过真实就绪接口显示站内审核通知已接入，邮件、企业微信、钉钉和短信仍未接入，不提供假保存或假发送。

本轮仅执行相关Python AST、Ruff和前端类型检查，留有未执行的 `scripts/check_notification_boundaries.py`。未通过提交CR生成消息，未发真实通知，未运行后台测试、HTTP脚本或迁移测试。迁移应用与空收件箱的实际页面证据由主任务另行记录；不据空态核查声称非空消息、重提去重和跨身份拒绝已实际执行验证。

实际部署与页面核查：主任务先备份 `rengu_ontology` 至 `%TEMP%\rengu-ontology\before-notify-0022-77d20e73.dump`（1,743,312字节），随后增量应用 `notify_0022`，未回填或清理记录。空间2、空间1的铃铛均能切换“全部/未读”并刷新，结果为真实空列表；没有未读消息时不显示“全部标为已读”。管理区通知页已实际加载“站内通知：已启用”“通知类型：变更单待审核”“外部渠道：未配置”，移除了实现说明。截图为 `frontend/shots/admin-build-20261027/inbox-empty.png` 和 `frontend/shots/admin-build-20261027/notification-settings.png`。本轮没有提交CR、制造消息，也没有实测非空投递、已读写入、权限拒绝或并发；未运行源码自检及后台测试。这些页面证据仅证明空收件箱读取、筛选、刷新和渠道状态展示。

## 2026-10-27 实际部署与页面证据

- 主任务已先对 `rengu_ontology` 做完整 schema/data 备份，文件 `%TEMP%\rengu-ontology\before-ontology-0018-767aabf2.dump`（1,388,933 字节）；未验证恢复。随后在该库实际增量应用 `ontology_0018`，约 6.8 秒完成。未清库、未重置 seed，`auto_ontology` 未动。
- 后端及事件、动作/对账、定时规则三个 worker 重启并完成启动，没有观察到新的 worker 异常。未启动问数评测 worker；启动成功不等于领域任务执行或跨空间安全边界已逐项实测。
- 通过真实前端创建空间 `2`「设备运维本体」（`asset-ops`）。首次进入为 0 个对象定义、0 条关系、无发布版本；成员抽屉仅有创建者 `U010001` 为 owner，对象角色绑定为空，没有复制零售角色或授权其他账号。
- 切回空间 `1`「零售经营本体」后，页面恢复既有 17 个对象、26 条关系、当前 `v2026.10.3` 及历史发布记录，含 `CR-0018` 的 11 项待办恢复可见。上述是页面读取证据，不代表所有历史记录已逐行核对。
- 页面核查曾发现切回零售后待办持续空白，实际后端访问日志却反复返回 200，分页大小在 11 与 12 间交替。原因是每次加载先清空列表，改变可用高度，`ResizeObserver` 再修改分页大小并触发下一次加载，使上一响应不断失效。修复后仅切换本体或筛选时清空，分页尺寸变化时保留已有行直至新响应替换，并保留分页空态去重。主任务随后通过实际浏览器确认 11 项待办恢复，含 `CR-0018`、`CR-0017`；新标签页控制台未见错误。该证据不涉及后台测试或合成 HTTP 请求。
- 显式访问不存在的 `ontology=999999` 显示工作空间错误，不回退到空间 1；平台身份和组织仍正常读取。新空间问数历史、FAQ 和“我的页面”均为真实 0，没有向模型提问或生成样例会话。
- 本轮未运行后台测试、pytest、迁移测试、集成测试或严格回归；没有将增量部署冒称迁移测试通过。未实际验证两空间同名定义的发布/回滚、成员授权或撤权、API 密钥跨空间真实调用、真实外部事件接入/投递、外部业务写入或调度任务跨空间执行。
- API 密钥的数据库作用域只做了限定源码核验：共享密钥查询开启事务时，`after_begin` 将本体 GUC 置空；成员与密钥范围交集验证通过后，`set_ontology_scope` 对已有事务执行受控 `set_config('rengu.ontology_id', '2', true)`，随后才返回本体 2 的认证身份。合法空间 2 请求的数据库作用域与最终认证作用域一致；非法或无权请求在绑定前拒绝。这是调用顺序的源码证据，没有签发密钥、发送真实密钥请求或证明真实调用已通过。

本轮完成的是多本体工作空间结构与隔离接线。设备运维等新领域尚未接业务执行适配；可独立建模不代表已经支持任意领域的实例查询、业务动作或事件执行。

### 最后实际页面核查

- 原零售空间对象搜索读到真实 1,286 条订单，首页 10 条。未在新空间复制或新建业务实例。
- 工作空间列表在 1920×1080、2560×1440 均满足 `document.scrollHeight <= innerHeight`，无外层溢出；新空间 2 的空总览在 1920×1080 也无外层溢出。这些证据不扩称全站布局回归。
- 指标分组已改为按本体读取独立轻量目录：实际零售空间仍显示 5 类、15 个指标；新空间 2 只有“全部”且无指标。未新建指标，未验证新分类写入闭环。
- 在空间 2 点击 logo 进入当前本体的空总览，没有进入零售 KPI 首页。API 密钥范围本轮仅核验源码，未签发或进行真实 Bearer 调用；没有新增后台测试。

页面证明图：[工作空间列表](../../frontend/shots/workspaces-20261027/workspace-list.png)、[新本体空总览](../../frontend/shots/workspaces-20261027/empty-workspace.png)、[新本体指标分类空态](../../frontend/shots/workspaces-20261027/metric-groups-empty.png)。
