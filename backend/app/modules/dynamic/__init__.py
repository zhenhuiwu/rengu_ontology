"""模块 dynamic —— 占位。负责人：动态本体负责人。

范围：告警、规则、自动化、动作中心、场景、生命周期
表前缀：dy_    接口前缀：/api/ontology/v1（在 app/api.py 挂载）
函数（Functions）单独存 fn_version 表（按函数各自递增版本），不走 versioning.create_version。
照 app/modules/versioning 的结构填充；规范见 docs/backend/CONVENTIONS.md。
"""
