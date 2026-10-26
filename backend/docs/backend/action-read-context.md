# 动作 / 事件共享目录读取边界

`frontend/src/api/action.ts` 导出 `actionContextKey()`（身份 demoAs、预览 previewAs、版本 ver）和 `snapshotReady()`。共享快照按完整上下文判断有效，只读取完整轻量类型目录，不预加载所有类型的参数、载荷、条件或订阅子表。选中类型后懒加载详情；可见列表按后台分页，编辑时另取完整目标基线。所有后继请求显式固定调用时headers/query。动作和事件分支先构造局部结果，全部成功才原子更新snap；旧上下文的成功、失败及finally不会污染新一代状态。真模式未加载、空数据、403/404均不读演示定义。

运行记录按完整上下文+typeId+分页筛选读取，摘要使用后台独立聚合；写回系统候选按上下文+序号加载。ActionTypes、EventTypes及Rules目录watch同步上下文，运行健康/近期事件/动作日趋势同样丢弃迟到结果；真模式未知选中类型显示空态，不使用mock第一项。事件页近7天数字使用真实运行stats.weekly，不使用定义元数据的weekly旧值。

验证：`node scripts/check-action-snapshot.mjs` 执行真实API模块，覆盖201项类型 / 参数 / 载荷分页、两分支原子发布、固定身份 / 预览 / 版本、旧成功及旧失败、真空及403、执行记录 / 系统候选隔离和显式mock。兼容原PDE / used-by / context行为检查。
