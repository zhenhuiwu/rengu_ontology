"""模块 auto_ontology —— 表结构识别库。负责人：本体小白。

只是一组可复用的函数，**没有路由、没有表、没有迁移、没有 seed、没有 sections**
（不在 app/api.py 挂载，不在 app/db/models.py 登记，load_sections 也不会发现它）。
用途：给已有页面补「从表结构推出对象类型 / 属性 / 关系」的能力，例如数据连接页的「映射对象类型」、
对象类型页的「从数据源导入」「重新采集元数据」「新增属性 / 关系」。交接说明见
backend/docs/backend/handoff/versioning.md。

参考 NVIDIA/auto-ontology 的「从元数据生成本体」（只参考设计）：
1. 内省 introspect.py：SQLAlchemy inspect 读表、列、主键、外键、注释；
2. 规则 heuristic.py（确定性）：表 → 对象类型、列 → 属性、外键 → N:1 / 1:1 关系，纯外键中间表 → N:M 关系；
3. 大模型 llm.py（可选）：qwen-flash 只改显示名 / 分组 / 说明，结构以内省为准；
   没配 DASHSCOPE_API_KEY（get_llm() 返回 None）或调用失败 → 保留规则结果并记 warning；
4. generate.py：把 1–3 串起来，返回 {proposal, warnings, generator, model}，不写库。
"""
