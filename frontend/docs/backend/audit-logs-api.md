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

返回 `{ jobId }`。**桩**：不生成文件、没有任务查询接口；`jobId = "EXP-" + 新写入那条审计的 code`。
按 api-index §4.3「导出本身也写一条审计」：`actor=「姓名（编号）」`、`action=导出`、`target=审计日志（<from> 至 <to>）`、`ip`=X-Forwarded-For 第一个或连接地址、`result=成功`。
`?ver=` / `?previewAs=` 下是写请求 → 全局守卫 403 `READONLY`（不在 §1.7 白名单）。

## 写审计的入口

`app.modules.admin.repo.insert_audit(session, *, actor, action, target, ip, result="成功", time=None, created_by=None)`：
同事务追加一条，不 commit。`app/common/audit.py`（PDE 的文件，现在只打日志）落地时可以直接调它；本分支**没改** `common/audit.py`，
所以其他模块的写操作目前还不会进 `audit_log`。

## 下一阶段 / 没做

- 用户、角色页接口（权限 JSON、行范围、成员、row_filter）。
- 真正的导出任务（生成文件、`GET /export-jobs/{id}`）、日志保留 180 天的清理。
- `common/audit.py` 接 `insert_audit`（等 PDE 点头）。
