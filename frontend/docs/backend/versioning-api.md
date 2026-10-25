# 版本接口（versioning）

前缀 `/api/ontology/v1`，前端 `apiClient('versioning')`（src/api/releases.ts:13，config.ts 里 versioning → ontology）。
实现：`backend/app/modules/versioning/router.py`。接口全部**只读**，只要登录（`X-Demo-As` / CurrentUserDep），不区分角色。
契约测试：`backend/tests/test_versioning.py`（种子数据与前端 mock 一一对应）。

## 接口

| 方法 | 路径 | 前端调用方 | 说明 |
|---|---|---|---|
| GET | `/releases` | releases.ts:31 `fetchReleases` → `loadReleases`（bootstrapApi） | `{items: Release[], current}`，不分页，新→旧，`items[0].ver == current` |
| GET | `/releases/current` | releases.ts:32 `fetchCurrentRelease` | `{current, next, release}`；`next` = 当前版本同月 +1（前端 NEXT_VERSION） |
| GET | `/releases/{ver}` | 暂无前端调用 | 单版详情：Release 字段 + `snapshotSha256`、`changeSummary` |
| GET | `/releases/{ver}/snapshot` | 暂无前端调用 | 该版本整份快照 `{section: {...}}`，各 section 由负责模块组装 |

### Release 形状（对应 src/mock/releases.ts `Release`）

```json
{ "ver": "v2026.10.3", "publishedAt": "2026-10-24 21:17", "by": "王敏", "note": "销售额改为不含税口径；新增 gmv_tax",
  "byId": "U010001", "parentVer": "v2026.10.2", "kind": "发布", "rollbackOf": null, "changeOrderIds": ["CR-0007"] }
```

前端只读 `ver / publishedAt / by / note`，其余是附加字段，前端可忽略。`kind` 取 `发布` 或 `回滚`。

## `?ver=` / `?previewAs=`

http.ts:69-73 给每个请求都带上这两个参数。版本接口**忽略**它们：列表永远是全量，`current` 永远是最新版本，
这样 VersionPicker 在查看历史版本时仍能切回去。带这两个参数的写请求会被只读守卫拦成 403（`app/common`）。
`?ver=` 本身由各业务模块校验（例如 `action/service.py` 里的 `by_ver_or_404`），`get_view_context` 不做校验。

## 错误

统一信封 `{code, message, details, requestId}`。

- 版本不存在：`404 NOT_FOUND`，`message` 为「版本不存在」，`details: {ver}`（`/releases/{ver}` 和 `/snapshot` 都是）。
- 未知演示用户：`401 UNAUTHORIZED`。
- 回滚的目标不是最新版本：`409 ROLLBACK_NOT_LATEST`（由 create_version 抛出，接口归静态）。

## 发布 / 回滚（写入口不在本模块）

`ontology_release` 表只追加。唯一的写入口是 `VersioningService.create_version(change_order_ids, rollback_of=…)`：

- 发布：`POST /releases` → **静态模块负责**（static-api.md:634），在事务里调 create_version。
- 回滚：`POST /releases/{ver}:rollback` → **静态模块负责**（static-api.md:635）。实现方式是调
  `create_version(..., rollback_of=<当前最新版本号>)`，生成一版新的版本，`kind=回滚`，内容等于上一版的快照，
  并调用 restore_sections。新版本会自动出现在 `/releases` 里（`test_rollback_release_shows_in_switcher`）。
- 合并检查 `POST /releases:merge-check`、变更单 diff `GET /change-requests/{id}/diffs` → 静态模块负责。
- 版本间 diff `GET /releases/{a}/diff/{b}` → **第二期**（前端 ActionTypes/EventTypes 的「对比/回滚」按钮目前是禁用状态，提示「暂未开放」）。

## 快照 section 现状

seed 后快照里有这些 section：objectTypes、properties、links、metrics、dimensions、terms、qaPaths（静态），actionTypes、eventTypes（Palantir）。
**rules / lifecycles 还没有**：动态模块需要在 `app/modules/dynamic/sections.py` 里导出 `BUILDERS`（见 CONVENTIONS §7）。

## PDE 接入真实后端

1. 设置 `VITE_API_REAL_MODULES=versioning`（或在 `VITE_API_MODE=real` 下一并打开）。
2. 前端有几处仍在读 mock 或写死的版本号，需要改成读 store（`app.ontologyVersion`）：
   OntologyOverview.vue:25、HistoryTab.vue:11-12、Releases.vue:19（从 mock/changes 读 CURRENT/NEXT_VERSION），
   TopBar.vue:86（写死 v2026.10.3），QaTuning.vue:23（读 mock/qa-tuning 的 CUR_VERSION）。
3. `loadReleases` / `bootstrapApi` 目前把 `/releases/current` 返回的 `next` 丢掉了，需要存进 store，替代 NEXT_VERSION。
