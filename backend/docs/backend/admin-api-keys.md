# API 密钥：真实管理与元数据读取

2026-10-27 实现，管理前缀 `/api/admin/v1`。真实模式不读取 mock 密钥、角色或服务账号，也不自动授予角色。

| 接口 | 权限 | 结果 |
| --- | --- | --- |
| GET /api-keys | api-keys·view | 数据库分页（默认 10 条），status/scope/kw 筛选，items/total/page/size/counts |
| GET /api-keys/meta | api-keys·view | 当前真实身份绑定、已实现 scope、授权页面、canManage |
| GET /api-keys/{id} | api-keys·view | 掩码密钥、配置、真实调用统计；永不返回 secret/hash |
| GET /api-keys/{id}/calls | api-keys·view | 数据库调用记录分页，默认 10 条 |
| POST /api-keys | api-keys·manage | 创建并仅在本次响应返回 secret，Cache-Control: no-store |
| POST /api-keys/{id}:revoke | api-keys·manage | 真实吊销，重复吊销幂等 |

创建结构为 `{name, env, scopes:["元数据读取"], binding:{kind:"用户", userId:<当前用户编号>}, lifeDays, note?, ipAllow?}`。只可固定绑定当前真实启用用户，不支持代理绑定角色或虚构服务账号。有效期仅允许 0/30/90/180/365 天；IP/网段用标准库校验、规范化。未吊销名称唯一，已过期但尚未吊销的同名凭据也需要先吊销。counts 按关键词和 scope 筛选统计各状态，不受单个 status 筛选影响。失败显示错误、保留表单输入，不返回 mock 或伪造零计数。

Bearer 入口复用 `resolve_user`，每次重新加载用户启用状态、角色成员和功能权限，再与创建时快照取交集，最多授予 view。Token 不继承本体管理员标志或业务行访问权，不能调用管理接口、创建更多密钥、切换 X-Demo-As 或 previewAs。没有 Authorization 的原登录/演示身份入口保持原行为；带无效 Authorization 的请求不能回退到开发身份。历史/预览写请求继续使用全局 403 守卫。

唯一 scope 为“元数据读取”，只允许 GET `/api/ontology/v1` 下的：`/object-types`、`/object-types/brief`、`/object-types/{id}`、`/object-types/{id}/properties`、`/metrics`、`/dimensions`、`/dimensions/{id}`、`/knowledge`、`/knowledge/{id}`。逐一按对应页面功能权限再次检查。`full` 参数被拒绝；指标详情因包含运行值不开放，维度值、运行记录、试算、问数、动作执行、数据导出、Webhook 和其他未列路由均默认拒绝。

密钥使用 `secrets.token_urlsafe(32)`，只持久化 SHA-256 摘要、展示前缀和尾号；原文不进入 URL、日志、审计或浏览器持久缓存。创建与吊销复用真实 append-only AuditLog，actor 含姓名与用户编号，target 仅记录密钥编号、名称、绑定身份和 scope 等非敏感元数据。调用记录只存路由模板、连接来源 IP、真实响应码与耗时，不存请求头、请求参数和请求体。IP 白名单只信任 ASGI request.client，不接受未受信的 X-Forwarded-For；反向代理必须正确配置受信代理。

安全有效期使用真实 UTC 系统时间，独立于演示业务固定时钟。状态在读取和认证时即时计算，到期直接 401，不依赖每小时 worker；无需新增到期落库任务。吊销提交后，后续认证直接 401。

部署需先增量应用 `admin_0017`（down_revision=`event_0016`），仅新增 `api_key` 与 `api_key_call` 空表和索引，没有 seed、回填或已有数据修改。已有角色需按正常权限管理分配 api-keys·view/manage，本功能不自动添加角色成员或能力。生产网页登录尚未接入的既有限制仍存在。

验证范围：只执行 Python 静态 AST 与前端 vue-tsc。按用户要求，未执行后台测试、pytest、集成/严格回归或迁移测试，未创建/吊销任何真实凭据；`backend/scripts/check_api_key_boundaries.py` 留作未运行的纯边界自检。实际浏览器由主任务核查空表筛选与表单。调用记录独立事务持久化；若该写入失败，只记录不含秘密的错误并保留原请求结果，因此数据库故障时统计可能缺失。

## 2026-10-27 实际部署与页面证据

- 主任务已仅对 `rengu_ontology` 保留数据增量应用 `admin_0017`，新增两张真实空表及索引；未 seed、未创建密钥、未修改 `auto_ontology`，也未执行迁移测试。
- 重启项目后台加载新代码后，真实列表成功读取数据库，四种状态计数均为 0，无错误提示；搜索 `missing-key` 的服务端筛选返回真实空结果。最初状态计数 CASE/GROUP BY 参数不一致造成的 500 已修复；页面证据来自重启后新请求，不沿用旧服务的失败结果。
- 实际打开新建抽屉并读取 `/api-keys/meta`：权限仅显示可勾选的“元数据读取”，固定绑定王敏（`U010001`）且不可修改。已操作取消、看到未保存提示，选择放弃修改后返回列表；没有点击生成或提交任何凭据操作。
- 1920×1080、2560×1440 两档实际页面均 `outerScroll=false`。[新建表单截图](../../../frontend/shots/api-keys-real-20261027/create-form.png)。
- 独立只读审阅已核对认证、权限交集、白名单、secret 处理与原内部事件 Bearer 协议隔离，并修正发现的问题。该证据属于静态审阅，不能替代真实凭据认证验证。

本次未实际签发、复制或吊销密钥，也未用真实 Bearer 验证成功访问、越权拒绝、IP 白名单、禁用用户、移除角色、到期或吊销后的认证结果；secret 一次展示、写审计、调用记录持久化与有数据详情/分页也尚未通过真实凭据操作验证。没有运行后台测试、pytest、集成/严格回归、迁移测试或上述边界自检脚本，不能据此宣称完整签发至认证闭环已实测通过。当前已确认的是代码实现、静态检查和真实空表/筛选/新建表单行为。
