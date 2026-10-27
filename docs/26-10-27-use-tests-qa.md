# 使用区 QA 改动测试记录

2026-10-27，基线 `9d31149`。用户已明确授权针对改动进行完整测试，本轮许可取代前轮“不运行后台测试”的限制；前轮文档中的未测试事实仍保留，不回写为当时已通过。

## 隔离与执行范围

- 唯一写入、seed、增量升级及 UI 测试数据库为 `rengu_test_use_qa_20261027`。开发库 `rengu_ontology` 仅用于读取本地连接配置、确认/创建唯一测试数据库；未修改其业务数据或角色授权，未访问 `auto_ontology`，没有 DROP 任何库。
- 共用启动器从 ignored `backend/.env` 在内存读取本地 5433 URL，校验来源数据库并显式传入独立测试库。迁移前检查 `pg_auth_members` 已有当前连接用户到 `rengu_ontology_scoped` 的直接成员边，不新增集群授权。凭据不写报告或命令输出。
- 现有 `tests/conftest.py`、公共 auth/db 边界未被 QA 组修改。QA 专属夹具补齐真实工作空间、成员、对象角色绑定。业务源 baseline 在独立测试库提交，以便独立只读连接可见；API 用例仍在外层事务中回滚，不将隔离事务内看不到数据的问题伪装为查询成功。
- 新回归使用真实 PostgreSQL 连接、实际 `SET LOCAL ROLE`、只读事务、本体 GUC、`run_guarded` 与真实角色/行权限。没有 mock 查询网关、身份解析、RLS 或业务查询结果；缺配置/超时等边界仅调整本地测试参数。唯一问答链路错误用例使用既有 `FakeLLM`，未连接 provider。
- 仅测试 QA 页面/模板、会话与回答卡的相关文件，不运行全仓库测试、不执行评测队列、不发送模型或外部业务请求。

## 命令与结果

从 `backend` 执行，命令不包含凭据：

```powershell
.venv/Scripts/python.exe scripts/run_use_tests.py --db rengu_test_use_qa_20261027 -- tests/test_qa_page_execution.py -q -k pure --tb=short
.venv/Scripts/python.exe scripts/run_use_tests.py --db rengu_test_use_qa_20261027 -- tests/test_qa_page_execution.py -q --tb=short
.venv/Scripts/python.exe scripts/run_use_tests.py --db rengu_test_use_qa_20261027 -- tests/test_qa_pages_write.py tests/test_qa_session_write.py tests/test_qa_api.py tests/test_qa_turn_actions.py tests/test_qa_page_execution.py -q --tb=short
.venv/Scripts/python.exe scripts/run_use_tests.py --db rengu_test_use_qa_20261027 -- tests/test_qa_api.py::test_templates -q --tb=short
.venv/Scripts/python.exe scripts/run_use_tests.py --db rengu_test_use_qa_20261027 -- tests/test_qa_api.py::test_admin_shared_member_still_requires_execution_provenance -q --tb=short
```

- 来源完成状态的两个回归首次真实失败（未抛出拒绝），修复后 `2 passed`。
- 新执行模块完整回归最终 `19 passed`，11.06 秒。覆盖真实低权限只读连接与无 GUC 的 RLS 拒绝、实际查询整理/刷新/模板创建、模板空数据、共享临时视图、拥有者撤权、原条件锁定、隐含/未公开字段拒绝、版本不兼容、整体失败原子性、总超时、截断、真实导出 grant 与公式转义、历史及伪造预览身份。
- 既有用例首轮 `18 passed / 8 failed`（达到 maxfail 后停止）；失败项是旧契约断言，包括假范围标签、无 lockVersion 的共享、旧样例值、可保存未启用计划、12 条分页等，已改为当前真实行为断言。后续合集中的旧断言、已提交测试 baseline 及角色范围夹具差异也已调整；未为旧断言放松产品安全边界。
- 最后五文件合集实际为 `81 passed / 1 failed / 1 skipped`，共 83 项，437.74 秒。唯一失败是旧模板断言仍认种子固定 22 条，隔离 UI 已合法新增 1 条；改为完整保留种子 ID 集合且允许真实新增后，定点 `test_templates` 为 `1 passed`，8.13 秒。原跳过是种子缺少“他人共享给管理员”的前置；现以正常 owner 的共享 API 明确建立该前置，不绕鉴权，定点为 `1 passed`，8.33 秒。实际结果是整轮 81 项通过，加两个定点通过，83 个不同用例均已有通过证据；没有再跑五文件整轮，不写成同一轮 83/0。
- `test_qa_turn_actions.py` 属 W9–W12 的口径选择、结果表分页/CSV/XLSX/赞踩/拥有者及只读约束，与 `turn_out` 新增 pinnable 输出有调用联系，已纳合集；它不是真实动作暂存/确认系统或 provider 测试。公开手动来源防伪由 ops 组另测。
- 本轮前端 tab 修复后的 `npm run typecheck` 已通过；最后所属源码/测试/测试服务脚本的 Ruff F/I 及 6 文件 AST 均通过。主任务早前完整 build 为4,475模块/28.37秒，发生在最终修复前；随后最新 `npm run build`（vue-tsc + Vite，4,475 模块，25.02 秒）实际 exit0，已包括 QA intent、对象及 ops 最新前端。此项记当前前端构建通过，不当作后端测试通过；若之后再改 UI 则另需确认。

原有 pytest-asyncio `event_loop_policy` 弃用警告仍有记录；既有“故意触发 SQL 执行错误”用例出现 nested transaction deassociated 警告。它们不被当成静态/业务全部通过或失败的替代指标，最终回归结果单独记录。

## 确定问题与修复

1. **未完成轮次被错误当作成功来源**：原 `turn_plan` 仅检查 `kind=answer`，未检查 `status`；带 SQL 和发布锚点的 failed/streaming 轮次可以进入来源准入。回归先红，再将来源 helper、会话整理查询与 `pinnable` 同步要求 `done`，两项回归转绿，不改历史记录。
2. **整理与分享入口落同一共享页签**：真实 UI 点击“把会话整理成页面”，创建后仍选共享，因为两按钮未区分意图。最小增加 `page_intent`：整理进入预览，分享进入共享。主任务已实际分别创建验证，见下节。
3. **公共 seed 的真实冲突键缺陷**：`AuditLog` 的可空 `ontology_id` 是平台共享审计追溯，原 upsert 却按领域表追加冲突键，导致真实 PostgreSQL admin seed 报 InvalidColumnReference。此项由 ops 组独占公共 adapter 修复并补真实 PG 回归；QA 未在夹具绕过该错误。其公共回归数量归 ops 报告，不重复计入 QA。

未将测试夹具不完整、过时 mock 断言、Windows 输出编码或没有明确设置测试角色的范围假设算作产品缺陷。安全判断以真实查询和授权检查为准；例如 seed R01 没有对象 export 时返回 403 属正确拒绝，不能为了旧断言删除授权检查。

## 独立 UI 测试服务与实际证据

现有 Vite 支持 `VITE_API_PROXY_TARGET`，无需改公共配置。使用工具可跟踪的前台会话启动唯一 loopback 测试 API 与前端，无 reload；原开发 8000/5280 未动。Hidden Start-Process 的首次启动尝试被自动审批拒绝（仅返回 blocked by policy），改用经主任务确认的可跟踪前台方式，未扩大权限或改用隐蔽后台。

```powershell
# backend：只接受唯一测试库，准备可执行来源并启动 8001
.venv/Scripts/python.exe scripts/qa_use_test_server.py

# frontend
$env:VITE_API_PROXY_TARGET='http://127.0.0.1:8001'
$env:VITE_API_MODE='real'
node node_modules/vite/bin/vite.js --host 127.0.0.1 --port 5282 --strictPort
```

测试服务起初只允许 QA pages 路径写入；后续按主任务明确授权，仅为 ops 本地 UI 验证增加 `ops_local` / `OPS-UI-LOCAL` / `U010001` 的精确 validate、手动 stage 与数据库确证本地 run 的 confirm 例外。模型、外部执行及其他 ops/integration 写入口仍拒绝，没有启动测试库 worker。该扩展由专属纯 prepare helper 创建 1 个本地测试目标和明确动作/角色，未重置 QA UI 页面；其业务验证结果由 ops 组/主任务记录。

专用源 `QA-UI-SOURCE` / `QA-UI-SOURCE-T001` 为隔离测试库真实查询：当前发布、非 seed 已完成轮次，最初只读查询公开 `store_no,name` 得 95 行。它不是开发库数据、人工补造业务结果或 LLM 回答。后续 ops 专属目标使测试库门店增为 96；此前 95 行的 UI/CSV 检查仅描述当时数据，不声称始终是 95。

主任务实际操作：

- 原开发界面只读：Workbench 搜索“门店”返回 22 条、每页 10 条，从 1/3 到 2/3 更换 10 条；搜索 `NO-SUCH-SESSION-TEST` 返回 0，原会话内容保留，清空恢复。模板“大区经营周报”预览 4 张卡均只结构、无计算值，新建禁用。
- 原开发历史模式 `ver=v2026.10.2` 进入 MyPages，历史只读条保留、28 页正常读取；新建、重命名、删除、刷新禁用。P001 共享设置可查看，radio/switch/保存禁用；未点击复制、未改开发数据。
- 隔离 UI：从真实源创建 `UICHECK-门店页面-01`（P906），实际预览 1 张表格卡、5 行缩略内容，受控缓存共 95 行；立即刷新成功。保存模板时空名称禁用，`UICHECK-门店模板-01` 保存成功且预览没有结果值。
- 用该模板新建 `UICHECK-模板生成页面-02`（P907），重新读取真实测试源 95 行，缩略预览显示 5 个实际行。[模板新页截图](../frontend/shots/use-tests-20261027/template-page-created.png)。
- P906 指定张娜 `U010252` 为可查看成员，保存成功；浏览器重载后仍有 1 位成员。该持久化只发生在测试库，没有修改开发库授权。
- P906 首次 UI 点击 CSV，后端日志及独立 API 检查均为 403，因为缺真实 export grant；不算下载成功，也不是绕过授权。随后按主任务授权，仅测试库新增 `QA-UI-EXPORT` 角色及当前本体绑定，明确给王敏/store 的 view+export；真实授权过滤 COUNT 授前 95、授后 95，没有扩大可见行。再请求 P906 CSV 为 200，实际解析 `text/csv` 得公开表头 `store_no,name` 和恰 95 条数据行，标注“最多200行 / 未截断”。主任务随后点击 P907 CSV，8001 日志已确认 200；未读取其浏览器下载文件，不把点击冒充下载内容检查。
- tab 修复后，从源点击整理创建 `UICHECK-整理预览-03`（P908）实际自动进入预览；点击分享创建 `UICHECK-分享入口-04`（P909）实际进入共享。四页、一模板均为测试库 UI 前缀记录，未重置或伪装成开发数据。
- P909 预览进入卡片调整，移除唯一卡片仅形成草稿；取消出现未保存保护，放弃后恢复原 1 卡。再次调整、不改变顺序，保存顺序成功；随后立即刷新仍读取原执行源，执行计划未丢失。
- 主任务测试标签页实际 CSS 视口为 2560×1440、devicePixelRatio 0.75，主标签页 1920×1080；不根据截图物理像素推断其他布局结论。

测试 API 最初 PID39448 / session70601，后为明确的本地 ops profile 重启到 PID47560 / session93760；为加载最新 ops 批量权限修订再次重启到 PID19440 / session39263，均无 reload且已启动完成，原四页一模板/目标/待确认记录不重置。前端 session69913 保持不变。最后仅加载应用、不调用 prepare/seed，重启 API 为 PID26248/session48186，供主任务第二次本地动作确认。两次操作及只读数据库核验见[总报告](26-10-27-use-tests.md)：标记 false→true→false，最终2条成功Run/2条完成事件/0个外部调用，新生成日志准确，旧历史不改。主任务确认无草稿、UI完成后，已通过对应前台会话 Ctrl+C 关闭 API48186 与前端69913，随后查询8001/5282均无监听；两者中断退出码1不代表业务测试失败。没有关闭主开发服务，测试数据保留。启动成功仅说明服务就绪，不表示 provider、外部执行器或后台 worker 已验证。

## 范围与未测边界

本轮验证涵盖本地 PG、受控公开表格查询及已有 QA 写读生命周期；没有连接模型 provider、外部数据源或发送真实外部副作用。自动计划/订阅执行、任意自然语言 SQL 生成、跨领域新源适配仍未启用。本轮没有验证生产登录、生产网络部署、模型质量或预测；ERP、Kafka、模型提分继续后置。并发多进程发布切换及超大真实业务源的负载不据本组单进程功能回归宣称通过。

后续用户反馈的指标试算另属对象组补充范围：仅在同一QA测试库新增专属metric/门店/3笔订单，再次开放受限测试API69181与前端47999。真实POST响应10.20/30.40及取消999排除已核，图表/长文本修后仅2560×1440实际布局确认；1920未验证。主任务确认无草稿后已Ctrl+C关闭两个会话，并查询8001/5282无监听。完整时序与边界见[总报告](26-10-27-use-tests.md)，不重复计入QA83个用例。
