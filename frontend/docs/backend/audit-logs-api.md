# 审计日志接口（admin · AuditLog.vue）

> 分支 `audit-logs`（本体小白，admin 模块负责人是 PDE，本页认领待 PDE 确认）。契约来源：api-index §1.5、§4.3；
> 前端 `src/pages/admin/AuditLog.vue`、`src/mock/admin.ts`（`AuditRow`、`AUDIT_LOGS`）、`src/mock/paginate.ts`（`CursorResult`）。

挂载：`/api/admin/v1`（`app/api.py` 已有，未改）。功能权限 `require_func("audit", "view")`（现在是桩，登录即可；未登录 401）。

## 表 `audit_log`（只追加）

| 列 | 类型 | 说明 |
|---|---|---|
| id | bigint identity | 代理主键（keyset 第二键） |
| code | varchar(64) unique | 前端 `AuditRow.id`：seed 原样（`AL1792915715000`、`ALCR0`…）；新写入 `AL<毫秒><4 位十六进制>` |
| time | timestamptz | 发生时间；接口输出北京时间 `YYYY-MM-DD HH:mm:ss` |
| actor | varchar(256) | 展示串「姓名（用户编号）」，软引用，不做外键（照 mock） |
| action | varchar(64) | 中文动作，如「导出」「发布本体」 |
| target | varchar(512) | 对象 |
| ip | varchar(64) | 来源 IP |
| result | varchar(16) + CHECK | 库里 `success/failure/denied`，接口输出 `成功/失败/拒绝` |
| created_at / created_by / is_seed | | `AppendOnlyTableMixin`；**无 lock_version、无软删除** |

- 迁移 `admin_0001`：建表 + 索引 `ix_audit_log_time_id(time, id)`、`ix_audit_log_action`，挂 `append_only` 触发器（禁止 DELETE / TRUNCATE / 改内容的 UPDATE；seed 重复 UPSERT 放行）。
- **表名**：按 api-index §1.1「已定」写法用 `audit_log`；`app/common/audit.py` 注释里的 `sys_audit_log` 是旧写法。
- **合并**：`down_revision` 现在是 `event_0002`；合并时改成当时的 head（预计 `data_0001`）。
- seed：`seeds/json/system/AUDIT_LOGS.json`（`npm run export-mock` 原样导出，TZ=Asia/Shanghai），521 行 = 512 条固定种子生成 + 9 条变更单固定行。

## GET /audit-logs

参数（query）：

| 参数 | 必填 | 说明 |
|---|---|---|
| `from`, `to` | 是 | 北京时间 `YYYY-MM-DD HH:mm:ss`（也接受到分钟、ISO 带时区）；**两端都含**（前端 `r.time >= from && r.time <= to`）。缺任一个 → 422 `VALIDATION_FAILED`。`from > to` → 空列表，不报错 |
| `action` | 否 | 等值；不传 / `全部` = 不限 |
| `result` | 否 | `成功`/`失败`/`拒绝`/`全部`；其他值 422 |
| `field` | 否 | `actor`（默认）/ `target`；其他值 422 |
| `kw` | 否 | 先 trim；对 `field` 列做**区分大小写的子串包含**（`strpos`，`%`、`_` 不是通配符），同前端 `includes` |
| `cursor` | 否 | 上一页的 `nextCursor`；不传 = 第一页；**解不开的游标当第一页**（同前端 `paginateCursor` 的 decode） |
| `size` | 否 | 默认 20，1–200 |

返回 `CursorResult<AuditRow>`：`{ items: AuditRow[], nextCursor: string | null }`，**没有 total**。

- 排序 / keyset：`ORDER BY time DESC, id DESC`，下一页条件 `(time, id) < (cursor.time, cursor.id)`，每次多取 1 条判断是否还有下一页：**刚好取完时 `nextCursor = null`**（同前端 `end < rows.length`），不会出现空的尾页。
- 游标格式：`"k" + base64url("<微秒时间戳>:<id>")`，不透明，前端不解析。前端 mock 的 `c<偏移36进制>` 游标传进来当第一页。
- 默认「近 24 小时」由前端算好 from/to 传过来（mock 的「现在」= `2026-10-25 16:12:41`），后端不补默认值。
- 同一秒的多条：mock 里时间无重复；真实数据按 id 倒序（后写入的在前）。

## GET /audit-logs/actions

返回 `string[]`：库里去重的动作，按**首次出现（时间倒序）**排列，和前端 `new Set(AUDIT_LOGS.map(r => r.action))` 同序。**不含「全部」**，前端自己在最前面加。

## POST /audit-logs/export

Body（JSON）：`{ from, to, action?, result?, field?, kw? }`，和列表同一套筛选，from/to 必填（422）。

返回 `{ jobId }`，`jobId = "EXP-" + 新写入那条审计的 code`。在同一事务保存 `audit_export_job`（真实创建者 ID、规范化筛选）和 `audit_export_row`（job_id / audit_id 唯一成员）。单条 `INSERT SELECT` 固定语句快照中符合条件的 ID，显式排除本次导出自己的审计；创建后新增记录、创建时尚未提交的较低 ID 记录均不会混入，日志只追加使后续下载结果稳定。任一步失败，清单、成员和本次审计一同回滚。`action` / `kw` 的 NUL、不可编码 Unicode → 422，未写入清单或审计。
按 api-index §4.3「导出本身也写一条审计」：`actor=「姓名（编号）」`、`action=导出`、`target=审计日志（<from> 至 <to>）`、`ip`=X-Forwarded-For 第一个或连接地址、`result=成功`。
`?ver=` / `?previewAs=` 下是写请求 → 全局守卫 403 `READONLY`（不在 §1.7 白名单）。

## GET /export-jobs/{id}、GET /export-jobs/{id}/download

详情返回 `{jobId, status: "ready", rowCount, downloadUrl}`。`ready` 表示固定成员清单已持久化、可以下载，**没有后台异步生成文件阶段**，没有临时 CSV、文件存储或通用导出队列。重新启动后清单仍可查询、重复下载。

两个 GET 均复用审计页 `require_func("audit", "view")` 依赖，并限定真实当前创建者；其他用户和未知任务统一 404，当前用户已停用仍由认证层返回 401。历史版本 / 身份预览显式检查并返回 403 `READONLY`，GET 不因安全方法而放行下载。该功能权限依赖仍是系统一期放行桩，本功能没有补全全站生产鉴权。

下载按固定成员的 `(time DESC, id DESC)` keyset 分批，每批至多 1000 行。首批在请求 session 内读取、序列化，错误可在 CSV 响应头之前正常返回；请求连接释放后发送首批，后续每批独立 session 且释放连接后才发送，不占两个池连接、不随慢客户端下载持有事务。中途读取异常会中止响应，不能改回 JSON 错误，页面 fetch/blob 失败不会提示成功。服务端内存为一批记录/CSV，不全量加载；浏览器现有下载方式需在内存接收完整 Blob，超大文件仍受浏览器内存与网络时限约束。清单成员存储随导出行数增长，当前无自动清理。

CSV：UTF-8 BOM、标准 CSV writer（逗号 / 引号 / 换行正确转义）、所有列加引号，表头为编号/时间/操作人/动作/对象/来源 IP/结果；公式触发前缀 `= + - @`（含前导空白）及开头制表/回车/换行的单元格加单引号，避免表格公式执行。空清单仍返回表头。页面复用 HTTP 身份与只读参数、显示 busy 和失败信息，只有上下文始终一致才触发下载；切换身份 / 版本后的迟到结果丢弃。演示模式明确提示不生成文件。

## 写审计的入口

`app.modules.admin.repo.insert_audit(session, *, actor, action, target, ip, result="成功", time=None, created_by=None)`：
同事务追加一条，不 commit。`app/common/audit.py`（PDE 的文件，现在只打日志）落地时可以直接调它；本分支**没改** `common/audit.py`，
所以其他模块的写操作目前还不会进 `audit_log`。

## 下一阶段 / 没做

- 用户、角色页接口（权限 JSON、行范围、成员、row_filter）。
- 后台生成文件任务、日志保留 180 天及导出清单的清理策略（当前不承诺这两项）。
- `common/audit.py` 接 `insert_audit`（等 PDE 点头）。
