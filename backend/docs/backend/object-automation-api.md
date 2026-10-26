# 对象类型“自动化”只读真实汇总

`ObjBehaviorAutomation.vue` 由 `src/api/objectAutomation.ts` 汇总已有接口，不新增规则执行器，不创建任何动作或事件。

- 静态 / 动态 / 动作事件三个模块任意为真时走真实汇总；依赖开关未齐则明确接口未配置，不混入演示。仅三个模块均为显式mock时保留原演示。
- 对象名称与API名从真实 `/object-types/{id}` 读取；规则目录由 `/rules?objKind={id}` 在SQL中过滤。规则状态读 `displayStatus/cr`，不借mock变更单猜状态。
- 动作 / 事件目录复用完整上下文共享快照。事件按真实对象code（objKind/object）、API名或真实对象显示名匹配；订阅数取真实subscribers条数，周事件数取实时stats.weekly，不取类型定义的weekly存储值。
- 命中表翻页请求 `GET /object-types/{id}/automation/hits?ruleId=&page=&size=10`，SQL计数及LIMIT/OFFSET取近7日；不再逐规则拉全量命中。返回标准Page，items为Hit并补ruleName。
- `GET /object-types/{id}/behavior/stats` 返回 `rules[ruleId]={hits7d,alerts7d,pending}`、`centerStatusCounts`、`actions30d[typeId]={total,rate,failed,unknown}`、`actions`。这些字段均为真实数据库聚合；actions只带匹配当前对象的轻量定义，不含参数/条件/编辑段。actions30d按实际ActionRun近30日执行结果计数，不拿旧演示日统计推算。
- 对象动作表请求既有 `/action-runs?scope=center&objKind=&actionType=&status=&page=&size=10`；参数仅在打开对应动作抽屉时加载。状态摘要取对象内全部记录的聚合，不从当前页统计。
- 请求及后继分页全部固定demoAs/previewAs/ver；上下文或对象变化清空只读汇总，所有数据成功后原子落地，旧响应/失败不进入新上下文。加载 / 空数据 / 403 / 404 / 后台不可达均不回演示。

规则命中窗口同步修正：`RuleService.hits` 使用后台clock.now的滚动7/30天（含边界），排除未来记录；前端不再用固定SINCE_7D过滤真记录。其它模拟/回放逻辑不由本轮扩展。

验证：`tests/test_rule_hit_window.py` 覆盖滚动时间边界；`tests/test_object_behavior_pages.py` 覆盖SQL分页、真实聚合、轻量定义和对象筛选。`check-object-automation.mjs`、`check-object-behavior.mjs` 执行真实API模块与SFC，检查翻页请求、total保留、后台clamp、迟到响应及抽屉懒加载。

历史边界：现有/rules尚不读取历史发布快照，故真实历史视图直接显示“历史规则配置尚未接入，当前页不提供历史自动化汇总”，不发请求或展示当前规则冒充历史。动作/事件类型其他页面保留各自历史定义与实时运行说明。
