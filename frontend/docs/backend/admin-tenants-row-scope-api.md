# 当前用户行级范围 / 顶栏租户与组织（admin 模块）

前缀 `/api/admin/v1`，任何已登录用户可调（dev/test 用 `X-Demo-As` 切身份）。迁移 `admin_0004`（sys_tenant、sys_org）。

## GET /me/row-scope

当前用户在每个对象类型（seeds `PERM_OBJECTS`）上能看到哪些行，问数「范围」和对象检索读它，平台本身不写死任何行业维度。

```json
{ "userId": "U010231", "userName": "苏刚",
  "items": [
    { "objType": "门店", "visible": true, "unrestricted": false,
      "conditions": [{ "prop": "大区", "op": "∈", "values": ["华东一区", "华东二区"], "text": "大区 ∈ 华东一区、华东二区" }],
      "text": "大区 ∈ 华东一区、华东二区" },
    { "objType": "订单", "visible": false, "unrestricted": false, "conditions": [], "text": "无权查看" }
  ] }
```

规则和角色页「有效权限预览」(`/users/{id}/effective-access` 的 objs) 完全一致：
- 有查看权、且不是「defaultDeny 又没配范围」的角色才算授予；
- 任一授予角色没配范围 → `unrestricted=true`，`text="全部"`；
- 否则 `conditions` 是各授予角色的范围，**条件之间是「或」**，`text` 用「；或 」连接；
- 没授予 → `visible=false`，`text="无权查看"`。不在 sys_user 的身份（服务账号）全部 `visible=false`。

前端：`src/api/rowScope.ts` `loadMyRowScope()`，mock 模式返回 null（页面继续用本地数据）。

## GET /tenants

顶栏下拉（api-index `GET /tenants`），每个组织一项：

```json
[{ "value": "hd", "label": "鲜邻零售集团 / 华东事业部", "tenantId": "TN-XL-000317",
   "tenantName": "鲜邻零售集团有限公司", "orgName": "华东事业部", "default": true }]
```

- `sys_tenant`：一期单租户，取第一行；seed `backend/seeds/extra/system/TENANT.json`（照 mock settings.ts）。
- `sys_org`：顶栏可切的一级组织，按 seq；seed `extra/system/ORGS.json`（照 stores/app.ts TENANTS）。
- 名称只是演示数据，换行业改这两个 seed（或以后的系统配置页）即可，前端不再写死。

前端：`src/api/tenants.ts` `loadTenants()`，`TopBar.vue` 挂载时拉取；显式 mock 模式使用 `TENANTS`。真模式初始空候选，失败清空并显示错误/重试，真实空列表不伪造候选；当前值不在有效非空列表里时切到 default。`rowScope.ts` 真模式失败同样抛出，不回退演示权限范围。通知没有后台服务，真模式通知列表为空且提示未接入。

## 未做

- 全量系统设置 `/settings` 仍未实现；已接入的租户基础信息扩展见下节。
- `app.tenant` 只在前端切换，后台一期单租户不按组织过滤（`X-Tenant-Id` 只透传）。

## GET /settings/tenant、PUT /settings/tenant（租户基础信息扩展）

此扩展只读写现有 `sys_tenant` / `sys_org`，不冒充 api-index §4.5 的全量 SystemSettings。GET 需 `settings · view`，PUT 需 `settings · manage`（沿用现有权限依赖）；PUT 在历史 / 预览下由全局守卫返回 403 READONLY。租户不存在时返回 404，不用 mock 值创建一行。

GET 与成功 PUT 返回：

```json
{
  "tenantId": "TN-XL-000317", "name": "鲜邻零售集团有限公司", "shortName": "鲜邻零售集团",
  "industry": "零售 · 生鲜连锁", "defaultOrg": "hd",
  "orgOptions": [{"value": "hd", "label": "华东事业部"}, {"value": "hn", "label": "华南事业部"}],
  "lockVersion": 1, "updatedAt": "2026-10-26 10:00"
}
```

- `defaultOrg` 使用稳定的 `SysOrg.code`；候选按 seq / code 排序，名称可重复，不用名称或用户部门作键。尚无默认组织时返回 null，保存前须选择有效候选。
- PUT body 必须且只能为 `{name, shortName, defaultOrg, lockVersion}`。名称和简称去首尾空格后分别为 1–40 / 1–12 字符；默认组织必须存在，版本为正整数。NUL、无效 Unicode、超长、错误类型、只读字段和其他设置字段均返回 422 VALIDATION_FAILED。
- 父租户行锁覆盖验证、组织默认旗标和真实审计写入；等待后重读版本，旧版本返回 409 VERSION_CONFLICT，整次不变。所有校验完成后才更新，每次成功 PUT 推进父租户版本一次（只改默认组织也一样），保证默认组织唯一；审计使用 insert_audit，与名称和默认旗标同事务，审计失败也回滚。
- 前端 `Settings.vue` 在 admin 真模式只开放名称、简称、默认组织；读回租户 ID / 行业和更新时间。加载失败保持不可编辑；409 保留草稿及原版本，重新加载须先确认放弃修改。保存仅更新租户已保存基线，不会把 LLM / 通知 / 安全草稿标成已保存。
- `api/tenants.ts` 共享响应式候选，GET /settings/tenant 和成功保存按真实返回刷新顶栏标签；仍有效的当前组织选择保留，只有无效时才改用默认项。旧的在途 GET /tenants 不得覆盖保存后的名称。
- 默认组织只是顶栏兜底，不改变新用户归属、问数权限或租户隔离；简称目前只改变顶栏显示，不代表导出抬头已接入。时区、币种、财年、周起始、日切、管理员 / 联系邮箱、安全、LLM 和通知配置仍未落库或接入消费者，真模式显示未接入并关闭其编辑 / 测试；mock 模式保留原演示流程。
