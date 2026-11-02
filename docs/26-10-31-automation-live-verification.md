# 通用动作与事件自动化真实联调记录（2026-10-31）

## 实测结论

在当前本机平台 `/api`（127.0.0.1:8000，正常 lifespan）与当前平台库 `rengu_ontology` 上，专用验证本体 `15 / dm-live-20261031` 已真实跑通：

`DM只读镜像真实Task身份 → HTTP事件入站 → 单规则订阅 → 规则命中 → 告警 → 自动动作持久排队 → 本机HTTP接收端 → 真实回执 → 对账终态`。

另通过真实机器执行接口验证 `201新建 / 相同幂等键200返回同一run`，并取得第二张独立回执。重复调用指定事件投递与指定动作执行器没有重复发送。结论仅适用于本轮明确的通用链和下表范围，不能表述为整个bug清单或所有部署配置已经全面验收。

## 范围与环境

- 仅向新建验证本体追加定义及运行记录，没有改用户既有本体、动作、规则、历史或源业务行。
- 对象 `LiveProject / LiveTask` 由镜像联调代理通过用户指定DM连接只读镜像。Task当前83条；该代理通过真实 `identity_rows` 核验83条业务身份，物理rowid均与业务id不同。本轮动作 `target_access / existing_row` 使用其中一个已同步真实Task；原始业务标识未写本文或工具输出。
- 新增动作 `live_auto_receipt`（“通用验证回执动作”）、外部事件 `LiveVerificationRequested`、规则 `R-LIVE-AUTO-1031`、唯一订阅 `Local verification rule`。条件为 `event.testFlag == True`，载荷含显式验证标记；事件自身为正常入站记录，未冒用平台 `is_test` 通道执行规则。
- 定义fixture通过现有ORM只向新本体写入，并追加正式快照 `v2026.10.2`。本轮证明运行链，未把fixture准备冒称为“所有定义编辑UI都已经测试”。
- 接收端仅监听 `127.0.0.1:8766`，只接受固定验证本体、动作API及指定Task，按真实 `Idempotency-Key` 计数并核对请求体。没有访问报告中的生产HTTP服务或执行报告中的业务动作。
- 动作执行仍要求部署网络/路径ceiling，ExtSystem持久目录与动作授权本身不会绕过这一要求。本轮保留用户 `.env` 原文件，仅在临时API进程设置新本体入站token和唯一loopback适配器；原8000短暂替换期间正常运行lifespan。每次结束均恢复原启动参数、原环境和原`.env`。恢复后临时适配器和接收器已撤销，因此页面再次执行该验证动作需要重新明确配置通道；不能将本轮结果表述为原常规部署配置已经具备执行这个验证动作的条件。
- 从未启动通用worker、sweep或outbox派发。只调用 `deliver_event(15, 明确新eventId, 唯一订阅)` 和 `dispatch_one(明确新callId, 15)`。导入worker模块出现任务注册日志，不代表启动了队列消费。

## 实际证据

完整成功轮发生于北京时间14:39:58–14:40:05；原始脱敏结果位于被忽略的 `backend/.local/automation-live-evidence-063954851910.json`。

| 步骤 | 实际结果 |
| --- | --- |
| 当前8000正常启动 | `/readyz` HTTP200，验证进程PID30868，正常lifespan |
| 真实账号会话 | `/api/auth/v1/login` HTTP200；认证内容仅在进程内存使用，没有输出或复制 |
| 只读试算 | `POST /api/ontology/v1/action-types/live_auto_receipt:dry-run` HTTP200，`passed=true`，该验证动作没有额外提交条件（checked=0） |
| 事件入站 | `LIVE-AUTO-1031-063954851910-E1` HTTP200；同一完整信封重放HTTP200，复用同一事件 |
| 指定订阅投递 | delivery19，`delivered`，attempts=1，event outcome“已生成告警” |
| 规则命中 | `RH-a2d020f01ad7b845ef177723eed6ca43b3545d07d822822b9ab1e17c74d6`，event_payload模式，规则v1 |
| 真实告警 | `AL-a2d020f01ad7b845ef177723eed6ca43b3545d07d822822b9ab1e17c74d6` |
| 自动策略排队 | `ACT-20261031-0002`，call1535，初始attempt0；自动策略不是停在待确认 |
| 自动动作HTTP | `POST http://127.0.0.1:8766/receipt` HTTP200，attempt1，回执 `LOCAL-a3121e2af20d8eb320ff` |
| 动作终态 | “已对账”，结论“外部已成功”；该系统持久排队后通过既有对账listener收敛，不等同“执行中” |
| 回执核对 | 按同一幂等键真实 `GET /status` HTTP200，“查到·成功”，回执与run一致 |
| 重复执行保护 | 对同一call再次dispatch，并对同一event再次deliver后，接收端POST次数1、唯一回执1 |
| 机器入口 | `POST /api/ontology/v1/machine/actions/live_auto_receipt:execute` HTTP201，policyMode=auto，run `ACT-20261031-0003` |
| 机器入口重放 | 同一幂等键再次POST HTTP200，仍为 `ACT-20261031-0003` |
| 机器动作HTTP与终态 | 真实POST HTTP200，回执 `LOCAL-62f7a4e16945c4936d00`，“已对账 / 外部已成功” |
| 双动作最终计数 | 重复dispatch后，接收端总POST次数2、唯一回执2；每个独立业务意图只发一次 |
| 恢复原API | 14:40:05 `/readyz` HTTP200，原配置launcher33588，监听PID14864；临时适配器和token覆盖已移除，本机接收器关闭 |

前一轮14:39:17–14:39:23也取得真实回执：delivery18、`ACT-20261031-0001`、call1534、HTTP200、`LOCAL-e9f84f3b4640cf33774a`，重复执行后POST=1/唯一回执=1。脚本最初误把最终状态断言写成“成功”，真实结果为既有契约规定的“已对账 / 外部已成功”，因此第一轮脚本非零退出；修正验证断言后完整第二轮退出0。两轮是三个不同动作意图，不是同一幂等键重新发送。首轮结果保留于 `.local/automation-live-evidence-first.json`。

## 故障分支与准确边界

| 分支 | 已实际核验 | 边界 |
| --- | --- | --- |
| 旧空白订阅过滤 | 完整事件成功链使用持久快照中的纯空白filter，delivery19真实delivered | 未修改用户历史快照 |
| 新空/空白规范化 | 对真实SubscriberOut规范和运行时filter解析器分别检查空串、空白、“全部”，均为全部 | 此项为当前真实解析组件调用，不是HTTP编辑表单验收 |
| 非空非法filter | 当前真实解析器拒绝不可执行非空表达式 | 没有将非法配置发布到用户本体；未覆盖全部表达式语法 |
| 动作授权版本过期 | 从新本体真实ExtSystem和ActionType取定义，只在未提交事务内设置授权v0，`submit_adapter`抛 `ACTION_EXECUTOR_AUTHORIZATION_VERSION_MISMATCH`，status409，接收次数仍2 | 409是实际运行时异常的HTTP映射，未声称通过API请求收到409；事务回滚，持久授权未变 |
| 所选目录不存在 | 同一真实运行时调用抛 `ACTION_HTTP_ENDPOINT_NOT_FOUND`，status409，接收次数仍2 | 同上，目录临时变化仅在未提交事务内，随后回滚 |
| 新对象尚未镜像 | HTTP入站200；新event `…-MISSING`、delivery20初次为pending / `EVENT_OBJECT_NOT_SYNCED` / attempts0 / nextAt存在；接收次数仍2 | 后续已按明确授权进行仅此测试记录的期限故障注入，见下文；没有实际等待15分钟 |
| 执行身份无权限 | 指定新event `…-DENIED`、delivery21，使用未获此本体访问授权身份，真实prepare失败为 `failed / UNAUTHORIZED / attempts0 / nextAt=null`；重复deliver仍不执行，接收次数仍2 | 临时路由只在进程内对新本体生效，结束恢复；未修改该身份的角色或成员关系 |
| 身份歧义 | 后续已使用真实本地PG镜像故障注入与真实授权解析路径，得到 `EVENT_OBJECT_AMBIGUOUS` 终态failed | 未破坏DM源主键；只临时增加一个受控本地损坏镜像行，随后精准清理，见下文 |

## 真实服务边界故障注入补充

北京时间14:43:47–14:43:48，在主会话明确授权后执行 `backend/scripts/verify_live_preflight_boundaries.py`，退出0；结果保留于被忽略的 `backend/.local/automation-live-boundaries.json`。没有新增登录、改登录限流、重启API或启动worker。这里是实际数据库与生产服务函数路径的有界验证，未冒称为新的HTTP鉴权验收。

1. 仅对新本体本轮创建的delivery20，将 `created_at` 暂时设置为真实UTC的16分钟前，调用指定 `prepare(session, 20)`。真实结果为 `failed / EVENT_OBJECT_NOT_SYNCED / attempts=0 / nextAt=null`，未取得执行claim。随后恢复它原本的创建时间，保留终态及新追加的预检审计；没有清错或自动补投。此操作证明真实到期分支，不能冒称为实等15分钟。
2. 在确认 `LiveProject` 没有活动同步、并与镜像代理协调后，记录该对象原9条镜像物理PK集合。仅在workspace15中复制一个已同步Project的payload与binding signature，新建独立 `source_key`、自动物理主键214、`is_current=true`。真实模型字段为 `source_key`，没有杜撰 `external_key_hash` 字段；DM源表不变。
3. 新增专用Project事件 `LiveIdentityPreflightRequested`、规则 `R-LIVE-IDENTITY-1031`，单规则订阅、无自动动作，追加正式快照 `v2026.10.4`，保留此前发布的其他新fixture。真实 `identity_rows(unique_only=False)` 返回目标业务身份的两个物理候选；真实 `resolve_user` 解析当前有效执行身份。
4. 经实际入站服务 `receive` 接收新event `LIVE-IDENTITY-1031-064348333018`，再调用仅此事件/唯一订阅的 `deliver_event` 两次。delivery22稳定为 `failed / EVENT_OBJECT_AMBIGUOUS / attempts=0 / nextAt=null`，动作调用账本集合没有新增。
5. `finally`仅删除同时匹配本体、对象、插入物理PK214及专用source_key的那一条受控镜像行。实际删除1条，Project恢复9条且物理PK集合与注入前完全相同，DM源写入0。没有删除任何原镜像行、事件或投递历史。

`NOT_SYNCED`和歧义记录仍保留在新本体异常列表，均为明确失败，等待人工核查，未自动回放。原有队列和用户历史未消费。

## 可重现的通道前置

该验证动作需要本机接收端运行，并由部署进程明确允许以下**非密钥**目标配置：

```json
{
  "integrationRealConnect": true,
  "integrationAdapters": {
    "15/LIVE-GENERIC-RECEIVER": {
      "origin": "http://127.0.0.1:8766",
      "submit_path": "/receipt",
      "query_path": "/status"
    }
  }
}
```

对应环境变量为 `RENGU_INTEGRATION_REAL_CONNECT` 与JSON形式的 `RENGU_INTEGRATION_ADAPTERS`。新本体事件入站另需 `RENGU_INTERNAL_EVENT_TOKENS` 中该本体的真实临时密钥，不应将密钥写入文档或共享脚本。动作授权仍必须匹配动作v1和`/receipt`，所选目录为`receipt`。留存脚本只在自己的进程内建立上述通道、生成私有入站token，并在结束后恢复原API；本次没有将临时执行权限永久写入用户配置。

## 留存与检查

新增可复核脚本 `backend/scripts/verify_live_automation.py`，要求显式新workspace、对象、已核实原API PID。它限制本机目标、限定唯一event/call，错误输出脱敏，异常时恢复原API。补充故障注入脚本为 `backend/scripts/verify_live_preflight_boundaries.py`，固定验证本体及测试投递前置条件，并以finally清理唯一插入行。原始凭据、token、Cookie、原始业务对象标识未写入文档。

执行过Python静态编译及目标文件差异空白检查。只执行本轮有界真实平台联调；没有跑pytest、全量回归、迁移测试、build、打包、commit或push。本轮未发现需要改产品运行时代码的失败；改动集中于验证脚本和本文，未覆盖其他代理的未提交成果。
