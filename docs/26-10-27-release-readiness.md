# 发布就绪检查补齐（2026-10-27）

在B组已冻结的“未实现项不默认通过”基础上，增加可真实执行的V1／V5。没有改旧15项指标、主库业务定义、历史检查或种子；未修改原默认时间范围实现，未新增迁移。

## 实际实现

- **V1：主键与映射结构。** 只读当前本体对象／属性，再在内存中叠加本单已保存def/config/properties行；不执行临时发布、不造ObjectType、不使用SAVEPOINT写投影。复用发布代码的`_prop_type`检查属性类型，核对主键引用确实存在、来源／源表／来源列文本结构。已绑定来源但主键无来源列、主键指向不存在属性或属性类型非法为阻断；纯逻辑定义缺来源、未映射的其它属性或缺主键为明确警告，不能记通过。
- V1通过的含义仅为**保存定义的结构完整**，说明逐字保留“未验证源数据唯一性、列存在性或连通性”。不抽样查询源表，不保证PK在数据中真正唯一，也不把非空文本当已连接源。
- **V5：指标草稿映射编译。** 复用`metric_definition(crId)`、`execution_bindings`、`compile_metric`，按当前本体与本单指标改动检查公式／时间／过滤中的真实对象属性和受控映射，不另写公式解析器。缺属性／歧义／受限／未映射等可判定映射错误为阻断；语义或执行能力未支持、缺执行环境／身份等给警告，不能记通过。
- 同单还修改对象时，当前编译器没有完整未来对象执行映射，V5明确警告尚未认证，建议先发布对象再复核指标；不拿旧已发布对象映射假报通过。当前用户指标查看权限也需满足。
- V5通过说明明确“仅定义编译，未执行源查询或EXPLAIN”。`run_checks`原有`refresh_analysis`可能另行执行受限指标试跑，结果继续单列；本新增helper没有新增业务源查询。

本结构校验阶段V2源唯一性与V14目标库EXPLAIN仍保持未执行／不适用（随后追加的有界V2抽检见下）；B组V7／V11／V15实际分支、V3／V4／V12及旧seed更高严重度合并全部保留。不为17项全绿补假结果，也不重算ROOT已发布的CR0001。

## 定点验证

新增`backend/tests/test_release_readiness.py`两项检查：纯对象结构（有效、主键不存在、类型非法、主键未映射、无来源警告、入参不变）；独立PG中真实CR公式覆盖（不存在属性→V5阻断，再改COUNT真实对象→通过），并确认已发布Metric.formula未改变、V2／V14仍未执行。

B组`test_cr_check_execution.py`仅增加新helper的精确桩，继续验证fallback和旧seed阻断保留，不拿最小mock session冒充真实映射检查。

```powershell
# cwd backend；只写明确隔离库
.venv/Scripts/python.exe scripts/run_use_tests.py --db rengu_fix_static_20261027 -- -p tests.use_objects_support tests/test_release_readiness.py tests/test_cr_check_execution.py -q --tb=short --show-capture=no --maxfail=2 --junitxml=testlogs/release-readiness.xml
```

**4 passed，3.81s**，仅既有pytest-asyncio event_loop_policy弃用warning。没有重跑此前15项默认范围case／全仓／迁移往返／真实外库或模型。限定Ruff F/I、4文件AST和diff检查均通过；真实新校验UI待ROOT在安全时点统一加载，不把当前旧进程当已载该功能。

## 精确文件

新增`backend/app/modules/static/release_readiness.py`及`backend/tests/test_release_readiness.py`；`cr_write.py`只在calc初始化后调用新helper更新V1／V5，保留B原有全部修改；`test_cr_check_execution.py`只补新helper桩。没有前端改动、业务数据迁移或永久定义写入。新检查自身只读定义元数据，校验端点只沿原合同更新CR检查／事件记录。


## 后续追加：V2当前授权主键样本

ROOT从真实对象分面发现订单业务code重复，已只读核证1286行、code去重1284、id去重1286。平台不能把手选业务字段当天然唯一；旧已发布CR和源数据均不回改，ROOT按真实草稿调整主键，本补丁不替用户保存任何定义。

新增`backend/app/modules/static/pk_sample.py`，由现有release_readiness的对象检查接入V2：

- 复用`object_source.resolve_source`、`Bound.stmt/read`及原只读连接／非高权／源RLS／本体GUC／当前行权限／超时；在内存中按本单已保存的PK和属性映射构造候选，不借旧已发布PK替代新键，int PK允许。原字段transform、hidden／sensitive约束保留。
- 只SELECT待检PK一列，LIMIT既有EXPORT_MAX+1（当前10001），在内存分析最多10000条。没有未索引候选键ORDER BY、全表COUNT／GROUP BY或任意SQL，避免为校验强迫全源排序。样本不承诺稳定顺序或全源覆盖。
- 授权样本发现NULL／空串或重复组即阻断，只显示条数和组数，**不输出原键、SQL、连接或凭据**。未发现只说明“当前授权样本N条内未发现……不代表全源唯一”；截断明确上限；空样本为警告，不能当唯一。
- 缺源、缺权、无公开主键映射、部署绑定无法支持草稿主键时不适用／未执行；读取失败为未完成警告，不把无数据／失败当通过。SELECT错误用savepoint隔离，未用于临时发布或定义写模拟；专用只读源仍由自身事务管理。
- 多对象只有部分可抽检时不会用一个通过认证另一个未检查目标。V14仍明确未配置／未执行EXPLAIN。

新`backend/tests/test_pk_sample.py`两个定点case，**2 passed，7.01s**，日志`backend/testlogs/pk-sample.log`、JUnit`backend/testlogs/pk-sample.xml`：空／NULL／重复／截断单位边界；实际独立PG私有表1286行、code重复2组及另一本体行，草稿code阻断、草稿int id在RLS后授权1286样本通过，未更改已发布ObjectType.pk，隐藏敏感PK不读。准备／清理仅测试库和临时schema／只读角色，不碰主库或生产源。

命令（cwd backend）：

```powershell
.venv/Scripts/python.exe scripts/run_use_tests.py --db rengu_fix_static_20261027 -- -p tests.use_objects_support tests/test_pk_sample.py -q --tb=short --show-capture=no --maxfail=1 --junitxml=testlogs/pk-sample.xml
```

保留现属性transform安全约束后，实际PG单例复测 **1 passed，7.20s**（日志`backend/testlogs/pk-sample-final.log`）；新3文件Ruff F/I、AST与限定diff检查通过；不重跑此前4项／15项。原发布结构／指标默认代码不再扩功能。该测试是受控样本读取，不是主库源数据唯一性认证；主UI新CR校验需ROOT在安全时点重新加载代码。


## 后续追加：对象逻辑PK派生标记（2026-10-28）

ROOT发布v2后只读确认：ObjectType.pk=id及快照对象定义正确，但Property.id和旧code两个is_pk仍为true。`Property.is_pk`是本体逻辑主键／展示派生字段，shapes.prop与properties快照会输出；源数据库物理PK来自独立只读内省Table.pk／实际约束（当前代码并无另一个DataColumn.is_pk模型），不能因本体改选主键就改物理源。

追踪发现：导入先按物理PK生成属性pk标记；对象def/config发布只改pk_property，属性发布_set_prop又可保留旧表单pk:true。现在writeback新增共享`_sync_object_pk`，复用现有primary_property显式逻辑API优先、合法legacy物理名回退语义，只迭代**当前ObjectType.properties**：可确定主键时恰好该属性true，其余false；缺失／歧义不猜选。

对象def/config与properties两条发布路径都同步；def同步改变属性flags时同时标properties dirty，保证只改定义也重建新快照。此处不修改标题、敏感、显示设置、源映射或源约束。历史构建／整版restore仍忠实保留原快照值，不后台洗历史；ROOT随后已通过真实CR3正常发布v2026.10.3，并通过CR4发布v2026.10.4；代码本身未代保存、发布或后台清洗原v2。

新增`backend/tests/test_object_pk_publish.py`一个真实PG case，最终 **1 passed，2.28s**：两工作空间同编码对象，第一轮仅def将code→id，断言live与新properties快照旧false／新true；第二轮properties尝试重新写code:true／id:false，经正常审核发布后仍服从逻辑id。另一工作空间同编码对象／属性两轮均未改。原失败一次为fixture试图在同一Session切scope，被正确拒绝；改复用既有双工作空间fixture与独立scoped Session，不削弱产品隔离。较早单轮1.77s结果被最终同case两发布轮验证覆盖，不相加。

```powershell
# cwd backend，仅隔离库
.venv/Scripts/python.exe scripts/run_use_tests.py --db rengu_fix_static_20261027 -- -p tests.use_objects_support tests/test_object_pk_publish.py -q --tb=short --show-capture=no --maxfail=1 --junitxml=testlogs/object-pk-publish-final.xml
```

最终日志`backend/testlogs/object-pk-publish-final.log`；限定Ruff F/I、2文件AST和diff检查通过。产品仅改`static/writeback.py`共享发布同步，未改B事件hook／QA源码、主库或外部源，未全仓／迁移测试。ROOT已实际完成CR3及后续CR4发布，UI复验见下；B已独立只读核验live／v4 snapshot一致：order.pk=id、is_pk仅[id]；store.pk=code、is_pk仅[code]。95家门店／1286笔订单的业务字段SHA与复制前manifest一致，未改变源业务事实。

## 收口文件与实际UI边界（只读整理）

```text
backend/app/modules/static/release_readiness.py            新；V1/V5与V2调度
backend/app/modules/static/pk_sample.py                    新；有界授权样本
backend/app/modules/static/cr_write.py                     仅本组release_readiness调用；B另有fallback/事件保存hook
backend/app/modules/static/writeback.py                    仅本组_sync_object_pk及两发布调用；默认范围段见另一报告
backend/tests/test_release_readiness.py                   新；2case
backend/tests/test_pk_sample.py                           新；2case
backend/tests/test_object_pk_publish.py                   新；1case两次发布
backend/tests/test_cr_check_execution.py                  B新增2case，本组仅补新helper精确桩
docs/26-10-27-release-readiness.md                        新
```

Git建议将B“未实现校验不默认通过”的基础hunk与本组V1/V2/V5、相应fallback桩按共同校验交付组协调，保留B标准事件保存hook的独立归属；对象逻辑PK同步可单独作为小修复组，按hunk暂存`writeback.py`，不遗漏新properties快照dirty标记。不要将同名Property全表UPDATE或数据清洗当作本补丁内容；本组没有此类操作。

ROOT实际已在隔离UI复验V2：草稿code取得授权样本1286、重复2组、空0阻断；改选id并真实保存后，V2样本通过且注明非全源唯一，V1结构通过。随后CR2已正常发布v2026.10.2，明确PK=id；同时暴露旧code.is_pk残留，才触发上述发布同步小修复。ROOT已确认真实CR3发布v2026.10.3，随后CR4发布v2026.10.4。

发布后实际UI复验：ROOT打开`/build/ontology/object-types?ontology=2&type=order`，v2026.10.4概览显示“主键 Id（id）／标题属性 展示订单号”，v2中将code误显示为主键的现象已消失。没有追加第二套展示修复或改写旧版本；B随后已独立只读核验live／v4 snapshot一致：order.pk=id、is_pk仅[id]，store.pk=code、is_pk仅[code]；95家门店／1286笔订单的业务字段SHA与复制前manifest一致。

手册需要交维护者同步的用户说明（本次未改`docs/user-guide.md`）：选择公开、稳定、非空且真实唯一的逻辑主键，业务code未必唯一；V1只检查定义结构，V2通过只表示当前授权样本内未发现重复／空值，空样本、缺源、无权限都不能当唯一认证。切换本体逻辑主键要经CR审核发布，源表物理PK和数据不会因此修改；不要将发布前导入的旧pk标记当成永远正确的对象主键。

准确分组：结构／映射2项加原fallback2项共4pass（3.81s）；V2新2pass（7.01s）及其同一PG单例复测1pass（7.20s）；逻辑PK一个PG case最终1pass（2.28s）。这些分组与复测不能相加成全量、唯一总数或额外验证。本次清单整理未重复任何检查或启动服务，源码仍冻结。
