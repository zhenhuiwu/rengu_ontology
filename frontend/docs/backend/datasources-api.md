# 数据连接接口（data 模块，api-index §3.1）

对应页面 `src/pages/data/Connections.vue`，数据来自 `src/mock/datasources.ts`（导出到 `backend/seeds/json/system/`）。
前缀 `/api/data/v1`，前端开关键 `data`（`src/api/config.ts`）。

## 接口

| 方法 | 路径 | 权限 | 说明 |
|------|------|------|------|
| GET | `/datasources?type=&kw=&page=&size=` | connections:view | 列表；`type` 取 `DS_TYPES` 中文；`kw` 匹配名称 / 主机（区分大小写）、库名（不区分），同前端 |
| GET | `/datasources/meta` | connections:view | `{types, defaultPort, dmCompat, kbModes, erpSystems}`，与 mock 常量一致 |
| GET | `/datasources/{id}` | connections:view | 详情（同列表项） |
| GET | `/datasources/{id}/tables?kw=&page=&size=` | connections:view | 库表清单，顺序 = mock `dsTables()`（已映射在前）；`kw` 匹配表名（不区分）/ 说明 |
| POST | `/datasources` | connections:manage | 新建，201；编号 `DS-<最大+1>`，状态「未测试」，`createdAt` = 今天，`cronText` = cron |
| PUT | `/datasources/{id}` | connections:manage | 修改；`password` 留空 = 不修改；同步只改 mode / cron |
| DELETE | `/datasources/{id}` | connections:manage | 无定义/未发布草稿引用时204，级联删除清单与同步记录；有引用409 `DS_IN_USE` |
| POST | `/datasources/{id}:test` | connections:manage | 测试已保存的连接，写 `lastTest`；成功 → 状态「正常」 |
| POST | `/datasources:test` | connections:manage | 测试表单里未保存的参数，不落库 |
| POST | `/datasources/{id}/tables:refresh` | connections:manage | 真连源库内省，刷新库表清单，返回 `{count}`；没开真实连接 / 类型没驱动 → 422 `REAL_CONNECT_UNAVAILABLE`，连不上 → 502 `INTROSPECT_FAILED` |

分页：`{items, total, page, size}`，越界页码收拢到最后一页（同 paginate.ts）；`size` ≤ 200。
错误：统一错误体，查不到 404 `NOT_FOUND`，参数错 422。

## 字段

列表项 / 详情与 mock `Datasource` 一一对应（`id, name, type, host, port, database, user, compatMode, databaseMode,
erpSystem, appId, ssl, status, lastTest, owner, objectTypes, sync{mode,cron,cronText,window,cursorField,nextRun},
runs[{time,result,rows,secs,msg}], tableCount, createdAt, note, refOnly`）。**密码从不返回**。库里 `password_enc` 存 Fernet 密文（`fernet$…`，`app/modules/data/secrets.py`），真连时解密使用。

写入体：`name, type, host, port?, database, user, password, compatMode?, databaseMode?, erpSystem?, appId?, ssl, owner, mode, cron`。
`port` 不填取类型默认端口；与类型无关的字段（如 PG 的 compatMode）丢弃。

## 密码加密

- 密钥：环境变量 `RENGU_DS_SECRET_KEY`，用 `python -c "from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())"` 生成。
  可写多把（逗号分隔）做轮换：第一把加密，全部能解密。prod 没配 → 500 `DS_SECRET_KEY_MISSING`；dev / test 没配用内置开发密钥。
- 上一版存的 PBKDF2 摘要解不开，按「没有密码」处理（真连时提示重新填写）；seed 行本来就没有密码。
- 测试连接失败的原因里如果带了密码原文，会打码成 `***`。

## 测试连接

- 真接口各环境都不再模拟成功。未开启真实连接 → 422 `REAL_CONNECT_UNAVAILABLE`（`details.reason=disabled`），未执行外部调用、不修改已有 `status / lastTest`；旧 `mockFail` 参数仅兼容接收，不影响真实测试。
- 真实连接：`RENGU_DS_REAL_CONNECT=1` 或 env = prod 时开启（`app/modules/data/connector.py`）。
  - 驱动配置：PostgreSQL、人大金仓 KingbaseES（PG 协议）使用 `postgresql+asyncpg`；MySQL 使用可选 `mysql+asyncmy`；达梦 DM8 使用可选 `dm+dmPython`。驱动未安装时测试返回 `ok:false` 和明确缺失原因，内省502 `INTROSPECT_FAILED`。不自动安装、不回退演示结果；ERP仍422 `REAL_CONNECT_UNAVAILABLE`。达梦同步驱动在线程执行，调用超时不终止底层线程，部署需配置驱动/网络超时。非PG表行数当前未提供估算，以0返回。
  - 已保存配置没有可解密密码时返回 `{ok:false, ms:0, msg:"未保存密码…未执行测试…"}`，不真正发起连接，也不覆盖数据库已有状态与历史。真正测试失败会记录真实失败原因、保留原状态，成功才变「正常」。前端结果展示重新读取数据库详情，未执行的返回结果不能伪装成已持久化 `lastTest`。
  - 已保存连接测试成功后，**顺带内省刷新库表清单**（失败只在 msg 后追加说明，不影响测试结果）。前端测试成功后会重新拉清单和库表数。
  - 超时 10 秒按失败处理；失败时用真实原因，不沿用上次信息。

前端真模式初始列表为空，加载失败/空结果不切演示数据，不改变后续写请求模式；切换身份/版本清掉上个上下文的数据，迟到列表/库表/测试/保存响应不会覆盖新上下文。列表与库表按后台分页取齐，超过200条也能浏览（每一组分页请求固定开始时的身份与查看参数），本地筛选/分页仍保留。只有显式关闭 `data` 真接口开关才使用演示数组。

## 内省刷新库表

真实接口的`objectTypes`和库表`objectType`由已发布对象定义反查。旧seed冗余展示标记仍保留在库里，但不当作映射事实。专用属性映射列未填时，字段映射页显示实际`source_column/data_type`。

按 (schema, 表名) 对齐 `datasource_table`：已有行保留映射（objectType / ref），更新说明、字段数、行数（PG 用 `pg_class.reltuples` 估算，
不 count）、最近更新；新表加进来（未映射）；源库没了的表，未映射的删，已映射的保留（等人处理映射）。排序 = 已映射在前，再按 schema + 表名。
跳过系统 schema（pg_*、information_schema）和 alembic_version 等框架表；最多 2000 张。

## 从连接导入静态定义草稿

只读源表结构、复用确定性规则生成，不需要模型密钥；不复制数据、不写源库或旧auto_ontology草稿、不扩大对象查询/任意SQL执行白名单。

| 方法 | 路径 | 权限 | 说明 |
|------|------|------|------|
| POST | `/datasources/{id}/import-preview` | connections:manage + object-types:manage | `{schema,tables:string[]}`，单schema、1–60张表；真实列/主外键内省 |
| POST | `/object-types/{id}/metadata-diff` | connections:manage + object-types:manage | 无需表单，读取对象当前已发布的源库/源表配置，重新采集后返回新增/删除源列；不写定义/草稿 |
| POST | `/datasources/{id}/import-to-cr` | 同上；选关系另需link-types:manage | 确认预览，201创建或加入静态草稿单，不发布 |
| GET | `/datasources/{id}/mappings` | connections:view | `[{objectId,name,schema,table,datasourceName}]`，当前定义反查 |
| PUT | `/datasources/{id}/tables/{schema}/{table}/mapping` | connections:manage + object-types:manage | `{objectId,...变更单字段}`，201；只写对象config的source/table差异，发布后生效 |

预览返回`{datasourceId,datasourceName,schema,tables,proposal,warnings,fingerprint,generator:'规则'}`。`tables[]`含`name/comment/pk/columns[{name,type,nullable,comment}]/fks[{columns,refTable,refColumns}]/uniqueSets`；`proposal`为既有规则的`objectTypes/linkTypes`形状。表→对象，列→属性，外键/中间表→关系建议。

确认体：`{schema,tables,fingerprint,objectTypes:[{table,id,name,api,owner?}],linkIds?:string[],includeLinks?:boolean,...变更单字段}`。对象必须按`table`与预览一一对应，只接受对象编号、API名、名称、负责人编辑，不接受任意属性或SQL。`linkIds`逐条选择候选ID，显式`[]`不生成；省略才按兼容`includeLinks=true`全选，默认不选关系。负责人用`/api/ontology/v1/build/owners?kind=object`真实用户编号，发布重验后在定义存真实姓名。

共用变更单字段：新建`{title,reason?,reviewer?}`；加入已有草稿`{crId,lockVersion}`。返回`{crId,lockVersion,targets:[{kind,id,isNew,name}]}`。已有目标内容不覆盖；任一目标失败，整次变更单/全部目标行/审计同事务回滚。确认重新内省，结构、主外键、唯一约束或连接版本变化409 `SCHEMA_CHANGED`；无主键/复合主键预览警告，确认409要求人工建模。当前或未发布草稿预留的编号/API名冲突409。

新映射把稳定连接code存`ObjectType.datasource_name`，完整`schema.table`存`source_table`；连接显示名从Datasource取`datasourceName`。旧连接名兼容反查，仍被旧名称定义引用时改名409，先CR改稳定编号。映射编辑重新内省，缺已有映射主键/字段409，不默默重建属性。

导入/映射与删除锁DS父行至事务结束；通用发布也验证稳定`DS-数字`来源存在并取父行/每DS事务锁，普通旧来源文案（如`DS-legacy-label`）保留。删除保护当前定义及未发布CR引用。真实连接未开启/类型无配置422、驱动缺失或连接失败502，不模拟成功。

重新采集返回 `{objectId,datasourceId,schema,table,added:[{name,type,nullable,comment}],deleted:string[]}`。以已发布属性的 `map_column`（未设时 `source_column`）比较实际源列，新增指未映射源列、删除指映射列已从源表消失。缺映射/数据源不唯一409 `MAPPING_REQUIRED`，源表不存在404 `TABLES_NOT_FOUND`；`ver`/`previewAs` 下403 `READONLY`。差异只供人工查看，修改定义仍需变更单。

## 与 mock 的差异

- mock 里没有的可选字段（compatMode、note、refOnly…）输出 `null`。
- 测试结果的 `ms` / `msg` 来自真实连接器；缺配置、缺驱动明确失败。
- `time` / `createdAt` 取服务端当前时间（测试里固定 2026-10-25 15:42）。
- 删除不因已映射对象类型返回 409（api-index 待定，跟前端走）。
- 库表清单读取数据库 `datasource_table`，既有种子作为初始持久记录；真实连接成功后会内省更新，不在读失败时拿前端静态清单替换。

## 表

`datasource`、`datasource_run`（同步记录，seq 0 = 最新）、`datasource_table`（库表清单）；迁移 `data_0001`。
枚举（类型 / 状态 / 同步模式 / 运行结果）库里存英文 code，接口输出中文。

## 启动检查（xiaobai-r3）

服务启动时 `check_secret_key()`：prod 或 `RENGU_DS_REAL_CONNECT=1` 下没配 `RENGU_DS_SECRET_KEY` 直接失败（error 日志 + `DS_SECRET_KEY_MISSING`），密钥格式不对 `DS_SECRET_KEY_INVALID`；dev/test 没配只 warning、用开发密钥。新建数据源取号前加 advisory 锁。
