# 受控远程纯计算函数（2026-11-03）

新增 `remote_http` 执行模式，复杂算法和模型服务在平台外运行；平台不开放 Python 导入、`exec`、文件或任意网络访问。原有本地解释器及历史注解语义保留。

## 部署配置

服务端通过被忽略的 `.env` 或进程环境设置 `RENGU_FUNCTION_TARGETS`（JSON 数组，默认 `[]`）。每项是固定本体的纯计算合同，例如以下**合成示例**，须替换为真实部署配置；不要把凭据提交到仓库：

```json
[
  {
    "ontologyId": 17,
    "key": "office_score",
    "label": "办公合成计算",
    "revision": "r1",
    "url": "https://compute.example.invalid/calculate",
    "networks": ["192.0.2.0/24"],
    "token": "<仅在服务器配置的服务凭据>",
    "timeoutS": 10,
    "pure": true,
    "inputSchema": {
      "type": "object",
      "properties": {"value": {"type": "integer", "description": "待计算数值"}},
      "required": ["value"],
      "additionalProperties": false
    },
    "outputSchema": {"type": "integer"}
  }
]
```

`url` 是服务端精确登记的 HTTPS 地址，不能包含凭据、查询串或片段。DNS 所有解析地址须落在登记 CIDR 内，实际连接固定解析 IP，拒绝云元数据地址，禁重定向与环境代理。TLS 复用服务端 `RENGU_INTEGRATION_CA_FILE` 私有 CA 配置。仅合成回环验证可显式设置 `allowHttpLoopback=true` 使用 HTTP 字面回环地址；不支持把这个开关用于远端 HTTP。

同一 `key` 可以保留多个明确 `revision`，以服务旧函数版本。地址、网络、超时或 schema 变化须登记新修订；函数快照会核对部署绑定摘要，变更不匹配时明确拒绝。凭据可以轮换，不进入快照、浏览器、URL 或错误消息。修改部署环境后按现有方式重启 API/worker，不由函数页面写配置。

Schema 支持 `object/array/string/integer/number/boolean/null`、`properties`、`required`、`additionalProperties:false`、`items`、标量 `enum` 和说明。对象须明确字段并拒绝额外字段，不支持 `$ref`、代码或任意 JSON Schema 扩展。最多 128 个 schema 节点、8 层、每对象 32 个字段；值仍受 64 KiB、深度和容器预算约束，不把字符串或布尔值强制转成数值。

## 服务协议与平台入口

平台发送一次 POST，包含 `X-Ontology-Id`、请求 ID，以及配置的独立 Bearer 服务凭据；不转发用户或平台 API Key。请求体示例：

```json
{"functionId":"FN-001","version":"v1.0.0","revision":"r1","input":{"value":3}}
```

成功响应必须为 HTTP 2xx、`application/json`，并且严格包含：

```json
{"revision":"r1","output":6}
```

平台校验修订、输出 schema 和响应大小。排队、DNS、连接及响应共用超时，超时取函数定义和目标配置的较小值（上限 60 秒）；出站并发最多 8。HTTP 错误、非法合同、连接失败和超时均真实返回错误，**不自动重试 POST，不使用示例代替结果**。服务应保证固定修订、同输入的稳定纯计算，不执行业务写操作。

函数页选择“受控远程纯计算”及获准目标/修订，合同由服务端提供，测试输入由字段表单生成，无需填写 URL、密钥或配置 JSON。保存、锁、引用兼容校验、历史及回滚沿用原流程，合同存入不可变 `FnVersion` 快照，不新增数据库列。未登记目标时页面明确显示不可用。

`GET /api/ontology/v1/function-targets` 只返回当前本体可选目标及公开合同。现有 `functions/{code}:test` 和 `machine/functions/{code}:invoke` 保留响应形状，新增可选 `version`；不传时使用当前定义，生产调用宜明确版本。机器调用仍要求 Bearer、`X-Ontology-Id`、`functions:invoke` 及函数白名单。动作固定版本调用和规则 `rule_fn` 共享同一异步内核，另接统一用途/标记策略守卫，没有绕过权限的降级分支。

规则远程调用默认整轮最多 32 次、20 秒，可用 `RENGU_FUNCTION_RULE_MAX_CALLS` 和 `RENGU_FUNCTION_RULE_TIMEOUT_S` 收紧；硬上限为 32 次、30 秒。试跑候选数乘调用节点超过预算时在发送前拒绝；过滤与主条件共用预算，禁止按上万行无限扇出。旧本地规则保留原 2 秒纯计算预算。长时 GPU 作业、任意代码上传沙箱和有业务写副作用的函数不在此同步通道内；业务写仍走动作确认、幂等与未知结果对账。

规则条件可从固定输出 schema 选择标量返回字段，例如 `rule_fn("FN-001", "v1.0.0", 对象.数值)["result"] > 3`。嵌套对象逐级选择明确属性；数组不推测固定下标，也不能直接把对象或数组与数值比较。路径和比较值类型在固定版本合同下校验，执行时缺字段或类型不符明确报无法求值，不当成条件不满足。测试、机器及动作调用仍得到原始完整输出。`check_rule_function_outputs.py` 已离线验证该边界；真实规则页面验收另记。

## 本轮验证边界

`backend/scripts/check_remote_functions.py` 使用真实执行内核及 httpx 内存传输，验证输入/输出严格类型、修订与本体隔离、绑定变化阻断、共享测试/机器/动作/规则入口、规则预算、取消、响应限额、无第二次 POST、旧本地注解兼容。它不连接数据库、不打开 socket，也不等于真实外部服务、权限策略或模型推理已验收。前端类型检查通过；真实平台 API/UI 验收须在部署目标和统一权限策略加载后另记。

## 本地合成接收器（手工验收用）

新增 `backend/scripts/local_function_receiver.py`，仅绑定 `127.0.0.1`，不自行启动。先把随机凭据保存到仓库之外或已忽略的私有文件，再从 `backend` 运行：

```text
.venv/Scripts/python.exe -m scripts.local_function_receiver --port 8769 --ontology-id 17 --revision fixture-1 --token-file <私有凭据文件绝对路径>
```

也可由进程环境 `RENGU_FUNCTION_FIXTURE_TOKEN` 提供凭据。对应部署目标为 `http://127.0.0.1:8769/calculate`、`networks:["127.0.0.1/32"]`、`allowHttpLoopback:true`、`revision:"fixture-1"`，其余配置沿前文；输出 schema 改为 `object`，仅含必填整数 `result` 并禁止额外字段。该合成接收器只计算 `result=value*2`，输入 3 应得到 `{"result":6}`，校验 Bearer、本体和修订，不打印凭据或请求体、不接业务数据。它不能替代实际算法/模型服务验收。
