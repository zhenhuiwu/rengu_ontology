"""契约：静态本体 → 基础层（行级范围的取值映射）。

- `CATEGORY_SCOPE_ALIAS`：`roles.ts` 里「商品.品类」的范围值 → `product.category` 第一段（一级类目）。
  static-api.md §1.5（团队 master 443c009）：谓词 `split_part(product.category, '/', 1) = ANY($n)`，
  范围值先按这张表换名，不在表里的值原样比（对不上就匹配 0 行）。
  **[推断]** 前端没给对应关系，按名称最接近的一级类目定。
- TODO(静态本体负责人)：semantic_map（维度同义词，如「华东大区」→ big_region='华东'）也放这里。

常量与公开静态查询契约；基础层在 `app/modules/biz/governance.py` 里引用范围映射。
`evaluate_metric(session,user,code,ver=None,cr_id=None,days=30,period='day',
at=None,timezone=None,object_kind=None)`：真实指标执行，period可为day/week/month；
显式at固定原始数据截止时间；object_kind要求同一真实对象分组，返回objectCode，不猜跨对象口径。
返回 rows[{date,value:十进制字符串|null}] / dataCutoff / scope，缺执行含义返回422。
"""

from types import MappingProxyType

from app.modules.static.metric_trial import evaluate_metric

__all__ = ["CATEGORY_SCOPE_ALIAS", "evaluate_metric"]

# 范围值（roles.ts SCOPE_PROPS 商品.品类）→ 一级类目（seed PRODUCTS.category 的第一段）
CATEGORY_SCOPE_ALIAS = MappingProxyType(
    {
        "生鲜": "生鲜",
        "乳品烘焙": "乳品",
        "粮油调味": "粮油调味",
        "休闲零食": "休闲食品",
        "酒水饮料": "酒水饮料",
        "日化清洁": "日化家清",
    }
)
