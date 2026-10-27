# PDE 分页与待审批口径（2026-10-26）

2026-10-27 追加：下文“API 密钥仍不可操作”保留为本轮历史记录；当前已接真实管理接口，列表与调用记录均数据库分页（默认 10 条），真实空表计数、搜索筛选与新建表单已实际核查。创建仅可绑定当前真实身份，scope 限明确元数据读取白名单；未签发凭据做认证/吊销验证。当前规则及验证范围见 [API 密钥契约](admin-api-keys.md)。

本轮只改真实读取和既有管理流程；显式mock数组不改，无新增迁移，无模型调用。

- 用户列表沿原SQL COUNT/LIMIT/OFFSET；前端默认10、同身份翻页保留总数，ctx改变清旧记录；部门和角色目录不在翻页时重拉。
- 审计日志沿真实keyset LIMIT(size+1)，默认每页10；游标前后页在加载中禁用。操作候选按身份/版本重读，旧ctx结果不覆盖。
- 角色编辑初载改GET `/api/admin/v1/roles/state`：一次批量查询返回原有 `{roles,members,users}` 完整配置基线，保留`roles:view`及`users:view`边界。成员展示独立GET `/roles/{id}/members?page=&size=&kw=` 默认10，SQL页；显示分页不改完整成员/权限编辑集合，不在翻页时重拉整个state。
- 数据连接和库表前端真实模式使用page/size/type/kw按页请求，默认10；导入表单所需完整目录保留既有显式allPages适配且固定ctx，普通表格不再使用。连接列表批量取得本页实际对象映射名称，不逐源查映射；禁止关系自动加载全部运行，列表runs为空。详情runs最多最近10，新增GET `/api/data/v1/datasources/{id}/runs?page=&size=`提供SQL历史分页，真实同步页使用它。
- 库表SQL按实际已发布映射优先、schema/name C collation排序（保持先前Python大小写字节排序）；SQL筛选/COUNT/LIMIT/OFFSET后只组装本页映射名，不全表slice。连接独立详情保证深链选中不受左列表当前页限制。
- 外部系统普通GET `/api/data/v1/ext-systems?page=&size=&kw=`默认10，返回`{items,total,page,size}`，SQL筛选分页，actionCount仅聚合本页系统。显式`writeback=1`且未传page/size为动作写回候选目录，保留完整SQL过滤目录，不截断已有动作选项。普通列表与详情、关联定义、调用分页/抽屉按ctx及seq隔离；未加载/失败不补mock，页面选中未加载不展示正常状态占位。
- 移除本范围tab/segmented标题尾随数量，未知能力保留禁用/简短业务空态；移除可见内部执行器/接口开发说明。API密钥/未配置设置仍不可操作，不宣称补了服务。

## 用户待审批是实际任务

新增静态/动态公开只读`pending_approval_counts`，admin一次对本页启用用户聚合：

- CR：仅审核中，优先detail.reviewerCode稳定编号；没有编号的旧记录，仅在实际启用用户姓名全局唯一时兼容。未知编号或重名均不归给某人。
- 动作：仅审批中且approver为实际稳定用户编号；不按姓名/角色名称猜指派。
- 无记录为0，停用/待激活账户显示0；不再使用编号哈希。只查询本页姓名的唯一性，不拉全目录，也不逐用户await。
- 确有待办且未选择接收人返回TRANSFER_REQUIRED；提供transferTo仍返回409 TRANSFER_UNAVAILABLE，业务提示“请先完成待审批事项后再停用此用户”，保留账号和原待办，绝不伪称转交。无待办可正常停用。
- 真正审批转交仍未实现；需接收人的CR目标权限/非提交人、动作已发布审批配置及转交审计，不在本次计数修复中猜测授权。

## 窄测

`tests/test_pde_pagination.py`：数据源/23库表映射优先/31运行分页，外部系统默认页与完整写回目录/通配符字面搜索，角色批量配置基线/成员SQL页/双权限边界。
`tests/test_admin_pending_approvals.py`：两人不同实际待办、唯一旧名/重名/未知排除、0、停用拒绝伪转交不修改任务。
前端`check-data-real.mjs`、`check-admin-db.mjs`、`check-audit-export.mjs`：实际模块/SFC请求分页、总数保留、完整配置不截断、ctx迟到与写错误。

全量测试、构建、截图交ROOT统一。本地只读HTTP已确认数据源page2=6/16、库表page2=10/50、运行页越界回有效页、外部系统分页/写回目录200；不写开发业务数据。


## 对象使用与治理三个子页

MetricsTab / ViewsTab / HistoryTab真实模式复用useStaticPage，GET `/object-types/{id}/usage?kind=metric|view|cr&page=&size=&filter=&q=`；默认full10，可改size，筛选/换对象回第一页。收到单页只映射展示，不再all()+前端slice；完整mode/demoAs/preview/ver、旧请求abort/seq、ctx立即清旧、总数保留和越界收页沿公开helper。

- metric保持扁平真实指标DTO+base/via，仅转为原模板{m,base,via}。
- cr保持真实{cr,parts}，进行中由服务端筛草稿/审核中/已通过；历史截止仍后台负责，不读取全局mock CR/旧修订补行。
- view的q查名称/所有者、filter查类型；当前可编辑对象草稿才传crId，onlyHit发送affectedOnly=1。受影响标记只用服务端实际保存属性items计算的affectedProps；历史/preview不自动带CR，不调用mock draftPropDiff。mock分支原分页与演示值保留。

`node scripts/check-object-usage-pages.mjs`执行三个真实SFC与共享loader，覆盖page2不二次slice、size/过滤、保存CR影响与只读边界、ctx迟到/403、末页clamp和显式mock无请求。后端usage SQL由admin_scope负责，薄API/helper归QAagent，不在此组并改。
