# 行级权限执行

`resolve_user`按真实`SysRoleMember`聚合功能权限，并批量加载角色`objs/default_deny`、`SysRoleScope`与已发布`ObjectType`，生成`CurrentUser.row_filters`。`row_filter(user, object_type)`只读取该请求身份的已算映射，没有逐行查库。

- 对象授权按角色记录的`api`精确匹配当前本体`api_name`，不能靠同名对象或管理员标记补授权。单角色重复API、错误API、无view、没有实际成员关联均不授予。
- 范围可使用当前对象code/API/显示名，及该权限记录保留的旧显示标签；对象改名不会丢失同一稳定API的范围。范围对象不存在或名称歧义时不能因此变成“不限”。
- 复用`sql_guard.RoleAccess/effective_expr`：有view且非“defaultDeny又无范围”的角色才授予；任何有效授予角色明确无范围则不限，否则同角色和跨角色范围OR。未知权限依据/非法取值/无映射范围不贡献任何行。
- 无角色、无对象授权、没有真实本体身份映射、直接构造且没有已解析行权限的`CurrentUser`，默认`DENY_ALL`。测试应创建真实对象身份和角色成员关系，不能给管理员旗标增加旁路。
- 订单、门店、商品查询与分面沿`apply_row_filter`；QA/指标沿`run_guarded`注入同一身份范围；订单行沿既有`order_line → order → store`继承。商品品类保留既有精确别名映射，门店范围按实际全称，不猜简称。
- 每次请求重新解析角色/范围；后台worker也重新解析身份。生产鉴权未接入时仍401。不存在的角色授权不由seed、前端mock、姓名、主角色显示字段或`is_ontology_admin`补齐。
- 变更单试跑记录原执行用户及数据权限摘要；其他用户、预览其他用户、或同用户权限已变更时隐藏旧业务数值，避免撤权后仍读到旧试跑数据。

专项验证：`tests/test_object_row_access.py`（实际角色/范围、业务API、分面、SQLguard、继承、撤销成员、多角色OR、defaultDeny、未知映射、品类别名和全称门店）、`test_biz.py`、`test_sql_guard.py`、`test_static_trials.py`。全部使用独立测试库，不改开发库角色或业务记录。
