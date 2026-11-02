# 普通建模与管理列表查询调优

日期：2026-11-01。范围限于普通对象类型列表、工作空间目录；未执行真实数据库、HTTP API、业务记录、外部系统或模型调用。

## 已有实现与根因

- `static/service.py` 的对象、指标、维度等主列表已经调用 `static/read_pages.py`，当前定义已有 SQL 侧 count、页码收拢、offset/limit 与 focus 定位；对象属性数也已按当前页批量查询。不能把现状描述为所有列表都全表载入后 Python 分页。
- `static/read_pages.py::object_list` 虽已分页并禁止自动加载关系，仍选择 `ObjectType` 全列。列表序列化只需要主键、编码、名称、API、分组、来源表、记录数、状态，原先额外读取的 `description`、`sample_questions` 等文本/JSON 与列表输出无关。
- `ontology/router.py::workspace_directory` 在同一次调用中两次按相同用户编码查用户主键；完整目录 `/workspaces/catalog` 已经取回所有可见行，仍额外运行一次相同筛选的 COUNT。

## 本次最小修改

1. `backend/app/modules/static/read_pages.py`：对象类型当前列表使用 `load_only(..., raiseload=True)` 投影序列化所需列，并保留 `raiseload("*")`。未使用列不会被列表访问隐式补读；未来若扩充列表字段，必须同时更新投影。历史列表、对象详情与完整编辑目录保持原调用路径。
2. `backend/app/modules/ontology/router.py`：目录函数在请求内复用一次用户主键解析。分页列表保留 count、clamp 与数据库分页；完整目录使用已读行数给出 total。仍独立查询所选空间的有效成员关系，不能因它不在当前页就丢失 currentOntologyId，也不能信任请求头直接返回空间编号。

源码可确定的收益：对象列表当前页不再传输无关文本/JSON 列；目录函数自身的数据库调用数由 5 次降至 3 次，分页函数自身由 5 次降至 4 次（均不含身份依赖/事务与 RLS 设置）。没有缓存、没有新增依赖、没有表或索引变更。

## 调用链与保留约束

- 对象类型页及 `frontend/src/api/static.ts` 的对象列表消费者 → `/object-types` → `StaticService.object_list` → `read_pages.object_list` → `StaticRepo.page` → `shapes.object_list_item`。原关键词语义、count/page 相同筛选、稳定排序、focus、批量属性计数与响应形状均保留。
- `frontend/src/pages/Workspaces.vue` → `/workspaces`；`frontend/src/layouts/TopBar.vue` 与删除后的空间切换 → `/workspaces/catalog`；两者共用 `workspace_directory`。用户成员范围、`deleted_at IS NULL`、所选空间校验与创建权限输出均保留。
- 未改身份/RLS/数据库会话、软硬删除与恢复、对象隔离；保留 `ontology/router.py` 原有未提交删除/权限绑定成果。未改事件、动态规则、动作或军事/自动行动链。

## 离线验证与边界

仅运行 `backend/scripts/check_list_query_tuning.py`：使用 fake session/repository 和 PostgreSQL SQL 编译，禁止 socket connect/connect_ex；协程同步完成，不启动事件循环或后台进程。断言覆盖对象列表投影与输出字段、原关键词/排序/focus 参数、当前页属性计数，以及完整目录/越界分页/空目录、主键只查一次、count 仅用于分页和所选成员检查。

运行命令（工作目录 `backend`）：`.\.venv\Scripts\python.exe -m scripts.check_list_query_tuning`。最终结果：`list query tuning: offline assertions passed`。首次检查因 Windows 事件循环内部 socketpair 也被网络禁用守卫拦截，在执行检查逻辑前退出；已改为无需事件循环的同步协程断言，再运行通过。

尚未验证：真实执行计划、数据库响应时间、并发负载、真实数据量与 API 端到端耗时。因此不能声称已实测提速或已在运行服务中生效。既有 8000 后台进程未停止、重启、热加载，也未通过新端口、环境变量或代理绕过启动限制；全量/集成/迁移测试与 benchmark 均未运行。

本轮跳过其他宽载荷与跨工作空间聚合重构；需要实际耗时或执行计划证据后再决定是否继续。
