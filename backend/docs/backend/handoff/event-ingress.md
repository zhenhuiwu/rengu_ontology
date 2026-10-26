# 内部事件接收（2026-10-26）

`POST /internal/v1/events` 已实现真实数据库接收；没有投递执行器、外部 HTTP 或消息发送，也没有后台空 handler。收到事件不代表投递成功。

认证独立于开发期用户：配置 `RENGU_INTERNAL_EVENT_TOKEN`（Settings SecretStr），生产方发送 `Authorization: Bearer <内部凭证>`。未配置503 `EVENT_INGRESS_UNAVAILABLE`，无效或缺凭证401 `UNAUTHORIZED`；不读取X-Demo-As，不依赖默认管理员。不要把实际凭证写进文档、日志或版本库。带ver/previewAs的请求先统一403 READONLY。

请求：`{eventId,eventType,occurredAt,source,objectType,objectId,payload,dedupKey?,traceId?}`，返回原合同 `{eventId,deduped}`。响应只是持久接收/去重结果，不是订阅方回执。

- eventId为生产方全局编号（1..64），eventType为唯一当前code或api（1..64）；未知404、歧义/未发布/下线409。objectType须匹配实际事件绑定的objectApi或objKind，objectId保留生产方ID，不编造对象名称。source为真实生产来源标识（1..128），traceId可选（1..128），dedupKey可选（1..200）。occurredAt需时区信息。
- 原始HTTP信封最多64 KiB，在读取流时先限大小再JSON解析；JSON最多16层/2048总值/每容器512项。拒绝NUL、非法Unicode、非有限数、额外信封字段及不符合已登记必填/基础类型的payload。字符串/文本/枚举为JSON字符串，整数/金额/百分比为JSON数值，布尔为布尔，日期/日期时间为ISO文本；未支持的字段类型配置409，不猜转换。
- 按eventId取事务咨询锁；相同完整信封重放返回原deduped，不新增事件。不同信封复用ID409 EVENT_ID_CONFLICT；旧seed/未知来源记录没有信封指纹也拒绝冒认重放。
- 去重键缺省时只有空规则或“事件 ID/eventId”可直接用eventId；其他中文说明模板不是可执行表达式，须生产方显式计算dedupKey，缺少422 EVENT_DEDUP_KEY_REQUIRED。窗口支持正数秒/分钟/小时/天和永久，可带既有中文括号备注，无法执行的配置409 EVENT_DEDUP_CONFIG_INVALID。
- 取得真实EventType FOR SHARE行锁，在锁内读payload与当前发布头；发布更新类型及版本时会被互斥，避免类型/版本混读。再按type+dedupKey咨询锁，event和event_dedup同事务写入。窗口从最近一次实际接受时间起算，命中不延长窗口，等于截止点即可接受下一条。永久状态不随event保留期删除；没有实现任何自动保留期删除任务。
- 窗口未命中新事件为pending/待投递，订阅方数0、latency_ms=null；命中新ID保留真实事件并记deduped/已去重，订阅方数0、延迟仍未知。不会创建event_delivery或虚构回执/执行时间。

迁移：`event_0009`接`functions_0008`，加接收元数据可空列、pending、nullable latency/64位objKind和极简event_dedup；`event_0010`接event_0009，加nullable release_id。只记录新接收时真实发布版本，旧seed没有回填。无历史处理时延返回null，p95只取真实非pending延迟；stats新增pending独立数量，不能把pending当投递成功。前端真模式显示待投递和未知延迟/版本，mock值不变。

验证：`tests/test_event_ingress.py`包含独立凭证/只读、完整元数据/发布版本、无假投递、重放冲突、固定/永久去重、恶意JSON/大小/深度/坏配置无写、真实双连接ID与业务键锁、类型发布锁及事件/去重状态同时回滚。`tests/test_action_event_runtime.py`保留历史seed对拍，空延迟的p95改为null；`node scripts/check-event-ingress-real.mjs`实跑事件抽屉resolver检查pending/未知时延与真实来源版本。开发库只由root部署迁移和配置凭证，没有灌接收测试事件。
