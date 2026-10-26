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

前端：`src/api/tenants.ts` `loadTenants()`，`TopBar.vue` 挂载时拉取；mock 模式或后台没起来回退 `TENANTS`；当前值不在列表里时切到 default。

## 未做

- 系统配置页「租户信息」保存还没接后台（改简称暂不影响顶栏）。
- `app.tenant` 只在前端切换，后台一期单租户不按组织过滤（`X-Tenant-Id` 只透传）。
