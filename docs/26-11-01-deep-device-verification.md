# 通用设备维护深度业务验收（2026-11-01）

## 状态与范围

当前阶段：**已按主会话明确授权，为新工作空间16完成真实数据库fixture准备；正式UI数据连接、受控绑定、对象定义及业务验收仍由主会话进行，尚无本轮业务链路通过结论。**

主会话使用官方 CUA 创建了仅 admin 操作的工作空间 **16 / 设备维护业务验收20261101 / equipment-verification-20261101**。验收只涉及本机 loopback 的 `rengu_ontology`，不访问 `auto_ontology`、达梦原业务表、既有业务记录或真实外部接口。不启动或重启进程，不改环境、worker、模型配置，不运行 pytest、全量/集成/迁移测试或 benchmark，不用 seed 重灌、临时演示注册或 mock 替身制造产品成功。

主会话给出确切OID16并明确授权后，数据库准备已执行；未通过后台写入任何平台数据连接、绑定或定义。本记录独占维护；实际结果必须写明操作路径、观察值与证据，不把阅读源码、HTTP 提交成功或保存 toast 当作业务执行成功。

## 真实数据库准备结果

2026-11-01，专用准备器仅在内存加载被git忽略的 `backend/.env`，未输出连接串、用户名或密码。执行前逐项断言：配置host为loopback且解析地址全部loopback，主库与QA库名均精确为 `rengu_ontology`，主库/QA的host、port、database逐项一致，运行settings与`.env`值一致，真实数据库server地址仍为loopback，workspace16的code和未删除状态匹配。所有布尔守卫通过后才进入建表阶段。

三张新表、新专用LOGIN、限定授权、合成行在**同一个数据库事务**提交。执行前目标三表与专用角色均不存在；没有用IF NOT EXISTS吞掉未知对象冲突，没有DROP、TRUNCATE或重灌。三表COMMENT均写入专属标记 `Codex synthetic fixture equipment-verification-20261101 workspace=16`。准备器初期的本机导入路径/守卫查询兼容问题均在有效DDL之前停止或回滚；最终一次完整事务才提交下列fixture，这些准备器调整不属于产品业务验收。

| 新增表 | 已提交行数 | 真实字段与合成数据 |
| --- | --- | --- |
| `public.deep_device_w16_asset` | 25 | `id bigint PK`、`ontology_id bigint`、`code varchar(64)`、`name varchar(128)`、`device_type varchar(32)`、`office_area varchar(32)`、`status varchar(32)`、`last_inspected_at timestamptz`、`lock_version integer default 1`、`updated_at timestamptz default now()`；打印机13/投影仪12、办公区A13/B12，待检9/正常8/需维护8。主键16001–16025，编号OFFICE-001–025；最后两台检查时间为空，其余为2026-10-01起每日一台 |
| `public.deep_device_w16_task` | 37 | `id bigint PK`、`ontology_id bigint`、`code varchar(64)`、`asset_id bigint FK`、`title varchar(128)`、`status varchar(32)`、`due_at timestamptz`、`lock_version integer default 1`、`updated_at timestamptz default now()`；主键16101–16137，编号MT-001–037；前17台各两任务、18–20号各一任务、21–25号零任务；待处理13/处理中12/已完成12 |
| `public.deep_device_w16_extra` | 15 | `id bigint PK`、`ontology_id bigint`、`asset_id bigint FK`、`calibration_note varchar(256)`，`UNIQUE(ontology_id,asset_id)`；主键16201–16215，仅前15台有备注，其余10台无补充行 |

所有记录的ontology_id均为16；asset/task各有 `(ontology_id,code)` 唯一约束，task/extra的asset_id只引用本任务新asset表。使用显式整数主键，没有创建序列。

提交事务内的真实断言结果：

- 三表均ENABLE+FORCE RLS，均恰好一条PERMISSIVE/public/ALL标准ontology_scope policy，USING与WITH CHECK一致。
- 有效运行角色按正常工作空间作用域16分别读到25/37/15，换为无对应行的其他作用域分别为0；不是依赖超级用户绕过RLS所得计数。
- LEFT JOIN缺补充行计数精确为10，无工单设备计数精确为5。
- 新源LOGIN无superuser/BYPASSRLS/CREATEDB/CREATEROLE/REPLICATION/INHERIT及任何角色成员关系；直接表授权集合精确等于三张新表的SELECT，没有既有表授权，也无INSERT/UPDATE/DELETE/TRUNCATE/REFERENCES/TRIGGER权限。
- 应用/QA有效角色仅增加这三张新表SELECT；本地动作另给运行角色asset表 `status,last_inspected_at,lock_version,updated_at` 列UPDATE。真实断言status可UPDATE，id不可UPDATE；没有授业务主键/编号写入。

提交后再以新源LOGIN建立真实新连接（`ssl=false`），成功登录；核对所有非系统schema的**有效**SELECT权限集合，精确只有上述三表（不仅检查直接GRANT），未设置工作空间上下文时三表均读到0行。再以现有QA连接新建只读事务，沿用产品正常的scoped角色/GUC收窄语义，真实读到25/37/15，其他作用域为0；asset实际可UPDATE列集合精确等于status、last_inspected_at、lock_version、updated_at。这些是fixture提交后的最小真实数据库断言，不是产品HTTP/GUI成功替身，也不是pytest/全量测试。

新源用户名和随机密码仅存放在 **`%TEMP%\rengu-ontology\deep-verification-20261101\source-login.json`**；同文件包含host/port/db/type/schema/三表名供主会话正常UI填表。目录关闭继承并仅允许当前Windows用户和SYSTEM，文件继承此私有ACL，均已核对；文件不在仓库，不在日志或本文保存任何凭据内容。主会话获授权仅通过CUA在内存读取该新文件并填本机连接。

保留与清理：默认保留三表、源LOGIN、合成行和私有凭据文件，供业务复核与正常启动后恢复读取；目前未清理。将来清理必须再次明确目标与授权，先核对专属COMMENT/OID16及正式UI引用，先通过正常UI解除本轮新对象/连接引用，再只处置这三张新表、其精确授权和该新LOGIN（task/extra引用asset，顺序需正确），最后删除本任务私有文件；不得CASCADE波及未知依赖，不删除工作空间以外既有记录。未知依赖或专属标记不匹配即停止，不自动删除。

## 正式接入路径的源码结论

| 项目 | 当前源码契约 | 证据与实际状态 |
| --- | --- | --- |
| 本地业务数据登记 | `GovernedBinding` 持久化对象、主表、读列、写列和可选补充表；每个请求的受控 registry 从数据库加载并重新校验 | `backend/app/modules/data/governed_binding.py`、`backend/app/common/sql_guard.py`；仅源码核对 |
| 表范围 | 只接受应用 PostgreSQL **同库、public、非平台业务表**；表名需小写字母开头、最多63字符 | `inspect_table()` / `object_for()`；因此独立 schema 不适用，采用新专名前缀三表 |
| 表身份与隔离 | 必须有整数主键 `id` 和 `ontology_id`，普通表 relkind=r，ENABLE/FORCE RLS，恰好一条标准 policy | `inspect_table()`；未查询当前数据库验证 |
| 标准 policy | `TO public FOR ALL USING (ontology_id=NULLIF(current_setting('rengu.ontology_id',true),'')::bigint) WITH CHECK (...)`；两表达式一致 | `policy_safe()` 严格规范化校验；不新增宽松 policy |
| 数据源关联 | 对象已发布，`source_table` 为表名或 `public.表名`，来源为当前空间 PostgreSQL 数据源的编号或名称；新定义应采用稳定 DS 编号 | `validate_binding()`、`data/mappings.py` |
| 同库判定 | 数据源 host/port/database 要与应用配置逐项相同；QA只读连接也必须与应用 host/port/database 相同 | `validate_binding()`；`localhost` 与 `127.0.0.1` 不会在此自动等同 |
| 正式 UI 导入连接 | 新建连接的密码非空必填；导入预览解密该连接并真实内省，不允许无密码占位或假成功 | `schemas.DatasourceCreate`、`ontology_import.inspect_source()`；因此需要专用合成源登录身份 |
| 连接许可 | 现有真实连接许可及允许主机，或已启用的 `RENGU_DS_LOCAL_CONNECT=1` 精确本机同库路径 | `connector.permitted()`；未读取/改变本机环境，也未调用真实连接 |
| 读列 | 公开、非敏感、未变换的对象属性映射列；必须包含对象主键列；最多100，不重复 | `validate_binding()` |
| 写列 | 必须为读列子集，排除对象主键、敏感/隐藏/变换列和 `id/code/ontology_id/lock_version/updated_at/created_by`；表须有UPDATE权限及 `lock_version/updated_at` | `validate_binding()` |
| 补充表 | 同库public标准RLS表，1:1关联、关联列实际类型相同，补充侧 `joinKey` 或 `(ontology_id,joinKey)` 有有效立即非部分唯一约束；仅投影公开映射属性 | `validate_supplementary()` |
| 多条维护工单 | 建第二对象及N:1关系；不得将1:N强塞补充表 | 补充表唯一约束契约、正式UI说明 |
| UI权限 | 同时具备 connections/manage、object-types/manage，且是工作空间 owner | `governed_router.authorize()`；admin身份本身不能代替空间owner条件 |
| 重启后的恢复 | 表/数据/连接/定义/绑定均持久化，`load_bindings()`不依赖 `RENGU_RESCUE_DEMO` 或一次性全局注册；预期正常重启后恢复 | **仅源码推断，未实际重启验收**；本轮禁止为此擅自重启 |

## 待授权后的最小 fixture 约定

先锁定新空间编号 `OID`，表名建议 `deep_device_w<OID>_asset`、`deep_device_w<OID>_task`、`deep_device_w<OID>_extra`。所有行仅属于该 OID；创建前精确检查三张目标表不存在，若碰撞则停止，绝不替换、清空或重新写入未知表。仅在确认应用和QA连接都精确指向 loopback 的 `rengu_ontology` 后执行。

| 表 | 最小字段 | 数据与用途 |
| --- | --- | --- |
| asset | `id bigint PRIMARY KEY`、`ontology_id bigint NOT NULL`、`code varchar(64)`、`name varchar(128)`、`device_type varchar(32)`、`office_area varchar(32)`、`status varchar(32)`、`last_inspected_at timestamptz`、`lock_version integer NOT NULL DEFAULT 1`、`updated_at timestamptz NOT NULL DEFAULT now()` | 最终授权扩大为25台虚构办公设备、待检/正常/需维护、办公区A/B；用于对象、日期筛选、统计及本地主表status动作写回 |
| task | `id bigint PRIMARY KEY`、`ontology_id bigint NOT NULL`、`code varchar(64)`、`asset_id bigint`、`status varchar(32)`、`due_at timestamptz`、`lock_version integer NOT NULL DEFAULT 1`、`updated_at timestamptz NOT NULL DEFAULT now()` | 至少一台设备对应两条工单，另有一台零工单，验证N:1、反向关系与正确计数；仅合成记录 |
| extra | `id bigint PRIMARY KEY`、`ontology_id bigint NOT NULL`、`asset_id bigint NOT NULL`、`calibration_note varchar(256)`、`UNIQUE(ontology_id,asset_id)` | 对应设备的1:1校准备注，允许其中一台无补充行，以核对LEFT JOIN保留主表设备 |

三表均启用并强制标准工作空间RLS，禁止给新角色任何既有业务表权限。主表业务主键选真实源单列主键 `id`，显示标题选 `code`。三表 `id`可使用显式合成整数，避免为fixture增加序列权限。主表/补充关联均为bigint，避免SQL反射后精确类型不相同。

正式UI新连接需要独立的合成专用LOGIN：随机新密码，仅本机使用，非superuser、非BYPASSRLS、非CREATEDB/CREATEROLE，不授现有角色成员关系，不读或复制现有源凭据。该新凭据通过正常数据连接表单加密保存，不写入文档或共享命令。仅授本库CONNECT、public USAGE和三表SELECT；不授源连接写入。连接模式选“手动”，不新增同步或CDC。

应用有效角色 `rengu_ontology_scoped` 及QA真正生效的只读角色需要三表所需列SELECT；本地主表动作才授asset主表业务写列与乐观锁/更新时间所需UPDATE。只在这三张新表上授予，不扩大任何既有对象授权。QA连接如为scoped成员会在事务中切为scoped；如非成员则使用自身的非BYPASSRLS普通身份。具体实际角色和连接安全断言等待受限执行阶段，不输出凭据。

源连接元数据内省可能列出public中其他表的名称/结构；最小账号不因此获得其他表数据SELECT。本轮只选三张目标表，不读取其他表行。导入预览保持 `sampleValues=false`；源内省连接未设置工作空间GUC时标准RLS不会暴露fixture行。产品数据读取通过应用/QA正常工作空间作用域完成。

## 正式 UI 最少操作与接口字段

1. 官方CUA创建并进入新工作空间，记录OID、名称及当前owner。数据库准备方只在收到该OID与执行指令后建立前述三表/合成行/最小新源账号。
2. 构建 → 数据接入 → 数据连接，新建“深度设备合成源”：PostgreSQL、应用实际loopback host/port、database=`rengu_ontology`、新专用user及密码、手动模式，按本机真实TLS条件选择，不通过改变环境绕过拒绝。正常测试连接、保存、更新public库表目录。
3. 当前主会话已在正式导入预览准备两个对象：`OfficeDevice / 办公设备`、`MaintenanceTask / 维护工单`，owner均admin，主键id、标题code，并准备task.asset_id→asset.id关系。正常保存调用 `commitObjectImport(...publish:true)`，定义立即生效；随后核对映射为DS-001及两张正确源表，不能把保存定义当作读取授权。extra不导入第三对象。
4. 设备对象→“编辑定义”→“新增属性”：名称“校准备注”、API **`calibration_note`**、类型“文本 string”、来源列 **`calibration_note`**。展示普通、非敏感、非主键/标题，可保留列表显示，无变换；保存定义。此前示例calibrationNote不符合新增属性全小写规则，现已更正。不要用属性页快捷新增，因为该入口source初始为`--`；完整“编辑定义”能明确填写来源列。该列只存在extra，不勾进主表读列。
5. 数据连接→DS-001→“库表”→asset行“配置 ▾”→**“读取授权 · 办公设备”**，打开“配置受控业务表”。在“允许读取的属性”勾主表 `id/code/name/device_type/office_area/status/last_inspected_at`；“允许动作修改的属性”仅按需求选择status、last_inspected_at（列级授权潜在限制见后文）。启用“补充表（1:1）”，表名填 **`deep_device_w16_extra`（不带public.）**，点“检查表”，主表关联列选id，补充表关联列选asset_id，“补充字段映射”选校准备注（calibration_note→calibration_note），点“保存授权”。持久化fields=`{"calibration_note":"calibration_note"}`；UI携带当前bindingVersion，冲突时需重开加载，不伪造版本。
6. task同样正式绑定必要读列，写列空。关系类型新建工单→设备，task.asset_id→asset.id，N:1；不依赖猜测关联或笛卡尔JOIN。
7. 使用区核对对象列表、详情、补充字段、工单关联，再进入问数及本地动作；同时记录预期行和实际行。任何失败先记录失败，不用后台直接改定义/绑定来替产品返回成功。

接口均使用正常身份及当前 `X-Ontology-Id: OID`；下列仅记录契约，不代表已调用：

| 接口 | 请求要点 |
| --- | --- |
| `POST /api/data/v1/datasources` | `name,type:"PostgreSQL",host,port,database:"rengu_ontology",user,password,ssl,owner,mode:"手动",cron`；不在日志记录password |
| `POST /api/data/v1/datasources/{DS}/tables:refresh` | PostgreSQL不传请求体；当前仅DM接受schemas参数。正常测试连接成功亦会尝试刷新目录 |
| `POST /api/data/v1/datasources/{DS}/import-preview` | `{"schema":"public","tables":["目标asset表","目标task表"],"sampleValues":false}`；返回真实fingerprint和proposal |
| `POST /api/data/v1/datasources/{DS}/import-to-cr` | 使用预览fingerprint，`objectTypes`逐表给`table,id,name,api,owner,pk:"id",titleField:"code"`；新草稿title，正常UI保存时publish=true；不能拿固定旧fingerprint |
| `GET /api/data/v1/governed-bindings/{对象编号}` | 验证并返回候选列、当前读写授权、supplementaryAvailable及bindingVersion |
| `GET /api/data/v1/governed-bindings/{对象编号}/supplementary-columns?table=目标extra表` | 校验extra并返回实际列和可匹配对象属性 |
| `PUT /api/data/v1/governed-bindings/{设备对象编号}` | `{"readColumns":["id","code","name","device_type","office_area","status","last_inspected_at"],"writeColumns":["status","last_inspected_at"],"bindingVersion":GET返回值,"supplementary":{"table":"deep_device_w16_extra","mainKey":"id","joinKey":"asset_id","fields":{"calibration_note":"calibration_note"}}}`；writeColumns待UI实际可写候选核对，不通过扩大数据库授权绕过 |
| `PUT /api/data/v1/governed-bindings/{工单对象编号}` | 必要readColumns含id，`writeColumns:[]`，当前bindingVersion，`supplementary:null` |

## 待执行的业务验收与证据状态

### 已发现的真实UI缺陷：新建连接负责人默认旧演示姓名

主会话在ws16、admin已登录的正式UI打开“新建连接”，实际观察负责人默认仍为“王敏”；当时尚未填写凭据或保存。源码追踪确认 `Connections.vue` 的集中 `blank()` 将owner硬编码，初始化及表单重置两处均复用它。

按主会话授权，仅在该文件引入既有auth store，将 `blank().owner` 改为当前会话的 `user.name || user.account || ''`。编辑已有连接分支继续使用原 `d.owner`，不改历史owner数据，也不添加身份回填写库。一次Vue SFC脚本及模板编译通过，差异空白检查通过；主会话随后正式UI重开表单，负责人实际为admin，复验通过。未新加测试或执行全量类型检查/构建。

范围外仅定位、未改：`ExternalSystems.vue` 的OWNERS固定姓名清单（约301行）及blank owner硬编码（约304行），另有约217行人工核对演示文案带固定姓名；不据此断言真实API编辑路径有相同缺陷。

### 已发现的真实UI缺陷：源账号只能读取三表，目录却显示139表

主会话通过正式UI为ws16保存了 `DS-001 / 设备验收合成库`，负责人admin；表单真实测试37ms，保存后正常连接测试42ms成功。但“库表”实际显示139张public表，含平台表名和估算统计。主会话没有点击任何非fixture表。

限定只核对这个新DS的配置及加密密码，全部在内存处理，不输出用户名、密文、密码、连接串：存储host/port/database/user及新保存密码与私有fixture配置逐项一致；用真实 `connector._engine()` builder建立的源会话，`session_user=current_user=本任务新源LOGIN` 为true，当前数据库精确为rengu_ontology。源端仅执行只读身份/权限/结构目录查询，未读取既有表业务行。

真实结果：public系统目录有144张表，源身份拥有表SELECT或任意列SELECT的只有3张；现有 `connector._list()` 返回139张（排除5张框架系统表），新DS目录缓存也是139，当前该新DS映射对象数0。根因是Inspector的PG目录可见性未按源读取权限过滤；不是应用owner连接，不是演示fallback，也不代表源账号读到了这些业务行。

经主会话确认后已最小修复两个产品文件：`backend/app/modules/auto_ontology/introspect.py` 新增共享PG权限/统计批量查询，按schema USAGE及 `has_table_privilege(...,'SELECT') OR has_any_column_privilege(...,'SELECT')` 筛选；`backend/app/modules/data/connector.py` 仅对筛选后的表反射字段/备注，直接复用该查询的reltuples统计，不再另查全部表估算。每schema一次权限/统计查询，无每表权限N+1，column-only授权保留。显式按表导入也走共享过滤，请求存在但无权读取的表明确抛Forbidden403，不能手填绕过目录；DM/MySQL保持原机制。

新增且仅执行一次离线断言 `python -m scripts.check_pg_metadata_visibility`：批量查询一次、表授权和column-only表均保留、未授权显式表403、非PG不执行PG权限查询，通过。该离线小检查使用内存对象，仅验证代码分支，不声称真实权限通过。

额外仅一次限定新fixture真实builder只读元数据复验：同一新DS身份/密码仍匹配，目录结果精确3表，三fixture显式反射成功，手填无读取权限的action_run在反射字段前被拒403；未读取该表业务行。数据库旧目录缓存仍为139、映射对象仍0，准备方未在后台修改缓存，也未重启服务。

旧目录缓存不因代码修改自动重写。主会话正常加载后，通过正式UI再次“测试连接”，沿用既有refresh逻辑移除未映射且不再可见的目录行；本轮DS-001当时没有映射，**正式UI已真实刷新139→3，通过**。已映射目录和定义按既有规则保留，显式重新内省无源读取权限时返回403；本次未增加目录DTO/界面的单独“不可读”状态字段，历史目录快照保留不等同于当下源可读。

### 导入后读写绑定与补充表的只读源码指导

主会话正式UI已保存2对象及1条FK关系：OfficeDevice办公设备、MaintenanceTask维护工单，owner admin，工作checkpoint为 `v2026.11.3`；导入保存通过，后续授权仍需独立完成。界面可复用现有的 `Connections.vue → GovernedBindingModal.vue`，不需新建绑定页面。task行同样“配置 ▾→读取授权 · 维护工单”，勾id/code/asset_id/title/status/due_at，允许修改留空；asset先只读也可单独保存，再补充1:1配置，后一次仍需保留之前的读列选择。

补充字段候选只来自对象**已保存的公开属性**，其实际source/backing列需存在于extra；因此应先完整编辑定义，添加来源列明确的calibration_note，再重新打开读取授权和“检查表”。不在候选中时先核对来源列/隐藏/敏感/变换，不能随意给API写入不存在的映射。extra的asset_id与asset.id均bigint，数据库已存在UNIQUE(ontology_id,asset_id)及强制标准RLS；补充字段只读。主表readColumns必须含id，补充field不进入主表读列；任务1:N不使用此补充功能。正常保存后重开读取授权，检查读列、写列和extra选择仍保留，再到使用区看真实25设备/37任务、15备注/10空备注和5零任务设备。

为后续真实筛选/问数，可在办公设备“编辑定义”各属性“设置”中把status/device_type/office_area设为可分组、可筛选；last_inspected_at设可筛选、可排序；保留已有主键id、标题code、当前DS/sourceTable和所有真实源列。关系预览若选择了导入的FK，保存后核对维护工单.asset_id→办公设备.id为N:1，不再重复新建相同关系。

### 已发现并修复的真实UI缺陷：列级UPDATE被整表权限误拦

主会话在上述导入保存后，通过正式UI打开asset“配置→读取授权”：允许读取的10属性正常加载，但“允许动作修改的属性”实际为空，没有checkbox，复现此前源码预判。fixture严格按授权仅给status/last_inspected_at/lock_version/updated_at四列UPDATE，未给整表UPDATE；旧`inspect_table()`却只检查has_table_privilege(...,'UPDATE')，与最小列授权不兼容。

经主会话明确授权，grep全部调用后最小改动 `backend/app/modules/data/governed_binding.py` 和 `governed_router.py`：在既有表元数据SQL中使用has_column_privilege批量返回真实UPDATE列集合；整表UPDATE仍自然兼容。保存校验要求每个writeColumn以及lock_version、updated_at均有UPDATE；详情候选逐列检查并要求两个系统写列权限齐备。原主键/code/ontology_id/系统字段不可直接授权、敏感/隐藏/变换拒绝、平台表拒绝和强制RLS校验均保留。没有扩大GRANT、连接数据库、改表/数据或前端。

新增一个离线小检查 `python -m scripts.check_governed_column_updates`，验证反射列集合、精确UI可写候选、拒绝无权业务字段及缺系统字段权限，通过；首次检查的内存Session.scalar签名遗漏参数导致辅助检查本身停止，修正这个辅助对象后再通过，不属于产品真实运行失败。差异空白检查通过，未运行后台测试suite。

主会话随后正常API-only加载修复（listener22256、launch40676，15:45:04，readyz正常、envhash不变；进程操作由主会话完成）。**真实UI复验通过**：重开asset授权，原7个read保留，可写候选精确仅status/last_inspected_at；两项勾选保存后再次重开，两项仍checked，补充表启用、表名、id→asset_id关联和calibration_note字段映射全部保留。没有扩大整表GRANT。此结论证明授权持久化及候选正确，尚不替代实际动作写回。

### 对象、补充表与关系的真实UI结果

主会话真实观察：OfficeDevice列表总数25，末页21–25号设备全部仍在，校准备注均显示`--`，22号设备详情正常；这证明缺extra行的设备没有被INNER JOIN漏掉。打印机筛选从第3页自动回到第1页，总数13，与fixture一致。MaintenanceTask列表总数37，任务16101的关联设备为16001，关系跳转正常。根源数据未由准备方再次查询或修改，以上为主会话正式UI证据。

### 下一步本地函数接动作：仅源码契约，尚未执行

正式路径为“构建→动作类型→新建动作类型”，使用既有 `NewActionEventForm`、`ActionExecutionEditor`、`ActionValueSource`，不需新增产品实现。最小基本信息：名称“登记办公巡检”、API `RecordOfficeInspection`、关联对象选择“办公设备（OfficeDevice）”、负责人admin、允许发起人可填admin。参数页添加一个整数参数：名称“巡检得分”、API `score`、必填，默认值/校验保持`—`。本体编辑添加“修改”，对象选择办公设备，目标选“当前动作目标对象”，添加status属性赋值；取值选“已发布函数”，固定 `FN001 / inspection_label / v1.0.1`，其score输入选动作参数score。写回保持“本体（无外部写回）”，提交条件/副作用可空，正常保存立即生效。

编辑映射等价结构如下，由UI生成；obj必须来自真实对象下拉的ID，不能根据API名猜编号：

```json
{
  "target": {"context": "id"},
  "values": {
    "status": {
      "function": "FN001",
      "version": "v1.0.1",
      "inputs": {"score": {"param": "score"}}
    }
  }
}
```

执行前须有已保存动作、已发布的固定函数版本、目标设备可见、status在已授权写列中，以及action-center/manage和对象动作操作权限。当前admin若仍为对象及动作创建者、空间owner且没有显式冲突对象权限，可走现有owner创建者路径；若权限不足，正常发起目录会显示原因，不能扩大授权绕过。

使用区“动作中心→发起动作”：选择该动作、目标16001/OFFICE-001、参数`{"score":90}`，先校验参数与条件，再发起暂存，随后“确认提交→确认并执行”。该内部动作通过现有同步本地执行通道，不需额外worker；应核对动作最终成功及设备状态由待检变正常，再按需要新发起score89核对变需维护。动作类型页“试运行”只校验参数/条件，不能当作status已改；成功toast或待确认记录也不能替代最终状态。此段只是仅读源码指导，没有创建/执行动作或重新查询数据库。

| 验收项 | 预期应观察到的业务结果 | 本轮状态 |
| --- | --- | --- |
| 新空间、身份、当前版本 | 新OID、owner一致，既有空间无编辑 | 主会话已CUA创建ws16；后续定义待操作 |
| 正常连接与表目录 | 真实连接测试、三张目标表与正确结构；估算行数不冒充实际COUNT | 主会话正式UI连接测试通过，目录139→3复验通过 |
| 两对象导入和已有字段编辑 | id主键、code标题、映射不变；仅改说明可保存并即时更新概览 | 正式UI2对象+1FK导入保存通过，checkpoint v2026.11.3；已有字段编辑待执行 |
| 主表受控授权 | 仅勾选列可读；主表status可写，身份和系统字段不可写 | 原read7、仅2write候选、两write保存重开保持及supplement配置保持均正式UI通过；动作实际写回待执行 |
| 对象列表/详情/筛选 | 25台设备精确匹配fixture，状态/日期筛选正确 | 正式UI25设备、22号详情、打印机13及page3→1重置通过；日期筛选待执行 |
| 1:1补充表 | 设备备注正确，缺补充行的设备仍可见 | 15备注/10缺行fixture已准备；正式UI末页21–25缺备注仍保留、22详情正常，配置重开保持通过 |
| 1:N关系 | 两工单同属一设备、零工单设备存在，跳转对象正确 | 正式UI37工单、16101→16001关联跳转通过；零工单聚合待问数核对 |
| 自然语言单表问数 | 设备清单、状态分组及日期区间与fixture一致 | 待执行 |
| 自然语言补充字段/多表问数 | 校准备注、每台设备工单计数不漏零、不重复设备、不混字段 | 待执行 |
| 页面/图表 | 保存查询与重新打开，图表结果与实际查询一致 | 待执行 |
| 本地主表动作 | 正常动作授权/执行后仅指定设备status改变，锁版本推进，列表与问数读到新值 | 待执行 |
| 定义编辑后数据保持 | 修改说明/名称后既有数据、映射、绑定未丢失 | 待执行 |
| 正式重启恢复 | 同表/同定义/同绑定恢复读取 | **本轮不擅自执行；仅源码推断** |
| 跨空间隔离 | 新空间查不到未授权对象/数据；新fixture在其他空间不可见 | 待有正常UI路径再执行；不能以源码宣称通过 |

## 历史未测项与本轮边界

已阅读《保留现有数据重新同步操作说明》、26-10-31导入编辑、mirror、CDC相关契约。旧操作说明及完整手册部分达梦段仍包含“每批手点”“旧删除行仍可查询”等旧行为描述；26-10-31实现契约已规定手动任务自动续批、完整成功后通过is_current对齐源删除并物理保留历史，不能将这些不同版本说明混为本轮实测结论。导入已有大写/双下划线字段的编辑曾由26-10-31真实GUI验证，但本轮合成全小写字段不能代替对大小写导入场景的重复验证。

本地同库受控三表路线不使用外部镜像，也不能证明达梦镜像签名、重同步、取消续传、源删除对齐、CDC事务/ACK/基线恢复等边界。其既有实测见 `26-10-31-mirror-live-verification.md`、`26-10-31-dm-live-verification.md`、`26-10-31-cdc-live-verification.md`；本轮不新连达梦、不安装/修改捕获，不把过去实测写成2026-11-01重新通过。

目前已写本记录、只读源码并完成明确授权的真实本机fixture准备。跳过所有后台测试和进程操作，未创建任何平台定义/连接/绑定；需要注意的限制是正式UI导入要求真实新源连接、同库public与标准RLS，环境若未允许loopback则正常路径将明确受限。
