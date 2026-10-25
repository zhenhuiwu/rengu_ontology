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

分页：`{items, total, page, size}`，越界页码收拢到最后一页（同 paginate.ts）；`size` ≤ 200。
错误：统一错误体，查不到 404 `NOT_FOUND`，参数错 422。

## 字段

列表项 / 详情与 mock `Datasource` 一一对应（`id, name, type, host, port, database, user, compatMode, databaseMode,
erpSystem, appId, ssl, status, lastTest, owner, objectTypes, sync{mode,cron,cronText,window,cursorField,nextRun},
runs[{time,result,rows,secs,msg}], tableCount, createdAt, note, refOnly`）。**密码从不返回**，库里只存 PBKDF2 摘要。

写入体：`name, type, host, port?, database, user, password, compatMode?, databaseMode?, erpSystem?, appId?, ssl, owner, mode, cron`。
`port` 不填取类型默认端口；与类型无关的字段（如 PG 的 compatMode）丢弃。

## 测试连接

目前是 Mock 测试器（真实探测未接）：成功 `{ok:true, ms:42, msg:"连接成功（mock）"}`；状态为「异常」的连接继续失败（同前端），
沿用上次的失败信息，状态不变。超时 10 秒按失败处理。dev/test 下 `POST /datasources:test` 可传 `mockFail: true` 模拟失败。

## 与 mock 的差异

- mock 里没有的可选字段（compatMode、note、refOnly…）输出 `null`。
- 测试结果的 `ms` / `msg` 来自 Mock 测试器，前端原来写死 10000 ms /「连接成功」。
- `time` / `createdAt` 取服务端当前时间（测试里固定 2026-10-25 15:42）。
- 删除不因已映射对象类型返回 409（api-index 待定，跟前端走）。
- 库表清单只来自 seed（mock 的 dsTables 结果）；真实库表探查不在本分支。

## 表

`datasource`、`datasource_run`（同步记录，seq 0 = 最新）、`datasource_table`（库表清单）；迁移 `data_0001`。
枚举（类型 / 状态 / 同步模式 / 运行结果）库里存英文 code，接口输出中文。
