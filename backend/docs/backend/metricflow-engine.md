# MetricFlow 引擎（指标查询编译）

`app/common/metricflow_engine.py`。把静态维护的 MetricFlow 导出（`export_metricflow`，`/exports/metricflow` 同一份 JSON）编译成单条 SELECT，再经 `run_guarded` 执行。全部进程内，不走 HTTP，不依赖具体业务域。

## 服务函数（智能问数用）

```python
from app.common.metricflow_engine import metric_query

out = await metric_query(
    session, user,                       # user = 调用方 CurrentUser，会话已绑定本体范围
    metrics=["<metric_api_name>", ...],
    dimensions=["<entity>__<dim>", "metric_time"],
    filters=[
        "{{ Dimension('<entity>__<dim>') }} = '某值'",               # 模板写法
        {"dimension": "<entity>__<dim>", "op": "<>", "value": 3},   # 结构化写法（值自动转义）
    ],
    limit=1000,
    backend="auto",                      # auto | metricflow | builtin
)
# out = {"sql": 编译结果（未注入行级条件）, "backend": "metricflow"|"builtin",
#        "columns": [...维度名, ...指标名], "rows": [[...], ...]}
```

- 权限：导出按调用方 `metrics` view 校验；执行一律走 `run_guarded`（行级过滤 + `ontology_id` 边界，受控表以外的表拒绝）。编译出来的 SQL 不允许另行执行。
- 维度名：`<entity>__<dim>`（时间维度按天，返回 date）或 `metric_time`（模型默认时间维度，按天）。
- 过滤：只接受「维度引用 比较符 标量」，多条 AND；比较符 `= <> != > >= < <=`。
- 两个后端的输出列名一致；导出里没有的指标 / 维度、非法过滤一律 422 `METRIC_QUERY_INVALID`（不猜测、不补口径）。
- 旧入口 `query_metrics(session, user, metrics, group_by, where, backend="builtin")` 保留，返回值多了 `backend`。

## 后端

| backend | 行为 |
|---|---|
| `auto`（默认） | 装了 `metricflow` 用 MetricFlow，否则内置编译器 |
| `metricflow` | 同 auto（包缺失时回退内置编译器，`backend` 字段如实返回） |
| `builtin` | 只用内置编译器 |

MetricFlow 路径：导出 JSON → `PydanticSemanticManifest` → 官方 `PydanticSemanticManifestTransformer`（把导出里的 measure 写法转换成 0.213 的 `metric_aggregation_params`）→ `MetricFlowEngine.explain`。SqlClient 是只渲染桩（Postgres 方言），MetricFlow 不连库、不执行；生成 SQL 带绑定参数时拒绝。引擎按清单内容哈希缓存（最多 8 份）。外层再包一层 SELECT 把 `metric_time__day` 等名字换回请求里的名字。

内置编译器：simple 指标（含导出 filter，编成 `AGG(CASE WHEN … THEN expr END)`）和同一 semantic model 上的 derived 指标；跨模型 join、cumulative / conversion、非 day 粒度不支持（422）。MetricFlow 路径支持的指标类型更多，但 join 到的表同样必须是受控表。

## Time spine

MetricFlow 0.213 要求清单里有 time spine。导出没有 `project_configuration` 时补默认配置：`public.metricflow_time_spine(date_day)`，按天（`TIME_SPINE` 常量）。普通 simple / derived / 按天分组查询不会读它；累计、补零、时间偏移类查询会读它，此时这张表也必须是登记过的受控表，否则 `run_guarded` 拒绝。本批次没有建这张表（没有迁移）。

## 依赖

- `backend/pyproject.toml` 可选 extra `metricflow`：`metricflow>=0.213,<0.214`、`sqlglot>=30.25.0`（Apache-2.0，不含 dbt-core）。
- 安装：`uv sync --extra metricflow`。`uv.lock` 已按这个 extra 重新锁定。因为锁文件是全局解析的，基础环境里 sqlglot 也从 30.24.0 升到了 30.25.0，python-dateutil 从 3.0.0 降到了 2.9.0.post0（metricflow 限制了它的版本）。
- 不装 extra 时什么都不用改，自动回退内置编译器。

## 测试

`tests/test_metricflow_engine.py`，用手写的最小导出夹具（门店 / 大区只是测试数据）。没装 extra 时，MetricFlow 包路径的两个用例会跳过；装了 extra 时，会在 run_guarded 下验证 MetricFlow SQL 的行级过滤。
