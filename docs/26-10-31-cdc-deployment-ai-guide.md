# CDC部署操作单：交给另一台电脑的AI执行（2026-10-31）

目标是保留部署机已有数据、对象、授权与事件历史，将平台库增量升级到`data_0049_external_cdc`，然后仅在明确选定的DM表上安装事务触发器CDC。本文不包含真实地址、账号、密码、令牌、连接串或密钥。执行者必须使用部署机自己的配置与备份；禁止拿开发机dump覆盖部署机。

## 0. 确认范围并保留现场

- 读取部署仓库适用的AGENTS.md与现有部署配置。保留所有未提交成果，不reset、不清库、不seed、不stamp，不执行auto_ontology任务或通用事件/动作worker。
- 安全备份本机`.env`和原数据源加密密钥。不要在聊天、日志或命令参数打印配置内容。确认进程环境没有把`.env`目标覆盖到其他库，只输出目标校验布尔值。
- 记录当前API启动方式、工作目录、PID/端口及原同步计划。先在UI暂停已启用的CDC并等待当前批结束，再停止相关API/镜像任务；积压与源capture资源全部保留。
- 使用既有Python 3.12虚拟环境，不重建venv、不用精确依赖同步卸掉现场DM驱动/DLL。新环境确需装依赖时先检查项目锁文件及厂商驱动ABI，不能把“驱动未安装”当作连接成功。

## 1. 本机完整备份与只读前检

使用部署方已有PostgreSQL安全认证方式。若现场已配置不含明文密码的libpq service，可在部署机执行：

```text
<PG_BIN>/pg_dump --format=custom --file <本机受限备份目录>/before-data0049.dump --dbname service=<部署方备份服务名>
<PG_BIN>/pg_restore --list <本机受限备份目录>/before-data0049.dump
```

`<PG_BIN>`必须解析为部署机现有PostgreSQL工具路径。也可由本地Python读取`.env`，只在子进程环境中设置PG连接变量后调用原生pg_dump；不要把解密后的DSN拼入命令或打印。核对dump非空、custom清单含TABLE DATA，记录文件大小与SHA256。此步骤不执行restore。

在`backend`目录，用已确认的本机虚拟环境运行：

```text
python -m alembic current
python -m alembic history --verbose
```

核对源码`backend/migrations/versions/data_0049_external_cdc.py`：revision=`data_0049_external_cdc`，唯一down_revision=`data_0048_mirror_scan_visibility`。本操作单的直接前置为已完成0048。若部署机更早，先按[0048操作单](26-10-31-mirror-database-migration.md)审查缺失迁移并升级；遇未知分叉/版本与表结构不符先停止，不手工stamp跳过。

只读记录升级前`external_mirror_row`总数、按ontology/object的分组计数与物理ID集合hash；不要导出原业务payload。另确认已有角色`rengu_ontology_scoped`存在。

## 2. 只应用明确0049目标

确认备份后，在同一虚拟环境和同一目标配置中执行：

```text
python -m alembic upgrade data_0049_external_cdc
python -m alembic current
```

禁止替换为`upgrade head/heads`，不运行downgrade或迁移测试。0049只新增`external_cdc_subscription`及其ontology FK、唯一约束/索引、工作空间RLS policy与scoped授权，不清旧镜像。

以只读系统目录检查以下事实：

- 当前revision精确为`data_0049_external_cdc`。
- 新表存在，ENABLE ROW LEVEL SECURITY与FORCE ROW LEVEL SECURITY均开启；ontology_scope同时有USING/WITH CHECK。
- ontology FK、(ontology_id,object_code) UNIQUE、ontology索引及PK存在；scoped角色拥有表CRUD及identity序列USAGE/SELECT。
- 既有mirror分组行数与物理ID集合hash保持。新安装应没有enabled订阅；既有订阅不得被清空或自动启用。

缺表时API应明确提示`CDC_UPGRADE_REQUIRED`。升级失败时保留原库、dump及日志，核对实际revision/结构；不DROP、stamp或自动降级。0049 downgrade本身明确拒绝丢弃恢复位点。

## 3. 正常恢复API

使用原启动环境与`.env`正常启动API，不启动动作/通用事件worker。例如已有项目入口：

```text
python <项目绝对路径>/backend/run_dev.py --api-only --host 127.0.0.1 --port 8000
```

若由AI后台启动Windows服务，使用`Start-Process -WindowStyle Hidden`及现有日志目录。不要覆盖原环境文件；核对其hash。执行一次`GET /readyz`确认status/db均ok。正常lifespan会启动CDC消费者循环；只有持久订阅enabled=true时才消费，API启动不会自行对所有源表安装trigger。

## 4. DM源端最小权限与安装方式

本实现只支持已明确选定表的普通DML触发器CDC。DM原生日志CDC是另一机制：归档/附加逻辑日志未开启时不能靠选择“实时”自动获得原生日志流。本轮不改DM归档、全局日志参数、许可证，不重启DM或安装未知软件。

推荐由源schema所有者/现场DBA执行可下载DDL，而不是长期让运行账号持有全库DBA。安装者需要在目标schema创建两张私有表及一个trigger的权限；跨schema创建需要达梦对应ANY权限，应优先用目标schema所有者，避免为单表订阅扩大运行账号权限。以实际官方权限模型为准，不臆造GRANT语句。

运行连接至少需要：批准业务列SELECT；私有journal SELECT与精确DELETE ACK；私有metadata SELECT；读取本实现校验所需ALL_OBJECTS、ALL_TRIGGERS、列及PK元数据。安装阶段还必须能够取得目标源表的bootstrap锁。`probe`中的V$读取权限与DDL/LOCK权限是不同能力；probe显示可用不代表安装权限已验证。此版本不支持把不同安装凭据藏在UI字段中，若DBA先外部应用DDL，仍须以正式连接完成幂等install校验与栅栏。

不要把平台“只读业务列授权”误解为ACK可以在READ ONLY事务中执行；源码的ACK连接独立可写，只删除该订阅专用journal的精确ID，不修改业务表。绝不向journal以外表授予DELETE作为替代。

## 5. 逐表明确安装、建立基线与启用

1. 在正式工作空间确认数据源、schema/table、单列主键和所选字段，保持未选列未授权。先选择一张获准测试表，原业务表不自动装trigger。
2. 保存数据源同步方式为“实时（CDC）”。这个保存本身不会对所有绑定自动执行源DDL。进入目标对象的CDC面板“检测”，读实际能力状态。
3. 点击预览plan，审查generation、三条DDL及独立bootstrapFenceSql。DDL与栅栏均参与planDigest。确认对象名属于该订阅；不同订阅有独立journal，不共享破坏性ACK队列。
4. 可下载SQL交现场DBA执行。disql中前两条DDL以`;`结束；CREATE TRIGGER的`END;`之后另起一行`/`，不要按每个分号拆trigger体。独立栅栏区域为`LOCK TABLE ... IN EXCLUSIVE MODE NOWAIT;`然后`COMMIT;`。DM DDL会隐式commit，不能假定rollback可以撤销整次安装。
5. 通过正式POST install/界面“确认安装”完成安装或幂等验证。源码验证归属、generation、资源ID、journal身份与trigger定义/启用，再取得选表EXCLUSIVE NOWAIT锁并commit释放。成功返回capture.bootstrapFenced=true，才允许baseline。
6. 如果提示事务栅栏未取得，可能是源表仍有未结束事务或连接没有相应LOCK权限。捕获资源保留，订阅不启用；等待源事务结束/补正确权限后重试**同generation计划**，不要drop、replan到不明新对象或忽略栅栏。它是短暂安装边界，不长期锁住源表。
7. 安装成功后明确“启用并建立基线”。捕获先于baseline存在；baseline分页期间积累dirty-key日志，完整扫描后排空全部pending再持续追更。状态应依次显示snapshot、catching_up、running，不能把首批成功当作完成。
8. 在批准测试源上用独立连接做INSERT/UPDATE/DELETE，并确认源commit后镜像自动追更；验证rollback不会产生可见journal。直接dmPython做长事务时必须显式`autoCommit=False`，不能以默认连接加一个commit调用冒充未提交事务。

普通DML触发器捕获真实行变更线索，消费者按批准主键重读当前行；它不保留所有中间after-image，不承诺多行源事务在PG原子发布。捕获时间用UTC，但capturedAt不是commit时间或commit延迟SLA。它轮询私有变更日志，持续阶段不做每秒全表扫描。

## 6. 重启、追赶与日常维护

正常重启前不删除pending。每批先在PG原子提交镜像+pending_ack，之后向DM精确ACK该批ID；进程重启先重试已持久化ACK，再读取所有可见未ACK事件。ID不是commit顺序，不得改成`id>MAX(id)`下限。迟提交小ID仍必须被消费。

监控API心跳、phase、lastObservedAt、pendingCount、pendingAckCount、最老捕获时间、错误，以及DM源journal/数据文件所在磁盘空间。未ACK日志没有TTL删除：停止消费时会继续积累，因此需要容量告警；不能用定时清表“解决积压”。低流量时没有事件不表示故障，检查观察心跳；捕获时间年龄不等于源commit lag。

暂停消费或退出CDC模式不会卸载源trigger/journal，源仍可能继续积累日志。卸载须另行明确批准，先停止订阅并核对未ACK积压与保留要求，按该generation确切对象处理；实现没有自动DROP/替换未知同名对象。旧generation恢复信息也应保留。

TRUNCATE、绕过trigger的装载、管理员禁用/篡改capture、源备份恢复或源对象重建不能当作普通DML实时捕获。维护前暂停，维护后重新验证并明确建立新基线；对象缺失/禁用、generation或所选结构失配时应失败保护，不静默降级成“实时全量轮询”。管理员无痕删pending无法由sequence空洞证明，sequence回滚本来就会有空洞；严禁旁路清理捕获表。

## 本机证据与部署边界

开发机已完成0049备份、明确revision升级及metadata核验，详细事实见[源能力记录](26-10-31-cdc-source-capabilities.md)。这些证据不能代替另一台电脑的目标核对、独立备份、权限配置、选表安装和运行验收。不要复制开发机凭据、数据源密码密文、dump或capture generation到另一套实例充当已部署。
