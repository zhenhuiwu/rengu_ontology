# seeds/extra/<module>/

后端补充的 seed：前端 mock 里没有，但后端需要的行（例如只为满足外键的 `repeat_sales` 指标）。

- 文件格式同 `seeds/json/`（JSON 数组，字段名沿用前端 camelCase 写法，便于和 mock 对照）；
- 模块的 `seeds/__init__.py` 里用 `SeedSpec(..., source="extra")` 声明；
- 加载顺序：同一模块先 `json/` 再 `extra/`，同一事务；写入时自动 `is_seed=true`；
- 每个文件开头的那一行请在模块 seeds 的 docstring 里写清“为什么需要、对应哪个外键”。

`seeds/json/` 由 PDE 的 `npm run export-mock` 生成，**不要手改**；手写的数据只能放这里。
