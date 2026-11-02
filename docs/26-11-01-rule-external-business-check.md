# 办公复杂规则与本机正式外部通道核查（2026-11-01）

本轮范围仅为工作空间 16 合成办公对象。现有本体、军事规则、真实远端、旧事件积压及原外部配置不用于执行取证。不启动全局 event worker、定时扫描或队列 sweep；不写数据库 fixture，不跑 pytest/后台测试。

## 已证实的产品契约

- 规则条件 UI 支持对象字段、事件字段、指标、固定版本 `rule_fn("函数ID", "版本", 字段)`，以及已发布关系的一跳 `related("关系ID", "目标属性API")`；全部满足/任一满足可组合，亦有表达式编辑入口。
- 跨对象读取必须从当前规则对象出发；目标字段不能隐藏、敏感或未映射；目标行先过滤权限；关联键必须唯一，最多读取四个关联字段。MaintenanceTask → OfficeDevice 的已导入 FK 可作为办公样本，不能猜测其真实关系 ID。
- 规则“当前数据推演/回放试跑”明确只计算条件，不生成告警或动作。当前 UI 没有单次正式触发规则按钮。因此条件试跑成功不能称规则已自动执行外部动作。
- 外部动作确认后只持久排队；真正 HTTP POST 通过既有 `dispatch_one(明确新callId, 16)`。当前没有 UI 单 call 派发入口。只可在明确新合成 call 的范围内执行一次派发，不能启动全局消费者取巧完成。
- 正式外部配置需要持久 ExtSystem、HTTP 目录与请求/响应 mapping、动作授权，以及部署精确 origin/path/action ceiling。UI 配置本身不绕过部署 ceiling。
- 本地通道要求精确 HTTPS loopback、`local_only` 和部署 LocalDemo 配置匹配；标志在创建系统时设置，先创建普通系统再补环境不会自动转换。新系统 code 为当前本体 EXT-N 最大序号加一，空目录通常为 EXT-N01，需根代理实际核验。

## 最短且真实的界面路线

1. 新建/复用本轮合成纯函数：输入 MaintenanceTask 的已授权数值字段（例如 asset_id），返回可明确解释的标签；或在表达式编辑中给 FN001 显式合成分数常量，不能把资产 ID 冒称巡检得分。
2. 新建办公规则，以 MaintenanceTask 为对象，条件同时绑定自身字段、真实 FK 关联 OfficeDevice 的可读状态字段、固定函数版本。触发保持关闭；先当前数据推演，用明确对象条件核对 true 样本，再只改变比较阈值核对 false。核对样本与无法求值原因，不以候选数量替代条件结果。
3. 规则不改为消费旧事件或开启全局定时器。若要把“规则 → 告警 → 动作”连成正式闭环，必须另建唯一带标记事件/订阅并按明确新 ID 单次投递；本轮无 UI 单次投递入口，需单独说明这种受控执行器步骤。
4. UI 新建“设备巡检回执联调”外部系统，类别 Webhook、环境测试、鉴权选择有 Bearer/HMAC 的类型；精确部署授权准备好后保存。新动作 API `recordExternalInspection`，目标 OfficeDevice 16002，参数 score 整数。外部仅写本机回执，不配置设备字段编辑或任何其他业务副作用。
5. 保存目录请求/响应 mapping、系统动作授权与版本；UI 只读试运行，再对明确目标确认一次。对唯一新 call 单次派发，核对真实 HTTPS 回执和按同幂等键查询。不得把“排队/执行中”称为成功。最后核对设备原值未改、收据唯一、同 run 状态及刷新恢复。

步骤 1–5 是待根代理真实操作的路线，不代表本轮已完成这些业务验收。

## 专用本机接收器

代码：[local_office_receiver.py](../backend/scripts/local_office_receiver.py)。复用现有 TLS/私有目录与 HMAC 工具，不复用原军事端点、旧密钥或旧收据。

联调期间曾启动仅 loopback `https://127.0.0.1:8767`，初始 launcher 32340、listener 53008；最终已停止，见本文收尾记录。未启动 worker。凭据、证书、日志、收据仅位于当前 Windows 用户 ACL 私有临时目录 `%TEMP%\rengu-webhook-office-nmw05j3r`；本文不记录密钥内容。临时服务不作为持续部署能力。

| 用途 | 精确端点 | 契约 |
| --- | --- | --- |
| 只读探测 | GET `/office/health` | 返回 ready 和 ontologyId=16，无业务读取/修改 |
| 接收回执 | POST `/office/receipt` | Bearer + X-Ontology-Id=16 + HMAC 时间戳有效期 300 秒 + Idempotency-Key |
| 幂等查询 | GET `/office/status?idemKey=...` | Bearer + X-Ontology-Id=16；返回 extStatus、extRef，不返回凭据或业务载荷 |

POST 必须恰有以下八个字段，不接受其他工作空间、动作、对象或标记：

| 顶层字段 | UI mapping 来源或固定值 |
| --- | --- |
| ontologyId | 同名上下文，必须整数 16 |
| actionRunId | 同名上下文，ACT- 前缀 |
| actionType | 同名上下文，必须 recordExternalInspection |
| idemKey | 同名上下文，必须与请求头一致且 ont-16-act- 前缀 |
| objKind | 同名上下文，必须 OfficeDevice |
| objId | 同名上下文，必须字符串 16002 |
| score | params.score，整数 0–100（布尔不接受） |
| code | 固定字符串 office-check-20261101 |

响应 mapping：status 读取 `status`，回执读取 `receiptId`，成功/失败/未知分别 `SUCCESS` / `FAILED` / `UNKNOWN`，不需要 reason/code 字段。相同幂等键和相同原始载荷返回同一回执；相同键改变载荷返回 409。每张收据记录 postCount，便于核对是否重复发送。收据采用临时文件刷盘后原子替换；没有设备/数据库写入。

部署最小 additions（必须合并保留既有配置，不能覆盖其他系统或泄露值）：

- `RENGU_INTEGRATION_REAL_CONNECT=true`。
- `RENGU_INTEGRATION_LOCAL_DEMO` 追加 ontologyId=16、真实 systemCode、origin=https://127.0.0.1:8767、submitPath=/office/receipt、queryPath=/office/status 的精确配置。
- `RENGU_INTEGRATION_ADAPTERS` 仅追加 `16/<真实systemCode>`，origin 同上、submit_path=/office/receipt、query_path=/office/status、probe_path=/office/health、actions=[recordExternalInspection]、token 和 signing_secret 从私有文件读取；不可在工具输出/聊天中打印整个 JSON。
- `RENGU_INTEGRATION_CA_FILE` 指向私有 ca.pem；若已有 CA 配置，不得直接替换损坏原通道，应生成保留原信任链的私有 CA bundle。
- 如使用只读“测试调用”，部署探测网络只追加 127.0.0.1/32 的 8767 端口，不扩大到整个内网或任意端口。

其中 LocalDemo/Adapter 使用 Pydantic 别名可接受 UI 常见 camelCase，实际环境配置建议沿现有 snake_case 格式，最终以源码字段校验为准。API 重启和实际 environment 合并由根代理统一协调；本子任务尚未修改运行环境。

## 当前验证与边界

接收器内置 `--self-check` 已通过：错误 token/签名拒绝、跨空间/错误目标/错误动作/布尔分数/错误标记拒绝、重复同载荷收据复用、改变载荷冲突、台账重读、回执查询。该检查无网络 socket、无数据库、无后台服务。第一次签名 string/bytes 类型问题已纠正后定点重跑通过；真实接收器目前只确认 loopback 监听和空错误日志，尚未接收平台动作。

旧审批说明和“审批中”状态筛选仍需按实际业务模型区分：当前 ActionStatus 与后端审批权限逻辑仍存在，不能为清理文案删除真实权限或记录状态；本轮没有因此改变审批模型。生产第三方 URL、认证材料、合同及部署网络授权未提供，不能将本机模拟器联调称为真实第三方生产接入完成。

## 本机部署接线补记

根代理真实确认工作空间 16 外部目录为空、尚未创建，故首个正式 UI 系统 code 为 EXT-N01。随后按授权接线：先确认 `backend/.env` 被 Git ignore，检查原 REAL_CONNECT 已为 true（没有从关闭变为开启激活旧适配器），把原文件原样备份到当前用户 ACL 私有临时目录。

本次仅修改四个 integration 配置键：Adapters 原四项保留并增加 `16/EXT-N01`；LocalDemo 原两项保留并增加同一系统；探测网络原条目保留，仅增加 `127.0.0.1/32:8767`；CA 文件改指向包含原 CA 全文和本轮 office CA 的私有 bundle。新 adapter 仅允许 recordExternalInspection、精确 HTTPS origin 和 receipt/status/health 路径。所有其他 `.env` 键逐值核对未变，包括 REAL_CONNECT 与 worker 相关配置。

备份为私有临时目录内 `backend.env.before-office`；`environment-change-manifest.json` 记录修改键名和修改前后摘要，不含密钥。初次跨卷原子替换被 Windows 拒绝且原 `.env` 未动；随后经被忽略、当前用户 ACL 的 `backend/.local/office-channel` 同卷中转并原子替换成功。新进程读取应用配置，确认精确 scope/system/path、LocalDemo ready、CA bundle 均通过；全程没有数据库/网络调用、后台测试或 API 重启。原进程尚须根代理统一 API-only 重启后加载新配置，不能将配置校验当作正式 UI 保存/探测/HTTP执行已通过。

恢复时应先核对 manifest 的修改后摘要，防止覆盖之后的新配置；如 `.env` 期间被其他工作修改，应仅撤回本轮追加项及本轮 CA bundle 指针，保留后续变化。私有证书和收据不得随源码提交。平台配置与 receiver 的最终保留/关闭由本轮根代理统一收尾，不自动撤销尚在联调的精确通道。

## 新建外部系统负责人修复

根代理 API-only 重载后、尚未填写的新建表单真实显示默认“王敏”，下拉只有固定演示姓名而没有当前 admin。源码 `ExternalSystems.vue` 的 blank 和 OWNERS 均为固定值。最小修复复用现有 `useAuthStore`：真实模式新建默认当前登录用户 name/account；下拉合并当前真实用户、当前表单原负责人和已加载真实系统负责人并去重，显式 mock 模式保留演示候选。编辑旧记录仍以读取到的旧负责人回填，不批量修改历史数据。

该组件单次 script/template SFC 编译通过，无新端点或后台测试。根代理重新开表单后的负责人显示及正式保存仍待真实复验。认证方式建议选择已有“API Key + HMAC 签名”；实际适配器对非“无需授权”方式发送部署 Bearer，提交另外添加签名。凭证说明仅填用途标识，不将 secret 写进表单或文档。

## 11 月 2 日复杂规则关联候选修复

根代理真实新增 FN-002 `has_asset_reference(asset_id:int=None)->bool`，平台运行 asset_id=16001 返回 true、0 返回 false、空参数返回 false。新建 R-001“办公工单关系与函数条件验收”，规则对象 MaintenanceTask，已采用自身 Id=16101 条件；添加关联对象属性时，已发布一跳关系下拉为空。此前已导入并保存 MaintenanceTask→OfficeDevice 的真实 FK，工单详情中 task16101→设备16001 跳转已验证。

源码发现字段目录契约不一致：`RuleService.meta` 的关联字段只接受 `backing_column`；同方法对象字段和真实 `rule_relations` 执行器均使用 `backing_column or source_column`。导入对象仅有 source_column 映射时，关联目标字段列表为空，导致整项关系候选被过滤。

最小修复仅改后端关系字段筛选，统一 backing/source 映射，同时保留并补齐可见性、敏感字段、transform、真实物理列和受控 projection 校验；未改变真实 FK 一致性、两端发布状态及对象行权限守卫，没有硬编码关系或示例字段。未修改前端 Rules，避免 HMR 影响尚未保存的条件。

新增局部 `check_rule_relation_fields.py` 从真实 meta 方法 AST 提取字段投影表达式，断言 source-only 可用、backing 优先、隐藏/敏感/transform/未授权列/缺列排除；已通过，无数据库、网络、后台测试或规则求值。此时尚待根代理 API-only 重载后的真实关系候选与条件试跑验证；若仍空，下一步应只读核查 relation_binding 拒绝原因，不能据本项局部修复提前宣称整个关联执行通过。

### 真实仍空后的受控诊断与只读绑定修复

根代理于 00:04:47 正常重载后硬刷新规则页，关联候选仍为空，故字段 fallback 修复虽必要但未解决完整根因。随后根代理明确授权一次同一问题的受控本机只读元数据诊断，数据库目标限制 localhost:5433/rengu_ontology、scope16、`SET TRANSACTION READ ONLY`；不读取业务行、密钥或其他 scope。

元数据证据：`deep_device_w16_task_asset` 已发布，MaintenanceTask.asset_id→OfficeDevice.id，via 为空；两端已有正确受控 projection，均非 sourceSql 镜像，但动态主表与补充表的 `links` 注册字典均为空。属性均仅有 source_column，无 backing_column。正式新建 `EXT-N01.local_only` 只读核对为 true。

真正阻断：规则错误复用了写动作的 `action_edits.relation_binding`；后者要求写路径 `sg.links[link.code]`，动态显式 GovernedBinding 从未填入该写路径，因而被拒绝。该方法还对元数据使用 FOR SHARE，在本次严格只读事务中返回 SQLSTATE 25006；首次捕获异常后事务中止并回滚，续诊通过只读 savepoint 保留元数据输出与具体异常。全程无业务数据写入或规则求值，不能把此次诊断称为规则已经执行。

最小完整修复为新增规则专用 `read_relation_binding`，供规则 meta 与 `bind_rule_relations` 共同使用：只按当前 scope 已发布 Link/Object、唯一受控本地映射、明确授权列及反射真实 FK 的目标表/列绑定；不依赖写 registry，也不信任 join_sql 字符串。写动作的 relation_binding 和写入权限完全未改。目标行权限、隐藏/敏感字段、目标键唯一性继续保留。

绑定在 session/事务/scope 内复用，同关系多个字段不重复读绑定；已有 governed 元数据事务缓存继续负责反射，不添加跨工作空间全局缓存，不在逐行求值时反射。局部离线断言覆盖独立 MetaData 反射的真实 FK、无写 registry 读取、无 FOR SHARE、错误 FK、未发布、投影拒绝及同事务二次绑定复用，通过后未再读取数据库或运行后台测试。待根代理统一加载并真实复验关联候选和 true/false 试跑。

## 正式系统与接口保存、探测通道缺项

根代理通过 UI 正式保存 EXT-N01“设备巡检回执联调”，负责人 admin、Webhook、测试环境、HMAC 鉴权、精确 HTTPS origin；上文负责人候选与本地系统创建路径已由实际保存补证。业务接口 `office_receipt`、POST `/office/receipt`、八字段请求 mapping 与 status/receiptId 响应 mapping 已通过 UI 持久保存，没有后台 fixture。规则 R-001 仅自身 Id=16101 条件已保存即生效为 v1，触发尚未启用；复杂关联条件仍待本轮修复后添加。

保存第二个只读接口 `office_health`、GET `/office/health` 时真实报“本机演示系统未配置只读探测通道”。源码确认并非仅环境值遗漏：接口保存 handler 对全部 local_only probe 无条件拒绝，LocalDemoConfig 没有 probe_path 字段，get_probe_adapter 同样拒绝本机目标。

最小修复新增可选 LocalDemo.probe_path；未配置的旧 LocalDemo 保持禁止探测。仅当精确 scope/system/origin 匹配，且 LocalDemo、Adapter 与新保存/已保存探测路径一致时允许目录保存和只读 GET。没有放开任意 localhost 路径、任意端口或通用私网探测。仅向本轮 16/EXT-N01 LocalDemo 补 `/office/health`，旧配置项和其他环境键逐值保持；私有原文件备份 `backend.env.before-office-probe` 与追加 manifest 已记录，API 未由子代理重启。

局部 `check_office_probe_config.py` 执行实际目录校验 guard 与 adapter，覆盖首次 stored probe 为空时精确路径保存、精确探测配置、错误路径、未授权 probe 和错误 scope 拒绝，已无 HTTP/DB 离线通过。根代理将与只读关系修复统一 API-only 重载后再真实保存和探测，此时未预记成功。

新外部动作最短 UI 路线已按当前组件确认：同一“新建动作”表单填写基本信息和 score/整数参数；“写回配置”页点击添加，外部系统选 EXT-N01，接口下拉选已存 `office_receipt · POST /office/receipt`，组件自动复制目录 mapping，无需手填 JSON。本体编辑、副作用保持零条，具体目标16002在动作执行时选择；保存后再由外部系统动作授权选择该动作当前版本与精确路径。尚未执行任何新外部动作。

## 代码与环境变更清单（本轮）

| 文件/配置 | 最小变化 | 验证边界 |
| --- | --- | --- |
| `backend/scripts/local_office_receiver.py` | scope16/动作/目标/marker 专用签名 HTTPS 接收器、持久幂等回执、只读查询 | 内置离线 self-check 与 loopback 监听通过；尚无平台业务 POST 证据 |
| `frontend/src/pages/data/ExternalSystems.vue` | 真实新建默认登录用户，负责人候选合并真实数据 | SFC 编译通过，随后真实 UI 保存 owner=admin |
| `backend/app/modules/dynamic/rules_service.py` | 关联候选 source/backing fallback 与 projection；使用只读关系绑定 | 首次字段修复不足；后续只读绑定尚待统一加载 |
| `backend/app/modules/dynamic/rule_relations.py` | 发布/受控列/真实 FK 校验的专用只读绑定，事务与 scope 内复用 | 局部元数据断言通过，未对军事规则求值 |
| `backend/scripts/check_rule_relation_fields.py` | 从生产表达式及读绑定验证真实 FK、权限和缓存契约 | 仅局部离线；无数据库/规则运行 |
| `backend/app/modules/integration/adapters.py` | LocalDemo 可选精确 probe_path，匹配才允许只读探测 | 旧 localdemo 未配 probe 继续拒绝；局部断言通过 |
| `backend/app/modules/integration/router.py` | local_only 目录探测保存从无条件禁止改为精确已授权路径校验 | 实际新 probe 保存待重新加载 |
| `backend/scripts/check_office_probe_config.py` | 新 probe 保存/运行的精确 ceiling 正反断言 | 无数据库或 HTTP |
| ignored `backend/.env` | 仅本轮16/EXT-N01 adapter/localdemo/probe网络及保留旧CA的bundle；随后仅该localdemo增probe_path | 私有前后备份与摘要；旧配置值均保留；API加载由根代理统一控制 |

环境及临时服务的状态是“为本次明确联调配置”，不是任意生产第三方已接入。未修改全局 worker 开关，未启动队列消费者，未替换审批模型。

## 只派发新 call 的最小模板（尚未执行）

`dispatch_one(call_id,16)` 自身会锁定对应 run/call，只有 `ACTION_DISPATCH_QUEUED` 且 run 为真实“结果未知”才提交 claim，随后发一次 POST；已 claim、已完成或重复调用不再发 POST。POST 成功时直接保存回执并经 DynamicReconciliationListener 收敛为已对账，不能把外部成功误说成尚需全局 worker。包装器还检查本体仍存在且 workers_paused=false；若检查拒绝，不能绕过包装器或擅自启用后台工作。

下列模板必须由根代理拿到**新 run code、数字 WritebackCall.id、本轮创建时间**后填入私有临时脚本，先核对预期，再明确授权单次执行。当前只是文本方案，未运行；不查询“最新一条”、不调用 `workspace_ids`、`dispatch_queued`、`_dispatch_workspace`、`worker.sweep` 或任何全局任务。脚本运行目录为 backend，使用现有 `.venv` 和 `PYTHONPATH=.`。

```python
import asyncio
from datetime import datetime
from sqlalchemy import func, select, text
from sqlalchemy.engine import make_url
from app.common.ontology import set_ontology_scope
from app.core.config import get_settings
from app.db.session import get_sessionmaker
import app.db.models
from app.modules.action.models import ActionType, OntologyEditRule, SideEffect
from app.modules.dynamic.models import ActionRun, ActionStatus
from app.modules.integration.models import CallKind, ExtSystem, WritebackCall
from app.modules.integration.execution import QUEUED, dispatch_one

CALL_ID = 0                  # 填根代理核实的唯一新数字 ID，不是 UI 字符串 code
RUN_CODE = "REPLACE_NEW_RUN" # 填同一新 run code
NOT_BEFORE = "REPLACE_ISO_DATETIME_WITH_OFFSET"

async def main():
    assert type(CALL_ID) is int and CALL_ID > 0
    assert RUN_CODE.startswith("ACT-")
    earliest = datetime.fromisoformat(NOT_BEFORE)
    assert earliest.tzinfo is not None
    url = make_url(get_settings().database_url)
    assert url.host in {"localhost", "127.0.0.1"} and url.port == 5433
    assert url.database == "rengu_ontology"
    async with get_sessionmaker()() as session:
        await set_ontology_scope(session, 16)
        await session.execute(text("SET TRANSACTION READ ONLY"))
        assert await session.scalar(text("SHOW transaction_read_only")) == "on"
        stmt = (select(WritebackCall, ActionRun, ExtSystem, ActionType)
                .join(ActionRun, ActionRun.id == WritebackCall.action_run_id)
                .join(ExtSystem, ExtSystem.id == WritebackCall.sys_id)
                .join(ActionType, ActionType.code == ActionRun.action_type_code)
                .where(WritebackCall.id == CALL_ID, ActionRun.code == RUN_CODE,
                       WritebackCall.ontology_id == 16, ActionRun.ontology_id == 16,
                       ExtSystem.ontology_id == 16, ActionType.ontology_id == 16))
        call, run, system, action = (await session.execute(stmt)).one()
        assert not call.is_seed and not run.is_seed
        assert call.created_at >= earliest and run.created_at >= earliest
        assert call.kind == CallKind.write and call.msg == QUEUED
        assert call.attempt == 0 and call.http_status is None
        assert run.status == ActionStatus.unknown and run.confirmed_at is not None
        assert run.alert_id is None and run.obj_kind == "OfficeDevice" and run.obj_id == "16002"
        assert run.params == {"score": 90}
        assert action.api == "recordExternalInspection" and action.http_endpoint_id == "office_receipt"
        assert action.sys_id == system.id and system.code == run.sys_code == "EXT-N01"
        assert system.local_only and system.endpoint == "https://127.0.0.1:8767"
        assert call.method == "POST" and call.path == "https://127.0.0.1:8767/office/receipt"
        assert call.idem_key == run.idem_key and run.idem_key.startswith("ont-16-act-")
        for model in (OntologyEditRule, SideEffect):
            assert await session.scalar(select(func.count()).select_from(model)
                                        .where(model.action_type_id == action.id)) == 0
        await session.rollback()
    # 唯一业务执行点；只有上面的新记录 identity 全部匹配才可到达。
    await dispatch_one(CALL_ID, 16)

asyncio.run(main())
```

此模板的只读预检和正式派发不是同一数据库事务；原派发器在 claim 事务中再次检查发布定义、权限、系统授权、路径、幂等键与参数，配置变化时真实拒绝。不得因模板断言不通过便删断言或改用原业务 run。若本次实际参数不是 score=90，需先与 UI 确认方案一致，再显式调整预期，不能从读到的记录自动采纳任意参数。

派发后先由 UI 刷新同一 run，并独立核 receiver 台账同幂等键唯一回执与 postCount。需要真实幂等 GET 证据时，可在同一scope16、同一系统/新 run 身份核对后调用 `get_adapter(system).query_by_key(run.idem_key)`；这是只读外部查询，不是重复 submit。只输出查询结论和合成回执 ID，不输出 adapter/token/signing secret。若需持久的自动对账查询记录，既有 `reconcile.process(session, 明确runCode, listener)` 只在未解决且到期的 task 上发 GET；已 resolved 则直接返回，不能为制造查询证据重开/篡改已完成 task，也不能用人工“外部已成功”假扮真实查询。

## 00:27 加载后真实进展与 TLS 修复

根代理正常 API-only 重启后，真实关联条件下拉已可选已发布 task_asset 关系及 OfficeDevice.status，关联候选修复通过；尚不等同含关系及函数的条件求值已通过。新 `recordExternalInspection` v1 已通过 UI 保存，绑定 EXT-N01 / office_receipt、八字段 mapping、本体编辑与副作用均零条。office_health 探测接口也已真实保存。

00:32:47 的第一次真实 UI GET health 探测失败，约 87ms、HTTP 空；scope16 受控只读核对新 probe call 原因为 EXT_QUERY_FAILED，接收器仍监听且无错误日志。随后仅以应用 CA bundle 请求本机 GET health，得到真实 certificate signature failure，未发送任何业务 POST。

TLS 根因：新旧接收器生成的 CA 使用同一默认 subject，但密钥不同，合并 bundle 后因缺少 AKI 可能选到错误同名 issuer。没有关闭证书验证或移除旧 CA。给 office CA 使用随机唯一 subject 重签本轮服务证书，保留整个旧 bundle 并仅追加新 office CA，保留原 Bearer、HMAC 与环境值。`local_webhook_receiver.prepare` 仅增加可选 ca_common_name（原调用保留默认行为），`local_office_receiver --prepare` 使用唯一办公 issuer 防止重复。

只重载本轮 loopback 接收器后，应用同一 CA bundle 的 GET `/office/health` 实际返回 200 / ready / ontologyId16，证书链正常验证，业务 POST 数仍为零。新 launcher=26180、listener=18364，原32340/53008已按模块身份核对后停止。API 没有由子代理重启，真实 UI 重试测试调用仍由根代理进行。

同页 tooltip 出现 undefined/对象空白来自 mock `objKindLabel` 只认识固定旧类型。根代理明确允许 HMR 后，仅修改 `Rules.vue` 列表 tooltip、对象说明和命中对象列名三处显示，优先当前真实 meta 名称，再原 label，再 API kind。未改草稿、保存或触发逻辑，SFC 编译通过；根代理仍需看实际页面显示及未保存条件保留情况。此为本轮代码清单的追加项，不能将显示修复算作规则求值证据。

## 00:40 真实探测通过与 HMR 后空改动

根代理 UI 重试只读测试调用，00:40:32 返回 HTTP200、11ms，office health 目录保存→平台实际 GET→证书校验→本机响应链通过；这仍不代表外部业务 POST 或第三方生产接入通过。

规则显示补丁的 HMR 后，本地尚未保存的两个条件消失；再次“保存并生效”遇到 CR_EMPTY，保留原 v1 与仅自身 ID 条件的草稿 v2。只读源码确认：Rules.saveDraft 在 await 成功前不删除 edits 或重置条件，失败 catch 只显示错误；后台 :apply 同事务先 save_draft 再发布，空 CR 拒绝时事务回滚。未提交 body.conds 时后端保留当前条件并更新阈值，不会主动删除原本已发送的新增条件。现有证据符合开发期 HMR 重建本地 edits，不能据此另称正常保存产品路径丢条件。根代理将重新录入后独立验证保存。

CR_EMPTY 旧“先保存到草稿再提交审核”来自 frontend/api/dynamic.ts 的统一硬编码错误映射，后台原文已是不含审核的“变更单里还没有改动，无法发布”。建议最小修为“没有新改动，修改后再保存”；当前根代理明确禁止进一步前端 HMR，为保护正在录入的条件，该一行修复暂未应用，不能记为已完成。未删除审批状态、权限守卫或变更模型。

随后根代理重新录入并真实保存 R-001 v2，三条件为自身 ID=16101、related 设备 status=正常、固定 FN-002 v1 对 asset_id 返回 true，保存成功且表达式完整。此结果支持正常保存链保留三条件；当前数据求值仍待单独试跑。根代理确认无未保存草稿并明确开放一次 HMR 后，已将 dynamic.ts 的 CR_EMPTY 映射最小改为“没有新改动，修改后再保存”。纯文案无新增测试；未改任何审核业务或权限逻辑，此后停止新增前端 HMR。

## 复杂规则正反例实际通过

根代理对 R-001 v2 运行当前数据试跑，真实返回 37 个已求值样本、1 个命中、0 个无法求值；v3 将关联设备 status 改为“需维护”后，37 个已求值、0 命中、0 无法求值。条件同时包含自身 ID、一跳真实 FK 关联状态、固定版本 FN-002 对 asset_id 的纯函数，正反例均有实际 UI 证据。规则触发仍未启用，这不是自动生成告警/自动动作链已执行的证明。

恢复 v4 正例时，编辑表格阈值为“正常”而原始表达式仍为“需维护”的现象已只读追溯：本地 conds 修改会移除本地 expr 覆盖值，保留服务端原始表达式显示，标题为“原始表达式（保存后更新）”；保存请求包含 conds 且不含 expr 时，后台强制重新编译。尚未把编辑中的显示差异认定为已发布序列化缺陷，根代理将明确采用正常并保存后独立核对已发布表达式。未为此修改前端或追加数据库诊断。

## 唯一新外部动作真实派发、查询与防重复

根代理从问数确认新动作 `ACT-20261102-0001`，唯一新调用 code=`EXEC-7db0d1f51e2b42e59da53f63ed10661d`，创建时间 2026-11-02 00:50:10（北京时间），scope16 / EXT-N01 / OfficeDevice16002 / score90。根代理明确授权该新 call 单次派发、同幂等键一次真实查询、再重复同 call 一次证明不重复发送。

执行前先在 scope16、READ ONLY 事务中以精确 call code+run code 查询，得到数字 call ID=1539；核对新建时间、非种子、QUEUED、attempt0、HTTP空、已确认、无告警、score90、office_receipt、精确 HTTPS POST 路径、同幂等键，以及本体编辑/副作用均零条。没有查询最新 call、扫描队列或访问其他本体。

随后仅调用 `dispatch_one(1539,16)`：真实 HTTPS POST200、attempt1，平台调用成功，run 经原 listener 收敛为“已对账”，外部回执 `OFFICE-07e70d9cb4d3c4997090`。接收器持久台账仅该键一张回执、postCount=1，请求体确为八个办公合成字段：ontologyId16、该 run、recordExternalInspection、同幂等键、OfficeDevice、16002、score90、固定本轮 marker。没有输出 Authorization、token 或签名密钥。

对该系统执行一次真实 `query_by_key(ont-16-act-20261102-0001)`，返回“查到·成功”及同一回执。此为受控适配器真实只读 GET，不冒充 UI 查询按钮或已解决 task 再次自动查询的日志。再仅重复 `dispatch_one(1539,16)` 后，台账仍一张同回执、postCount 仍1，证明该已处理 call 没有第二次 POST。

脱敏合成证据保留在私有临时目录 `office-dispatch-evidence.json`。没有启动 worker、sweep、全局派发或重放旧 call；子代理没有运行任何其他业务动作。此结果证明当前本机正式持久配置与平台派发/真实回执/对账回调链，不证明生产第三方系统已连接，也不将独立的规则试跑与问数外部动作合并虚构成规则自动触发链。平台 UI 同一 run/调用刷新终态与设备原值独立核对仍由根代理继续完成。

## 最后真实 UI 回归与类型检查修正

根代理确认 R-001 v4 已发布，关联阈值“正常”和固定 FN-002 表达式完整，整页刷新保持一致，触发关闭；编辑中旧表达式问题没有延续到已发布定义。函数 FN001/FN002 的真实引用计数各为 1，也已通过 UI 核对。

外部系统 UI 调用显示 HTTP200、56ms、attempt1；问数刷新及整页重载均恢复同一 ACT-20261102-0001，显示“已对账/外部已成功”和同一 OFFICE 回执，没有再次确认按钮。此为平台 UI 终态恢复证据，不冒称新的 POST 或独立生产系统验证。

根代理统一 typecheck 发现新增显示 helper 将通用 string 直接传给仅接受四个旧 ObjKind 的 mock lookup。已仅在 Rules.vue 为旧 kind 添加显式字面量判断，利用 TypeScript 控制流收窄；任意新 kind 仍优先真实 meta、否则显示原 API 名，没有类型强转或绕过检查。子代理按要求未重跑检查，最终统一 typecheck 由根代理完成。

## 最终对象核对、统一检查与通道停止

根代理独立 UI 查看 OfficeDevice16002，status=正常、last_inspected_at=2026-10-02 10:00:00、补充备注02均保持。证据仅覆盖可视字段，没有把隐藏 lock_version 等未显示字段写成已核对。

根代理最终统一 `npm run typecheck` exit0，backend/frontend `git diff --check` exit0；未执行 build 或 backend tests。最终两张 UI 截图已由根代理保存并审阅：[外部接口调用成功](evidence/external-interface-call-success-2k-20261102.jpg)、[问数外部动作已对账](evidence/qa-external-action-reconciled-2k-20261102.jpg)。

收尾已先核对 listener18364/launcher26180 的 Python executable、`scripts.local_office_receiver` 模块和本轮私有目录身份，并确认仅监听 127.0.0.1:8767，再停止这两个专用进程。停止后 8767 无监听，API49000 与前端66608仍运行；没有停止 API/前端，没有全局 worker。私有配置、原始备份、证书、token/signing 和收据保留用于可恢复，但当前办公外部通道已经停止，不能承诺后续探测/提交继续成功。不会为截图或收尾重新执行任何动作。

## 可复现工具与交接给下一部署代理

正式仓库工具路径：

- `backend/scripts/local_office_receiver.py`：新私有凭据/唯一 issuer TLS 生成、固定办公接收器启动、内置离线检查。
- `backend/scripts/local_webhook_receiver.py`：复用私有目录/TLS helper，新增可选 ca_common_name，旧调用行为保留；不需要运行原 workspace1 接收器。
- `backend/scripts/prepare_local_action_integration.py`：仅固定16/EXT-N01办公部署配置，合并保留旧 adapters/localdemo/probe 网络及旧 CA（未显式 CA 时保留系统信任根）；默认只写私有 proposal，显式 `--apply` 才备份并原子应用 ignored `.env`。不会自动把 REAL_CONNECT 从关闭变为开启，不覆盖已存在但不同的16/EXT-N01凭据/合同，不启动 API/worker/接收器，不含内嵌运行密钥。

本次运行该准备工具的纯内存 `--self-check` 通过，验证旧配置保留、重复合并幂等、禁止自动开启全局真实调用、禁止覆盖不同 office adapter；随后以现有私有目录执行默认 proposal 模式成功，applied=false、credentialsPrinted=false、serviceStarted=false。未为归档脚本重新应用 `.env` 或开启任何服务，产品源码已冻结。

同机恢复现有私有材料时，先核对 `%TEMP%\rengu-webhook-office-nmw05j3r` 仍属于当前用户且文件齐全，并确认8767未被其他进程占用。在仓库 backend 目录，下面命令只启动前台专用接收器，结束用 Ctrl+C；不会恢复/执行旧 call：

```powershell
$officeDir = Join-Path $env:TEMP 'rengu-webhook-office-nmw05j3r'
& .\.venv\Scripts\python.exe -m scripts.local_office_receiver --directory $officeDir
```

另一机器或私有材料已清理时，不复制旧明文密钥/证书进仓库，也不直接照搬旧绝对 CA 路径。先明确当前本体确为授权的合成16、首新系统code确为EXT-N01，然后在 backend 目录生成新私有材料；生成命令只输出目录/CA路径，不输出秘密：

```powershell
& .\.venv\Scripts\python.exe -m scripts.local_office_receiver --prepare
# 把上一命令返回的 directory 赋给 $officeDir；此目录每次随机生成。
& .\.venv\Scripts\python.exe -m scripts.prepare_local_action_integration --directory $officeDir
# 审核私有 proposal/manifest 后才显式应用；不要打印 proposal 内容到聊天或日志。
& .\.venv\Scripts\python.exe -m scripts.prepare_local_action_integration --directory $officeDir --apply
```

若已有不同的16/EXT-N01配置，工具会拒绝覆盖，应停下核对现有正式对象，不新加宽泛“替换”开关。REAL_CONNECT未明确启用时，工具同样拒绝；部署代理须先审核既有 adapter 和自动执行影响，由操作者明确决定是否启用，不能为跑通本样例激活旧业务。应用配置后只做正常 API-only 重启，再从正式 UI 创建匹配的测试环境 Webhook 系统、目录、动作和版本授权；接收器启动命令同上。本轮已经完成的 run 不用于再次验证 POST，新请求需新合成目标/明确新 run 与新 call 授权。

真实第三方生产接入仍缺操作者提供的目标地址、凭据/签名材料、TLS信任链、只读探测/提交/幂等查询合同、网络与动作/版本授权，以及目标系统侧独立对账证据。其他部署机还需已确认的 schema 升级、业务源受控授权和同范围 UI 复验。不能拿本机模拟器结果宣称上述生产环境已接通，也不能启动全局 worker 消费旧积压补证。

## 新增截图问题的独立只读复核（待授权修复）

以下根据根代理转述的截图编号及当前源码复核，没有直接重现截图、访问业务数据、运行规则/队列/worker 或后台测试。既有“本机明确成功回执”等通过项仅保留原样本范围，不能覆盖本节新增失败、过期、版本及生命周期问题。尚未为本节修改任何产品代码。

| 截图问题 | 当前证据与根因 | 最小修复或准确能力边界 |
| --- | --- | --- |
| 2：外部明确 FAILED 被收为已对账/未执行，原因或外部编号及规则告警联动缺失 | execution.dispatch_one 将 reason 留在 call.msg、code 留 run.result_code，却不写 run.fail_reason；FAILED 统一转换 not_executed 后调用只接收 outcome/receipt 的 listener。listener 统一置 reconciled，失败分支不发结果事件/不写关联告警日志。AlertOut 仅 action_status，无法展示已对账的成功/未执行差异。但直接 POST 返回的非空 receipt 已在 callback 前写 run.ext_ref，不能一概声称直接回执丢失；自动查询负向结果的 listener 仅成功时接收新 receipt，确有遗漏可能。 | 先补业务失败原因及实际 receipt 在 run/日志/关联视图的真实传递，区分“已确认外部未执行”与“成功”。告警保持开放并记录动作失败，不自动当解决。是否将直接明确 FAILED 置 failed、查询确认未执行仍为 reconciled/not_executed，应统一产品状态合同后修改，不能仅把 UI 改红或盲目删除对账终态。外部没有返回编号时不补造。 |
| 3：自动动作排队两分钟后无 TTL，触发快照未执行前复核 | dispatch_one claim 前已有 preflight：当前权限、目标访问、参数/提交条件、固定动作定义/外部动作授权版本、映射、副作用检查。不能声称完全不复核。缺少队列年龄到期和原规则触发条件的新鲜度检查；network timeout 仅限制单次 HTTP，不是排队 TTL。 | 明确自动动作的有效期与“事件事实执行/当前条件仍满足才执行”策略；claim 前检查新动作固定有效截止、来源规则版本及必要当前条件，过期则阻止 POST 并给可核查结果。不能未经业务选择硬编码两分钟，不能把当前动作提交条件复核冒称原规则全部复核，也不能临时重扫所有规则。事件类原触发依据需明确可回溯 source/event snapshot；现有 run 的 rule_code/version 不等于完整事件输入快照。 |
| 5：对象歧义终态 failed、attempts0 不进入可干预异常 | delivery.preflight_failure 对身份歧义终态 failed、不增加 HTTP attempts，准确表示从未发送。exceptions.RETRY_ERRORS 已包括 EVENT_OBJECT_AMBIGUOUS，failed+attempts0 已允许人工 retry，且重排队重新核原快照/身份。实际缺口可能是 inbox.visible_events 使用 identity_rows 默认唯一投影，使物理受控表的歧义身份先从列表消失；authorize 本身已允许完整对象授权者对歧义作元数据处置。 | 将列表可见性与明确的完整对象授权保持一致：仅完整对象行授权使用非唯一 identity 投影纳入歧义异常；局部行权限继续唯一解析，不能暴露隐藏碰撞对象。修复身份后走原审计 retry，不把 attempts0 改成已发送或自动放行。外部镜像完整授权已有单独可见性路径，不能以此推断所有物理表也已覆盖。 |
| 7：规则新版本沿用旧版本去重窗口 | event_rule_runtime 与 scheduler 的 recent hit 查询只按 rule_id/dedupe_key/result/time，不过滤 rule_version；dedupe_spec 的“规则”token 是稳定 rule.code，而非版本。新事件/新调度若规则键未变会被旧版本命中压制。 | 若用户合同要求版本隔离，最小同时在事件/定时两条 recent-hit 查询增加 rule_version 限定，并定义描述性升版是否也重置窗口；不删除历史命中。单个原 event+rule 的已有结果幂等与入站发布锚点另有保护，不应为新窗口把原事件重新执行。跨版本合并如果是业务意图则应明确显示，不能默默假定。 |
| 12：事件规则读取当前对象字段与事件 payload 混淆 | event_rule_runtime 在 expression.columns 非空时通过 existing_row 读当前受控行，关联字段同样读当前授权目标；结果明确标 evaluationMode=current_object。仅事件字段时为 event_payload，读取持久事件原始载荷。ingress 保存的是事件定义/订阅快照与 payload，不是任意对象完整历史快照。 | 时间敏感的事实判断使用明确声明的 event.<field>，生产方须提供事件发生时值；当前状态守卫使用对象字段并清楚显示求值时刻/模式。若要求对象/关联在事件时刻可重现，需新增可信输入快照/版本合同，不能将后台实时 SELECT 偷换成历史快照或用当前值补造旧事实。 |
| 13：高频中间态事件不可恢复 | 现有 CDC 报告已明确是触发器日志按键通知后读取源当前态，不提供每个中间版本或跨表事务原子可见。若两次变化在读取前已覆盖，中间值从当前表无法重建。事件 API 若已收到独立事件/完整 payload 则会保存，不能说所有事件都不能重放。 | 需要保留每次转换时，源端提供持久 append-only change/outbox，保存提交身份、业务身份、前后值/版本，再以独立事件 ID 导入。只改重试、轮询频率或队列排序不能恢复未捕获内容；不篡改旧事件/补造历史。 |
| 14：循环复用单行去重没有轮次 | 当前“规则+对象”并选择“不重复”在该规则键上永久去重，状态绕一圈仍同一对象键；系统没有自动推断业务循环。现有 dedupe_spec 已可使用声明的 payload 标量，事件入站另要求独立 eventId/显式 dedupKey。 | 普通设备多次巡检应显式提供 cycle_id/inspection_id，循环内稳定、不同循环不同；按所需粒度组成规则+对象+cycle_id 去重，且每次事件有独立 eventId。同轮重投仍幂等，下一轮可再触发。不能以清空去重表、随机修改原事件 ID 或任意缩短窗口冒充轮次能力。 |

建议先修“失败事实传递”和“完整授权者异常可见性”，再由根代理明确版本窗口与自动动作有效期合同；时态输入及循环/CDC 则先明确生产事件契约，不能把缺失的历史模型包装成几行补丁。最小办公验证可用独立合成设备和两个 cycle_id：同轮重复事件不新增动作、次轮正常再命中；显式事件分数与后来对象当前值不同可验证 event_payload/current_object 区别；明确 FAILED 回执可验证原因/编号/告警日志。所有这些均为待授权方案，不能执行原军事记录、启动全局消费者，当前接收器仍处于已停止状态。

## 随后授权的最小修复：#2 / #5 / #7 / #10 / #11

根代理明确授权后已改产品代码，尚未将这些新增分支记为真实业务通过。未调用数据库、HTTP、业务动作或 worker，未处理旧队列、未修改旧记录或重启已关闭的办公接收器；此前单个 SUCCESS 回执通过证据不覆盖本节失败/歧义/版本分支。

- #2：`integration/execution.py` 复用已有 ReconcileResult.detail 传 submit/source/reason/code，外部 FAILED 在 run 保留失败原因及已有真实回执，不改变“已对账=已核实终态”的状态定义。共享 listener 保留负向查询回执，明确 not_executed，日志不再把直接 POST 回执伪称自动 GET。canonical 关联告警追加 action_result 失败事实与异常标记，不自动关闭、重试或覆盖其他关联动作。告警 API 追加 outcome/reason/extRef，原因/回执沿现有 action-center 读取权限限制；告警与规则关联动作显示实际“已对账 · 外部未执行”，保留原状态参与权限/在途判断。
- #10：只有真正 SUCCESS/FAILED、人工已核实对账、自动已核实对账或执行前明确失败终态才清 active unknown_reason；仍不确定、排队和继续查询分支保留原因。历史动作/告警日志不删除、不改写，重复同结论回调不重复追加事实。
- #11：共享 create_run 绑定 run.alert_id 和告警 canonical action_run_id；generic stage 幂等回放也核对并恢复同一 canonical 指针。绑定严格核同工作空间、同目标/规则；已有不同 canonical 或已有其他未绑定记录时拒绝覆盖。只有显式重提且父记录确为当前 canonical 失败记录的 retry 允许迁移指针。告警发起路径与通用 stage 先使用同 scope 告警暂存互斥，幂等旧 run 先锁 run 再锁 alert；告警非键更新使用 PostgreSQL FOR NO KEY UPDATE，避免与新动作 FK 的 KEY SHARE 互锁。未放松既有权限、确认人、目标和参数守卫，未自动修复历史关系。
- #5：仅完整对象行授权者在异常 inbox 中使用非唯一身份投影，能看见身份歧义且从未发送的 failed/attempts0 事件；局部行权限继续 unique-only，保留防隐藏碰撞泄漏。原人工 retry 的快照、路由和身份重核保持；未把 attempts0 改成已尝试或自动补投。
- #7：事件规则与定时规则的近期命中去重查询都限定当前 rule_version。每次规则升版的新事件/新调度使用自身窗口；旧命中记录不删。同一原 event+rule 的既有命中幂等返回、入站发布锚点及事件主键均未改变，不通过改版本重执原事件。

一个新增离线脚本 `backend/scripts/check_action_outcome_links.py` 执行真实共同创建/绑定、stage replay、对账/提交分支和异常可见表达式，验证 canonical 冲突及跨 scope/目标拒绝、受控 retry、run→alert 锁序与 NO KEY UPDATE SQL、失败 reason/code/receipt、告警不自动关闭、终态回调幂等、未知原因清理边界、完整/局部授权身份投影、两类版本去重 SQL。内存替身与 SQL 编译通过，无数据库/HTTP/规则运行。Actions.vue、Alerts.vue 与 Rules.vue 的新增结果显示仅做局部 SFC 编译并通过；没有跑 pytest/后台 tests 或全量检查。脚本首次发现测试替身缺 action_type_code 已补齐，新增锁模式及缺失 canonical 的第三条关联保护后定点重跑同一脚本通过；不把替身断言当真实并发/权限界面验证。

本节新增涉及 `dynamic/service.py`、`dynamic/schemas.py`、`integration/execution.py`、`event/exceptions.py`、`dynamic/event_rule_runtime.py`、`dynamic/scheduler.py`，以及前端 `api/dynamic.ts`、`ops/Actions.vue`、`ops/Alerts.vue`、`ontology/Rules.vue` 的必要实际结果展示。Actions 负向对账说明追加已有 failReason，不新增执行按钮或自动重试。QA 意图/链路与 worker 分项统计由其他代理负责，本子任务未改这些文件。

### #3 的最小后续设计（尚未写代码）

当前 ActionRun 可经 alert_id→Alert.hit_code→RuleHit 找到规则/版本/求值时间，但 RuleHit 没有 event_id，run 也没有直接 event/schedule 引用。事件投递 result 里虽有 hitId/actionId，但不应在每次 claim 扫 JSON 反查所有历史投递，更不能解析日志文字推断来源。

最小可复用现有 JSON `run.review_snapshot`：在服务器已知来源时保存 origin 的 eventCode/hitCode 或 scheduleRunId、规则版本、求值模式与时刻；不新增用户业务参数。若明确启用可配置 queueTtlSeconds，在入队时冻结有效截止，claim 前检查，不设置隐含两分钟默认、不重新解释已排队旧动作。原本没有完整 origin 的历史自动队列应明确转人工核对，不猜旧输入或任意重发。

claim 前只重核这一个来源/目标：原规则仍启用且版本与 origin 相符；事件 payload 模式只使用该持久原事件事实，当前对象模式才重读当前授权目标/关联字段；需要纯 predicate 求值则复用现有 expression/function/relation 构建器，不能调用带创建告警/动作副作用的 event dispatch，也不扫描全部规则。具体选择“事件事实仍有效”还是“当前条件仍成立”及 TTL 值须根代理确定后另行授权。当前没有实现或宣称此能力。

### #12–#14 的精确提示及接口合同建议（尚未改产品）

- 条件来源建议明确标为“对象当前值（求值时）”与“事件原始载荷（发生时）”；结果保留已有 evaluationMode/evaluatedAt，历史事实展示引用事件 occurredAt。接口不得用当前行补缺失的历史 payload，也不应将两种模式混称“历史回放”。
- CDC 提示建议：“仅同步源当前态，不保留读取前已覆盖的中间变化；完整转换历史需源端事件或审计日志。”没有可信旧事件时返回缺失/不可重现，不生成伪造中间态。
- 轮次去重提示建议：“不重复按当前规则版本和所选键永久去重；循环业务应将已有业务轮次字段纳入键，系统不从状态回环推断新轮次。”现有已声明 payload 标量可作为键，但本轮不新增业务字段或替生产方生成 cycle_id，不清空旧去重记录。

### #3 后续明确授权的实现（取代上节未实现设计，待真实 UI 验收）

根代理随后授权完整的单目标指标复核及可配置有效期。规则“命中后生成动作”增加有效期秒输入，整数范围 0–86400，默认 0 表示不额外设限。保存、草稿差异、发布验证和只读元数据均使用同一 validitySeconds 合同；不存在隐含两分钟默认值。

事件/定时规则在服务器已知来源时、执行策略前冻结 review_snapshot.origin：规则编号/版本、原 hitCode、目标、eventCode 或 scheduleRunId，以及由提交时间计算的 expiresAt。重提保留原来源与原截止时间，不延长已过期动作。没有可信 origin 的旧规则动作不会猜测来源或扫描投递 JSON：执行前明确失败，需人工核对并新发起；普通手动/问数动作未新增规则来源要求。

外部 dispatch_one 在既有 run→call 锁与 claim 事务内，先检查截止时间，再核原规则版本、启用开关、动作类型、canonical 告警/命中/目标和持久事件或定时运行快照。随后复用纯 evaluator 读取这一个目标的当前条件；原事件载荷仍来自同一持久事件，对象属性来自当前授权行。不存在唯一受控目标、谓词不成立、来源变化或条件不可核验均拒绝外发。配置和签名检查结束、标记 CLAIMED 前再次检查截止时间；拒绝为 failed、attempt 保持 0、无 HTTP，并向 canonical 告警追加失败事实，不关闭告警、不重发 unknown 外部结果。

单对象 evaluator 复用 existing_row(lock=False) 的唯一业务身份及行权限读取。指标 evaluator 新增仅内部可选 object_identity，SQL 在原工作空间/行权限与时间桶上进一步限制此业务编号，原公开调用省略参数时行为不变；禁止用整体快照代替单对象计算。关联读取复用原受控 FK/元数据缓存，通过当前 source_row 的外键限定目标，最多读取 2 行以拒绝歧义；不逐条反射、不全表构建关联查找、不创建新命中/告警/动作。已核所有 evaluate_metric、bind_metrics、规则 stage_action 调用点，默认参数与非复核调用保持原逻辑。

新增唯一离线脚本 `backend/scripts/check_rule_dispatch_guard.py` 验证 0/显式有效期、到期边界、旧来源拒绝、版本变化、条件不符、单目标唯一性拒绝、指标实际 SQL 的业务编号/权限限定与默认不限定行为、关联实际 SQL 的 source_row/行权限/LIMIT 2，执行通过。Rules.vue、RuleDefinitionEditor.vue、Connections.vue 仅局部 SFC 编译通过。未运行后台 tests/pytest、build 或数据库/HTTP；未启动服务/worker、处理旧队列或重启接收器。新增超时/条件变化分支尚无真实 UI 调用证据，不能将此前 SUCCESS 外联验收算作本分支通过。

### #12–#14 已落到实际操作点的说明

条件编辑器取值来源显示“对象当前属性”“关联对象当前属性”“事件入站载荷”，旁注说明对象/关联字段读取执行时当前值、事件载荷保留入站事实。去重键输入旁注明确规则版本隔离、循环业务应将已有轮次字段纳入键、每次事件使用独立 eventId。连接页“配置 CDC”说明按键读取源当前态不能保证保留读取间中间状态，需要完整转换历史应接保存每次变化的源事件或审计日志。本轮没有新增业务轮次参数、生成伪快照或宣称恢复全源历史；以上提示的真实浏览器显示仍由根代理后续核验。

### 动作升版后的历史参数展示回归（待重载真实验收）

根代理发现旧 S0004 成功外联卡在 recordExternalInspection 写回映射升为 v2 后，被显示成“原记录无参数/不匹配”。获授权仅对 scope16 单记录进行 SET TRANSACTION READ ONLY 核验：ACT-20261102-0001 仍固定 type_version=v1，结构化 params 与 params_text 均完整保留 score=90，当前动作版本为 v2；同工作空间发布快照 128/129 保留 v1。没有执行、重发、改写旧 run。

根因在共享 parameters_visibility：此前要求当前 ActionType.version 与 run.type_version 相等，升版即脱敏 params，随后 QA 严格身份比较将隐藏参数误判为空。修复读取层按 (typeId,typeVersion) 批量查询固定历史发布定义，查询显式限定当前 ontology_id、版本及每组最新一份快照；不以当前 v2 替代 v1、不按每条记录重复查询。继续核当前操作对象行权限与引用可见性，历史快照缺失/不完整或引用不可见时仍隐藏。执行用 execution_definition 的版本不符拒绝守卫不变，历史可见不表示可按旧版本重执行。

新增一个离线脚本 `backend/scripts/check_historical_run_visibility.py` 通过：固定 v1 在当前 v2 下可读、同版本两记录仅一次元数据批量查询、SQL 明确 scope16/v1/LIMIT1、原结构化参数不修改、缺快照不借新定义、当前行权限拒绝及隐藏对象引用仍隐藏。仅内存替身与 SQL 编译，无业务调用/后台 tests。QA 的参数不可用明确提示交由矩阵代理 UI owner 接线，未修改 QA chain 或旧卡记录。旧卡真实恢复与新增 FAILED 调用结果仍待根代理验收。
