# 函数真实引用显示与签名保护修复（2026-11-02）

根因不是前端计数：全仓FnUse产品调用只有`functions.service.current_rows`读取usedBy，以及`functions.sections.publish_definition`读取后保护不兼容签名；没有动作/规则正常保存写FnUse的入口，仅测试fixture手工写。真实固定函数绑定另存在动作参数default_text/本体写回field的JSON及规则expr的rule_fn AST中。因此FN001虽被recordInspection实际引用却显示0，原签名保护也可能漏掉这类引用。

按root确认采用最小共享只读读取，不加迁移、不在读取时回填FnUse、不要求用户重新保存已存在动作。新增`backend/app/modules/functions/references.py::function_uses`，由函数列表/详情序列化和发布签名保护共同调用：

- 保守合并原FnUse，不删除或重建历史引用账本；同kind/name避免动作参数及写回重复显示。
- 批量读取当前作用域动作参数固定函数JSON、动作写回字段映射及规则表达式固定rule_fn引用；动作仅非draft且未retired，规则version须非null。包含仍保留定义的停用规则，保护范围不因停用就放宽。
- 识别固定function/version/inputs结构与嵌套inputs，常量value内形似函数的普通数据不当引用；规则复用正式parse_expression再按AST找rule_fn，不靠字符串搜索。current别名、无固定版本、普通说明文本不计真实绑定。
- 原FnUse一批、当前三种来源union一批；列表按当前页函数codes合并，无逐函数N+1，不扫描所有历史快照。所有父/子表使用可信require_ontology_id显式空间条件并继续沿用数据库RLS；不读取其它空间或绕过现有函数页面权限。
- `sections.publish_definition`仍保留原“used且old_signature != new_signature”阻断；只是used现在含真实定义引用。函数名/版本锁、固定FnVersion快照和动作/规则自身权限校验没有被改。描述等兼容修改仍按原正常发布合同。

产品改动只有新references.py及functions/service.py、sections.py两个调用点，没有改Actions/Rules或其它前端，也没有写任何DB/业务数据。只读聚合不会改已固定动作绑定到的旧函数版本，更不会自动重新执行业务。

唯一新增离线脚本`backend/scripts/check_function_references.py`通过：实际解析器、合并器配内存Session，覆盖参数/写回/规则固定引用、动作多来源去重、常量伪引用拒绝、current拒绝、legacy保留、SQL空间条件、当前定义筛选、固定两次查询；AST核对展示和签名保护调用同一helper且原不兼容签名raise仍在。第一次检查辅助SQL断言假设rule表有引号而失败，改该断言接受真实编译结果后同脚本通过，不是数据库或产品运行失败。

```text
Function current-reference merge and signature guard passed (offline only).
```

本agent运行未导入应用/lifespan或读取凭据，没有HTTP、DB、UI、真实模型、后台suite、pytest、集成/迁移测试或全量build。没有提交/推送，由root按用户授权统一分组处理。补丁交付时待正常API-only重启及UI核对；后续真实结果见下节，不为验收制造不兼容函数保存或改原业务定义。

## 正常加载后的真实UI结果

root于2026-11-02 **00:48:02** 正常重启API，实际listener **49000**。随后ws16正式函数页真实显示：

| 函数 | 被引用 | 真实引用展示 |
| --- | --- | --- |
| FN001 / inspection_label | 1 | 动作“登记设备巡检” |
| FN002 / has_asset_reference | 1 | 规则“办公工单关系与函数条件验收” |

因此本次动作JSON与规则AST两种真实来源的函数引用显示均已通过UI验收，不再只属代码或内存断言。没有为显示结果后台回填FnUse或重保存原动作。**不兼容签名阻断仍仅有共享调用与原guard保留的离线断言，未实际尝试破坏式签名变更**；不能把引用显示通过写成不兼容保存真实拒绝已验。

范围限制：这是当前定义的引用及旧FnUse保守账本，不宣称穷举每个历史run/发布快照的函数依赖。旧FnUse无来源编号/版本，不能在本补丁中据此安全删除或重命名既有记录。历史版本保留及精确引用生命周期治理需另有明确需求，不在此扩展。
