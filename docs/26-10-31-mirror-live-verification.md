# 达梦真实镜像联调记录（2026-10-31）

本轮使用现有真实API与真实DM源，专用工作空间 `15 / dm-live-20261031`（达梦真实联调 20261031），新建数据源 `DS-001`。凭据从原工作空间11已保存连接在进程内解密后提交；不记录密码、令牌、连接串或原业务payload。专用验证阶段不改原工作空间11；随后按本轮明确授权，在原字段授权保持不变的情况下由正常前端重新同步旧镜像，具体保留性核对见后文。原 `project`、`task` 源表始终只读。本记录是有界真实联调，不是pytest、全量回归或迁移测试。

## 已完成的原业务只读链

- 登录与新工作空间创建成功；新数据源设为手动同步，不启用生产同步计划。限定现有schema与 `project/task` 的导入预览、发布、读取授权均成功（HTTP 200/201）。
- `LiveProject / LiveProject`（code/API）选择4列；run `4`、每批 `maxRows=2` 自动续批至 `9` 行，`complete=true`、`deletionsAligned=true`、`currentStored=9`，完整发布时间 `2026-10-31T06:36:28.759969Z`。
- `LiveTask / LiveTask` 选择17列，包括真实大小写列 `experiment_Id`、`report_Download_Address`；run `5`、每批2行自动续批至 `83` 行，`complete=true`、`deletionsAligned=true`、`currentStored=83`，完整发布时间 `2026-10-31T06:37:33.891916Z`。此时原工作空间旧镜像仍为65行；本轮没有用旧镜像计数冒充源现状，后续旧镜像恢复另有记录。
- 同一时段从真实源仅查询主键集合，与新镜像payload主键集合比对：Project 9/9、Task 83/83，集合均完全相等。未输出原业务主键值或payload。
- 真实公共 `identity_rows` 对Task映射83行、Project映射9行，所有镜像物理rowid与业务id均不同，业务id未被当成内部rowid。
- 使用现有专用只读连接与 `scope_readonly_conn(15)` 执行 `run_guarded`：`live_task`、`livetask`、`LIVETASK` 均返回83；`live_project`、`liveproject`、`LIVEPROJECT` 均返回9。验证canonical名、对象别名与大小写解析。未设置连接本体scope的直接连接会因RLS得0，正式调用按公共只读契约设置scope后计数正确。
- 对象搜索真实HTTP返回 `bound=true`；每页2条，仅返回所选业务字段，部分同步时能返回已有提交并标记部分镜像，完整后状态显示完整。

## 独立合成表验证（已完成）

源端专用表 `rgv_1031_fc38f7783a` 已创建101行，无FK/trigger。包含业务 `id/code/ontology_id`、同名内部候选字段 `__mirror_row_id/__mirror_ontology_id`、大小写列、数字、时间与含NULL的CLOB。该表是本轮唯一允许写入的受控测试表；原业务表不执行DML/DDL。真实CRUD、取消恢复、结构漂移、恢复及自动轮询结果如下。

### 真实前端取消/续传与101行基线（已完成）

Root使用已登录官方IAB正常前端触发 `LiveFixture` 同步、取消、继续，没有提取浏览器Cookie、绕过登录限流或模拟HTTP认证。

| Run | 操作 | 批大小 | readCount/writeCount | 结果 |
| --- | --- | --- | --- | --- |
| 6 | 首次同步并在前端取消 | 2 | 50/50 | cancelled，processed50，complete=false，保留scanId6 |
| 7 | 前端继续同步 | 20 | 51/51 | succeeded，累计processed101，沿用scanId6，complete=true，deletionsAligned=true |

run6开始 `06:43:12.636906Z`，取消完成 `06:43:54.820005Z`；run7开始 `06:44:37.108017Z`，发布完成 `06:44:42.203482Z`。运行计数只计提交行，不包含探测行。run7运行时向真实 `_run_batch` 提交已取消旧run6作为补充服务边界验证，返回False，不能接管新run7。

基线101个业务主键集合与101条完整payload的SHA256全部相等；10个所选列包括业务id/code/ontology_id、同名候选内部列、MixedCaseValue/TestNumber/ChangedAt/State与nullable_clob。非空CLOB1行（含1500+字符），NULL100行，均完成镜像且payload hash相等。内部列真实避让为 `__mirror_row_id_1`、`__mirror_ontology_id_1`，原有同名业务值保留。真实identity映射101条，物理行号均不同业务id。受控SQL `live_fixture`、`livefixture`、`LIVEFIXTURE` 均返回101。

公共对象consumer服务补查Project9、Task83和Fixture：第一页/第二页各2行且主键集合不重叠，详情返回公开业务字段及一致业务身份；Fixture取消时返回50行并明确partial，Project/Task完整时为full。后续最终前端实际分页/详情由Root操作，服务调用不冒充HTTP鉴权覆盖。

联调脚本早期独立进程重复登录触发本机10次/15分钟防爆429，此后停止登录，未清除限流、未修改防护，后续GUI操作复用正常已登录前端会话。

### 真实CRUD与未选列新增（已完成）

仅对上述唯一独立合成表执行insert1/update1/delete1，源总数仍101。前端以批40从头扫描run8，完整完成后当前行101、物理行102，当前PK集合与真实源相等、101个完整payload hash全相等。已删主键在当前镜像0行、物理仍保留1行；新增主键当前1行，修改行payload hash与源新值一致。未清空镜像。

之后仅新增未选列 `UnselectedExtra VARCHAR(32)`，源物理11列；前端批40更新镜像run9，仍完整101行、101个payload hash全部相等。所选结构指纹保持原值、稳定签名仍匹配；读取绑定仍10列，UnselectedExtra不在授权列、不在公共projection，所有102条保留的物理payload都没有该字段。新增物理列不自动扩权。

为下一步类型漂移，前端以批2发起run10，并在累计processed52时取消；scanId10与旧结构指纹均保留，complete=false。随后源端才允许对所选TestNumber做受控类型变更。

### 所选列真实类型漂移与读取保护（已完成失败保护）

run10取消后，源端仅把TestNumber从INTEGER改为VARCHAR(32)，反射类别由integer变为string；PK集合与101行数字语义保持。前端继续同步创建run11，在源结构核验阶段failed，readCount=0、writeCount=0，累计processed52与scanId10保持，complete=false、stale=true、restartRequired=true、resumable=false，明确要求确认映射后重新开始。

失败后物理102行和当前可见条件内101行均保留；公共受控registry不登记该对象，直接公共mirror_source服务读取返回 `MIRROR_STALE / 409`，不会把已知结构失效数据作为当前绑定提供。之后源端只恢复TestNumber为INTEGER，真实反射kind与101条所选payload hash均恢复至CRUD后基线，未选列保持未授权。等待前端明确从头扫描后才恢复完整可读状态。

### 恢复与100/101边界（已完成）

恢复源所选类型后，Root在真实前端以批100明确重新同步，run12实际状态序列为：`running/processed0/complete=false → running/processed100/complete=false → succeeded/processed101/complete=true/deletionsAligned=true`。确实自动读取剩余1行，没有把首批100行误报整次完成。

完整发布后当前101、物理102，101个主键及101个完整payload hash再与真实源完全匹配；稳定签名匹配，所选结构指纹回到原值，未选列仍不在绑定/projection/payload。原失败与取消run历史保留。真实前端漂移保护截图见[2K截图](evidence/dm-live-schema-drift-2k-20261031.jpg)。

## 原工作空间11旧镜像恢复（已完成）

专用空间验证后，Root按本轮明确授权，先核对原数据源仍为已验证真实DM、原映射主键/所选列均有效，再通过正常前端对原Project/Task明确“重新同步”，不修改原连接、对象定义、属性或读取字段授权；源业务表仍只SELECT。并非把失配旧payload直接信任成新源。

重扫前保存对象定义hash、全部属性hash、读取绑定hash、物理IDs、业务主键单向hash及当前源PK集合hash，不保存业务payload或凭据。原镜像Project9/Task65；同一时刻真实源Project9/Task83。

| 原对象 | 新run | 结果 | 物理行变化 | 完整发布时间UTC |
| --- | --- | --- | --- | --- |
| Project | 13 | succeeded/current9/complete=true/stale=false/deletionsAligned=true | 9→9 | 06:55:26.767453Z |
| Task | 14 | succeeded/current83/complete=true/stale=false/deletionsAligned=true | 65→83，仅新增18 | 06:56:25.369291Z |

逐项比对：两对象定义hash、属性hash、读取绑定hash全部与重扫前相同；原9+65共74个物理行ID全部保留。当前9/83个PK集合及所有已选择字段的完整payload hash分别与新读取的真实源逐行完全相等。已有历史run保留，没有清表、重建原定义或扩大字段授权。工作空间15与11各自使用独立镜像，不串读。

## 普通说明保存与重启后的稳定签名（已完成）

Root通过真实前端只保存LiveFixture说明，数据库description确实改变、对象lockVersion由2变4；镜像stored signature保持不变，重新按当前语义计算的source fingerprint仍与它匹配。没有重新同步，仍为run12、complete=true、stale=false、deletionsAligned=true。该路径最初暴露导入已有大小写/下划线API被新建校验误拦的问题，前端修复后Root重新实际保存才取得上述通过证据；没有把保存被拦当作已通过。

已完成的Project/Task镜像在本轮API正常重启窗口后仍可按当前稳定签名完整读取，计数与身份映射保持。这里验证正常持久化与签名稳定，不声称测试了强杀活跃源驱动调用。取消与续传另由真实前端run6/7、10/11覆盖。

## 一分钟定时全量自动追更（已完成，非CDC）

在全部手动/漂移/旧镜像恢复完成且无活跃镜像任务后，使用真实数据源更新入口与数据库解析出的当前所有者身份，仅把工作空间15 DS-001临时设为“定时全量 / `* * * * *`”。原manual配置保存在进程内。没有改业务时钟、没有直接调用scheduler sweep、没有点或调用手动同步，没有启动通用worker。

- 实际arm：`2026-10-31T06:59:50.176648Z`，nextSlot `07:00:00Z`。
- 唯一synthetic源表第二组insert1/update1/delete1实际提交确认：`07:00:05.732515Z`，源仍101行；原Project/Task依然只SELECT。
- 现有API lifespan调度器自行建立同一波scheduled run，startedAt均为 `07:00:05.532920Z`；即本次源提交恰在调度波启动附近、实际源读取前。

| 自动run | 对象 | trigger | read/write | 结果 | 发布完成UTC |
| --- | --- | --- | --- | --- | --- |
| 15 | LiveFixture | scheduled | 101/101 | succeeded/complete=true | 07:00:07.742390Z |
| 16 | LiveProject | scheduled | 9/9 | succeeded/complete=true | 07:00:09.731820Z |
| 17 | LiveTask | scheduled | 83/83 | succeeded/complete=true | 07:00:11.924929Z |

自动发布后Fixture当前101、物理103，全部101个当前业务主键和完整payload hash与真实源独立读取结果完全相同。插入/修改可见，第二个源删除也退出当前结果，物理历史保留。提交确认到Fixture发布本次实测 **2.010秒**；它是本次恰逢调度时点的结果，不能作为固定延迟承诺。当前实现是每分钟定时全量轮询与本地镜像，不是DM日志CDC，不是源事务实时订阅。

`finally`于 `07:00:14.306213Z` 恢复原manual/cron，明确撤销schedule actor并清空nextRun。最终只读核验工作空间11、15的DS-001均为manual、actor为空、nextRun为空、活跃镜像任务0；没有残留自动计划。源侧于07:00:55.834700Z再次READ ONLY核验：原Project9/Task83、synthetic101，TestNumber为INTEGER、UnselectedExtra保留，10个选列hash与本次自动发布一致。

## 最终保留状态

- 保留一个整洁专用验证空间15，LiveProject9 / LiveTask83 / LiveFixture101均完整可读，三对象公共consumer分页与详情复核通过；Fixture业务字段10个，未选列不进入结果。
- 原空间11 Project9 / Task83恢复完成，原74个物理行ID及原定义、属性、读取授权都保留，新增18个当前Task行与真实源对齐。
- 唯一synthetic源表保留101行、INTEGER类型已恢复、未选列保持未授权；原DM业务表全程无DML/DDL。
- 原取消、失败、成功与自动run历史保留；无全量测试、pytest、迁移测试、新迁移、通用worker、清旧数据、打包、提交或推送。本记录不覆盖真实DM日志CDC或强杀进行中的驱动调用。

最终GUI v6说明再次保存后，Root确认未切对象/未reload即显示新说明一次，覆盖说明展示刷新修复的真实回归；只读末尾核查对象lockVersion5、stored signature仍与编辑前相同且等于当前semantic fingerprint，run15仍full/complete、stale=false、deletionsAligned=true，10属性type/source映射与导入一致，且签名所覆盖的visibility/敏感性/transform均未变化；工作空间11与15仍manual、actor/nextRun为空、活跃镜像0。此收尾没有新同步、源写或worker。
