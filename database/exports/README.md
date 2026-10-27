# rengu_ontology SQL 数据快照

文件：[rengu_ontology-2026-10-27.sql](rengu_ontology-2026-10-27.sql)。这是**去凭据的真实结构与数据快照**，不是 seed，也不是保留凭据的本机完整备份。

| 项目 | 值 |
| --- | --- |
| 源库 | 本机 `localhost:5433/rengu_ontology`，未读取 `auto_ontology` |
| 快照开始时间 | 2026-10-27 15:20:34.024987，Asia/Shanghai（UTC+08:00） |
| 源 PostgreSQL / pg_dump | 16.13 / 16.13 |
| 扩展 | `plpgsql` 1.0、`vector`（pgvector）0.8.1 |
| 代码版本 | `4a1293bb2b9a78d64d70a7eabf0f0610b9ffe828` |
| 数据库迁移 | `notify_0022`，包含此前的多本体、平台设置、质量调度及独立码表结构 |
| 导出范围 | public 下 122 张表，33,712 行，121 个序列及其状态；结构含函数、触发器、约束、索引、RLS 策略 |
| 文件大小 | 6,811,894 字节，UTF-8 纯 SQL |
| SHA-256 | `5ade3fd65a66b5031fe60a19e8b41819000752258212039e2be9f75738b0781f` |

## 导出与去凭据

源库使用只读 repeatable-read 事务导出快照，原生 `pg_dump --snapshot` 在同一快照读取结构与数据。随后只在新建的 `rengu_export_verify_*` 临时库原生恢复、去凭据，再用 `pg_dump --format=plain --no-owner --no-acl` 生成 SQL。未更新、清空或重置源库，未停止现有服务。业务金额、定义、历史版本、主外键和运行记录不以示例值替代。

- 不导出 `api_key`、`api_key_call` 的数据行；本次源库两表本来均为 0。表结构和序列保留，恢复后需重新签发密钥。
- `datasource.password_enc`、`platform_setting.secret_enc`、`qa_page.link_token` 强制清空；本次源值已为空。连接元数据、非密钥模型设置和页面结构仍保留，原链接访问凭据不迁移。
- `ext_system.auth_detail` 清空，本次实际修改快照中的 10 个单元格。外部认证说明不能作为可用凭据恢复。
- 文本/JSON 检查数据库连接串、带用户名密码的 URL、认证头、常见令牌/加密凭据，以及本机配置中已知的敏感值；发现的凭据字段清空或替换。最终 SQL 的已知凭据与对应模式扫描通过，不在报告中输出其值。
- 不迁移数据库登录用户、口令、owner 或本机临时 QA 角色授权；只按源库恢复 `PUBLIC` 与 `rengu_ontology_scoped` 的对象权限。SQL 不执行 `CREATE/DROP DATABASE` 或 `CREATE/ALTER/DROP ROLE/USER`。
- 原始含凭据的中间 dump 和导出操作脚本仅保留在本机临时目录，没有加入 Git；本目录只提交最终 SQL 和本说明。业务数据按本次明确授权提交，不能把去凭据等同于匿名化业务数据。

## 恢复到新空库

仅在新空库恢复，勿指向现有开发库或生产库。目标需安装 PostgreSQL 16 及 pgvector 0.8.1；原生 SQL 会创建所需 vector 扩展。使用具备建库、安装扩展和还原对象权限的管理员，恢复期间不要连接应用或启动 worker。

1. 目标集群需已有安全的 `rengu_ontology_scoped` 角色；若不存在，由管理员**仅新建**一次：

   ```sql
   CREATE ROLE rengu_ontology_scoped NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
   ```

   若同名角色已存在，先核对其安全属性，勿盲目修改。SQL 开头会拒绝缺失、可登录、superuser 或 BYPASSRLS 的同名角色。实际应用登录身份所需的角色成员关系由部署管理员另行配置，不在本快照中迁移。

2. 通过交互密码或私有 `PGPASSFILE` 提供凭据，不把密码写在命令或仓库。以项目根目录为工作目录，替换 `restore_admin` 为目标管理员：

   ```powershell
   createdb -h localhost -p 5433 -U restore_admin -T template0 -E UTF8 rengu_ontology_restore_20261027
   psql -X -h localhost -p 5433 -U restore_admin -d rengu_ontology_restore_20261027 --set=ON_ERROR_STOP=1 --single-transaction --file=database/exports/rengu_ontology-2026-10-27.sql
   ```

3. 恢复后先保持 worker/调度关闭，重新配置外部凭据并检查待处理任务后再启用；这是保留运行记录的数据快照，不能一导入就触发旧队列或对外写入。不要重新灌 seed 覆盖导入记录。

领域表保留强制 RLS，应用事务须先降权 `SET LOCAL ROLE rengu_ontology_scoped`，再设置受控 `rengu.ontology_id`；直接用 superuser 查询不代表验证了隔离。缺少或非法本体上下文不能用关闭 RLS 解决。该快照不包含本机 ignored `.env`、外部提供者配置或部署密钥。

## 已完成的恢复核对

最终提交的纯 SQL 已通过 `psql -X --set=ON_ERROR_STOP=1 --single-transaction` 在第二个新建临时库完整恢复。122 张表逐表行数、121 个序列的 `last_value/is_called` 均与去凭据前后预期一致；102 张领域表保留 ENABLE/FORCE RLS。以 runtime 角色分别设置本体 1/2 后，对象定义可见数为 18/0，包含未发布或下线定义，不能直接等同于 UI 已发布统计。

| 主表 | 快照行数 | 主表 | 快照行数 |
| --- | ---: | --- | ---: |
| ontology_workspace | 2 | ontology_member | 136 |
| sys_user | 135 | datasource | 16 |
| object_type | 18 | link_type | 27 |
| metric | 15 | platform_setting | 1 |
| action_type | 17 | event_type | 15 |
| rule | 16 | alert | 154 |
| action_run | 1,991 | event | 1,243 |
| biz_order | 1,286 | store | 95 |
| qa_session | 105 | qa_page | 33 |
| api_key | 0 | api_key_call | 0 |

首次最终恢复核对发现权限补段在空 search_path 下无法解析 pgvector 的 `vector` 参数类型，已在权限补段显式设置并恢复 search_path 后，重新生成并完整恢复通过。两个临时验证库均已删除，未修改既有用户或集群角色。

本次只验证 SQL 可恢复、行数、序列、敏感字段与必要 RLS 读取，没有运行后台业务测试、外部调用或任务消费；不据此宣称所有业务功能通过。15 项既有指标的真实定义/绑定缺口原样保留，恢复不使其自动可算。
