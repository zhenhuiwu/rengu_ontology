# 平台模型配置

## 功能与接口

管理区 `/admin/system/settings?group=llm` 读取平台共享配置，提供“问数解析”和“本体生成润色”两个用途。没有持久配置时显示实际部署环境的模型、Base URL、超时与密钥是否已配置，来源为“部署配置”；不会显示 mock 连接、测试成功或延迟。

- `GET /api/admin/v1/settings/models`：真实 `settings:view` 权限。返回 `items`、`canManage`、`trustedOrigins`；每项包含 `usage`（qa/ontology）、label、model、baseUrl、timeoutSec、enabled、hasKey、source、keySource、lockVersion、updatedAt。没有密钥或密文返回字段。
- `PUT /api/admin/v1/settings/models/{usage}`：真实 `settings:manage` 权限。输入 model、baseUrl、timeoutSec、enabled、lockVersion，可选 `apiKey`。初次从部署配置保存使用 lockVersion=0；已有配置必须提交当前版本。并发通过用途级事务锁、行锁和乐观版本校验串行化，冲突返回 409，不覆盖其他人的配置。
- 不提供模拟的测试连接按钮；保存不访问外部模型。表单错误和保存失败保留输入，重新加载或放弃修改需确认。跨分组保留草稿，离开页面有未保存保护。保存成功清空本次填写的密钥明文。
- `platform_setting` 为共享平台表，不带 ontology_id，不复制到新本体。真实平台配置权限与当前选择的本体成员角色无关；历史版本和身份预览仍只读。租户基础信息返回 canManage，前端只读用户不能编辑已有真实租户表单。

## 密钥和地址边界

密钥复用现有数据源 Fernet 加密机制与部署密钥，持久化在 secret_enc；API 输入使用 SecretStr，输出只给 hasKey/keySource。省略或传 null 保留原秘密，显式空串无效；停用模型不会删除密钥。若尚未保存独立密钥，模型配置继续使用部署环境凭据。配置 GET/保存响应不解密数据库凭据，密钥轮换或损坏时仍可打开配置并更换；实际调用解密失败明确不可用，不回退借用部署密钥。

Base URL 仅允许 HTTP(S)，不得包含用户名、密码、查询串或片段。源站必须属于当前部署环境已授权的问数/本体模型源站（协议、主机和端口共同匹配），不能通过管理页把现有凭据发送到任意新主机。新源站先由部署配置授权；保存和实际读取运行配置均核对信任边界。模型名、超时（5～300 秒）、状态、版本和额外字段均校验。

校验错误复用全局 validation_fields，仅返回字段与固定文案，不返回原始 input/ctx；不记录请求体或原始校验错误。审计只记用途、模型、启停与密钥“更换/保留”，不记录密钥、密文或完整配置。

## 实际调用接线与限制

问数 HTTP 依赖和评测 job/CLI 在新调用或新任务开始时从数据库读取配置，没有跨进程配置缓存；旧的同步环境 helper 供显式环境调用兼容。禁用或无密钥时问数拒绝调用，不伪造回答。

本体润色模块提供 `get_configured_llm(session)` 读取同一持久设置；未配置或禁用时返回 None。当前数据源导入仍走规则生成，没有因本轮设置页改造而自动新增模型调用；不能把保存本体润色配置表述为导入流程已启用 AI 润色。通知渠道和登录安全策略缺少实际执行链，本轮仍明确不可用，不假保存或伪称执行生效。ERP、Kafka 继续延后。

## 2026-10-27 部署与实际证据

- 主任务完整备份 `rengu_ontology` 到 `%TEMP%\rengu-ontology\before-admin-0019-e2a196b4.dump`，1,729,083 字节；未验证恢复。随后实际增量应用 admin_0019（down=ontology_0018），仅新增空平台配置表和运行角色授权，没有 seed、配置回填或旧记录修改，不是迁移测试。`auto_ontology` 数据库未动。
- 实际浏览器读取两种用途的真实 qwen-flash、30/60 秒超时及密钥已配置状态，没有展示密钥明文。随后将问数超时从 30 改为 31 保存成功，整页刷新仍为 31、来源变为“平台配置”；再从 31 改回 30 保存成功，恢复原超时。keySource 始终为部署配置，“更换密钥”未勾选且请求未传 apiKey，没有复制环境密钥、替换凭据或调用模型。
- 输入 `bad model name` 保存被拒；输入未授权地址 `https://unconfigured.invalid/v1` 保存也被拒，两次均保留当前输入。切到通知分组再返回模型分组，未保存 URL 仍保留，没有通过浏览器持久存储保存密钥草稿。
- 最终实际页面确认：模型页下方旧 mock 安全字段已消失；真实通知分组只有不可用空态。截图未显示多余星号，DOM 中隐藏的必填伪元素不作为视觉问题。证据：[模型配置实际保存](../../../frontend/shots/admin-build-20261027/model-settings-saved.png)。
- 同轮质量调度部署前另有完整备份 `%TEMP%\rengu-ontology\before-quality-0020-a1b396fa.dump`，1,732,712 字节，并实际增量应用 quality_0020；该记录只说明部署保护措施，不冒充本组模型功能或迁移测试通过。两次部署均未改动 `auto_ontology` 数据库。
- 仅做 Python 语法/Ruff 检查及一次前端 vue-tsc，无重复构建；未运行后台测试、pytest、集成/迁移测试或边界自检脚本。`scripts/check_model_settings_boundaries.py` 保留了不访问数据库/网络的输入和输出边界检查，未执行。
- 已实际核验非秘密超时保存与整页刷新持久化；尚未实际替换密钥、验证进程重启后的配置、多管理员版本冲突、权限拒绝、解密失败、停用后的实际问数或外部模型响应。实现接线不等于这些运行闭环已经实测通过。
