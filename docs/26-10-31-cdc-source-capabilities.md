# 真正 CDC 的真实 DM 能力探测（2026-10-31）

本记录是源端只读探测和实现约定。连接复用工作空间11的已保存DM数据源，在进程内解密凭据；没有输出密码、连接串、授权序列号、客户名称、原业务数据或归档文件路径。使用现有 `.venv` 和官方 dmPython；所有DM查询在 `SET TRANSACTION READ ONLY` 后执行并最终rollback。未改全局参数、归档配置、许可证或源表，未安装软件、重启DM、创建系统包或启动日志挖掘。仓库和其父级未发现适用的AGENTS.md。

## 真实能力结果

| 检查 | 真实结果 | 结论 |
| --- | --- | --- |
| 版本 | DM Database Server 64 V8；DB Version 0x7000c；build 03134284294-20241225-255012-20119 | 当前实例实际版本，不能以最新文档示例当作已支持所有接口 |
| DBMS_LOGMNR | SYS下PACKAGE、PACKAGE BODY均VALID，V$LOGMNR_CONTENTS列可读 | 安装了原生日志挖掘能力，不等于已开启CDC所需日志 |
| 归档 | ARCH_INI=0；V$DATABASE.ARCH_MODE=N；V$ARCHIVED_LOG计数0；V$DM_ARCH_INI计数0 | 当前无可挖掘归档及归档配置 |
| 附加日志 | RLOG_APPEND_LOGIC=0；RLOG_IGNORE_TABLE_SET=1 | 当前没有记录可重构行变更的逻辑日志 |
| LOB解析 | LOGMNR_PARSE_LOB=0 | 日志方案还需独立核验CLOB/BLOB，不可假定完整值可挖掘 |
| 许可证 | SERVER_SERIES=D；SERVER_TYPE=3；有效日期2026-11-02 | 记录实际值；不据此推断额外CDC商业授权，部署需核对许可续期 |
| 权限 | 当前账户可读所查系统视图，SESSION_ROLES含DBA、RESOURCE、SYS_ADMIN等 | 只读查询成功。未执行CREATE权限试探，不能声称源DDL已验证 |
| 受控合成源 | 指定fixture表当前trigger计数0 | 尚未安装CDC捕获基础设施 |

实际读取的V$LOGMNR_CONTENTS列含SCN、START_SCN、COMMIT_SCN、XID、ROLL_BACK、SAFE_RESUME_SCN、SQL_REDO、REDO_VALUE、UNDO_VALUE等，但仅查询 `WHERE 1=0` 的元数据，没有挖掘原业务日志。

当前实例不能直接提供原生日志CDC。达梦官方说明DBMS_LOGMNR针对归档日志，需先开启归档和逻辑日志；启用后仍需处理已提交事务、跨文件事务、LOB与字典变化。本轮不自动修改这些实例级设置。[DBMS_LOGMNR官方文档](https://eco.dameng.com/document/dm/zh-cn/pm/dbms_logmnr-package)

官方C/JNI接口提供事务ID、起始/提交LSN和回滚信息，且寻找事务结束记录涉及跨文件等待；这说明长期日志连接器应以安全恢复位点及完整提交事务为准，不能用本地行号冒充commit LSN。[Logmnr接口官方文档](https://eco.dameng.com/document/dm/zh-cn/pm/logmnr-interface-instructions.html)

版本和许可证列的含义来自[系统动态视图官方文档](https://eco.dameng.com/document/dm/zh-cn/pm/dm8-admin-manual-appendix2.html)。DDL权限应由安装阶段真实验证；跨schema操作另需相应ANY权限，不能因能SELECT就推断能安装trigger。[自主访问控制官方文档](https://eco.dameng.com/document/dm/zh-cn/pm/discretionary-access-control.html)

## 本次建议：选表事务触发器与变更日志

在用户明确启用的绑定上安装私有journal与行级trigger，只捕获源主键变更线索，再按当前授权列重读对应业务键。消费者短间隔轮询的是变更日志，不扫描源全表；初次baseline和显式重建才分页扫描源表。界面应明确显示“事务触发器CDC”，不能称为原生日志订阅。

达梦官方说明，trigger属于触发它的语句，trigger失败会令语句失败。普通trigger写journal与业务DML在同一事务内，不能使用自治事务、显式commit或吞掉捕获错误；回滚应同时撤销journal，另一个连接只消费已提交日志。[触发器官方文档](https://eco.dameng.com/document/dm/zh-cn/pm/trigger)；[事务管理官方文档](https://eco.dameng.com/document/dm/zh-cn/pm/management-affairs.html)。这属于待真实合成表验证的实现约定，此只读阶段没有冒称已证明运行时回滚行为。

具体契约：

1. 每个订阅有独立capture generation、不可变capture配置签名和私有journal；多个工作空间或绑定不共用破坏性ACK队列。配置签名覆盖schema、table、主键、所选结构与trigger预期定义。重新安装换generation，旧位点不得沿用。
2. INSERT写new key；DELETE写old key；UPDATE覆盖old/new key（主键变化必须同时处理）。journal只需event ID、操作、old/new key、generation与捕获时间，不保存未授权业务列。event ID是唯一身份和排序帮助，不是提交顺序，不是源commit LSN。
3. 消费查询所有当前可见的未ACK行，按ID排序取有界批，**不使用 `id > last_id`、`MAX(id)` 水位或时间戳下限**。事务A先分配小ID未提交、事务B大ID先提交并ACK后，A后来提交仍在pending集合里，下一批必须捕获。
4. 对这批涉及的业务键，读当前已提交源行并按绑定授权投影；存在则upsert，不存在则将镜像行退出current。这样迟提交小ID、ACK重放、同键连续更新和删除后重建都最终趋于源现态，不把早期after-image倒灌覆盖新状态。它是当前状态同步，不承诺保留每个中间版本。
5. PG先在同一事务中提交镜像变化、精确事件去重身份和本地批状态；成功之后才能在DM独立事务中精确ACK该批event ID。ACK失败保留pending供重放。重放必须重新检查当前源状态或确保后续事件仍将刷新该键，不能把去重事件的旧payload重复当新状态。
6. 最简源ACK为精确删除已成功应用的journal ID（含generation校验）；不以最大ID批量删除。源ACK前崩溃会重放，源ACK后重启依靠PG已持久化状态。两个数据库之间没有分布式原子commit，不声称exactly-once传输；幂等镜像状态和精确ACK实现至少一次安全重放。
7. 安装全部捕获对象并验证后才启动baseline。baseline期间继续积累journal；baseline完整完成后排空所有pending，再标记进入持续追更。baseline不设MAX(ID)截止水位。恢复baseline失败不能清空pending或旧镜像。
8. 单消费者订阅由本地持久锁协调，不并行处理同一绑定批次。多行源事务可能分批发布，当前实现不承诺跨多行业务事务在PG原子可见；每批为本地原子发布。若业务依赖跨行事务一致性，应采用commit LSN可用的日志连接器并按源事务聚合。
9. pending永不按年龄自动淘汰；只有精确ACK才清理。监控pending计数/最老时间/源空间压力，停止消费期间journal持续保留。若人为清理、重建或丢失capture对象，必须停止CDC并要求baseline，不能从本地最大ID继续。普通sequence空洞来自回滚，不能把每个空洞误判日志丢失。
10. 每次消费前核验generation、配置、journal与trigger存在和启用状态及所选结构。缺表、缺trigger、禁用、重建或结构失配均应明确失效，不降级成“实时”全量轮询。仅新增未授权列不扩大投影。安装器不能修改业务列定义。

任意持有DBA权限者直接删除pending、临时禁用trigger后又无痕恢复，无法靠普通journal序号证明没有缺口。此方案需将capture对象当受保护基础设施，禁止旁路修改；若有过旁路维护、TRUNCATE、绕过trigger的批量装载或恢复，应明确重新baseline。实现应检测可观测的对象缺失/禁用/签名变化，不能宣称抵御管理员篡改。TRUNCATE属于DDL，普通DML触发器不能覆盖，需列为受控维护操作而非静默漏删。

源端安装/卸载DDL必须可导出，显示目标源表和capture对象。部署说明需区分PG应用迁移与源端选表安装；源DDL通常隐式提交，失败时不能假定rollback清理整个安装。停用应保留未ACK日志，卸载应先明确停止并处理积压，不能顺手drop用户表。

## 后续有界真实验证要求

仅对已授权的独立合成表验证：INSERT/UPDATE/DELETE、长事务未提交不可见、整体/保存点回滚、两个独立键事务逆序提交（小ID迟提交）、主键变更、ACK失败重放、停止消费后的追赶、源结构漂移、缺capture对象错误。不得对原project/task加trigger或执行DML。本记录写成时这些CDC运行验证尚未执行。

## 实现与本机准备进展（运行边界）

新增 `backend/app/modules/data/dm_cdc_source.py`，提供显式plan/probe/install/validate/read_changes/ack；订阅资源由generation确定，安装不替换或删除不明同名对象。所有权metadata用CTAS与创建一并写入；逐步安装承认DM DDL隐式提交，失败保留对象并可校验后继续。验证包含源结构、generation、资源对象ID、journal identity/主键、trigger启用及预期body。事件主键保持str/int原类型，兼容现有mirror的JSON key格式。

触发器明确通过 `SYS_EXTRACT_UTC(CURRENT_TIMESTAMP)` 写UTC捕获时间，API返回带`+00:00`的capturedAt，避免把无时区的源本地timestamp误标UTC。已在真实DM只读调用此函数成功；这是捕获时间，仍不是源commit timestamp。函数依据[达梦官方日期时间函数](https://eco.dameng.com/document/dm/zh-cn/sql-dev/practice-func.html)。

已执行最小离线检查 `python -m scripts.check_dm_cdc_source_contract`：独立订阅资源隔离、安全标识符引用、精确ACK、event2确认后晚提交event1仍被读取。新模块实际对真实fixture执行只读probe/plan成功，未将这些检查冒称为trigger事务运行证明。

本机PG升级前已确认目标为批准的本机`rengu_ontology`。完整custom备份：`exports/database/rengu_ontology_before_data_0049_2026-10-31_073250_767074Z.dump`，2,181,441字节，SHA256 `ca3e615a28a05de44e281f0a78179aec553f964605db2084353a9e7b2d4a9f9a`；原生`pg_restore --list`成功，含140个TABLE DATA条目，没有执行还原。

经主会话明确信号，已从`data_0048_mirror_scan_visibility`仅应用`data_0049_external_cdc`。新订阅表ENABLE/FORCE RLS、1条工作空间policy、3个索引和ontology FK核验通过，scoped表及identity序列授权存在。迁移时原mirror共289行，其分组计数和全部物理ID hash前后相同；新增sub为0、enabled为0。没有做迁移回归、降级测试或seed。

等待sink最终READY后，已核实旧API为当前用户的`app.main:app`、8000监听进程，再隐藏重启为PID67340；一次`/readyz`返回`status=ok/db=ok`，Vite仍PID63072。启动前active mirror=0、enabled CDC=0，未启动动作worker，`.env`文件hash保留。此段记录时尚未在源端安装capture，后续真实事务验收另记，不把API就绪当作源CDC通过。

## 真实选表安装中发现并修复的问题

正式GUI仅对ws15的`LiveFixture`生成并执行安装，generation=`5e306f6d96f844b4b37de15a445cd262`，源为唯一授权合成表；原project/task没有安装capture。源端已提交对象始终保留，没有通过DROP、替换未知对象或换generation绕过错误。

1. **Trigger body兼容。** 首次真实安装返回`CDC_CAPTURE_INVALID`，提示触发器被禁用/定义不匹配。只读核对STATUS为Y、目标schema/table正确；本DM的ALL_TRIGGERS.TRIGGER_BODY实际返回完整`CREATE OR REPLACE TRIGGER ...`（133 tokens），并非仅BEGIN...END体（113 tokens）。已严格规范化完整前缀后比较完整预期语句或预期体，仍拒绝修改后的定义。已有资源同generation验证成功。
2. **目录NUMBER返回Decimal。** 栅栏成功后的GUI安装retry曾HTTP500。真实只读检查证实三个ALL_OBJECTS.OBJECT_ID均为Decimal，capture.resourceIds因而无法JSON持久化。已只将对象ID规范为int；实际validate返回可完整json.dumps，最小离线检查增加该回归。没有把此500当作源DDL rollback；原资源继续保留供同plan重试。
3. **初装事务栅栏。** 安装资源严格验证后，必须执行选表`LOCK TABLE ... IN EXCLUSIVE MODE NOWAIT`，成功commit释放后才返回bootstrapFenced及UTC确认时间；enable拒绝没有证明的capture。DDL与栅栏均参与planDigest，GUI和下载SQL分别显示。真实忙测试使用显式autoCommit=False：自己连接新增1条journal，独立READ ONLY连接仍读到业务旧值及既有pending数1；GUI安装实际返回409“源表事务栅栏未取得…保留…同一安装计划”。随后于UTC`2026-10-31T07:55:03.779108Z`确认commit，独立读取pending从1增为2；释放后同plan/generation正式GUI安装成功，状态为已安装待启用、pending2。

**撤回早先无效的MDL推断。** 最初测试holder直接用dmPython默认连接，后来实查其autoCommit=1；那次UPDATE已经立即提交，不能作为“旧未提交DML持锁”证据，也不能据此断言CREATE TRIGGER是否等待既有DML。该观察已标记无效，没有用于CDC正确性结论。后续有效测试明确设autoCommit=False，并通过另一连接旧值/日志不可见断言证明真实未提交事务，源用户业务表没有参与测试。

一轮有效未提交验证先因前端嵌套确认框遮挡而没有实际install请求；已及时释放事务，只记录未提交可见性，不把它当busy通过。前端修复后重新短持锁，才取得上述真实409与提交后pending2证据。

这些真实问题与最小回归均是当前实现范围内的实际失败修复；没有运行全量pytest、迁移测试、修改DM全局参数或开启原生日志挖掘。另一台电脑的AI部署步骤见[独立部署操作单](26-10-31-cdc-deployment-ai-guide.md)。

## 真实初始snapshot中断与恢复（已完成）

正式GUI在同generation安装成功后，以每批2行启用CDC基线。只读watcher捕获新`cdc_snapshot` run18/scan18：`status=running / processed=2 / complete=false / batchSize=2`，观察UTC`2026-10-31T07:59:05.607475Z`。随后核实当前用户的`app.main:app`与8000监听确为PID24600，仅强停该API，再按原`.env`正常隐藏启动；未重启DM、Vite或事件/动作worker。

新API为PID56840，单次readyz确认status/db均ok，`.env`hash保持。恢复过程中真实观察到原run18/scan18、batch2继续22、24…100；100时仍complete=false，并没有将整批边界误报完成。之后101时succeeded/complete=true，CDC转catching_up。原private pending2在基线期间保留，随后观察到本地pendingAck2→0，最终`phase=streaming / pendingCount=0 / pendingAckCount=0`。

最终核验完成于UTC`2026-10-31T08:03:35.289020Z`：**run18、scan18、generation均与中断前一致，processed101、batch2、complete=true**。未换run、重plan、清空镜像、扩大字段、改变批大小或注入时间来制造通过。最初150秒观察窗口结束时自然扫描已至80，因而延长一次只读观察直至完成；这不是CDC失败或重新触发全量扫描。精确脱敏证据保存在开发机`backend/.local/cdc-snapshot-restart.json`。

Windows启动子进程句柄导致第一watch helper等待未返回；仅结束了该自有测试helper，运行中的API56840不受影响，再用只读finalizer核对上述最终状态。这个测试覆盖真实API进程中断后的基线追赶；不宣称强杀了DM服务或测试了数据库损坏恢复。

## 原业务与工作空间保护收尾（已完成只读核验）

在上述恢复完成后，对原源表仅执行READ ONLY SELECT：Project9、Task83，各自新增RGCDC trigger均为0；目标schema中授权fixture以外的RGCDC trigger总数为0。原ws11当前镜像仍Project9/Task83，CDC订阅0，原DS-001仍manual。没有读取/输出原业务payload。该阶段的有界验证写入仅属于唯一合成源fixture；后续fixture的CRUD/回滚/逆序提交等验收由主会话协调另一执行者，避免多agent并发改同一测试行。

## 最终事件遗留修复载入后的CDC保持性

事件遗留六文件最终静态编译通过后，按主会话明确指令正常隐藏重启原API56840，载入事件相关最终改动；当前API为PID27060，Vite仍63072，`.env`hash不变。一次readyz返回status/db均ok。只读PG核验显示重启后新源观察及心跳已更新至UTC`2026-10-31T08:12:03.492287Z`，仍streaming/enabled、processed101/complete=true、pending0/ACK0，run18/scan18及原generation保持。该轮仅确认事件代码载入后CDC自动恢复，没有执行DML、源DDL或启动动作worker；事件GUI验收由主会话另记。
