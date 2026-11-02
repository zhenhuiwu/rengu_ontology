# 部署与联调操作补充

本次已在获授权的本机平台库备份后应用唯一目标增量 `data_0048_mirror_scan_visibility`，并正常重启API；未运行后台测试、worker、队列schema安装或真实外部请求，没有改写用户 `.env` 或数据库连接配置。下文部署命令仍供部署者在自己的环境审阅后执行，本机完成不代表部署机已完成。

## 本机实际执行记录

2026-10-31先核对目标为本机 `rengu_ontology`，使用现有Python环境及PostgreSQL 16工具做完整custom格式备份：`exports/database/rengu_ontology_before_data_0048_2026-10-31_061239_354564Z.dump`，2,153,693字节；SHA256 `9150172c72f7e022bd554b2f07a4de78e0293245652a0807de643167aa396f61`。只用`pg_restore --list`核对结构及数据清单，没有还原或降级测试。

实际仅执行明确revision：`admin_0047_login_sessions` → `data_0048_mirror_scan_visibility`。两列及复合索引元数据存在，`is_current`非空且新行默认false，物理镜像行数76→76；未重写或删除业务记录。API使用原本的backend/.env和正常lifespan重启，单次`/readyz`返回200，前端进程保持运行。未启动事件worker、outbox或真实镜像同步。部署机仍需独立备份和应用，不能导入本机dump覆盖部署数据。

## 本地联合管理入口

新增 `backend/run_dev.py`，沿用当前Python环境和既有API/事件模块入口，无额外依赖。默认与 `--api-only` 都只运行单个API进程；不自动启动事件消费、不安装依赖/schema、不执行迁移或seed。

```text
python <项目绝对路径>/backend/run_dev.py --api-only
```

选择联合管理时必须明确工作空间、事件类型、唯一订阅和上线前事件主键高水位：

```text
python <项目绝对路径>/backend/run_dev.py --with-worker --ontology <工作空间ID> --event-type <事件类型编码> --subscriber <订阅名称> --after-id <高水位>
```

子进程固定在backend目录；API通过绝对路径 `--env-file` 加载backend/.env，worker CLI在导入应用模块前读取同一绝对路径，均保留进程环境变量优先级。脚本不输出配置值。Ctrl+C或一方退出会结束整个联合启动组，避免API已经退出但worker仍独自派发。

`--with-worker` 复用所选单订阅轮询，不使用通用sweep，不扫描高水位之前的记录。高水位必须先只读核实，不能随手填0；该模式不处理多订阅事件，不承诺API自动全量投递。它直接使用已迁移的业务表，不依赖Procrastinate周期任务schema。API现有的、已获授权的镜像定时同步协程仍按原生命周期运行，API-only只表示没有联启事件worker。

## 生产worker与首次安装

API与事件worker分别部署，事件worker保持单实例，不随API进程数扩容。后台周期任务原有 `event.deliver` / `outbox.dispatch` 任务锁、对象顺序锁、投递行锁和“先持久化processing再执行”行为保留。API不内嵌事件worker，不会因多个Uvicorn API进程重复登记派发。

2026-11-02调度修复升级时须先正常停止旧版全部周期worker，再换新版并按原命令启动，不能新旧混跑。新版不新增schema；用原生心跳和任务归属advisory锁筛选失联周期外壳，120秒门槛、启动及30秒巡查、仅当前登记队列/任务白名单，标failed保留记录，不重放具体业务或未知外部写入。事件worker仍单进程，但内部固定2个槽位且两个原任务各自全局锁仍在；订阅公平另由候选分轮/配额保证，不以并发数字冒充。细节及离线证据见[恢复与公平选择](26-11-02-worker-recovery-fairness.md)。这些新代码本轮没有启动真实worker或处置既有队列。

在backend目录、已准备依赖和业务迁移的环境中，首次只安装队列schema：

```text
uv run python -m app.modules.event.worker --install-schema
```

该命令不运行业务迁移、不消费事件；已安装时直接返回。没有schema而直接启动通用worker时，现在会明确提示这条一次性安装命令。后续升级队列schema仍按Procrastinate既有升级流程处理，不在API启动时隐式安装。

经过积压预览和明确处置后，独立通用worker命令为：

```text
uv run python -m app.modules.event.worker
```

这条通用命令会消费全部工作空间符合条件的现有事件和outbox，因此不能用于“只验证新事件”。旧权限失败与超时等待已有代码保护；其他无错误的历史pending遵循原队列行为。上线需要暂不消费积压时，保持通用worker关闭，使用固定高水位selected模式；历史记录逐条复用既有disposition处置。详见[事件修复与保留数据升级](26-10-31-event-integration-fix.md)。本次没有启动上述任一命令。

## 固定业务时钟不能冻结运行期限

静态核查确认：公共 `CreatedColumnsMixin.created_at` 的应用默认值为 `clock.now()`，SQL服务端 `now()` 只是直接写SQL时兜底；原先事件等待、HTTP重试next_at、processing恢复判断也使用同一业务时钟。显式 `RENGU_FIXED_NOW` 会让旧机制的期限停滞。

事件投递操作现在统一使用模块内 `execution_now()` 的真实UTC：新投递记录显式创建时间、next_at、预检15分钟期限、processing开始/结束与5分钟中断恢复、人工重排队及selected轮询到期判断保持同一时间源。业务事件occurredAt/入站定义快照不改变。旧记录按其保留的创建时间判断，到期先结束并要求人工选择，不因更换时钟而静默重放。

compose仅去掉API环境中硬编码的 `RENGU_FIXED_NOW` 演示常量，保留原全部数据库、端口、卷与账号配置。用户自己的.env不会改写。生产部署仍应核对没有显式启用演示时钟，因为其他业务模块仍按统一业务时钟解释“今天”等含义；不能把本次事件期限修复视作其他模块自动兼容冻结时钟。

## 角色绑定增量接口

| 方法 | 路径 | 语义 |
| --- | --- | --- |
| GET | `/api/ontology/v1/workspaces/{ontologyId}/role-bindings` | 查询当前绑定与角色选项 |
| PUT | 同路径 | `{roleCodes:[...]}`完整替换；遗漏项会删除，语义保持 |
| PATCH | 同路径 | `{add:[...],remove:[...]}`仅变更提及的角色，其他绑定保留 |

PATCH的add/remove各最多200项；未知角色、同时添加和移除同一角色会拒绝。重复项去重，已绑定角色不重复添加，移除未绑定角色是幂等操作。PUT与PATCH锁同一工作空间行以协调并发。沿用 `require_owner`、成员归属、本体管理权限、路由挂载的只读守卫和API Key范围守卫；不更改用户所属角色、角色本身的对象授权或跨空间权限。

## API密钥管理实际挂载

API Key管理始终挂在 `/api/admin/v1`：

- GET/POST `/api-keys`；GET `/api-keys/meta`。
- GET/PATCH `/api-keys/{id}`；GET `/api-keys/{id}/calls`；POST `/api-keys/{id}:revoke`。
- GET/POST `/service-accounts`；PATCH `/service-accounts/{id}`；POST `/service-accounts/{id}:revoke`。

没有在ontology前缀增加兼容重复路由。路径正确仍须满足管理权限；业务API的scope限制不因管理路径说明而放松。任何密钥创建/轮换仅由用户在已有管理页明确操作，本次未调用这些写接口。

## requestId关联核查结论

`RequestIdMiddleware`已从 `X-Request-ID` 读取或生成标识，以contextvar传递；响应头、统一错误体与未处理500都携带相同标识。`setup_logging()`的handler filter读取该contextvar，应用日志格式含 `[request_id]`。`main.create_app()`已经安装它们，本次未改动已有效的实现，也未把堆栈、SQL或凭据加入客户端错误响应。

异步worker执行不在入站HTTP请求上下文中，requestId默认 `-`；按worker日志中的投递ID/事件ID和只追加的attempt/disposition历史关联。worker CLI补用现有setup_logging统一日志格式，不伪造原HTTP上下文。排查HTTP错误先用返回的requestId筛应用日志，再定位事件/投递记录；不要通过向浏览器开放堆栈解决关联问题。

## 留存验证

源码验证只执行Python静态编译与差异空白检查，未执行后端测试。必要功能增量应用后的操作核验仅限当前revision、列/default/索引、物理镜像行数及一次API readyz，不属于迁移测试套件。保留最小回归覆盖默认API-only/限定worker命令、增量绑定不触碰未列角色、add/remove冲突，以及冻结业务时钟时投递期限继续推进；这些回归未运行。前端检查由主任务统一执行，本阶段不重复。
