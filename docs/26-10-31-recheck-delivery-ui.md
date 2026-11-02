# 投递失败明细前端复检（2026-10-31）

本次仅完善通用投递失败明细展示。已只读核对 `backend/app/modules/event/delivery_history.py` 的脱敏白名单，以及 `exceptions.py` 返回的顶层 `errorDetails` 和历史 `history.items[].detail.errorDetails` 契约；未调用业务 API、连接数据库或操作投递任务。

## 实现

- `frontend/src/api/action.ts` 补充可选 `DeliveryErrorDetails` 类型，顶层详情与历史共用。
- `frontend/src/components/DeliveryErrorDetails.vue` 和现有 `deliveryPresentation.ts` 格式化已脱敏的位置、数量、阶段和 HTTP 状态。字段与校验项分别最多展示 20 条，数量多于返回条目时说明展示范围；没有名称时仅展示接口索引，不推断名称或字段值。
- `DeliveryReview.vue` 的原详情及尝试记录均复用该组件。`DeliveryExceptions.vue` 的“核查与处置”原入口继续打开这个弹窗，无新增页面或产品路由。
- 原因只映射固定的 `FIELD_VALIDATION_FAILED`、`SUBMISSION_CRITERION_FAILED`；未知原因显示“原因未提供”，不输出原始 reason、message、input、payload 或对象 JSON。
- 旧记录没有 `errorDetails` 时不增加明细区，继续展示原错误原因。仅有诊断代码而无字段/校验项时显示“未提供字段或校验项明细”。没有推断旧记录的失败字段或值。
- 使用现有灰阶、海军蓝 token 和字号工具类；新增样式仅涉及 flex、间距、边框和换行，无新依赖。

## 已运行的检查

在 `frontend` 目录执行 `node scripts/check-delivery-error-details.mjs`，通过。

该离线检查编译新增组件及两个现有入口的 SFC，SSR 渲染真实明细组件，断言阶段、数量、缺名索引、20 项上限、旧记录 fallback 及私密属性不渲染。使用合成对象，不访问业务接口。未运行全量 typecheck、构建或 pytest。

## 本地合成 UI 校验入口

本地临时文件：`frontend/shots/_check/delivery-error-details.html`，由现有 `shots/_check` 忽略规则覆盖，不进入产品路由或构建入口。页面直接导入实际展示组件，不请求 API，并标注“合成 UI 校验”“不代表真实投递结果”。当前本地 Vite 路径：`http://127.0.0.1:5280/shots/_check/delivery-error-details.html`。

默认展示投递前检查和投递执行两种阶段。点击“旧记录”，两处仅保留 `VALIDATION_FAILED`，不应生成字段/校验项；点击“仅代码明细”，两处显示未提供明细提示；点击“切换白 / 海军蓝”可观察 token 配色。该入口只验证展示，不证明真实服务端故障或生产投递结果。

本子任务未执行 GUI、2K 视窗或 Chrome 102 实机验证，不能将代码检查记作这些验证已通过。
