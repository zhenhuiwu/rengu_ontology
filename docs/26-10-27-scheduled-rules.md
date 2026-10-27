# 定时规则、真实指标桶与条件编辑（2026-10-27）

## 已落地后端

入口保持“构建 → 规则与自动化”。规则草稿经现有 CR 保存、审核、发布；保存或未保存的输入都不会创建调度。只有显式发布 `trigger.enabled=true` 的定时规则创建 `rule_schedule_cursor`。存量 seed 不回填配置或游标，worker 启动不扫描旧规则触发业务运行。新规则默认未启用。

独立 worker：在 backend 运行 `.venv/Scripts/python.exe -m app.modules.dynamic.worker`，消费 `rules` 队列，一分钟扫描一次；启动立即检查已经持久保存的游标和 pending 运行。显式加载与 HTTP 服务相同的受控表登记，不依赖主服务进程内状态。

`dynamic_0015` 以 `event_0014` 为前置，只增加 `rule_schedule_cursor`、`rule_schedule_run` 空表，不更新既有记录。协调者已将开发库 `rengu_ontology` 增量到后续 `event_0016`；未运行升降级测试。没有操作 `auto_ontology`。

## 计划、身份与幂等

触发 JSON 保存 `kind`、五段 `cron`、IANA `timezone`、`catchupMinutes`（0–1440）、`enabled`、固定真实用户 `runAsUserId`。保存时后端把运行身份固定为本人，发布和每次运行重新读取真实用户、功能权限与对象行权限；不接受请求冒充执行身份。

发布事务同步创建/重置游标，下一次计划严格晚于发布时间。运行唯一约束为 `(rule_id, rule_version, scheduled_at)`。发布咨询锁、游标/运行行锁及事务共同保护并发。先提交 pending 运行与游标，再在单事务内求值、创建命中/告警并保存结果；中断回滚业务结果，重启可处理持久 pending，不会重复提交部分命中。

0 分钟补偿只接收当前一分钟内计划；正值补偿边界含指定分钟。停机积压只合并到最新计划，超过边界标记 skipped；已存在较新计划时旧 pending 同样 skipped。`result.coalescedFrom` 保留积压起点。不回放旧对象状态：补偿读取恢复时真实当前数据，计算时间另存。排队后发布版改变、规则版本/配置改变或权限指纹变化，本次明确失败，不借新定义执行旧任务。

只为明确告警输出保存真实 `RuleHit + Alert + AlertLog`，命中与结果同事务。去重读取非 seed 真实命中，支持规则、真实对象显示名和日期/周/小时身份以及明确冷却；未知维度拒绝。通知和自动暂存缺真实通道/映射时不伪称成功，不执行外部业务请求。已有事件规则的显式 `enabled=false` 返回未命中及停用原因；legacy 缺字段保持原通道语义。事件触发配置不会自动生成事件订阅或部署路由，新事件规则仍需真实订阅及明确 `rule_code/user` 路由。

`GET /rules/{id}/runs?page=1&size=10` 返回 pending/done/partial/failed/skipped、计划/开始/结束时间、真实候选/求值/缺值/命中/告警/去重数及失败原因。只返回本人且当前权限指纹一致的运行，避免低权账号读高权聚合结果；更换角色后旧运行仍保存在库中，但不借当前身份暴露旧计数。告警/命中沿既有行权限页面读取。

## 指标时序

公开 `app.contracts.static.evaluate_metric` 增加可选 `at/timezone/object_kind` 与 `period=week`，原调用默认行为保持。复用真实已保存公式、属性映射、SQL 白名单与行权限，按真实时间字段聚合。没有公式、时间字段、版本映射或对象分组映射返回明确 422，不能把汇总值分摊到每个对象。

结构条件编译为 `metric_value("指标编号", 版本整数, "day|week", "value|prev|wow")`。也支持严格单对象 `metric@vN(object, day|week)` 和完整引用外包的 `prev(...)`/`wow(...)`；省略对象/时间桶的历史写法、多维调用及不具备映射的跨对象口径继续拒绝，不根据显示名猜测。

value 读取所选时区上一完整日、或上一个周一至周日完整周；prev 再前移一个同类桶；wow 与七天前同类桶比较，公式 `(本期 - 上周) / 上周`。读取真实原始行的最多93天聚合，没有桶、不完整执行口径、非有限值和上周零分母均为无法求值；不填0、不读取展示字段 `Metric.value/prev`，不生成指标历史或成本数据。当前支持同一规则对象的明确分组；既有 gmv/gross_margin 等缺跨对象映射或可执行口径时仍保持不可计算。

## 编辑接口与前端交接

`GET /rules/meta` 增加 objects/fields/expression、metrics/真实版本列表、events/原始字段和当前 runAsUserId。`PUT /rules/{id}/draft` 保留原 autoStage/thresholds/cr/lockVersion，增加可选 conds、logic、expr、objKind、dedupe、cooldown、trigger。明确结构 cond 使用 expression 或 metric/metricVer/period/transform，后端编译表达式；显示名 field 仅展示。高级 expr 是明确表达式，不从自然语言生成。旧阈值保存会按原条件与常量严格一一替换表达式，不能对应时拒绝并保留输入，避免发布后仍用旧阈值。

CR conds 段覆盖新增、修改、删除；config 段保存对象、组合、表达式、触发与去重/冷却差异，发布快照沿现有规则段保存。规则 UI 由同组前端负责人接入；取消、冲突保留输入、历史只读与真实页面操作由协调者最终核验，本文不将接口实现当作已完成浏览器验收。

## 检查与边界

仅做 Python 语法/模块导入和 ruff 静态检查；纯边界断言保留于 `backend/tests/test_scheduled_rule_boundaries.py`，按用户要求未执行。未运行后台测试、pytest、集成/迁移测试或长回归，未为验证生成事件、指标、告警或动作。前端真实操作证据由协调者补充。

另已联动 `admin.roles.load_perm_objects`：已发布未退休动作的明确编辑对象、关联两端及自定义事件对象通过动作公开发现函数加入可授权候选；只扩目录，不改任何角色授权。实际执行继续按对象真实 API、真实角色成员和行范围校验。
