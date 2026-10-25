"""模块 static。负责人：静态本体负责人。

范围：对象、关系、指标、维度、业务知识、变更单、发布（调用 versioning.create_version）
表名：业务名词，不加模块前缀    接口前缀：/api/ontology/v1（在 app/api.py 挂载）
函数（Functions，PDE 定归静态）：function / fn_param / fn_use / fn_version，按函数各自递增版本，
编辑保存、回滚直接生成新 fn_version，不走 versioning.create_version、不写 ontology_release。
规范见 docs/backend/CONVENTIONS.md。

导入本包即登记静态负责的 7 个快照 section（objectTypes / properties / links / metrics /
dimensions / terms / qaPaths，见 sections.py）。
"""

from app.modules.static.sections import register_all

register_all()
