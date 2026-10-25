"""模块 biz —— 占位。负责人：待定（PDE 指定）。

范围：演示业务数据：门店、商品、订单等（首页看板、问数对象搜索用）。所有查询必须经过基础层的行级过滤
（app/common/row_filter.py）：
    stmt = await apply_row_filter(stmt, user, "store", {"region": Store.region, ...})
表名：不加模块前缀（CONVENTIONS §6）    接口前缀：/api/biz/v1（PDE 定）（在 app/api.py 挂载）
照 app/modules/versioning 的结构填充；规范见 docs/backend/CONVENTIONS.md。
"""
