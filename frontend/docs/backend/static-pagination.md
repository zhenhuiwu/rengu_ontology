# 静态页面真实分页（2026-10-26）

对象类型、关系类型、指标、维度与字典、业务知识五页不再由 StaticGate 整表预载后 slice。Gate 的 paged 模式只负责身份/版本隔离；Body 独立请求当前页，并以单独完整定义缓冲保留所选资源和编辑表单。

- 左侧 simple 列表沿原一屏容量，点击翻页/筛选请求 page/size；full 表格默认10，改变条数会请求相同 size。响应采用后端的准确 total 与收拢页码，加载时保留 total，避免被公共分页重置为第一页。
- 对象属性/映射、指标版本/引用、维度取值/说法/使用/历史、知识引用/历史、关系使用/历史均在对应页签取可见页。翻页不改所选ID、CR视图，也不重建该资源完整编辑缓冲；取值编辑按稳定key回查完整缓冲，避免只保存当前页。
- 对象和维度 properties/values/mappings 的已保存草稿页传 crId，由后端先合并真实CR再分页，含未发布新目标空基线。未保存表单保留本地输入，切资源/页签仍沿既有未保存保护。
- 工作流徽标读取 change-requests?workflow=true 的完整轻量header/真实targets/legacy touches目录，不预取全部CR diffs/events/items，不按当前页截断工作流。审批/编辑详情仍独立加载，历史版本不混当前工作流。
- 关系图使用 link-types/catalog 的完整轻量对象/关系/路径目录，不能用当前列表页代替全集；真实对象位置按目录生成，不依赖演示ID。表单候选完整目录仅在编辑时读取，不逐条预取所有属性、维度完整定义或知识引用。
- 维度跨范围说法检查走真实 dimensions:match，知识冲突读取完整轻量 synonym-conflicts 目录；不把已访问页缓存当成全局无冲突依据。未知命中数显示“—”。
- useStaticPage 支持 number/Ref<number> size（1..200），取消旧GET，按请求序号、身份/版本/预览与卸载状态拒绝迟到结果。403/网络错误明确显示，无数据保持真实空态，不选演示资源。

专项脚本：check-static-pages.mjs（真实5Body+分页helper，含翻页请求、所选缓冲/未保存输入/行identity、草稿ID、size、完整图谱、真实空库），check-static-loading.mjs（单页恰好一次请求与固定上下文），check-static-draft-hydration.mjs，check-definition-forms.mjs。对象子页签分页另见 check-object-usage-pages.mjs。
