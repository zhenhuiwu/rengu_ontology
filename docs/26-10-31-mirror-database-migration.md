# 镜像数据库增量升级操作单（2026-10-31）

适用于保留已有业务数据、绑定、配置与事件历史的部署。用户已授权必要的结构调整；本次必须应用增量迁移，不能仅更新应用而跳过新列。本机已于2026-10-31在完整备份后应用0048并重启API，部署端仍待独立执行；本文不提供任何真实连接地址或凭据。

## 改表内容与兼容边界

迁移文件：`backend/migrations/versions/data_0048_mirror_scan_visibility.py`；revision为`data_0048_mirror_scan_visibility`，前置为`admin_0047_login_sessions`。

| 变化 | 含义 |
| --- | --- |
| `external_mirror_row.last_seen_scan`，可空BigInteger | 记录某行最后被哪次扫描看到；同次扫描断点恢复沿用scanId。 |
| `external_mirror_row.is_current`，非空Boolean | 升级已有行先设true，随后新插入行默认false；保持旧数据可见，不清空物理记录。 |
| `ix_external_mirror_row_scan` | 为本体、对象、源签名及扫描标记增加索引。 |

每批仍按源键upsert。只有一次完整扫描成功的最后事务，才把该本体、对象、源签名中未被本扫描看到的旧行排除出当前镜像，并提交完整状态；物理行保留。失败或取消不把未扫行当源删除，恢复沿用同一scanId。部分扫描可混合旧值与本轮已提交新值，不能用于声称源库事务快照、多表同一时刻一致或CDC。

旧镜像原来即使显示complete，升级后也需看`deletionsAligned`：false表示删除尚待核对，须明确完成一次新扫描。`dataPublishedAt`表示完整扫描发布时间，区别于非空批次写入时间`dataUpdatedAt`、源批次核验时间`dataCheckedAt`与任务状态时间`updatedAt`。迁移前的部分扫描缺少已见标记，需明确从头扫描，不能直接据旧前缀判定源删除。

稳定签名在新同步时启用。旧签名只有当前旧元数据能精确验证时才兼容；仍有效的旧镜像应明确完整同步以升级。已失配旧镜像必须核对源绑定后重新同步，不能伪造信任或静默改签名。

## 操作步骤

以下命令仅供部署人员在自己的环境审阅后执行，全部占位符必须本机替换；不把密码、令牌或实际连接串写入命令。依赖要求为Python 3.12，以该版本`backend/pyproject.toml`及`uv.lock`为准。

1. **停止事件worker并保留积压。** 同时停止API和独立镜像任务以避免DDL期间继续写入；核对原有镜像计划，恢复API后原已授权计划可能继续运行。不要为升级清理事件、投递、镜像或快照。
2. **备份部署自己的数据库及配置。** 使用部署方已有安全认证方式备份完整平台库，另安全备份`.env`及部署配置，保留数据源加密所需原密钥。禁止用开发者本机dump覆盖部署库。以下使用部署方事先配置的PostgreSQL连接服务，不包含凭据：

   ```text
   pg_dump --format=custom --file "<部署方备份目录>/pre-data0048.dump" --dbname "service=<部署方备份连接服务名>"
   pg_restore --list "<部署方备份目录>/pre-data0048.dump"
   ```

   单独将`backend/.env`复制到受限备份位置，确认备份文件非空且可读取；验证备份恢复应使用独立库。不要提交备份、环境文件或凭据到仓库。
3. **部署同一版本代码，确认目标库与迁移链。** 确认发布材料包含0048及所有前置迁移、模型和前后端改动；不要仅依赖不含未提交文件的旧归档。在`backend`目录执行下列只读迁移信息命令，先人工核对所加载`.env`/进程环境确实指向目标部署库，勿输出连接串：

   ```text
   uv sync --inexact
   uv run --no-sync alembic current
   uv run --no-sync alembic heads
   uv run --no-sync alembic history --verbose
   ```

   本机正式CLI `alembic heads`结果为`data_0048_mirror_scan_visibility`；只读源码解析必须包括带类型注解的revision赋值，不能把已有后继的`admin_0026`/`notify_0022`误判为额外head。仍只升级明确目标revision，不执行`upgrade head`或`upgrade heads`。若目标仍早于0043，必须包含并审阅0043–0047及更早缺失的依赖；不要手工stamp跳过。发现未知head、分叉或表结构与版本表不符时先核对部署版本，不强行覆盖。

   `--inexact`用于保留现场已有但未列入锁文件的厂商DM8驱动、配套DLL与其他运行依赖；后续`uv run --no-sync`避免命令再次精确同步而卸掉它们。也可直接用已确认的Python 3.12虚拟环境执行`python -m alembic ...`，不要为迁移清理/重建现场venv。核对厂商驱动与当前Python 3.12 ABI及DLL路径一致；本次不安装新驱动，也不修改现场驱动配置。
4. **应用增量升级。** 备份确认后在同一环境执行；由Alembic沿实际依赖链升级，不手写DROP、重建表、重置库或seed：

   ```text
   uv run --no-sync alembic upgrade data_0048_mirror_scan_visibility
   uv run --no-sync alembic current
   ```

   确认当前revision为0048，新两列及索引存在。缺新列时应用状态会提示`migrationRequired=true` / `MIRROR_UPGRADE_REQUIRED`；这不是可忽略的完整镜像状态。事件队列schema与业务Alembic迁移不同，确实首次缺队列schema时另按[操作说明](26-10-31-deployment-integration-operations.md)安装。
5. **先仅恢复API，读取状态并核对绑定。**

   ```text
   python <项目绝对路径>/backend/run_dev.py --api-only
   ```

   事件worker继续停止。先看同步计划、刷新状态及详情记录；核对`migrationRequired`、源指纹、结构指纹、`restartRequired`、`complete`、`deletionsAligned`与时间字段。读取授权弹窗可能实际连接源库，不把它当纯本地只读状态页。核对源、对象主键、所选字段及当前身份权限后，再由操作人明确更新/重新同步；可恢复的新扫描用“继续同步”，旧部分扫描、失配或无法验证结构按提示重新确认并从头扫描。不中途清空/解绑。
6. **完整扫描后受控验收。** 确认目标对象覆盖、当前行数、`complete=true`、`deletionsAligned=true`及发布时间；人工抽查源新增、修改、删除后的镜像可见性和物理保留情况。扫描期间源会变化，必要时再做一轮核对；不宣称全表实时相等或多表原子一致。
7. **高水位单事件验收，随后处理积压。** 先只读记录上线前事件主键高水位，明确选择高水位后的一条新事件及唯一订阅：

   ```text
   python <项目绝对路径>/backend/run_dev.py --with-worker --ontology <工作空间ID> --event-type <事件类型编码> --subscriber <唯一订阅名称> --after-id <只读核实的上线前高水位>
   ```

   此命令联合启动API与限定worker，切换前先停止之前单独运行的API以免端口冲突。它不启动通用sweep，不支持多订阅事件；高水位不能随手填0。核对实际接收端回执、规则/告警/动作结果与幂等。旧积压按原快照及业务结果明确人工处置，unknown先核查；之后才恢复单实例通用事件worker。不得把迁移成功当作积压已经处置或端到端已通过。

## 异常与恢复

迁移失败时保持worker停止，保留日志和备份，先只读确认当前revision与实际列结构；不要盲目重跑部分DDL或stamp。默认优先保留新增列，修复应用或部署兼容新可见性语义的上一应用版本。回退到完全不认识`is_current`的旧应用可能重新暴露源已删除行，应先评估，不能当无风险恢复。

源码`downgrade()`明确拒绝自动撤销当前可见性，以免重新暴露源已删除行。本文不提供或执行downgrade、DROP列、删除数据命令。确需数据级恢复，由部署人员把自己本次备份恢复到独立库，核对后再决定切换；保留原库以供核查，不把他人或开发机备份导入覆盖现网。

本机初次迁移结果：完整备份后以现有venv应用明确目标0048，当前revision已为`data_0048_mirror_scan_visibility`；两列、default与索引元数据符合设计，迁移当时镜像物理行76→76。API正常重启后单次readyz返回200，Vite保持运行。完整备份路径及哈希见[操作记录](26-10-31-deployment-integration-operations.md)。该阶段没有后台测试、迁移测试、降级验证、真实源库同步或外部动作；这是初次范围记录，不包含随后用户明确要求的真实补验。

初次本机官方IAB 2560×1440只读状态及事件空列表核查，无页面溢出、500或新增console警告/错误；旧镜像行数保留、失配及删除待核对提示符合预期。该阶段未打开读取授权、连接真实源、同步、重试或补投；当时[截图](evidence/mirror-state-after0048-2k-20261031.jpg)及范围见[统一说明](26-10-31-deployment-integration-fixes.md)。

随后按用户明确授权，已在当前0048平台上完成真实DM有界补验：原空间11 Project9/Task65→83完整对齐，原74个物理ID及定义/属性/读取绑定hash保留；专用空间15验证101行取消恢复、100+1分页、源CRUD删除soft flag、未选列不扩权及所选类型漂移失败后恢复。原源业务表只SELECT，唯一合成源表保留。现有API一分钟调度器自动追更成功后已恢复manual；真实事件/规则与本机HTTP动作回执、对账及幂等亦通过。证据见[总体验收](26-10-31-dm-live-verification.md)、[镜像技术记录](26-10-31-mirror-live-verification.md)和[自动化技术记录](26-10-31-automation-live-verification.md)。这不是新一次迁移回归或降级验证，也未覆盖部署端迁移、生产外部动作或CDC；部署方仍须独立完成本操作单。
