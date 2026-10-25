# 问数评测题库 EV-Q（SQL 级）v0.4 草稿

> 题目文件：`backend/seeds/extra/qa/eval/questions.json`（PDE 定的目录，v0.1 在 `qa_eval/`，已 git mv），共 100 题，编号 EV-Q-001 至 EV-Q-100。
> v0.2 改动：按静态本体负责人对指标口径的裁定（来源是前端 mock）重写了 gmv、含税销售额、出库及时率的 gold；同义词改为以语义映射为准；按 PDE 和本体小白的裁定改了越权拒答、会员题、表名列名、文件目录，每题加了 `kind`。
> v0.3 改动（静态本体负责人裁定，以前端为准）：凡读 `biz_order` 的 gold 和备选 SQL 都加 `not o.is_test`（测试单默认过滤）；有效订单数和出库及时率改按出库时间；GMV 确认是 gmv 的同义词，EV-Q-067 由反问改为作答。
> v0.4 改动（对齐本体小白骨架 `72204b0` 的 `app/modules/biz/models.py`）：`area_sqm` → `area`；下单时间 `created_at` → `created_at_biz`（`created_at` 是行审计时间，种子里全是同一时刻）；订单、门店状态改用库里的英文码（`'已出库'` → `'shipped'` 等）；`inferredCols` 只剩骨架自己标 [推断] 的 `store.big_region`、`store.is_test`；改了 58 条 gold、4 条备选。
> 状态：**gold SQL 已在骨架真实结构 + 真种子上跑过**。库是本机临时 PG 17（会话时区 Asia/Shanghai），用骨架自己的 `alembic upgrade head`（0001–0003）建表，`python -m app.cli seed -m admin`、`-m biz` 灌种子（门店 95、商品 12、订单 1286、订单行 3905、用户 128）；100 题的 80 条 gold 和备选 SQL 全部跑通，`scratchPreview` 已用这次结果覆盖。另外 80 条 SQL 全部过了骨架 `sql_guard.inject_row_filters`（不限制 / 华东一区范围 / 全拒三种身份都能执行；不限制时结果跟原 SQL 完全一致；EV-Q-025 注入华东一区后跟 gold 一致）。

## 0. 业务表结构

列名、类型以骨架 `72204b0` `app/modules/biz/models.py` 为准；骨架自己标 [推断] 的只剩 `store.big_region`、`store.is_test`。四张表另外都有 `MutableTableMixin` 的 `created_at`、`updated_at`、`created_by`、`is_seed`、`lock_version`（审计列，题库不用）。

| 表 | 列（已确认） | [推断] | 说明 |
|---|---|---|---|
| `store` | `id bigint` PK；`code`（门店编码 SH0031，UQ）；`store_no`（SH-PD-0031）；`name`；`city`；`region` 存区域**名称**（华东一区…五区）；`format`；`manager`（店长姓名原文）；`area numeric(10,1)`（㎡，6 店为空）；`opened_at date`；`status`（**英文码**：`open` 营业中 / `renovating` 装修中 / `closed` 已闭店） | `big_region`（由区域派生，华东一区 → 华东，种子只有 `华东`）；`is_test boolean`（测试店，种子全部为否） | 来自 `ALL_STORES`（95 家）。大区题用 `s.big_region = '华东'` |
| `product` | `id bigint` PK；`code` = sku（UQ）；`barcode`；`name`；`spec`；`category`（“一级/二级”路径串）；`brand`；`price numeric(12,2)`（含税单价）；`unit` | — | 来自 `PRODUCTS`（12 个）。一级品类用 `split_part(category,'/',1)` 取；称重生鲜的 brand 是 `--`。没有 `is_test` |
| `biz_order` | `id bigint` PK；`code`（SO…，`WHERE NOT is_seed` 部分唯一，另有 `(code, created_at_biz)` 唯一；详情页取 id 最小的那条）；`store_id → store.id`（下单门店）；`channel`；`status`（**英文码**：`pending_payment` 待支付 / `picking` 拣货中 / `pending_shipment` 待出库 / `shipped` 已出库 / `delivered` 已签收 / `cancelled` 已取消）；**`created_at_biz timestamptz`（下单时间）**；`shipped_at timestamptz null`（出库时间）；`amount`（实付，含税）；`discount`（订单级优惠）；`warehouse`；`waybill`；`member_phone`（脱敏，null 为散客）；`is_test boolean`（测试单，`objectProps.ts:45`“默认过滤掉”，种子全部为否） | — | 来自 `ORDERS`（1286 单）。mock 有 2 个订单号重复（SO202610240889017、SO202610220914198），都是种子行。**`created_at` 是行审计时间，不是下单时间** |
| `order_line` | `id bigint` PK；`order_id → biz_order.id`；`line_no`；`product_id → product.id`；`qty numeric(12,3)`；`price numeric(12,2)`（含税单价） | — | 来自 `ORDERS[].lines`（3905 行）。mock 没有行级不含税金额、行优惠、行出库时间，见第 4 节。没有 `is_test` |

**跟 v0.3 第 0 节（和已提交的 gold）对不上的列**，v0.4 已全部改掉：

| v0.3 写法 | 骨架 `72204b0` | 不改的后果 |
|---|---|---|
| `store.area_sqm` | `store.area` | EV-Q-009、EV-Q-020 直接报错 |
| `biz_order.created_at` 当下单时间 | 下单时间是 `created_at_biz`；`created_at` 是审计列，种子里全部 = 2026-10-25 15:42+08 | 按下单时间的题静默算错（时间窗全中或全不中），不报错 |
| `status` 存中文（`'已出库'`、`'已取消'`、`'营业中'`） | `enum_column` 存英文码（见上表） | 状态过滤静默匹配 0 行，不报错 |
| `biz_order.is_seed` 列名 [推断] | `MutableTableMixin` 的 `is_seed`，已确认 | — |
| 其余非主键列 [推断] | 除 `store.big_region`、`store.is_test` 外全部已确认 | — |
| “表名前缀 bz_”注释冲突 | `72204b0` 的文档字符串已没有这句 | — |

旧 gold 在骨架库上：2 题报错、45 题结果跟临时库不同；改完后 80 条全部跑通，结果跟 v0.3 临时库除状态显示成英文码外完全一致（EV-Q-093–095 只是无序分组的行顺序不同）。

- **没有会员表**（本体小白确认）。订单上只有脱敏的 `member_phone`，会员类问题一律拒答（`NO_DATA`）。
- **`store_sales` 不建表，题库也不用。** 来源：`src/mock/objects-qa.ts:60` 的 `storeSales()`（注释写“门店 9 月销售额（不含税，万元）与环比”），在 `objects-qa.ts:92` 作为门店对象的一列。数值要么取自 `src/mock/qa.ts:121` 的 `QA_EC_STORES = buildStores()`（`qa.ts:108-119`，`seeded(9038)` 随机生成），要么按门店 id 哈希随机生成。它是 9 月数据，订单只覆盖 10 月，数值也不是从订单汇总来的，所以**做不成订单上的 VIEW**。题库里没有任何 gold 依赖它。
- 种子里没有、题库因此跳过或拒答的：会员表和会员等级、退款单、成本、履约门店、发货单 shipment、门店直营/加盟、商品上下架、订单履约方式。

## 1. 目的

- 已有的 EQ-NNN 题库（`qa_eval_question`，6 个分组）比对的是**语义计划**。本题库比对的是**最终结果集**，同时检查系统的行为：该反问时反问，该拒答时拒答，解释要跟 SQL 一致。
- 试跑时两个模型都是 31/34。漏掉的 3 题（#13 日期格式、#16 解释和 SQL 不一致、#23 按名字分组），加上 7 道行为题全部没处理好，正是本题库重点覆盖的地方。
- 网关硬规则照 PDE：只允许单条 SELECT（sqlglot 校验），只读账号，身份取自会话，行过滤由网关 `sql_guard.inject_row_filters` 注入（订单一跳到门店，订单行经订单继承），同义词来自静态本体的语义映射 semantic-map（静态 §3.7）。

## 2. 分类与数量（共 100）

| category | 含义 | 题数 | 其中 answer / clarify / refuse |
|---|---|---|---|
| `agg` | 基础聚合 | 12 | 12 / 0 / 0 |
| `path` | 沿声明路径的筛选和关联（门店、区域、品类、发货仓；行过滤后作答） | 14 | 13 / 0 / 1 |
| `time` | 时间，含上月、近7天、近30天、本周等相对时间 | 14 | 14 / 0 / 0 |
| `caliber` | 口径：含税和不含税、指标定义、时间字段、草稿指标 | 12 | 10 / 0 / 2 |
| `synonym` | 同义词和口语问法（以语义映射为准） | 11 | 10 / 1 / 0 |
| `clarify` | 有歧义，应先反问 | 9 | 0 / 9 / 0 |
| `refuse` | 拒答：非 SELECT、越权、绕过行过滤、超出范围、会员类 | 13 | 0 / 0 / 13 |
| `explain` | 解释和 SQL 一致（试跑 #16） | 6 | 6 / 0 / 0 |
| `groupkey` | 按 id 分组而不是按名字（试跑 #23） | 4 | 4 / 0 / 0 |
| `datefmt` | 日期格式（试跑 #13） | 5 | 5 / 0 / 0 |
| 合计 | | **100** | 74 / 10 / 16 |

v0.1 → v0.2：EV-Q-031（会员数，原 time）和 EV-Q-092（按会员分组，原 groupkey）改为 refuse；EV-Q-058（外卖）改为反问。

v0.2 → v0.3：EV-Q-067（GMV 是多少？）由 clarify 改为 synonym 类作答题。

## 3. 固定的 now 和相对时间

评测时 now 固定为 **2026-10-25 15:42:00（Asia/Shanghai）**，不读系统时钟。窗口一律左闭右开，凡是“到今天为止”的窗口，上界都是 now。

| 说法 | 窗口 |
|---|---|
| 今天 | 10-25 00:00 至 now |
| 昨天 / 前天 | 10-24 全天 / 10-23 全天 |
| 近 N 天 | 含今天在内的 N 个自然日，下界是 (10-25 − (N−1) 天) 00:00，上界 now。滚动 N×24 小时写在 `goldSqlAlt` 里，也算对 |
| 近24小时 | now − 24h 至 now |
| 本周 / 上周 | 周一起算：10-19 至 now / 10-12 至 10-19 |
| 本月 / 上月 | 10-01 至 now / 09-01 至 10-01 |
| 今年以来 | 2026-01-01 至 now |

种子订单只覆盖 2026-10-22 03:10 到 10-25 15:38，所以“上月”“上周”的计数结果是 1 行 0，“第一季度”的销售额是 1 行 NULL（`sum` 没有匹配行，EV-Q-029），近7天、近30天、本周、本月的结果完全一样。光看结果集区分不了时间算没算对，这几题另加 `timeWindow` 检查（见第 5 节）。另外有 8 单的出库时间晚于 now（mock 生成的），按出库时间算的窗口一律卡在 now。

## 4. 口径约定（gold 的写法）

口径以静态本体负责人的裁定为准，来源是前端 mock。所有用到销售额的题在 `tags` 里标了下面的 [推断] 点。

- **测试单一律剔除**：订单属性“是否测试单”写着“默认过滤掉”（`objectProps.ts:45`），订单描述也写“测试单默认不参与统计”（`objectProps.ts:294`）。所以**凡是读 `biz_order` 的 gold 和备选 SQL 都带 `not o.is_test`**，指标题和非指标题（订单数、实付均值、优惠合计、销量等）都一样。`biz_order.is_test` 已由本体小白确认，种子全部为否，所以 v0.3 的结果跟 v0.2 只在改了时间字段的题上不同。

- **销售额 gmv v3（同名默认，不含税）**。`metricCatalog.ts:29-37`：基础对象订单行，时间字段是订单行.出库时间，默认过滤“订单.状态 = 已出库；门店.是否测试店 = 否”，公式是 SUM(订单行.不含税金额) − SUM(退款行.不含税退款金额)。gold 写法：

  ```sql
  select round(sum(l.qty * l.price) / 1.13, 2)
  from order_line l join biz_order o on o.id = l.order_id join store s on s.id = o.store_id
  where o.status = 'shipped' and not o.is_test and not s.is_test and o.shipped_at >= … and o.shipped_at < …
  ```

  - 只算状态为“已出库”的订单，**已签收不算**（v0.1 把已签收算进去了，已改）。
  - 每道 gmv 题都 join `store` 剔除测试店，同时剔除测试单。测试店来自口径里的“门店.是否测试店 = 否”（`metricCatalog.ts:33`）；[推断] 前端的门店属性里没有这个字段，所以 `store.is_test` 仍标推断，种子全部为否。测试单 `biz_order.is_test` 已确认，不再是推断。
  - [推断] 订单行不含税金额 = 数量 × 含税单价 ÷ 1.13，先汇总后四舍五入。种子没有行级不含税金额；订单级优惠不分摊到行，所以 gmv × 1.13 不等于含税销售额。
  - 订单行.出库时间取订单的 `shipped_at`（种子里行没有自己的出库时间）。
  - 种子没有退款，减退款这一项按 0 计，相关题标了“种子无退款”。比对时不含税金额允许 ±0.01 × 订单数的误差。
- **含税销售额 gmv_tax v2**：SUM(订单.实付金额) − SUM(退款.退款金额)（`metrics.ts:19`），gold 是 `sum(o.amount)`，不 join 订单行。[推断] 时间字段用出库时间；过滤条件 mock 没写，跟 gmv 保持一致（已出库、非测试店）。测试单照总规则剔除。退款按 0。
- **有效订单数 order_cnt v1**：COUNT(订单) WHERE 状态 ≠ 已取消（`metrics.ts:21`），剔除测试单。时间字段是**出库时间** `shipped_at`：认证问法 `objectProps.ts:296`（`cert: true`）写的是“time: 出库时间”，订单描述也写“按出库时间计入”（静态本体负责人裁定，不再是推断）。待支付、待出库这类还没出库的订单没有出库时间，自然不进任何时间窗。不剔除测试店（口径没要求）。涉及 12 题：EV-Q-024、034、041、047、048（客单价分母）、059、061、085、088、093、096、097。
  - 题面只说“订单数”“下了多少单”、没说“有效”的题，不是这个指标，仍按全部订单、下单时间（只剔除测试单）。EV-Q-053 优惠金额合计也仍按下单时间。
- **出库及时率 ship_ontime v1**：30 分钟内变为“已出库”的订单数 / 应出库订单数（`metrics.ts:24`）。mock 没定义“应出库”，本题库的假设 [推断]：
  - 时间字段是**出库时间**（认证问法 `objectProps.ts:297` 写“time: 出库时间”，静态本体负责人裁定，不再是推断）；剔除测试单；
  - 分母（应出库订单）：统计期内出库、状态不是“待支付”也不是“已取消”的订单（实际就是已出库、已签收）；
  - 分子：分母中 `shipped_at ≤ created_at_biz + 30 分钟` 的订单。种子没有状态变更记录，用 `shipped_at` 代替“变为已出库的时刻”；
  - 结果是保留 4 位小数的比例。涉及题：EV-Q-049。
- **客单价 atv**：gmv / 有效订单数。atv 还是草稿，可以回答，但必须提示“草稿口径”。
- 毛利率、退款率：种子里没有对应数据，期望拒答，`refuseCode=NO_DATA`。
- **同义词以语义映射 semantic-map（静态 §3.7）为准，synonym 类题按它评分**：
  - 指标同义词：gmv 是营业额、销售收入、流水、净销售额（`metricCatalog.ts`），**GMV 也是**（静态本体负责人裁定，不是推断：知识条目 K-011 的同义词含 GMV，`knowledge.ts:173-175`；术语表 `Glossary.vue:230` 有 `['GMV','gmv']`；`qa-tuning.ts:249`）。所以 EV-Q-067「GMV 是多少？」按 gmv（不含税）作答，答成含税判错。
  - 小程序 → 渠道「小程序到家」（`dimensions.ts:310`），美团 → 渠道「美团闪购」（`dimensions.ts:312`）。这两个是**渠道维度值的同义词**，gold 里是 `o.channel = …` 的过滤条件，不是指标同义词。
  - 外卖：mock 里没有 [推断]。最接近的是知识条目 K-013「到家业务」（`knowledge.ts:193`，美团到家、京东到家、小程序配送，同义词“到家/O2O”），但“到家”同时又是渠道「小程序到家」的同义词。所以 EV-Q-058 期望反问渠道范围。
  - 单量、几单 → 有效订单数，卖了多少钱 → gmv，卖得最好 → 销量：都是口语问法，语义映射里没有，都标了 [推断]。

## 5. 评分规则

每题先看 `expectedType`，再逐项跑 `checks` 里列出的检查（没写就只做 `result`），全部通过才算这题通过。

1. **answer / result**：比对结果集，不比 SQL 文本。规则照试跑的 `compare.py`：
   - 数字统一保留 2 位；时间如果是 00:00:00，按日期处理；
   - **状态枚举**：库里存英文码，gold 结果里也是码（`shipped`、`open`…）。系统回答里显示中文（已出库、营业中）也算对，比对前按第 0 节的码 ↔ 中文对照表把中文换成码；
   - 列名、列顺序、行顺序都不看。只有题目问前 N 名、排名第一时，才看排在前面的是谁；
   - 每列转成多重集合，再看能不能找到一种列的对应方式；
   - 系统多给的列（比如名称、编码）不扣分，前提是 gold 的列都能找到对应；
   - 跟 `goldSql` 或任意一条 `goldSqlAlt` 的结果对上就算对。
2. **clarify**：系统返回澄清事件（`clarify` 和候选项），而且**没有执行 SQL**，才算通过。候选项最好覆盖 `clarifyAbout`（只记录、不扣分）。直接作答判失败，即使答得合理也一样（试跑 #35 两个模型都直接答了）。
3. **refuse**：系统拒答，错误码等于 `refuseCode`（`NOT_SELECT`、`OUT_OF_SCOPE`、`NO_PERMISSION`、`NO_DATA`），而且网关日志里没有任何一条执行记录，才算通过。回答“查不到数据”或者返回 0，都判失败。
   - **越权**（PDE）：问到身份范围以外的区域，要拒答，并带上申请权限的入口（W16，`QaConversation.vue:55`）。这类题 `expected` 写「拒答，带申请入口」，并设 `applyEntry: true`，没有申请入口也判失败。涉及题：EV-Q-080、EV-Q-081、EV-Q-084。
   - 只有行过滤后本来就没有数据时，才可以返回空结果并提示已按权限过滤（如 EV-Q-025 只剩华东一区一行）。
4. **explain**：用 sqlglot 解析系统实际执行的 SQL，抽出 WHERE、JOIN 里的过滤条件（门店、区域、城市、业态、渠道、状态、时间窗），再和回答里的“口径和过滤”说明逐条比：
   - 解释里说了、SQL 里没有，判失败（试跑 #16 正是这种情况）；
   - SQL 里有、解释里没说，只记录，不扣分。
5. **timeWindow**：从 SQL 里抽出时间列和上下界，跟第 3 节的窗口比，时间列也要对（出库时间还是下单时间）。
6. **groupKey**：问“每家门店”“每个商品”时，GROUP BY 里必须有该对象的 `id` 或 `code`。只按名称或店长分组判失败。种子里有订单的门店名称不重复，结果集比对查不出按名字分组的错，所以要靠这条结构检查。
7. **dateFormat**：
   - 按月分组时，日期列必须是 date 或 timestamp 类型，或者 `YYYY-MM`、`YYYY-MM-DD` 格式的字符串，只给月份数字（`10`、`10.00`）判失败；
   - 按小时分组时，必须带上日期。
8. 另外几项检查：
   - `draftNotice`：回答提示了草稿口径；
   - `rowFilterNotice`：回答提示了已按权限过滤；
   - `orClarify`：直接作答对了算通过，先反问也算通过。
9. `asScope`：带这个字段的题，用评测身份加对应的行范围来跑，网关照常注入行过滤，gold 里已经手工写好了同样的条件。其他题用评测身份跑，不注入行过滤（照 qa-api.md T11 [推断]）。
10. **角色身份的影响（题库没有角色维度，题目未改）**：题库只有 `asScope`（行范围）这一个身份维度，不按角色出题，所以没有题要改预期。但按前端 `roles.ts:110`（`static-api.md` §1.5 L424，master `443c009`），R-05 业务查看、R-06 门店店长、R-08 大区经理（华东）都是 `defaultDeny`、都没配商品范围，**商品不可见**；用这三种角色（或只有这几种角色的用户）来跑时，凡连 `product` 的 10 题（EV-Q-002、006、011、016、017、022、039、060、089、094）预期应是「无权限 / 不可见」：网关给 `product` 注入 `FALSE`，结果为空，回答要提示已按权限过滤或拒答带申请入口 [推断]。R-06 另外因订单.门店范围值对不上种子（qa-api §2.2），所有读订单的题都是 0 行。骨架 `72204b0` 里 `product` 仍登记为不限制（`register_unrestricted`），全拒身份下这 10 题照样出数，跟前端不一致，**[待骨架实现]**；骨架改完后如果要测角色，再给题加 `asRole` 字段和对应预期。

## 6. 文件格式（`questions.json`）

整个文件是一个 JSON 数组，字段用 camelCase，跟 seeds/extra 的约定一致。

| 字段 | 必有 | 说明 |
|---|---|---|
| `code` | 是 | `EV-Q-001` 至 `EV-Q-100`，唯一 |
| `kind` | 是 | `semplan` 或 `sql`（PDE 定）。本题库全部是 `sql`；`semplan` 留给比对语义计划的 EQ 题 |
| `category` | 是 | 第 2 节的 10 个分类之一 |
| `question` | 是 | 题面，唯一，最长 68 字（`qa_eval_question.question` 有唯一约束） |
| `expectedType` | 是 | `answer`、`clarify` 或 `refuse` |
| `goldSql` | 是，可为 null | answer 题必填；clarify 和 refuse 题为 `null`。表别名固定：o=biz_order、s=store、l=order_line、p=product |
| `goldSqlAlt` | 否 | 同样算对的其他写法 |
| `expected` | 是 | 说明文字：口径、迁移改动、判对判错的要点 |
| `checks` | 否 | 第 5 节列出的检查项，不写就是 `["result"]` |
| `asScope` | 否 | 用哪个行范围的身份来问，比如 `华东一区` |
| `refuseCode` | refuse 必有 | 期望的错误码 |
| `applyEntry` | 否 | `true` 表示拒答时必须带申请权限入口（越权题） |
| `clarifyAbout` | clarify 必有 | 应该反问的点，取值 `metric`、`time`、`dim`、`caliber`、`path`、`entity`、`compare` |
| `source` | 是 | `ao_trial#N` 或 `new` |
| `tags` | 否 | 这题依赖的 [推断] 点和数据缺口（如“种子无退款”），口径确认后按 tag 批量改 |
| `inferredCols` | 否 | gold 用到、骨架里仍标 [推断] 的列（现在只有 `store.big_region`、`store.is_test`，30 题） |
| `scratchPreview` | 否 | 在骨架真实结构 + 真种子上跑出的 `{rows, sample}`（v0.4 已覆盖，种子变了要重跑）。`rows` 是结果行数（含全 NULL 的行），`sample` 里 SQL NULL 写成 `NULL`；行数已按 `select count(*) from (<goldSql>) t` 逐题复核，只有 EV-Q-029 由 0 行改成 1 行 NULL（生成脚本把 psql 输出的空行丢了）。EV-Q-006 的 gold 原来是 `order by 2 desc limit 1`，乳品、粮油调味各 3 个并列，结果不确定；改成返回并列全部（`having count(*) = max`，备选用 `rank() = 1`），2 行；其他带 `limit` 的题（EV-Q-017、018、039、060、091）在截断处没有并列 |

目录：`backend/seeds/extra/qa/eval/`（PDE 定，跟 `seeds/extra/README.md` 按导出分组的约定一致）。

## 7. 试跑 34 道 gold 题的迁移情况

试跑用的是演示库（regions、stores、customers、orders、order_items），不是我们的表。“已完成”统一迁成“状态 = 已出库”，“销售额”迁成 gmv 不含税。

- **原样或改写后迁入作答题，共 25 题**：#1 #2 #4 #5 #6 #7 #8 #9 #10 #11 #12 #13 #14 #15 #16 #18 #19 #20 #21 #22 #24 #25 #31 #32 #33。其中改写过的有：
  - #12：8 月改为 10 月。#22、#33：上月改为昨天（种子在这些时间段里没有数据）。
  - #14：大区改为区域。#15：直营/加盟改为业态。#25：大区经理改为按区域数门店。
  - #16：改成华东一区昨天（EV-Q-085），另外拆出一道华南越权拒答（EV-Q-080）。
  - #19：改为 gmv 按一级品类（gmv 的基础对象就是订单行）。
- **改成反问题，1 题**：#28 外卖，语义映射里没有（EV-Q-058）。
- **gold 作废，改成拒答题，共 4 题**：#26 毛利（没有成本数据，EV-Q-050）、#27 退款率（没有退款数据，EV-Q-051）、#29 会员数（EV-Q-031）、#23 按会员分组（EV-Q-092）。后两题是因为没有会员表。
- **不迁，共 4 题**：#3、#17、#34 都依赖会员等级，没有会员表；#30 依赖商品上下架，种子没有这个字段。
- **7 道行为题全部迁入**：#35–#37 改为反问题，#38–#41 改为拒答题。试跑时这几题的表现是：
  - #35：两个模型都直接作答；
  - #36、#37：coder 直接作答，flash 报元数据查询失败；
  - #38–#41：两个模型都只回了“无法构造 SQL”，没有给出拒答的理由。

合计：来自试跑的有 38 题（#16 用了两次），新写的 62 题。

## 8. 结果怎样写进 `qa_eval_run`（照 qa-api.md）

- 一次 EV-Q 运行对应 `qa_eval_run` 里的一行：
  - `trigger_label='手动运行'`；
  - `scope` 写 `EV-Q`，或者“EV-Q/模型名”；
  - `total=100`，`passed` 是通过的题数，`status` 依次走“运行中”到“已完成”；
  - `note` 里记模型、now 锚点和题库文件的 git 哈希。
- 每道失败题对应 `qa_eval_fail` 里的一行：
  - `actual` jsonb 放 `{sql, rows 前 20 行, explain, event: answer|clarify|refuse, code}`；
  - `kind` 取 FailKind 里的值，另外新增 4 种 [推断]：

| 失败原因 | kind |
|---|---|
| 结果不对 | 先用 sqlglot 定位：缺过滤条件记 `过滤条件缺失`，时间不对记 `时间解析错误`，维度不对记 `维度不匹配`，指标不对记 `指标选错` 或 `未召回指标` |
| 该反问没反问 | `应澄清未澄清` |
| 该拒答没拒答 | `应拒答未拒答` |
| 解释和 SQL 不一致 | `解释与SQL不符` |
| 日期格式错 | `日期格式错误` |

  新增的这 4 个值都不超过 `kind varchar(24)` 的长度。
- **跟现有表结构的冲突**：
  - `qa_eval_fail.question_id` 有外键指向 `qa_eval_question`，所以 EV-Q 的题也得先灌进 `qa_eval_question`；
  - 但这张表的 `group_name varchar(8)` 只认 6 个分组，`expect` 存的是语义计划。

  处理方式：
  - `qa_eval_question` 加一列 `kind varchar(8)`，取值 `semplan` 或 `sql`（PDE 已定）；
  - EV-Q 题的 `group_name` 填 `问数SQL`，`expect` 存 `{expectedType, goldSql, goldSqlAlt, checks, asScope, refuseCode, clarifyAbout}`；
  - T11 的 group 参数加一个 `sql` 选项。

  qa-api.md 同一提交里已对齐骨架 `72204b0`。

## 9. 模型

- 第一轮用百炼的 qwen-flash，对照组用 qwen3-coder-flash，跟试跑一致。用 `temperature=0` 跑 3 遍，看结果稳不稳定。
- 7B 以下的小模型：PDE 说了“没权限就先不测，别另开账号或换别人的 key”，这一版不测。等拿到正式授权，再用同一份题库补跑，`scope` 里写上模型名。
- 每次运行都记录 token 用量，跟试跑的 `usage_*.jsonl` 一样。

## 10. 待确认清单（剩下的 [推断]）

| # | 事项 | 涉及题 | 找谁 |
|---|---|---|---|
| 1 | `store.is_test` 测试店：口径写了“门店.是否测试店 = 否”（`metricCatalog.ts:33`），但前端门店属性里没有这个字段；种子全部为否 | 全部 29 道 gmv/gmv_tax 题 | 本体小白 / 静态本体 |
| 2 | 订单行不含税金额怎么算：种子没有，暂按行含税 ÷ 1.13，订单级优惠不分摊 | 27 道 gmv 题 | 本体小白 / 指标负责人 |
| 3 | 退款种子没有，gmv 和 gmv_tax 的退款项按 0 | 29 题；退款率 EV-Q-051 拒答 | 本体小白 |
| 4 | 含税销售额的时间字段（出库时间）和默认过滤（同 gmv） | EV-Q-043、EV-Q-052、EV-Q-087 | 指标负责人 |
| 5 | 出库及时率的“应出库订单”定义（暂按状态∉{待支付, 已取消}），以及用 `shipped_at` 代替状态变更时刻。时间字段已定为出库时间 | EV-Q-049 | 周晓峰（ship_ontime owner） |
| 6 | `store.big_region` 列名和取值（骨架仍标 [推断]，种子只有 `华东`；静态裁定维度值「华东大区」、同义词「华东」）。其他列已按骨架确认 | 见各题 `inferredCols`（30 题） | 本体小白 |
| 7 | 外卖指哪些渠道；单量、几单、卖了多少钱、卖得最好这些口语说法 | EV-Q-058、EV-Q-059、EV-Q-061、EV-Q-063、EV-Q-060 | 静态本体（semantic-map） |
| 8 | 履约门店数据什么时候接入 | EV-Q-026、EV-Q-068 | 本体小白 |
| 9 | ~~“表名前缀 bz_”注释~~ 已结清（`72204b0` 已删） | — | — |
| 10 | 优惠金额没有指标定义，暂按剔除已取消、下单时间 | EV-Q-053 | 指标负责人 |
| 11 | 散客 = `member_phone` 为空 | EV-Q-015 | 本体小白 |
| 12 | 商品可见性：`defaultDeny` 角色（R-05/R-06/R-08）没配品类范围时商品不可见，骨架 `product` 现登记为不限制 [待骨架实现]；题库暂无角色维度（第 5 节第 10 条） | 连 `product` 的 10 题 | 本体小白 |

v0.4 已结清：列名和类型按骨架确认（`area`、`created_at_biz`、状态英文码、`is_seed`），bz_ 注释。

v0.3 已结清：有效订单数和出库及时率的时间字段（出库时间）、`biz_order.is_test`（已确认）、GMV 同义词（已确认）。
