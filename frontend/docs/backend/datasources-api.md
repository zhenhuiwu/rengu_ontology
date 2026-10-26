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
| DELETE | `/datasources/{id}` | connections:manage | 204；同步记录、库表一起删（前端只提示影响，不拦） |
| POST | `/datasources/{id}:test` | connections:manage | 测试已保存的连接，写 `lastTest`；成功 → 状态「正常」 |
| POST | `/datasources:test` | connections:view | 测试表单里未保存的参数，不落库 |
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

- 默认（dev / test）：Mock 测试器，成功 `{ok:true, ms:42, msg:"连接成功（mock）"}`；状态为「异常」的连接继续失败（同前端），
  沿用上次的失败信息，状态不变。dev/test 下 `POST /datasources:test` 可传 `mockFail: true` 模拟失败。
- 真实连接：`RENGU_DS_REAL_CONNECT=1` 或 env = prod 时开启（`app/modules/data/connector.py`）。
  - 有驱动的类型：PostgreSQL、人大金仓 KingbaseES（PG 协议，asyncpg）。达梦 / MySQL / ERP 还没驱动：dev 继续 mock，prod 返回「暂不支持」。
  - 已保存连接测试成功后，**顺带内省刷新库表清单**（失败只在 msg 后追加说明，不影响测试结果）。前端测试成功后会重新拉清单和库表数。
  - 超时 10 秒按失败处理；失败时用真实原因，不沿用上次信息。

## 内省刷新库表

按 (schema, 表名) 对齐 `datasource_table`：已有行保留映射（objectType / ref），更新说明、字段数、行数（PG 用 `pg_class.reltuples` 估算，
不 count）、最近更新；新表加进来（未映射）；源库没了的表，未映射的删，已映射的保留（等人处理映射）。排序 = 已映射在前，再按 schema + 表名。
跳过系统 schema（pg_*、information_schema）和 alembic_version 等框架表；最多 2000 张。

## 与 mock 的差异

- mock 里没有的可选字段（compatMode、note、refOnly…）输出 `null`。
- 测试结果的 `ms` / `msg` 来自 Mock 测试器，前端原来写死 10000 ms /「连接成功」。
- `time` / `createdAt` 取服务端当前时间（测试里固定 2026-10-25 15:42）。
- 删除不因已映射对象类型返回 409（api-index 待定，跟前端走）。
- 库表清单只来自 seed（mock 的 dsTables 结果）；真实库表探查不在本分支。

## 表

`datasource`、`datasource_run`（同步记录，seq 0 = 最新）、`datasource_table`（库表清单）；迁移 `data_0001`。
枚举（类型 / 状态 / 同步模式 / 运行结果）库里存英文 code，接口输出中文。

## 启动检查（xiaobai-r3）

服务启动时 `check_secret_key()`：prod 或 `RENGU_DS_REAL_CONNECT=1` 下没配 `RENGU_DS_SECRET_KEY` 直接失败（error 日志 + `DS_SECRET_KEY_MISSING`），密钥格式不对 `DS_SECRET_KEY_INVALID`；dev/test 没配只 warning、用开发密钥。新建数据源取号前加 advisory 锁。
