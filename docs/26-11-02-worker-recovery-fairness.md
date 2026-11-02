# 2026-11-02 周期外壳恢复与订阅公平选择

对应第三轮问题1与8。主会话授权最小修复调度层与候选选择，没有授权执行现有队列；本轮没有数据库、HTTP、模型、worker、pytest或后台全量检查。指定 Downloads/docs 候选路径未找到第三轮 Markdown 文件，问题范围依据主会话提供的截图文字与后续精确指示。

## 根因与保护范围

安装且锁定的 Procrastinate 3.10 已有10秒worker心跳和失联worker清理，不是完全没有心跳。但其 `prune_stalled_workers` 仅删除worker，job外键置空，doing与唯一lock索引仍在；项目缺少已知周期外壳的终止恢复。QA评测原有 running 业务恢复只覆盖其特定业务状态，不等于所有周期任务锁恢复。

原事件worker把事件 sweep 与站内订阅 outbox 放在同队列且仅1槽，长sweep会阻塞另一任务。内部 `EventDelivery.due` 又全局 next_at/id 前200；页面通知全局 id 前100，高频订阅旧积压可能占满候选。相同优先级的库队列按id取任务，不据此声称发生了严格优先级的永久饥饿；本轮修复的具体对象是外壳阻塞和订阅批次分配。

## 最小实现

- `app/jobs/recovery.py` 的共享 `ReliableApp` 只接入库公共 `run_worker_async` 与 `worker_middleware`。六个既有builder采用它，CLI、队列名、注册任务名不变，没有额外schema或恢复队列任务。任务执行用独立队列连接持有 jobId advisory session锁，不占业务SQLAlchemy连接池；释放失败关闭连接，不让不确定的锁回池。
- 启动及后台每30秒检查当前app真正登记的周期任务、当前消费队列与固定项目白名单的交集。通过公共 `get_stalled_jobs` 获取候选后，在同事务锁定并重读job和worker，再核120秒心跳门槛；worker记录丢失时须有超过120秒的started记录。doing之外、无可信时间、未知任务/队列、非周期登记、带具体业务参数、有效或未来心跳全部保留；advisory锁仍被持有也保留。
- 确认失联外壳才调用正式 `procrastinate_finish_job_v1(..., 'failed', false)`，释放其doing lock但保留记录与正式状态事件。只记录 queue/task/job 警告，不打印参数、凭据或错误正文。不 retry、不重新执行当次函数体，不改EventDelivery/ActionRun等业务状态；下一正常周期继续遵守各业务的claim/人工核查/幂等保护。
- 事件worker固定2槽，两个既有任务各自全局锁仍在，因此事件sweep不能同时占两个槽位。该变化只隔离两类外壳，不作为订阅公平的证据。
- 事件候选按 `(Event.event_type_id, subscriber_name)` 分组，用原 `next_at NULLS FIRST, id` 排组内顺序，再按轮次、最近真实attempt开始时间、原顺序取总200。每订阅第一条先于其它订阅第二条，批次间优先近期较少服务者。没有修改prepare、对象ordering、失败前序、发布版本、身份、行权限或真实执行函数。
- 页面站内outbox按当前ontology及pageId分组，每页仅取最早未投递头一条，总批100；按最近成功或失败重试的服务时间排序。最早头未来才可重试时同页后续不越过；外层保留 available_at、FOR UPDATE SKIP LOCKED，接收人权限、去重、失败回滚/退避保持原样。

两个候选查询复用现有表和索引，最近服务时间会聚合保留历史，没有声称查询已提速或在大保留量下达到某吞吐；若这一步成为瓶颈，再做持久订阅游标及相应增量索引，代码已标注这一限制。

## 唯一离线自检

在backend仅执行一次 `.venv/Scripts/python.exe scripts/check_periodic_recovery.py`，返回：

```text
Periodic orphan guards and bounded subscription-selection contracts passed (offline; no DB/worker/HTTP).
```

自检以实际恢复函数和受控内存connector验证：失联白名单doing及可信旧started孤儿可终止；候选读取后心跳已更新、活advisory归属、未知任务/队列、具体业务参数、无started记录、todo及未来心跳均保留；正常执行期间持有归属，迟到失去归属时不调用业务。还编译实际 due/outbox 查询，检查分组、轮次、总批上限、due条件、头阻塞与行锁结构，并给高频201条/低频1条的关系选择例。

它没有连接数据库执行SQL、启动真正Procrastinate worker或杀进程；关系例和SQL编译不能替代真实吞吐、跨进程锁竞争、重启/崩溃及通知投递验收。本轮也未重复已有业务执行或队列积压处理。

## 部署与未验证项

**先正常停全部旧版周期worker，再部署、按原命令重启；禁止旧版无归属锁进程与新版混跑。** 这是保护遗留worker的升级前提，不通过120秒就断言旧版进程必死。API-only和限定单订阅/高水位启动入口保持原范围，不会因此自动开启通用周期消费；schema-only命令仍不运行恢复或消费。

本轮没有启停服务，没有动既有doing任务或记录，没有安装schema或运行迁移。新增保护与公平选择当前只具上述离线证据；真实隔离队列崩溃恢复和公平投递还需独立、获授权的合成环境验收，不得直接消费旧队列补证。最终提交manifest继续pending，不能沿用新问题出现前的哈希。
