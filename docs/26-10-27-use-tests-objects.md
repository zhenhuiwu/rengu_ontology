# 对象使用区针对性测试（2026-10-27）

用户本轮明确授权对改动进行完整针对性测试，覆盖此前不运行后台测试的限制。本文件记录对象组；QA 页面与动作组另有独立记录。

## 环境与数据边界

- 唯一可写数据库为本机 5433 的 `rengu_test_use_objects_20261027`。测试库迁移、seed、每例外层事务回滚均使用该库；未写开发库 `rengu_ontology`，未连接 `auto_ontology`。
- `backend/scripts/run_use_tests.py` 在内存读取 ignored `.env`，核对本机 5433 开发连接，仅用于检查/创建明确列出的测试库。启动前检查 `pg_auth_members` 中已有的直接 runtime-role 成员关系；此次该关系已经存在，迁移未新增生产角色授权。连接信息和密码不输出。
- launcher 显式设置 `RENGU_DATABASE_URL`、`RENGU_TEST_DB` 及 QA 只读连接到测试库，禁用 provider、外部投递、数据源实际连接开关；通用对象源测试单独临时启用测试库白名单。
- `-p tests.use_objects_support` 仅在该唯一库装配 scope1、真实已 seed 用户的本体成员和角色绑定。需要 CSV 的测试明确给测试角色 export grant；不以管理员身份跳过真实权限。
- 通用源回归在测试库创建随机 schema、真实实例和临时专用 LOGIN 角色；正常角色非 super、NOBYPASSRLS，仅有该表 SELECT。另创建仅用于拒绝验证的 BYPASSRLS 角色；均在 fixture 结束删除专属 schema/授权/角色，不变更现有账号。

## 自动化命令与结果

命令工作目录：`D:\项目\rengu_ontology\backend`。

```powershell
.venv/Scripts/python.exe scripts/run_use_tests.py --db rengu_test_use_objects_20261027 -- -p tests.use_objects_support tests/test_qa_explore.py tests/test_qa_objects.py tests/test_qa_objects_db_only.py tests/test_object_permission_projection.py tests/test_object_row_access.py tests/test_static_explorer.py --tb=line --show-capture=no -q --maxfail=12
```

初始准备因共享 AuditLog 的错误冲突键发生 8 个 setup error。ops 组经主代理授权修复共享 PostgreSQL upsert 判定并单独真实 PG 回归后继续，未在夹具绕开该问题。随后首轮 19 passed / 12 failed（maxfail 停止）：主要为旧测试仍断言合成 sales/KPI、旧筛选别名和默认 export 权限。按已发布公开属性/真实授权契约更新断言与测试授权，未放松产品校验。完整第二轮为 **88 passed / 7 failed / 1 warning，477.36s**；角色绑定夹具和真实日期边界修复后的定点复跑（下列命令）**56 passed / 1 warning，203.19s**。

```powershell
.venv/Scripts/python.exe scripts/run_use_tests.py --db rengu_test_use_objects_20261027 -- -p tests.use_objects_support tests/test_object_source_regression.py --tb=short --show-capture=no -q --maxfail=4
```

真实源首轮 8 passed / 2 failed（maxfail 停止）。历史测试错误地修改了只追加发布记录，数据库正确阻止，现改为追加测试专属发布快照。第二轮 **14 passed / 1 failed / 1 warning，103.68s**；万级聚合失败位于 API 调用前，独立物理计数已证明 executemany 的测试准备条数不足。改成单条 `INSERT ... SELECT generate_series(0,10000)` 并先断言 `INSERT 0 10001` 和物理 count10026，再验证 API；聚合实现未作放松，定点复跑通过。

```powershell
.venv/Scripts/python.exe scripts/run_use_tests.py --db rengu_test_use_objects_20261027 -- -p tests.use_objects_support tests/test_object_source_regression.py::test_full_aggregate_exceeds_id_expansion_limit_and_export_never_truncates tests/test_object_row_access.py tests/test_static_explorer.py --tb=short --show-capture=no -q --maxfail=4
```

合并命令为第一条完整六文件命令追加 `tests/test_object_source_regression.py`，使用 `--tb=short --maxfail=4`。结果为 **111 passed / 1 failed / 1 warning，442.70s**。唯一失败是新增测试错误地把 filterable 属性当作权限依据，期望自定义来源 `segment` 行范围返回13条；真实公共 `scope_expr_for_object` 对未登记 permissionBasis 正确返回 DENY_ALL，实际0。按既有契约修正此断言，未改变生产权限代码；追加搜索/分面/聚合无数据、详情404、显式导出 grant 仍403及未知跨对象属性仍无数据的断言。

最后执行上述真实源文件命令（`--maxfail=2`），结果 **17 passed / 1 warning，87.13s**，修正的拒绝用例及其新增断言通过。共 **112 个不同 case 已有通过记录**：合并轮中的111项与修正项复测，**不是最终一次112项整轮全绿**。源码及测试已冻结，不再重复完整合并运行。结束后仅SELECT核查：临时 `obj_source_%` schema为0，`obj_read_%`/`obj_bypass_%`角色为0，清理完成。

轻量检查：`npm run typecheck` 通过；`python -m scripts.check_object_source` 纯内存断言通过；14个相关Python文件 AST 通过；新增脚本/支持夹具/真实源回归/重写旧 DTO 测试的完整 Ruff 规则通过，相关既有 Python 文件的 Ruff F/I 检查通过。限定差异空白检查通过（Git已有LF/CRLF提示）。完整既有文件风格检查仍报告长行及原有规则告警，未为本轮测试批量重排生产模块。

## 已定位和修复

1. 对象搜索 Ant 排序 tooltip 覆盖上方范围提问按钮，鼠标无法点击。仅对对象搜索表关闭冗余 sorter tooltip；主代理已重现并复测鼠标点击恢复。
2. 范围提问建议沿用不可执行的零售 qaPaths。真实搜索和详情统一使用后端 `scopeQuestions`：对象数量及真实公开、已映射数值属性的合计/平均值，最多三项；显式 mock 保留原行为。不支持的问题仍明确报错，不调用 provider。
3. 日期范围端点曾以 `[:10]` 截取日期，导致带 NUL 的输入被误接受。改为严格日期解析并拒绝 NUL/无效 UTF-8；错误仍为字段校验。
4. 统一分面保留真实 enum 声明次序；无实例绑定的静态分面保留元数据空骨架并标记 `hasData:false`，不补造零售取值。

针对性覆盖范围：

| 边界 | 自动化证据 |
| --- | --- |
| 新旧搜索/详情/导出公开属性 | `test_qa_explore`、`test_qa_objects`、`test_qa_objects_db_only` 与真实源回归：hidden/sensitive 值不输出，受限主键整项拒绝，标题只回退公开主键 |
| 真实分页/排序/分面 | 实际 25 条受控源、每页10条不重叠、反向数值排序、枚举自排除分面、数值区间；目录 SQL 实际 COUNT/LIMIT/OFFSET |
| 完整范围聚合 | 物理 count10026；count/sum/avg/min/max、筛选范围、选中 ID 去重；不经5000 ID展开截断 |
| 关系权限/分页 | 源对象存在性与行范围、目标对象独立 ACL、13条真实单跳结果分页10+3；连接属性无需开放给用户筛选 |
| 导出 | 默认 grant 拒绝，测试库显式 grant 后通过；新旧 CSV 公式转义、受限列排除、空 IDs 只表头、5001 IDs拒绝、10026条拒截断 |
| 真实权限 | 实际用户/角色成员、角色绑定、零售已登记行过滤依据、撤销绑定；未登记新来源权限依据 DENY_ALL；仅详情权限可读共用元数据/单行/关系，但无搜索/批量提问权 |
| 历史/跨本体 | 追加不可变发布快照，当前无效映射不替代历史映射；同码另一本体无绑定时不借零售数据；实例均标 current |
| 来源约束 | 实际只读事务/GUC/RLS、写操作拒绝；super/BYPASS账号拒绝；来源库不匹配、URL options、非法表名在连接前拒绝；未映射/transform属性不执行；真实锁等待触发语句超时 |
| 输入边界 | 公开属性白名单、未知筛选/旧别名、NUL/无效Unicode、数值/日期端点、不支持问题；没有任意SQL或provider兜底 |

## 实际 UI 与只读对照

主代理在稳定主服务完成以下真实界面核查，无业务写入：

- 零售订单 1,286；按京东到家筛选 225，实付金额升序首值 7.03。无匹配关键词显示“没有符合条件的对象”，提问/导出禁用；清除恢复 1,286。
- 当前完整授权范围：实付金额合计 **387245.43**，平均值 **301.1239735614308**，最小值 **3.75**，最大值 **1229.7**。`verify_object_totals_readonly.py` 使用开发库强制只读事务和相同实际用户/Bound 行范围 SELECT 对照：平均值精确 Decimal 为 301.1239735614307932，其余一致。京东到家 count225 / sum66698.98 / avg296.4399111111111111 / min7.03 / max1229.70。
- 选中两笔订单提问数量为 2；不支持的“预测明年的销售额”返回 DATA_NOT_CONFIGURED，保留输入可重试，再点数量恢复 1,286。新建议仅数量、实付金额合计、实付金额平均值；点击合计返回 387245.43。
- 历史深链接 `/use/objects/search?ontology=1&type=order&ver=v2026.10.2` 保持“历史/只读”，当前实例 tooltip 明示按该版本定义查询当前数据；总数 1,286。进入 SO202610220701272 详情保留 ontology/type/id/ver 和只读状态。
- 先前已核门店 95、HF0004 关联 121，第二页 10 条；工作空间2对象/动作空，无零售串用。

修后截图：[范围合计与有效建议](../frontend/shots/use-tests-20261027/object-aggregate-after-fix.png)。这些证据不代表历史实例恢复、生产外源或动作执行已验证。

## 边界

通用源真实执行仅针对隔离的本地 PostgreSQL 测试库。未连接生产外源、未更改生产配置/源授权，其他数据库驱动与真实跨主机网络配置不在本轮证据内。不把只读检查或测试库角色分配声称为生产授权验证。新的来源白名单/可筛选属性不是权限依据：现有管理端只提供已登记的 permissionBasis，直接写入未登记来源属性范围会 DENY_ALL，不能为了启用数据而自动泛化权限字段；源数据库已有 RLS 仍实际执行。pytest 的既有 event_loop_policy deprecation warning 单独记录，不扩大修改通用 conftest。

## 补充：用户反馈的构建指标试算（同日独立任务）

原对象112项专项未覆盖构建区指标试算页面。用户反馈后主代理在主库真实 UI 复现 `gmv`（销售额不含税 / sales_amount_net v3）：点击试算，inline 与 toast 均报属性“不含税金额”不存在或含义不唯一。源码中原有 `test_static_trials` 有 GMV 不可执行应返回422的保护断言；不能把该历史断言称为本轮此前已经实际测试了这个入口。

主库核对仅用强制只读事务 SELECT，未更改任何定义或业务数据。当前及 v2026.10.3 快照中的 GMV 公式均为 `SUM(订单行.不含税金额) − SUM(退款行.不含税退款金额)`，对应真实已发布属性是 `order_line.amount_ex_tax`（行金额〔不含税〕）、`refund_line.refund_ex_tax`（退款金额〔不含税〕），并不等于公式所写名称。两个属性 `backing_column` 都为空；ERP 源列只是已登记元数据，本地 `order_line` 实际只有 qty/price 等列，没有不含税金额/税率字段。退款行没有受治理物理表绑定。不得用 qty×price、固定1.13或猜税率补造结果，也未自动把旧名称当别名替换。

另有独立阻断：现有指标 def_rows 中的 `订单行.出库时间`、`订单.状态`、`门店.是否测试店` 不匹配已发布属性；试算引擎目前只执行单对象聚合与过滤。对象关系图/related 接口已有单跳关联能力，但它不等于试算引擎已支持跨对象过滤或跨事实聚合契约；没有忽略默认过滤来取得“成功”结果。

### 主库当前15项 readiness 清单

以下按实际保存定义扫描，**可直接试算为0项**。这是现存定义/数据映射及当前执行能力状态，不声称15项已实现可算。

| 指标 | 当前阻断 |
| --- | --- |
| atv 客单价（不含税） | 当前为草稿，不能当已发布指标执行 |
| gmv 销售额（不含税） | 公式/时间/过滤属性旧名称不匹配；候选金额字段未绑定；退款行未绑定；跨对象聚合/过滤尚不支持 |
| gmv_tax 销售额（含税） | 公式引用“退款”而真实对象为退款单/Refund；时间/过滤字段不匹配；跨对象条件尚不支持 |
| gross_margin 毛利率 | 直接引用指标名称而未绑定聚合对象；时间/过滤字段不匹配及跨对象限制 |
| gross_profit 毛利额 | formula为空 |
| new_member 新增会员数 | formula为空 |
| order_cnt 有效订单数 | COUNT订单本身可识别，但状态名称、时间及门店默认过滤不匹配；跨对象过滤尚不支持 |
| refund_rate 退款率 | 自然语言/集合等尚无可执行表达式 |
| repeat_sales 复购客户销售额（不含税） | formula为空 |
| repeat_sales_tax 复购客户销售额（含税） | formula为空 |
| repurchase 复购率 | formula为空 |
| ship_avg 平均出库时长 | formula为空 |
| ship_ontime 出库及时率 | 自然语言描述，尚无可执行表达式 |
| stockout 缺货率 | formula为空 |
| turnover 库存周转天数 | 当前已下线，不能当已发布指标执行；formula亦为空 |

### 修改与真实回归

- `metric_trial.py` 把同一属性/对象解析用于完整引用诊断及执行编译，明确区分缺定义、歧义、受限属性、未绑定列/对象和跨对象能力限制。缺属性返回该已定义对象中的可唯一识别、公开真实 API 候选及其绑定状态；候选仅供核对，不替换引用或猜算业务含义。
- 新增纯元数据 GET `/api/ontology/v1/metrics/{id}/trial-readiness`，支持相同 ontology/ver/crId 定义上下文。指标页“近30日试算”局部展示具体原因与候选，随工作空间、版本、指标和CR锁版本失效；只有明确不可算的指标禁用试算。API直接 POST 仍按同一 compiler 拒绝缺项。有效定义正常执行，不因其他指标无效而整体禁用。
- POST `:trial` 本身只读取定义并经 `run_guarded` SELECT，不保存 CrTrial；CR `:validate` 的分析保存是另一条明确写入路径。主代理据此可在主UI查看/点击，未发布或修改主库定义。
- 后端先完成隔离测试，主代理重启主API载入新路由后才启用前端 GET，避免混合新旧接口。后续文案明确写“试算引擎暂不支持跨对象聚合或过滤”，不声称关系不存在。

```powershell
# 工作目录backend；同一objects唯一测试库
.venv/Scripts/python.exe scripts/run_use_tests.py --db rengu_test_use_objects_20261027 -- -p tests.use_objects_support tests/test_static_trials.py tests/test_metric_versions.py tests/test_metric_variants.py --tb=short --show-capture=no -q --maxfail=4
```

结果 **34 passed / 1 warning，50.58s**。新增真实 PG 用例经过实际角色/对象映射及 `run_guarded` 执行 `SUM(SalesOrder.paid_amount)`、明确 `SalesOrder.created_at` 与状态/渠道过滤，得到两日 **10.20、30.40**；准确显示名/API 两种引用可执行，同名歧义与未绑定字段拒绝。GMV 原始公式 readiness 与直接 POST 的 issues 一致，且未新增 CrTrial 或改变定义。候选唯一性完善后两项定点复测 **2 passed / 1 warning，3.78s**；状态与能力文案完善后最终两项定点 **2 passed / 1 warning，4.44s**，包含草稿/下线状态明确区分。

### 独立QA库 UI 成功核查准备

主代理另行明确授权，仅在 `rengu_test_use_qa_20261027` 新增 `uicheck_order_paid` / UICHECK 订单实付合计（v1），以现有真实 SalesOrder 属性映射为前提。`scripts/qa_test_metric_prepare.py` 只新增专属门店及3笔订单：10.20、30.40、被取消并排除的999.00，渠道为唯一 `UICHECK_METRIC_20261027`；不重置seed、不改既有4页/1模板/2个opsrun、不写用户角色/本体成员或对象属性。helper在提交前用同一 readiness 与 evaluator 断言10.20/30.40，任何已有映射或权限不符即失败回滚。

QA组已只调用该helper并commit，实际evaluator返回固定测试时钟下 **2026-10-23=10.20、2026-10-24=30.40**，取消999被排除；未调用原prepare或reseed。原安全测试服务仅为此固定指标、ontology1、U010001、恰 `{range:'30d'}` 放行试算POST，其余写限制、provider/外部连接/worker禁用保持。

主代理已实际查看主库GMV局部readiness：API200，七类真实缺项可见，试算禁用，没有再抛原始泛化错误。首版把候选全摊开信息过多，按真实UI反馈改为现有 `a-collapse ghost` 默认关闭“查看已定义属性”，无标签计数；默认只留具体阻断原因，展开才读全部真实API候选，不新增匹配算法或删除缺项。

主代理已在专用QA前端对 `uicheck_order_paid` 点击试算，POST200，图表真实两点、期间、口径及数据截至均显示。此过程发现另一真实UI缺陷：Y轴沿用演示固定除以10000并四舍五入，小金额刻度全为“0万”，后端数值并非0。仅修改本指标组件真实模式：Intl compact 按实际数量级显示，轴单位来自指标定义；tooltip用richText显示后端原始日期/十进制数值字符串及单位，不乘百分比或转换税率/口径值；小范围日期自动间隔。显式mock维持原格式，未全站修改Home等其他图表。主代理修后已实际确认：轴名元，Y轴0/5/10…35，X轴10/23和10/24都可读，真实图线10.2→30.4。截图 `frontend/shots/use-tests-20261027/metric-trial-success.png` 已更新；不将测试指标的成功称为主库GMV已经可算。

该截图同时暴露定义 Descriptions 默认过滤长串跨过左右分隔线、盖到图上的原有布局问题（1706 CSS像素亦可复现）。根因是Ant bordered表的auto布局覆盖了原低优先级fixed规则，且值不换行。仅在该定义区域固定100%表宽/布局、限制内容最小宽并允许长串换行；原title tooltip保留，没有扩大页面结构或改业务。

最终主代理确认正确测试指标页URL及目标DOM viewport为 **2560×1440**：document scrollWidth/clientWidth均2560，长过滤条件单元格right=2074.22，小于图表left=2091.11，无横向溢出或重叠；目视轴单位元、刻度0/5/10…35、10/23及10/24两点10.2→30.4正确，截图 `frontend/shots/use-tests-20261027/metric-trial-success.png` 已保存。1920 viewport API调用没有实际改变目标DOM，因此**不声称本次布局修复已在1920实测通过**。此后仅补文档，源码冻结，未重复测试。

图刻度修后 `npm run build`（含 `vue-tsc --noEmit`）通过，Vite耗时24.24s；随后布局CSS修复的**最终构建再次通过，Vite26.61s**，不把修复前构建当最终证据。4个相关Python文件AST、限定Ruff F/I、metric_trial/新增用例/helper完整Ruff及限定diff空白检查均通过。未提交或启动worker/provider。
