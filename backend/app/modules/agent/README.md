# 独立公共问数适配器

只挂载 `/api/agent/v1`，不使用登录 Cookie、Bearer 或客户端令牌，不改变旧 API 的权限依赖。服务端 `RENGU_AGENT_PUBLIC_ONTOLOGY_IDS` 是显式公开的本体 ID JSON 列表，默认 `[]`；空列表返回空目录，不创建用户或会话。开启示例：在部署者自己的 ignored `.env` 配置已获准公开的合成本体 ID，然后正常重载 API。未公开、删除或没有发布版本的本体不提供公共会话。

- `GET /config`：`{workspaces:[{id,label}],selected:null,metrics:[],calibers:[]}`。
- `GET /config?ontologyId=N`：selected 为 `{id,label,version}`，一次返回已发布指标选择项和口径；未知或未公开 ID 返回 404。
- `POST /sessions`：输入 `{ontologyId,caliber?:string|null}`，返回 `{id,title,ontologyId,caliber,turns:[]}`。
- `GET /sessions/{id}?ontologyId=N`：返回同结构及公共展示轮次。
- `POST /sessions/{id}/ask`：输入 `{ontologyId,question,refs?:[]}`，返回 `{turnId,turn}`。问题最多 500 字；口径沿当前会话。轮次沿用既有 QA 的文本、澄清和表格/图表形状。

会话编号是 `P_` 加 32 字节随机值的 43 字符 URL-safe 编码。请求同时核对编号格式、当前本体、专用 owner 和服务端 created_by 标记；旧后台 SID 不可读取或提问。首次创建时才幂等创建一个停用、无登录账号/口令/角色/成员关系的 SysUser，纯粹满足既有 owner_id 外键，不通过旧身份认证读取其权限。公开上下文只授予此本体已发布对象/指标的读取，不授动作、规则或管理操作。目录和输出不含 admin 身份、密钥、SQL 或内部 trace。

复用既有 QA `_ask_once`、语义链、回答转换及只读 SQL 网关，数据行仍受本体 RLS 和既有可查询列约束；新会话和问答会写入既有 QA 表，无新表或迁移。元数据读取过滤只安装在公共请求自身的数据库会话，不影响旧入口。首版连续澄清按最近已保存问题和服务器根问题摘要组装；部分明确续问带最近问题，并非通用全历史记忆。公共入口不挂载动作、规则执行、页面保存、管理或外部投递端点；若返回动作类建议，明确禁止在此执行。

离线检查：从 `backend` 运行 `.venv/Scripts/python.exe -m app.modules.agent.check`。它使用内存替身和 SQL 编译核对默认关闭、白名单、会话标记/跨本体隔离、停用 owner、只读授权、已发布源过滤、输出与澄清边界。已通过，无数据库连接/写入、真实网络/模型请求或队列处理；未运行既有后台测试。实际公开本体和真实前端问数由主会话另行验收，不能将离线检查当作实连结果。
