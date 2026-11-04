# MySQL CDC、源数据库写回与显式视图键

本轮代码与离线验证已完成，真实 MySQL 平台验收由主会话继续记录。本报告不将离线断言称为实际联调；未连接业务库、未安装源触发器、未运行后台测试或迁移测试。

## 数据与部署合同

- 迁移：`backend/migrations/versions/data_0052_source_channels.py`，父版本 `data_0051_datasource_internal`；新增视图声明键、CDC 行镜像审计表和独立源写回授权表，保留原数据。迁移及 ORM 已冻结，是否应用由主会话控制。
- 新依赖：`mysql-replication==1.0.17`、`pymysql==1.2.3`，已纳入依赖及锁文件。
- MySQL 需要 InnoDB、binlog ROW、row image FULL、row metadata FULL、关闭事务压缩、复制读取权限、唯一复制 server-id，以及足够日志保留时间。当前实现持久保存源 UUID、file/position 和事务 GTID；从精确 file/position 恢复，断档或结构变化明确停止，不跳过丢失事件。
- 初始镜像使用一个一致性源事务：P0 先取游标，复制快照后取得 P1，重放 P0 至 P1 的完整已提交行镜像，再公开镜像。DELETE、主键变更均处理；未提交事务不推进游标。每对象租约、平台镜像、行镜像审计与提交游标复用现有持久机制。不同对象订阅不承诺同时提交或源级全局快照。
- 视图不伪装主键。导入/绑定需要显式选择整数或字符串唯一键，验证 NULL 与重复；镜像批次复验，完整扫描前不公开。视图通道是镜像扫描，不是 binlog CDC，也不承诺跨批次源事务快照。

## 源数据库写回

外部系统填写 `database://实际数据连接编号`，通过“数据库写回授权”单独设置写账号、密码、对象、固定动作版本、授权字段至参数映射及回执表。仅本体所有者可授权，默认停用；不复用读取/CDC 账号，不自动创建源表或提权。

接口位于 `/api/data/v1/ext-systems/{code}/database-binding`（GET/PUT），只读探测为同路径 `:probe`。探测只验证元数据与回执读取，不证明 UPDATE 权限。首版仅明确列的参数化 UPDATE，不提供任意 SQL、INSERT 或 DELETE。

MySQL 与 DM 共用源事务回执协议：同一次提交内保存回执并更新锁定的目标行；重复幂等键核对 run/request hash 后返回原回执。无法确认提交即 `UNKNOWN`，只能只读查询回执，不自动重发；回执不存在不被当成“确定未执行”。动作及对账仍走现有持久 call/run 通路。

DBA 预建的 MySQL 合成环境回执结构：

```sql
CREATE TABLE office_demo.rengu_action_receipt (
 receipt_id VARCHAR(64) NOT NULL PRIMARY KEY,
 run_id VARCHAR(64) NOT NULL,
 request_hash CHAR(64) NOT NULL,
 outcome VARCHAR(16) NOT NULL,
 result_code VARCHAR(64) NOT NULL,
 reason VARCHAR(512) NOT NULL,
 created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB;
```

独立写账号仅需目标 SELECT、授权列 UPDATE、回执 SELECT/INSERT。MySQL 要求目标与回执均 InnoDB；DM 适配已编码但本轮尚无真实 DM 写回验收。TLS 保持验证，沿用现有连接器对地址授权/固定 IP 的限制，不降级关闭校验。

## 平台验收步骤与当前状态

1. 在连接页登记受控 MySQL 只读连接；导入真实主键表及显式声明唯一键的视图，并保存读取绑定。
2. 表的 CDC 面板探测、指定独立复制 server-id、预览并确认 binlog 配置、启用；“推进当前对象”仅推进这一订阅，不启动全局 worker。先完成初始复制，再推进重放达到 P1。
3. 在外部系统创建数据库写回系统，动作绑定该系统且不选择 HTTP 接口；授权独立写账号与固定动作版本。只从平台确认新合成动作，按新 call ID 派发一次，再查询回执对账；推进该对象 CDC 核对镜像。

主会话已部署独立 `mysql:8.0` 合成容器，监听 `127.0.0.1:13316`，源表 `office_demo.office_device`、视图 `office_device_view`、三个合成设备。密码只留在忽略的私有临时配置，报告不记录其值。真实平台验收尚待主会话完成，不能据此宣称生产源或 DM 已联通。

## 已完成检查

- `backend/scripts/check_mysql_view_contract.py` 复用 `check_source_channels.py`：无 DB/HTTP 的一次离线检查通过，覆盖视图列表/详细合同、提交事务、主键移动/删除、未提交尾部、断档/DDL/事务上限、镜像发布门槛、空值/重复视图键、回执幂等/哈希冲突/只读查询/超时 UNKNOWN。
- 新 Python 模块、迁移及上述脚本目标 Ruff 通过；已有改动模块仅检查 F/E9，避免全文件格式变动。
- 五个受影响 Vue SFC 已定点编译通过；后续主会话 typecheck 唯一报告的新增模态框空值问题已用可选链修复。最终全前端 typecheck 由主会话统一执行。
- 未运行后台测试、pytest、构建、迁移测试、真实模型请求或源业务写入。

已补导出工具的 `source_write_binding.password_enc` 显式清空（非空列保留空串），自检通过；未重新导出或修改已有 SQL。

## 显式 CDC 事实事件映射（后续增量）

已修复发现的两处真实功能断层：动作类型编辑写回配置时，`database://` 系统不再被强制填写 HTTP 映射；CDC 的完整提交行事实可显式进入既有事件入站与投递 outbox。新增模块 `data/cdc_events.py`，配置保存在订阅已有 cursor JSON，不增加迁移或默认开启旧数据源。

订阅安装后，“配置 CDC”中打开“行事实事件映射”。仅能选择当前对象已发布且来源为“数据同步”的事件及固定版本；选择新增/修改/删除操作，再将已声明事件字段映射至 `before.列`、`after.列`、`op`、`sourcePosition` 或其中明确字段。初始快照不发事件，未配置时只更新镜像。移除映射不追补此前未配置的事实。

保存映射必须暂停消费，校验读取绑定版本和独立映射版本。消费时复核事件发布状态/对象/版本/权限，复用严格 `IngressIn`、`receive()`、`discover(event_code)`，事实记录、事件、不可变订阅 outbox 与源游标在同一平台事务提交。事件 ID 根据本体、对象和精确源事实 ID 稳定生成；不调用业务执行或启动全局投递器。事件无订阅时明确无须投递，不能宣称规则已执行。

映射载荷不做类型强转：新增没有 before，删除没有 after，事件必填字段必须据此设计或只选择适用操作。类型不符/事件版本变更时整批回滚，消费暂停，修正映射后“恢复消费”从同一游标重读。事件规则中对象字段仍表示执行时当前镜像；载荷是已捕获的入站事实。删除目标或历史行后续不再满足现行对象授权时，原有投递授权仍可能拒绝，不能为了事实消费放松当前行权限。

`backend/scripts/check_cdc_events.py` 的一次离线检查通过：固定版本/对象/来源校验、未授权列拒绝、跨本体 ID 隔离、稳定事实 ID、主键变更后的事件目标、严格载荷校验、inbox→outbox 顺序、无映射不发事件。新增文件目标 Ruff 与三个 SFC 编译通过。未运行数据库/HTTP/后台测试；真实 UI 仍由主会话验收。

## 主会话逐项 UI 操作单（本体8）

1. 数据连接页点“新建连接”。名称填合成测试名称；类型 `MySQL`、SSL 关闭、主机 `127.0.0.1`、端口 `13316`、数据库 `office_demo`、用户名 `rengu_cdc`；密码只从私有 credentials 的 read 字段填入。点“测试连接”，成功后“保存”，记住实际 DS 编号。读连接不具有写回授权。
2. 对象类型页打开“从数据源导入对象定义”，选择这个数据连接、Schema `office_demo`、库表 `office_device` 与 `office_device_view`，点“生成规则映射预览”。表和视图使用不同 API 名，主键均选 `id`、显示标题选 `name`，点“保存并生效”。视图导入会实际验证声明键；NULL/重复拒绝，不伪造数据库 PK。
3. 回连接“库表”页，行的“配置 ▾”→“读取授权 · 对象”；选择必要公共列 `id/name/status/score`，“保存授权”。视图还须勾“确认 id 唯一且非空，用于此视图镜像身份”。
4. “同步计划”页先对视图点“同步数据”，完成后应显示3行；视图“配置 CDC”会明确拒绝，不应冒充 CDC。
5. 表行点“配置 CDC”→“检测 CDC 能力”，应通过 ROW/FULL 等配置检测。复制 server-id 填 `160081`，点“预览订阅配置”→“确认订阅配置”。如需事件，先在事件类型创建来源“数据同步”、目标为此表对象、严格载荷字段的事件；回 CDC 的“行事实事件映射”选择它并配置，例如操作仅“修改”，整数 score←after.score，字符串 operation←op。点“保存映射”。
6. 点“启用 CDC”，再点“推进当前对象”完成初始快照；再次推进到 P1，状态应“持续同步中”，当前镜像3行。此按钮只推进当前本体当前对象，不启动全局 worker。日志断档/DDL/租约/载荷错误应显示具体原因；不要用重建基线掩盖未提交事实。
7. 外部系统页新建类别“数据库写回”，环境选“生产”（现有动作系统选择要求），状态启用、基础地址 `database://实际DS编号`、幂等开关开启。此地址是逻辑引用，不填密码或 SQL。
8. 动作类型新建针对设备表对象的合成动作，参数例如整数 `score`；本体编辑与副作用均留空。写回配置选择这个系统，接口填写逻辑描述（例如 `office_device`）、幂等键规则按现有控件填写；不要选择 HTTP 接口或字段映射。保存生效，记录实际动作编码与版本。
9. 外部系统页点“数据库写回授权”：数据连接选上述 DS、目标对象选设备表、已发布动作与固定版本选刚创建动作、独立写账号 `rengu_writer`、密码只用私有 write 字段；回执表 `rengu_action_receipt`，授权字段 score→动作参数 score，明确启用并保存。“只读探测”只验证回执与元数据，不实际 UPDATE。
10. 从平台问数/动作正常确认新合成目标 `1`、score `90`，只执行这次新 run/call 一次；重复查询/派发同一 call 不应产生第二次源更新。状态需以回执为准，未知结果仅对账。最后对设备表“推进当前对象”，通过对象详情/问数核对 score90，原 name/status 保持。若配了事件，事件列表应出现稳定 CDC ID 与 score90，并有可见订阅状态；未投递不能宣称规则或自动动作已执行。

DM 兼容边界：源写回使用同样的事务和 SELECT FOR UPDATE 合同；回执表须由 DBA 创建，六个协议列名称必须精确匹配。DM 默认将未引用名称转大写，应使用双引号创建小写 `"rengu_action_receipt"` 与 `"receipt_id"/"run_id"/"request_hash"/"outcome"/"result_code"/"reason"`，或按真实反射名称配置目标；不能直接照搬 MySQL ENGINE 子句。DM 的独立写账号、事务隔离、驱动及实际权限尚待独立真实联调，不以离线检查代替。

### 实际验收发现：连接成功不等于目录成功

主会话在本体8实际登记 DS-001，MySQL连接成功15ms，但目录为空。已确认服务在目录刷新失败时保留成功连接状态并将安全原因写入 lastTest.msg；前端原来忽略该原因。现已修正为 warning，并在连接详情各tab顶部持久显示目录失败原因，不把连接成功当作目录成功。

MySQL驱动错误展示仅提取数值errno，1142/1143附 SELECT/SHOW VIEW 权限提示，不输出原始SQL、账号或数据。无DB的安全错误断言、目标lint与 Connections SFC编译通过。静态看到 SQLAlchemy 反射视图先 SHOW CREATE TABLE，再 DESCRIBE；仅SELECT账号可能缺SHOW VIEW。该权限解释仍待真实错误码确认，不以推断代替根因或擅自扩展源授权。

主会话确认旧API仅返回泛化目录错误后，已改 MySQL 目录批量列元数据与视图详细内省，共用固定 `information_schema.COLUMNS` 查询；Schema 和表名列表均参数绑定，不执行 SHOW CREATE VIEW。真实表详细主键反射保持原路径，不伪造视图主键。新增无DB窄断言通过列表/详情、避免视图反射及参数绑定检查；目标lint通过。仍需API正常重载后真实重试，才能确定现场是否已解除阻断。

主会话重载并真实重试后：连接成功69ms，无目录错误，库表列表出现 `office_device`、`office_device_view`、`rengu_action_receipt`，列数分别4/4/7；本体8的 `OfficeDevice` 与 `OfficeDeviceView` 已真实导入。此结果证明目录/视图导入路径解除现场阻断，未据此宣称写回或 CDC 已完成。

### 后续真实发现与收口

- 对象来源显示旧“推演主库/达梦smrw”来自 `static/shapes.py` 写死 DS-001/002 别名，而不是源绑定写错。已去掉固定编号别名，列表摘要/详情按当前本体 scoped session 读取真实连接 name/type/database；批量摘要仅一个查询，不读取密码。没有修改旧对象或数据源记录。
- 新数据库系统碰巧取得部署本机 HTTP 演示配置相同的本体/编号，旧创建逻辑强制 local_only 并误拒绝。数据库协议现先验证同本体已登记 MySQL/DM 连接，独立于 HTTP local-demo 分支；既有 local_only 不可变限制、HTTP 网络限制均保留。服务端统一数据库类别，前端输入 database:// 自动选择“数据库写回/无需授权”。尚待主会话原表单真实重试。
- 安全静态确认：MySQL FULL 源行在 `mysql_cdc_source.image` 中仅按 capture.readColumns 构造新字典，未授权原列不会进入 ExternalCdcChange.before_image/after_image；既有离线检查已断言示例 secret 列被排除。事件 sourcePaths 仅列出同一 readColumns，整体 before/after 也是投影后的行。绑定签名和每次消费 fast_context 仍复核当前对象/属性授权变化。
- 新增一次无 DB/HTTP 断言通过数据库协议不咨询 HTTP 演示选择器、来源标签取真实连接；目标 lint 通过。其余真实 CDC/写回/事件投递验收仍待主会话继续。

主会话重载后已真实创建 EXT-N01：数据库写回、生产环境、`database://DS-001`，确认旧 LOCAL_DEMO_INVALID 误拦解除。数据库系统详情现隐藏 HTTP 接口目录，改为独立数据库授权说明；最近调用列改称“响应”，不将 SQL 执行伪装 HTTP。

实际新建动作发现共享 HttpEndpointSelect 原来仍强制 HTTP 目录，现已从共享层补齐：按真实系统协议显示数据库逻辑接口输入框，不请求 HTTP 接口，清除不适用的 HTTP mapping/id。新建表单继续要求逻辑标识和幂等键规则，其他参数/编辑校验不放松。初始值保留、逐字输入不重置、切系统失效的窄 Vue 断言通过，三个 SFC 编译通过；新动作实际保存与执行仍待主会话验证，不把编译检查称为真实通过。

主会话已实际保存“更新合成设备评分”并将策略保存为v2；发现详情v2但左侧仍v1，已补保存后的真实分页列表重读，保留本体/挂载守卫。独立数据库授权原固定v1在动作升v2后不自动改成v2：下拉与警告明确原授权失效，显示固定版本，要求显式“重新授权当前版本”（清空旧字段映射后复核）；后台版本拒绝逻辑不放松。两个SFC编译通过，以上两处 UI 修复仍待主会话当前现场复验。

### 首次源写回未知：仅查询原回执

主会话真实完成 EXT-N01 的独立写授权与只读探测，重新授权动作v2、score→score成功。问数会话S0001的 `ACT-20261103-0001`（OfficeDevice 1 / score90）已从 inline 确认到源适配器，调用 `EXEC-93b0c8f692cd486a8456486711066f8c` 耗时56ms，旧适配器返回 SOURCE_DB_UNKNOWN。主会话停止写入，没有重新派发，没有提交人工“成功/未执行”结论。旧submit吞掉异常且没有原异常日志，不能据耗时猜权限或提交结果。

已补 `POST /api/ontology/v1/action-runs/{code}:query-result`，动作中心与QA未知卡都有“查询幂等回执”。入口核同本体动作可见/操作权限、锁定原run，严格原授权路径、原请求hash和幂等键，复用 reconcile.process 与现有终态回调，只读取原回执，绝不dispatch/retry。查询未核实终态保持UNKNOWN并保存查询原因；SUCCESS/FAILED回执分别对账为外部成功/未执行，不把“已对账”当一概成功。

源适配器现记录安全 phase/class/数值errno至结果原因（通过现有call/task/run持久化），不包含异常原文、SQL、参数或凭据。明确未到commit的失败标FAILED；commit开始或DM线程仍可能运行时才保留UNKNOWN。探测额外验证目标元数据及授权列，回执对账仍不依赖目标当前表结构。一个无DB的事务/回执窄断言通过，目标lint及四SFC编译通过；真实原run回执结论待主会话查询。

### CDC数值合同

确认旧镜像scalar会将Decimal编码为字符串，直接映射事件数字字段将被严格拒绝。现源事实仍保留精确文本，并仅为真正源Decimal、且在读取白名单中的字段记录类型路径。映射至数字事件字段时，只有输出JSON数字文本能够完整还原原Decimal才输出number（整数保持int）；不允许精度截断，也不猜普通数字字符串。无法保留的高精度小数明确暂停，保留原游标，可将事件字段显式设计为文本以保留精度。新增小数精度及源投影窄断言通过，不影响当前int score合成场景。

CDC映射界面顶提示已缩为操作说明，源/目标字段下拉支持按label搜索；实现细节不再占据主流程。

首次点击原run只读查询时，源查询已返回 not_found，但新增审计 method 文本超过既有varchar(8)，平台事务回滚而未显示变化。已将method改为 `SELECT`，不增加迁移；源查询没有写入，not_found仍不是“确定未执行”。字段长度和本次run范围派发的窄离线断言通过。真实第二次查询结论待主会话继续。

同时按主会话要求收窄 inline 确认后的即时派发：只查询本次 ontology+run 原 QUEUED call，复用 dispatch_one 的锁/claim/租约，不扫描全workspace队列，不新增重试；定时worker作为既有持久兜底保留，本轮未启动。

第七轮真实只读查询原ACT0001返回200，not_found明确保留UNKNOWN；新版目标主键/授权列及回执只读探测成功，未重新执行原动作。动作中心新建动作下拉仍误走HTTP可用性检查，已修 dynamic/action_options.launch_definition 的协议分支：数据库使用独立来源授权及治理守卫、固定版本/属性映射/独立凭据/驱动/网络配置检查，不做源 I/O；HTTP保持原adapter。内存窄断言确认治理/版本拒绝不被绕过，目标lint通过。等待主会话重新打开动作发起入口，以对象2的新run独立验证，不能宣称原未知已处理或写通道已成功。

主会话独立新建 ACT0002（对象2、score40），真实结果为已对账·外部未执行，安全诊断 `phase=receipt_lookup / OperationalError / errno=1142`，确认源事务未提交。根因是回执 SELECT FOR UPDATE 在该MySQL版本要求额外权限，而独立账号按合同只有回执 SELECT/INSERT。

已改不可变回执读取为普通SELECT，目标行仍FOR UPDATE，不扩权限。相同幂等键并发由回执唯一主键仲裁：插入冲突后先结束失败事务，再只读核验同key/hash/run原回执，确认其SUCCESS/FAILED则复用原结论，不做第二次业务UPDATE；请求不匹配明确拒绝，未查得或读失败保UNKNOWN。最小权限/并发窄内存模型及目标Ruff通过。原ACT0001仍保留UNKNOWN且不重发；新修复实际源写回待下一独立run验证。

### 主会话真实全链验收

- ACT0003 已从QA确认到源端并通过真实回执对账为外部已成功；CDC捕获score40，并生成 FixtureScoreChanged v1事件。当时没有订阅，不称规则执行成功。
- 后续主会话添加R001订阅，使事件升v2，并正式发布 v2026.11.12。CDC正确暂停旧固定v1映射。弹窗已补明确失效标签及显式采用当前版本按钮，不自动更换版本，保留兼容字段供复核。
- ACT0004 独立对象3、score45，真实源回执成功；CDC生成v2事件，R001以score45和FN001结果90、阈值50真实命中。主会话已保存实际投递与命中页面证据；这两张原始截图为1280×720，不混称2K截图。该结果验证源写回→CDC行事实→严格事件映射→规则函数链，不代表已覆盖生产源、DM写回或所有高频并发情形。
- ACT0001历史UNKNOWN仍保留，没有重发或人工伪定成功。ACT0002的确定未提交失败也保留历史，不以新成功覆盖。

正式血缘图补接真实SourceWriteBinding：复用治理模块同scope/固定版本/显式列映射解析，批量读取元数据且不读取凭据；全部同物理列属性别名以请求字段映射连接动作，身份主键以读取引用连接动作，再沿原动作→外部系统边展示。依赖需要connections与external-systems可见；缺映射/版本不匹配/属性不可见明确unresolved。纯窄断言覆盖别名、主键、权限交集、版本及缺失映射，目标F/E9 lint通过。该血缘增量的真实图形结果待主会话重载验收，未改已冻结Lineage.vue样式。

最终复验：本体8的数据库动作0003、0004实际成功；源事件CDC-3726…通过已发布v2订阅R-001，真实投递成功并生成告警，命中值Score45/远程函数result90。映射旧版本失效需显式采用当前版本并复核字段，界面已实际保存v2后从原游标恢复。普通API启动已恢复，临时SQL计数/计时包装不进入部署入口。正式血缘已实看Score/id→数据库动作→外部系统的依据。源写回及新CDC通道没有执行INSERT/DELETE验收，也未执行正式DM写回。
