# 动作试运行与共享条件校验（2026-10-26）

`POST /api/ontology/v1/action-types/{id}:dry-run` 已接真实数据库定义。`dynamic.ports.get_checker` 默认同一 `DatabaseSubmissionChecker`，用于动作暂存/确认/告警发起动作的既有条件检查入口。没有修改审批状态机或审批人配置；外部submitter、事件投递、自动对账仍503，不发送请求或消息。

请求 `{params:{...}}`，省略为真实空对象；响应200 `{passed,checked,failed:[{name,fail}]}`，条件未通过仍200。checked是当前视图未删除的提交条件数，参数必填/类型/规则失败也列入failed；不把没有参数解释为示例值。缺定义/坏视图等接口错误仍按404/409/422/503返回。试运行不写ActionRun、日志、外部系统或本体对象。

当前从ActionType/ActionParam/SubmissionCriterion读取；历史只读取所选release.actionTypes，缺段503、缺目标404，绝不退回当前定义。`view=draft&cr=CR-...`通过静态公开 `definition_draft_items` 读取真实目标/行，按params/conds覆盖和删除（def绑定字段亦覆盖），不读取前端未保存的行。历史不能与当前CR草稿混合。仅此无写路由允许history；preview仍403，所有真正写接口继续403，共享只读守卫未改。

支持矩阵：

| 来源/语法 | 真实支持 | 缺失或不支持时 |
|---|---|---|
| 参数 | 已声明API名或唯一完整显示名，以`参数.qty`/`参数.数量`引用；JSON字符串/枚举、列表、有限数字、整数、布尔、ISO日期/日期时间基础类型 | 必填缺失、类型不符、未声明参数failed；不猜显示摘要或默认值 |
| 条件表达式 | 有限算术`+ - * / // %`、数值链式比较、等于/不等于、字面量集合`∈/∉`、且/或/非，支持对应Python ASCII运算符；`空`表示null | 调用/反射/属性遍历/注释/幂/未知语法failed；未声明名称不能作为独立条件 |
| 当前用户 | 真实账号code；角色只取DB实际角色code/name，角色等于或属于集合按实际成员关系判断 | 不按姓名/部门/管理员标志补角色，不使用params伪造身份 |
| 业务对象 | objKind须匹配真实动作targetKind；仅已登记governed store/order/product当前真实表列，且有已发布、非敏感/非隐藏属性绑定；对象主键必须唯一 | 未登记表、无字段/范围外/不存在/敏感隐藏字段均只报上下文不可用，不泄漏实际值 |
| 对象权限 | 真实RoleBundle中object.view/defaultDeny/scopes，经公共RoleAccess/effective_expr/to_sql注入；没有使用当前None行过滤桩或展示范围字符串 | 未授权/未映射范围拒绝，不扩大授权；全站row_filter未在此重构 |
| 告警 | actor有alerts访问、实际不限范围告警对象view，且处理人能定位唯一当前在用账号并等于actor；只读实际状态/处理人code/编号 | 他人/歧义/未知/有未映射告警scope均不可用；不透露处理人或对象事实 |
| AST列 | 现expr_ast/rule_expr无约定可执行格式，非null明确failed；普通文本更新按既有发布逻辑清AST | 不把未知AST当通过，不自造持久AST格式 |
| 自然语言规则 | 不执行CRM查询、预算函数、每位会员、`不存在...where`、今天/外部动态上下文或隐式单位转换；没有任何数据库查询由表达式拼接 | failed并说明不支持；日期虽可做基础类型/显式相等检查，不做字符串顺序比较冒充日期比较 |

解释器复用函数模块已有限数值/JSON求值预算，只解释白名单AST，不使用exec/eval/编译Python字节码。表达式4KiB/256节点，最多2048步骤/1秒；值沿已有JSON深度/容器/整数预算。输入参数最多64KiB且拒NUL/非法Unicode/非有限数。未提供/不支持条件不通过，不能根据fixture或sample返回全通过。

动态已有params字段是描述字符串时，只有内容本身为合法JSON对象才可校验；不把“数量3”等描述猜成qty=3。默认checker真实失败返回既有422 SUBMISSION_CRITERIA_FAILED；明确保留UnconfiguredAdapters类用于未配置边界/显式测试替身。即使全部条件通过，也必须继续遵守既有审批与外部执行通道检查。

前端原“试运行”按钮真模式调用此接口，确认卡明确尚未提供执行参数、缺上下文将失败；不拿示例/默认值填参数，不模拟外部调用。未保存的草稿禁试算，保存后读取实际CR；busy防重入，确认/响应均绑定捕获身份/版本/当前类型。mock模式不变。

验证：tests/test_action_checker.py覆盖真实参数/角色、当前/历史/缺历史段/CR删除、默认dynamic端口条件失败无ActionRun、跨对象范围拒绝与敏感标记、告警实际处理人、未知语法/注释/无间隔Unicode运算符/坏参数。tests/test_dynamic_unconfigured.py保留明确未配置端口边界；C真实catalog回归保持。前端check-action-used-by.mjs实跑页面脚本验证真实失败/错误、无sample充参和迟到确认，不修改mock记录。
