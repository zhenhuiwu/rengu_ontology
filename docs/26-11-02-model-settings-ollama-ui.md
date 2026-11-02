# 大模型配置界面与 Ollama（2026-11-02）

## 本次变更

- `frontend/src/pages/admin/ModelSettings.vue`：左侧选择问数解析或本体构建助手，右侧配置服务与模型、认证与运行参数；每个用途独立保存和放弃，未保存标记继续汇总至系统配置分组。
- `frontend/src/api/settings.ts`：新增 `provider: openai | ollama` 与草稿连接检测合同。旧记录缺少 provider 时按 openai 处理。Ollama 默认地址 `http://localhost:11434`、无鉴权，也可选择密钥认证用于代理。
- `frontend/src/pages/admin/Settings.vue`：分组栏收窄至 208px，统一标题与内容间距，去掉无实际更新时间的占位符；组织、安全、通知的字段和业务逻辑未修改。
- 后续同页整理：`NotificationSettings.vue` 根据真实 `GET /settings/notifications` 返回呈现站内启停状态、通知类型与外部渠道状态，去掉整行成功横幅；`cr-review` 作为“历史变更通知”保留历史类型，不暗示新审核流程。没有新增配置、开关或外部渠道按钮。组织信息保留三个原字段并统一为紧凑单列详情；安全策略仍准确显示“暂不可用”。

模型名称可直接输入；“检测连接并读取模型”仅在手动点击时发送未保存草稿至 `POST /settings/models/{usage}/connection`，无需先填模型名。候选项仅来自返回的 `modelOptions`，没有内置模型清单。后端将 Ollama 根地址归一至 `/v1`；检测只读取模型目录，不推理、不下载模型、不保存配置。连接结果内联显示；修改服务地址、认证或密钥会清除旧结果，修改模型名保留已读取目录。

目录读取后，AutoComplete 使用 Ant 标准 `showAction: ['focus']`，空模型输入聚焦即可展开已有候选；没有自动选中或额外请求，输入筛选与选后收起沿用组件行为。

## 保存与权限

保存确认前冻结 payload、版本与上下文。确认期间表单或上下文变化，不发送旧确认；上下文离开后返回也使旧请求失效。迟到的检测、保存结果不写入其他上下文。保留原有只读权限、乐观版本冲突与输入保留行为。

已有密钥只显示配置状态与来源，不回填明文，新输入始终掩码且无显示按钮。只有同 provider、同 origin 且原密钥认证允许保留旧密钥，切换服务清空新密钥输入，跨来源须提供新密钥。保存失败不丢草稿；身份或空间切换、卸载时清除新密钥输入。

## 验证边界

`node frontend/scripts/check-model-settings-editor.mjs`：通过。检查实际 Vue SFC 编译，使用受控内存依赖覆盖默认 provider、Ollama 地址与认证、空模型连接、真实目录候选保留、迟到结果隔离、确认前快照与上下文往返、版本冲突保留输入。没有真实 HTTP、数据库写入或模型调用。

统一前端 typecheck 和浏览器实际检查由主会话继续；2560×1440、1920 与 Chrome 102 的最终视觉/真实服务连接结果在取得证据前不计通过。本 agent 未运行后端 tests、全量 build 或打包，未改部署密钥或已生效模型配置。

通知界面追加整理仅核对后端源码合同（`backend/app/modules/notifications/router.py::channel_status`）：接口验证通知表可读，返回站内启用、`cr-review` / `page-subscription` 两种来源、外部渠道未配置。本次没有发送通知，也不将能力列表视作通知投递实测；按主会话要求未为这次纯呈现追加重跑检查。
