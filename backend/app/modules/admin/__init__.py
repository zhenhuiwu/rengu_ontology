"""模块 admin —— 占位。负责人：PDE工程师。

范围：用户、角色、API Key、审计日志、系统设置
用户表 sys_user（PDE）：转派候选人 = alert_assignable=true 的用户，按 mock PEOPLE 顺序
（动态 GET /people 只读）。
表名：不加模块前缀（CONVENTIONS §6）    接口前缀：/api/admin/v1（在 app/api.py 挂载）
照 app/modules/versioning 的结构填充；规范见 docs/backend/CONVENTIONS.md。
"""
