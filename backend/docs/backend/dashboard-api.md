# 首页数据库订单日趋势

`GET /api/biz/v1/dashboard/sales/daily?days=30&cutoff=YYYY-MM-DD HH:mm[:ss]`

- `days` 为 1–90，默认 30；`cutoff` 可省略，默认后台当前时间，未来截止会收至当前时间。分钟参数包含该整分钟。
- 返回 `{items:[{date:"YYYY-MM-DD",value:number}],dataAsOf:"YYYY-MM-DD HH:mm",taxMode:"excl",taxRate:0.13}`，日期升序。按 Asia/Shanghai（项目展示时区）下单日期计算，含截止日；无订单日补 0，全空范围返回全 0。
- 数据来自 `biz_order.created_at_biz` 和 `amount`，按日先加总实付金额，再复用门店排行 `ex_tax`（合计 ÷ 1.13、四舍五入到分）。与现有排行一致，包含所有订单状态；没有从公式或固定演示曲线生成数据。
- 这是**订单实付折算趋势**：当前库无退款事实及订单行实际税率，因此不声称它执行通用指标 `gmv` 的“订单行不含税金额减退款”公式。通用 `/metrics/{id}/daily` 合同仍待指标执行能力接入，不能用本接口冒充。
- 需要登录，查询经过订单 `apply_row_filter`（ORDER_COLUMNS / ORDER_LINKS）。历史版本 / 预览参数不改变业务订单事实；沿已有业务接口权限边界，不将当前订单伪装历史定义快照。
- Home 真模式不传固定演示截止：订单、排行、KPI 截止元数据及趋势按后台时间返回。首页共同 `dataAsOf` 仍取原三类响应最早时间，趋势单独展示自身 `dataAsOf`。KPI 仍读数据库已配置指标值，不宣称实时订单求值。
- Home 真模式加载 / 接口失败 / 空数据均不回退演示 KPI、订单、排行或趋势；显示错误或空数据，切身份 / 预览 / 版本丢弃迟到响应。只有显式 mock 模式沿用原演示曲线和固定日期。

验证：`tests/test_dashboard_sales.py` 覆盖真实订单变更、时区跨日、截止、新订单默认截止、缺日零值、权限入口和错误参数；`scripts/check-home-cutoff.mjs` 执行真实 API/SFC 脚本，覆盖元数据、共同截止、乱序上下文、趋势错误及显式演示。

默认截止一致性：`GET /orders` 未传 cutoff、`GET /stores/ranking` 未传 to 时以后台当前时间作为查询上界，排除未来时间记录，与 dataAsOf 一致；显式 cutoff/to 的既有行为保持不变。
