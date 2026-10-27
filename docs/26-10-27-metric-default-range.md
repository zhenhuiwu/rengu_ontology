# 已发布指标默认时间范围（2026-10-27）

基线 `125dcbd`。落实用户3A：缺时间时仅采用本体该指标明确、已发布、可执行的默认；无默认／冲突时追问，不可执行或无权限时明确拒绝。旧15项定义、公式与数据不改，不给平台写死30天。本轮没有主库写入、迁移、真实模型调用或全仓回归。

## 存储与编辑合同

原模型只有时间字段和默认过滤，没有可用默认范围。现在复用 `Metric.def_rows` JSON及既有 `execDef`／CR／发布快照／指标版本账本，无新列或迁移：

```json
{"kind":"rolling_days","days":7}
```

- 只支持现有受控试算的1—93个本地自然日，含今天，复用同一 `metric_window`；不新做周／月／季度算法。`days`必须整数（不接受布尔、字符串、额外键）。
- 存储行为 `{k:"默认时间范围",v:<上述对象>}`；详情 `execDef.defaultRange` 和目录 `defaultRange` 返回对象，未设置为null。
- CR目标 `metric/def`、item key=`defaultRange`，after直接为对象；清空走delete。保存草稿不影响问数默认，审核发布后生效；默认范围变更进入真实指标版本。
- 新快照明确记录null。旧快照／旧账本没有该键时不推造“当时未设置”，恢复时不借旧缺键清空当前值。
- 指标编辑沿用现有Ant表单，只增加“缺少时间时：未设置／最近若干天”和天数，无预填天数。发布／草稿／历史详情显示各自的真实设置，清空和切换不会回退草稿或当前默认。

## 问数执行边界

识别对象来自当前会话本体的实际指标名／API／编号／已存同义词，取唯一已发布口径；草稿和下线指标不能供给默认。同名多口径不猜选哪条，指标查看权限和当前对象行权限均检查。历史问数写入仍由原API只读守卫阻断，默认解析器也拒绝直接调用借用当前默认；历史详情只读自身快照。

用户明确“昨天／本月”等窗口优先，不叠加默认；“按日／按月／每天”等只有分组粒度，不算给了日期窗口。默认未设置时，即使问句含“多少”，也在模型调用前追问时间。

**本地受治理SQL与指标层：** 默认区间先进入确定性上下文，执行前还实际注入WHERE，补回指标原有过滤；继续走SQL Guard和当前行范围，不仅依赖模型提示词。模型遗留的可识别ISO日期窗替换为发布范围；CURRENT_DATE、INTERVAL、CAST、DATE_TRUNC等无法证明等价的剩余时间约束保守拒绝，避免实际缩成1天却显示7天。只有确实进入查询后，成功／空结果才显示实际默认区间。

**显式专用来源：** 不把专用URL表偷偷交给主问数连接。对唯一已发布指标的精确裸名称／编号／API（可带问号），直接复用 `evaluate_metric(period="none",days=N)`，在原SQL上做完整聚合，不能加总日均或日比例；沿原 `Bound.read`、非高权只读账号、源RLS／可信本体GUC、公开属性和行过滤。附加条件、分组、会话范围／会话口径暂不支持时明确拒绝，不省略。不会新解析“X是多少／请问X”等包壳，本轮不宣传任意自然问句已支持。

专用来源结果无可重放SQL（`sql_text=None`），不捏造页面刷新执行计划；answer.canPin=false，正文说明暂不支持固定到页面，现有固定API仍会拒绝缺执行依据的回答。该路径可以0次provider调用取得真实聚合值，不属于真实模型生成成功。

## 定点验证记录

所有后端写验证仅用 `rengu_fix_static_20261027`，受限runner保持外部执行禁用；专用来源用测试私有schema及专属只读角色，未接生产外源、未改主库角色／权限。测试沿用真实对象映射、当前身份和PG Guard。

```powershell
# cwd backend；只跑该新增文件
.venv/Scripts/python.exe scripts/run_use_tests.py --db rengu_fix_static_20261027 -- -p tests.use_objects_support tests/test_metric_default_range.py -q --tb=short --show-capture=no --maxfail=2 --junitxml=testlogs/metric-default-range-source.xml
```

最终新增文件 **15 passed，11.92s**。涵盖：SQL与指标层实际执行、7天及取消过滤真实结果、无默认在模型前追问、显式时间优先、仅按日／月仍取默认、冲突、无权限／本体不符、非发布／不可执行拒绝；CR保存／发布／清空／历史快照及整版回滚；严格类型；专用只读来源合计172.25、原SQL整体比例13.25、额外条件／分组／会话范围拒绝、不可固定。实际源读取不是mock核心边界，provider均FakeLLM或禁止调用桩。

另一次定点合集选择新文件（当时14case）、原 `test_static_trials.py::test_metric_time_field_and_filter_via_cr_publish_and_rollback` 和 `test_qa_value_index.py`，**28 passed，20.49s**，JUnit `backend/testlogs/metric-default-range-final.xml`。此后补专用来源和严格时间约束，按上述15项重测；各轮有重叠，不相加为全仓或唯一总数。均只有既有pytest-asyncio event_loop_policy弃用warning。

首轮3失败来自直接服务fixture未登记业务治理表；显式登记并加载当前本体后，第二轮10pass／2fail：一处旧已通过seedCR与新发布目标冲突，隔离fixture按既有试算测试准备处理；另一处读取回答嵌套结构时暴露本轮默认说明写在顶层，已修为真实answer正文／basis。随后12pass，再补上述分支。原日志和JUnit保留，未改变产品鉴权求绿。

前端 `node frontend/scripts/check-definition-forms.mjs`、`node frontend/scripts/check-static-pages.mjs` 两入口exit0，覆盖未设不猜、7天保存、94拒绝、清空delete、普通编辑不制造默认、草稿7天与历史3天隔离。限定Ruff F/I通过。统一typecheck／build和实际浏览器由ROOT最后执行，未以脚本冒称已真实UI保存／发布默认。

## 文件归属与保留事项

- 后端：static `metric_defaults.py`（新增）、`metric_versions.py`、`metric_trial.py`、`writeback.py`、`shapes.py`；QA `metric_defaults.py`（新增）、`guards.py`、`chain.py`、`catalog.py`。
- 前端：`api/definitionForms.ts`、`ontology/parts/DefinitionEditor.vue`、`ontology/MetricsBody.vue`及上述两个脚本。
- 新测试：`backend/tests/test_metric_default_range.py`。未改共享runner、通用auth／db、B的UI helpers、C的模型预算代码或截图。

本轮只补平台机制。主库旧15指标没有默认范围，继续按明确缺项追问／拒绝；库存、税率、跨事实口径等未确认业务内容不变。通用外源SQL／跨对象聚合、任意自然语言表达和专用来源结果的页面固定／刷新不在此最小支持范围。


## 追加：真实试算天数选择（2026-10-28收口）

ROOT从实际新指标发现：指标默认7天已保存，但试算入口仍硬编码range:30d，标题固定近30日；数据恰好相同不能当7日结果。现在仅兼容扩展HTTP `MetricTrialIn.days` 为可选严格整数1—93，传给已有evaluator；旧range:30d／缺省仍30，无新增日期算法。前端trialMetric发`{crId,days:N}`，标题与请求复用同一个N。

MetricsBody真实模式增加小天数输入：尚未手选时取当前已发布／已保存草稿合法defaultRange，否则明确选择30天；手选用独立哨兵保存，迟到详情和CR头不覆盖。手动清空保持空并禁用试算，不偷偷变30。改变选择就清旧结果并废弃迟到响应；本体／指标／发布草稿意图／历史切换才重置手选，CR加载进度不当作新选择。mock原30日行为保持。

新增`test_static_trials.py::test_metric_trial_explicit_days_and_legacy_range`，只在独立`rengu_fix_static_20261027`执行：**1 passed，3.49s**，真实PG下1日只25日、2日有24／25日，旧30日范围仍9月26日至10月25日；0／94／true／字符串7均422。日志`backend/testlogs/metric-trial-days.log`、JUnit`backend/testlogs/metric-trial-days.xml`。没有重跑15项默认范围用例。

`node frontend/scripts/check-static-pages.mjs` exit0，实际MetricsBody VM新增初始30→默认7、手选90后迟到默认3与CR头不覆盖、请求90／显示90结果、清空零请求／清结果、7日迟到结果不落30日标题、历史重置取自身3日默认等断言。后端两文件Ruff F/I与AST通过，限定diff检查通过；类型／构建及真实UI由ROOT统一，未以此冒称主UI已成功试算7天。

新增修改仅`backend/app/modules/static/metric_trial.py`的输入与参数透传、`backend/tests/test_static_trials.py`追加一个case、`frontend/src/api/static.ts`、`MetricsBody.vue`及既有小脚本。旧15指标、数据、迁移、QA默认执行及V1/V2/V5不变。

## 收口清单、关联与手册待同步（只读整理）

本组精确文件（相对仓库根；新文件标“新”）：

```text
backend/app/modules/static/metric_defaults.py              新
backend/app/modules/static/metric_versions.py
backend/app/modules/static/metric_trial.py
backend/app/modules/static/shapes.py
backend/app/modules/static/writeback.py                    本组defaultRange段；另有本组PK同步段
backend/app/modules/qa/metric_defaults.py                  新
backend/app/modules/qa/guards.py
backend/app/modules/qa/chain.py
backend/app/modules/qa/catalog.py
backend/tests/test_metric_default_range.py                新
backend/tests/test_static_trials.py                       本组仅追加days单例；B另改V14期望
frontend/src/api/definitionForms.ts
frontend/src/api/static.ts
frontend/src/pages/ontology/parts/DefinitionEditor.vue
frontend/src/pages/ontology/MetricsBody.vue                本组逻辑/范围模板；B另有scoped CSS
frontend/scripts/check-definition-forms.mjs
frontend/scripts/check-static-pages.mjs
docs/26-10-27-metric-default-range.md                      新
```

Git建议将“指标已发布默认范围＋真实试算天数”作为一组，包含上下游合同与回归；`writeback.py`只取defaultRange存储／恢复段，不混另一组逻辑PK同步。`MetricsBody.vue`与`test_static_trials.py`存在B组独立hunk，须核对分段归属后暂存；可以协商同一交付组，但不能将其归为本组独自修改。不要`git add .`带入旧shots删除、旧未跟踪截图／baseline日志、模型预算或独立服务脚本。

关联风险：新增defaultRange有意在新快照记录null、在旧无键快照保持未知，不能把缺键等同默认30；专用来源的裸指标汇总无可重放SQL，不支持页面固定／调度；不明确的时间条件拒绝，不把样本或提示词当实际执行依据。旧15项没有被自动补默认或业务公式。

ROOT已实际确认新指标近7日UI试算成功，随后统一构建含tsc、4490模块、27.02s通过；本组没有另跑构建。该确认不等于任意自然问句、历史源映射或跨源SQL均可执行。后续样式、空上下文及标准事件改动归B/C另记，不能用此构建冒称包含所有后续源码。

只读核对`docs/user-guide.md`后，需要交手册维护者补充（本次未改手册）：

1. 指标定义段加入“缺少时间时：未设置／最近N天”的发布生效规则，明确显式时间优先、冲突／不可执行仍追问或拒绝；试算手选N不会修改问数默认。
2. 将固定“近30日试算”改为实际可选1—93天：初始取合法已保存默认，否则30；标题、请求和结果scope一起核对，不能用两范围恰好相同的金额当时间范围证据。
3. 专用来源仅精确指标裸名可安全汇总；不将其描述成任意问句／附加分组已支持，`canPin=false`不可固定到页面。旧截图14保留当时真实含义，不能当作新增默认范围控件截图。

验证分轮仍按上文15pass、28pass、days单例1pass及两个前端入口分别记录，不相加成唯一总数或全量通过。本次仅文档整理，没有额外测试、源码、数据库或服务操作。
