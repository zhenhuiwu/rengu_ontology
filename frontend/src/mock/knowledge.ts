/**
 * 业务知识（术语表、业务规则与计算说明、财务日历与大促、口径说明）示例数据。
 * 每条知识在问数中的作用：识别词（名称 + 同义词）→ 映射为对象集 / 过滤条件 / 时间范围；被指标、认证问答、规则引用。
 * 只做界面模拟，数据不要求与其他页面严格一致；接入真实数据时替换。
 */
export type KType = '术语' | '业务规则' | '财务日历' | '口径说明';
export type KStatus = '已发布' | '草稿' | '已停用';
export const K_TYPES: KType[] = ['术语', '业务规则', '财务日历', '口径说明'];

/** 适用对象 / 指标：kind 为 object-type 或 metric，能解析的用 ObjRef 打开 */
export interface KApply { kind: 'object-type' | 'metric'; id: string; name: string }
export interface KFilter { field: string; cond: string; note: string }
export interface KPeriod { name: string; start: string; end: string; note: string }
export interface KRef { type: '指标' | '认证问答' | '规则' | '看板' | '评测题'; name: string; note: string; hover?: { kind: string; id: string } }
export interface KHist { ver: string; cr?: string; status: '生效中' | '已替换' | '草稿' | '已停用'; by: string; at: string; change: string }
/** 可在草稿里修改的字段 */
export interface KFields {
  name: string; type: KType; definition: string; calc: string; synonyms: string[];
  applies: KApply[]; period: string; owner: string; maps: string; filters: KFilter[];
}
export interface KEntry extends KFields {
  id: string; status: KStatus;
  since?: string;          // 首次随哪一版发布
  stoppedIn?: string;      // 随哪一版停用
  cr?: string;             // 草稿所在变更单
  updatedAt: string;
  tries: string[];         // 试一试的示例问题
  periods?: KPeriod[];     // 财务日历的区间
  refs: KRef[];
  history: KHist[];
  draft?: Partial<KFields>; // 草稿中的改动（只记改了的字段）
  draftStatus?: KStatus;    // 草稿里把状态改成了什么（停用）
}

const O = (id: string, name: string): KApply => ({ kind: 'object-type', id, name });
const M = (id: string, name: string): KApply => ({ kind: 'metric', id, name });
const H = (ver: string, cr: string | undefined, by: string, at: string, change: string, status: KHist['status'] = '已替换'): KHist => ({ ver, cr, status, by, at, change });

export const KNOWLEDGE: KEntry[] = [
  {
    id: 'K-001', name: '复购客户', type: '术语', status: '已发布', since: 'v2026.08.2', updatedAt: '2026-09-19 19:12', owner: '孙浩然',
    definition: '统计截止日前 90 天内，有效订单数 ≥ 2 的会员。非会员订单不参与复购判断。',
    calc: '会员.近90天有效订单数 = COUNT(订单) WHERE 订单.状态 ≠ 已取消 AND 下单时间 ∈ [截止日 − 89 天, 截止日]',
    synonyms: ['回头客', '老客', '复购会员'], applies: [O('member', '会员'), O('order', '订单'), M('gmv', '销售额（不含税）')],
    period: '2026-09-19 起（v2026.09.3）', maps: '对象集「复购客户」',
    filters: [{ field: '会员.近90天有效订单数', cond: '≥ 2', note: '问题里没写时间窗时默认 90 天' }, { field: '订单.会员号', cond: '非空', note: '散客订单不计入' }],
    tries: ['上个月华东复购客户的销售额是多少', '老客客单价近 3 个月趋势', '各大区回头客占比'],
    refs: [
      { type: '指标', name: '复购率', note: '分子：复购客户数' }, { type: '指标', name: '复购客户销售额', note: '按复购客户过滤销售额', hover: { kind: 'metric', id: 'gmv' } },
      { type: '认证问答', name: '各大区复购率', note: '2026-09-18 已重新认证' }, { type: '认证问答', name: '复购客户贡献占比', note: '引用定义' },
      { type: '认证问答', name: '近 90 天复购客户名单', note: '引用定义' }, { type: '规则', name: 'R-027 大区复购客户销售额周环比下降', note: '按复购客户过滤' },
      { type: '看板', name: '会员经营看板', note: '复购卡片' }, { type: '评测题', name: '复购相关 5 题', note: '发布前回放' },
      { type: '看板', name: '大区周报', note: '复购客户销售额' }, { type: '认证问答', name: '老客客单价趋势', note: '同义词“老客”' },
      { type: '认证问答', name: '门店复购客户数排名', note: '引用定义' },
    ],
    history: [
      H('v2026.09.3', 'CR-0002', '孙浩然', '2026-09-19 19:12', '时间窗 30 天 → 90 天，与会员中心口径一致', '生效中'),
      H('v2026.08.2', undefined, '孙浩然', '2026-08-12 10:20', '创建：30 天内有效订单 ≥ 2 的会员'),
    ],
  },
  {
    id: 'K-002', name: '老会员', type: '术语', status: '已发布', since: 'v2026.08.2', updatedAt: '2026-08-12 10:20', owner: '刘婷',
    definition: '注册满 365 天的会员，不看是否复购。会员权益和积分活动按此口径圈人。',
    calc: '会员.注册天数 = 截止日 − 会员.注册日期 ≥ 365', synonyms: ['老客', '老用户'], applies: [O('member', '会员')],
    period: '长期有效', maps: '过滤条件「会员.注册天数 ≥ 365」',
    filters: [{ field: '会员.注册日期', cond: '≤ 截止日 − 365 天', note: '' }],
    tries: ['老会员的积分兑换率', '老客上月消费金额'],
    refs: [{ type: '认证问答', name: '老会员积分兑换率', note: '引用定义' }, { type: '看板', name: '会员权益看板', note: '老会员人数' }],
    history: [H('v2026.08.2', undefined, '刘婷', '2026-08-12 10:20', '创建', '生效中')],
  },
  {
    id: 'K-003', name: '新客', type: '术语', status: '已发布', since: 'v2026.08.2', updatedAt: '2026-08-12 10:20', owner: '刘婷',
    definition: '首笔有效订单发生在统计期内的会员。',
    calc: '会员.首单日期 ∈ 统计期', synonyms: ['新会员', '首单客户'], applies: [O('member', '会员'), O('order', '订单')],
    period: '长期有效', maps: '过滤条件「会员.首单日期 ∈ 统计期」',
    filters: [{ field: '会员.首单日期', cond: '∈ 统计期', note: '统计期取问题里的时间' }],
    tries: ['本周各门店新客数', '双十一新客客单价'],
    refs: [{ type: '认证问答', name: '各门店新客数', note: '引用定义' }, { type: '看板', name: '拉新周报', note: '' }],
    history: [H('v2026.08.2', undefined, '刘婷', '2026-08-12 10:20', '创建', '生效中')],
  },
  {
    id: 'K-004', name: '同店', type: '业务规则', status: '已发布', since: 'v2026.08.2', updatedAt: '2026-10-17 20:45', owner: '陈立军',
    definition: '可比门店：统计期开始时已开业满 12 个月、统计期内未闭店、且没有连续 30 天以上的装修停业。用于同比、环比时剔除新店和闭店的影响。',
    calc: '门店.开业日期 ≤ 统计期起始 − 12 个月 AND 门店.状态 ≠ 闭店 AND 门店.最长停业天数（统计期） ≤ 30',
    synonyms: ['可比门店', '同店口径', '老店'], applies: [O('store', '门店'), M('gmv', '销售额（不含税）'), M('order_cnt', '有效订单数')],
    period: '长期有效', maps: '过滤条件（门店范围）',
    filters: [
      { field: '门店.开业日期', cond: '≤ 统计期起始 − 12 个月', note: '开业满 12 个月' },
      { field: '门店.状态', cond: '≠ 闭店', note: '统计期内闭店的门店剔除' },
      { field: '门店.最长停业天数', cond: '≤ 30', note: '装修停业超过 30 天剔除' },
    ],
    tries: ['华东同店销售额同比', '上月可比门店客单价', '老店有效订单数环比'],
    refs: [
      { type: '指标', name: '同店销售额', note: '按同店过滤', hover: { kind: 'metric', id: 'gmv' } }, { type: '指标', name: '同店订单数', note: '按同店过滤', hover: { kind: 'metric', id: 'order_cnt' } },
      { type: '认证问答', name: '各大区同店销售同比', note: '引用定义' }, { type: '规则', name: 'R-021 门店销售额环比下降', note: '只对同店生效', hover: { kind: 'rule', id: 'R-021' } },
      { type: '看板', name: '经营月报', note: '同店增长' },
    ],
    history: [
      H('v2026.10.2', 'CR-0005', '陈立军', '2026-10-17 20:45', '补充：装修停业超过 30 天剔除', '生效中'),
      H('v2026.08.2', undefined, '陈立军', '2026-08-12 10:20', '创建：开业满 12 个月'),
    ],
  },
  {
    id: 'K-005', name: '大促期', type: '财务日历', status: '已发布', since: 'v2026.08.2', updatedAt: '2026-10-09 22:03', owner: '何静',
    definition: '公司级大促活动的统计区间，含预热期。问题里只说“大促”时，取离提问日期最近的一次大促。',
    calc: '日期 ∈ 财务日历.大促区间；同比时与上一年同名大促对比，不按自然日对齐',
    synonyms: ['大促', '促销季'], applies: [O('order', '订单'), O('promotion', '促销活动'), M('gmv', '销售额（不含税）')],
    period: '2026 财年', maps: '时间范围（按财务日历）',
    filters: [{ field: '订单.下单日期', cond: '∈ 大促区间', note: '含预热期' }],
    tries: ['大促期间华南销售额', '今年大促和去年比怎么样'],
    periods: [
      { name: '年货节', start: '2026-01-10', end: '2026-02-05', note: '含春节前 2 周' },
      { name: '618', start: '2026-06-01', end: '2026-06-18', note: '预热 5/25 起不计入' },
      { name: '双十一', start: '2026-10-20', end: '2026-11-11', note: '含预售期' },
      { name: '双十二', start: '2026-12-08', end: '2026-12-12', note: '' },
    ],
    refs: [{ type: '认证问答', name: '大促期间各大区销售额', note: '时间范围' }, { type: '看板', name: '大促作战室', note: '' }, { type: '评测题', name: '大促时间解析 4 题', note: '' }],
    history: [H('v2026.10.1', 'CR-0004', '何静', '2026-10-09 22:03', '双十一区间改为 10/20 起（含预售）', '生效中'), H('v2026.08.2', undefined, '何静', '2026-08-12 10:20', '创建')],
  },
  {
    id: 'K-006', name: '双十一大促期', type: '财务日历', status: '已发布', since: 'v2026.10.1', updatedAt: '2026-10-09 22:03', owner: '何静',
    definition: '2026 年双十一活动区间：10 月 20 日 20:00 预售开始至 11 月 11 日 23:59。',
    calc: '订单.下单时间 ∈ [2026-10-20 20:00, 2026-11-11 23:59]', synonyms: ['双11', '双十一', '大促'],
    applies: [O('order', '订单'), O('promotion', '促销活动')], period: '2026-10-20 至 2026-11-11', maps: '时间范围',
    filters: [{ field: '订单.下单时间', cond: '∈ [10-20 20:00, 11-11 23:59]', note: '' }],
    tries: ['双11 期间线上订单数', '双十一预售期客单价'],
    periods: [{ name: '预售期', start: '2026-10-20', end: '2026-10-31', note: '定金 + 尾款按尾款日计' }, { name: '正式期', start: '2026-11-01', end: '2026-11-11', note: '' }],
    refs: [{ type: '看板', name: '大促作战室', note: '双十一页签' }, { type: '认证问答', name: '双十一各渠道销售额', note: '' }],
    history: [H('v2026.10.1', 'CR-0004', '何静', '2026-10-09 22:03', '创建', '生效中')],
  },
  {
    id: 'K-007', name: '财年与财务月', type: '财务日历', status: '已发布', since: 'v2026.08.2', updatedAt: '2026-08-12 10:20', owner: '何静',
    definition: '财年自 2 月 1 日开始，按 4-4-5 周划分财务月。问题里说“本财年”“财务 3 月”时按财务日历换算，说“3 月”时按自然月。',
    calc: '财务月 = 财务日历.财务月（4-4-5 周）；财年 FY2026 = 2026-02-01 至 2027-01-30',
    synonyms: ['财年', '财务月', 'FY'], applies: [O('order', '订单'), M('gmv', '销售额（不含税）'), M('gross_margin', '毛利率')],
    period: 'FY2026', maps: '时间范围（按财务日历）', filters: [],
    tries: ['本财年累计销售额', 'FY2026 Q3 毛利率'],
    periods: [
      { name: 'FY2026 Q1', start: '2026-02-01', end: '2026-05-02', note: '4-4-5 周' }, { name: 'FY2026 Q2', start: '2026-05-03', end: '2026-08-01', note: '' },
      { name: 'FY2026 Q3', start: '2026-08-02', end: '2026-10-31', note: '当前财季' }, { name: 'FY2026 Q4', start: '2026-11-01', end: '2027-01-30', note: '' },
    ],
    refs: [{ type: '指标', name: '销售额（不含税）', note: '财年累计', hover: { kind: 'metric', id: 'gmv' } }, { type: '看板', name: '经营月报', note: '按财务月' }],
    history: [H('v2026.08.2', undefined, '何静', '2026-08-12 10:20', '创建', '生效中')],
  },
  {
    id: 'K-008', name: '春节错期对比', type: '业务规则', status: '已发布', since: 'v2026.09.3', updatedAt: '2026-09-19 19:12', owner: '何静',
    definition: '1–2 月做同比时按农历对齐：今年春节前后 N 天对比去年春节前后 N 天，避免春节落在不同月份造成的波动。',
    calc: '对比区间 = 去年春节日期 + (本期日期 − 今年春节日期)', synonyms: ['春节同比', '农历同比'], applies: [M('gmv', '销售额（不含税）'), M('order_cnt', '有效订单数')],
    period: '每年 1 月 1 日至 2 月末', maps: '同比方式', filters: [], tries: ['春节期间销售额同比'],
    refs: [{ type: '认证问答', name: '春节档销售同比', note: '' }],
    history: [H('v2026.09.3', 'CR-0002', '何静', '2026-09-19 19:12', '创建', '生效中')],
  },
  {
    id: 'K-009', name: '有效订单', type: '术语', status: '已发布', since: 'v2026.08.2', updatedAt: '2026-10-25 10:18', owner: '陈立军',
    definition: '状态不是“已取消”的订单。退款不影响订单是否有效，金额在销售额里冲减。',
    calc: '订单.状态 ≠ 已取消', synonyms: ['有效单', '成交订单'], applies: [O('order', '订单'), M('order_cnt', '有效订单数')],
    period: '长期有效', maps: '过滤条件「订单.状态 ≠ 已取消」',
    filters: [{ field: '订单.状态', cond: '≠ 已取消', note: '默认过滤，问题里说“全部订单”时不加' }],
    tries: ['昨天各门店有效单', '上周成交订单数'],
    refs: [{ type: '指标', name: '有效订单数', note: '口径', hover: { kind: 'metric', id: 'order_cnt' } }, { type: '指标', name: '客单价（不含税）', note: '分母', hover: { kind: 'metric', id: 'atv' } }],
    history: [H('v2026.08.2', undefined, '陈立军', '2026-08-12 10:20', '创建', '生效中')],
  },
  {
    id: 'K-010', name: '退款冲减口径', type: '口径说明', status: '已发布', since: 'v2026.08.2', updatedAt: '2026-10-24 21:17', owner: '陈立军',
    definition: '销售额按退款完成时间冲减，冲减到退款完成当天，不回溯原订单日期。部分退款按退款行冲减。',
    calc: '销售额 = SUM(订单行.不含税金额) − SUM(退款行.不含税退款金额 WHERE 退款完成时间 ∈ 统计期)',
    synonyms: ['退款冲减', '净销售'], applies: [O('refund', '退款单'), M('gmv', '销售额（不含税）')],
    period: '长期有效', maps: '计算说明（回答里注明）', filters: [], tries: ['为什么昨天销售额比 POS 少'],
    refs: [{ type: '指标', name: '销售额（不含税）', note: '口径说明', hover: { kind: 'metric', id: 'gmv' } }, { type: '认证问答', name: '销售额与 POS 差异说明', note: '' }],
    history: [H('v2026.10.3', 'CR-0007', '王敏', '2026-10-24 21:17', '改为不含税金额冲减', '生效中'), H('v2026.08.2', undefined, '陈立军', '2026-08-12 10:20', '创建')],
  },
  {
    id: 'K-011', name: '含税与不含税销售额', type: '口径说明', status: '已发布', since: 'v2026.10.3', updatedAt: '2026-10-24 21:17', owner: '王敏',
    definition: '“销售额”默认指不含税销售额（gmv）。问题里出现“含税”“实付”时改用含税销售额（gmv_tax）。',
    calc: '不含税 = 含税 / (1 + 税率)，税率按商品税目取', synonyms: ['销售额', 'GMV', '含税销售额'],
    applies: [M('gmv', '销售额（不含税）'), M('gmv_tax', '销售额（含税）')], period: '2026-10-24 起（v2026.10.3）', maps: '指标选择',
    filters: [], tries: ['上月含税销售额', '华东 GMV'],
    refs: [{ type: '指标', name: '销售额（不含税）', note: '默认指标', hover: { kind: 'metric', id: 'gmv' } }, { type: '指标', name: '销售额（含税）', note: '含税时使用', hover: { kind: 'metric', id: 'gmv_tax' } }, { type: '评测题', name: '含税识别 3 题', note: '' }],
    history: [H('v2026.10.3', 'CR-0007', '王敏', '2026-10-24 21:17', '创建', '生效中')],
  },
  {
    id: 'K-012', name: '线上渠道', type: '术语', status: '已发布', since: 'v2026.08.2', updatedAt: '2026-08-12 10:20', owner: '吴佳',
    definition: '小程序、App、美团到家、京东到家四个渠道的订单。门店自提计入线上。',
    calc: '订单.渠道 ∈ {小程序, App, 美团到家, 京东到家}', synonyms: ['线上', '电商', '到家'], applies: [O('order', '订单')],
    period: '长期有效', maps: '过滤条件「订单.渠道 ∈ 线上」',
    filters: [{ field: '订单.渠道', cond: '∈ {小程序, App, 美团到家, 京东到家}', note: '' }],
    tries: ['线上渠道上周销售额', '电商订单占比'],
    refs: [{ type: '认证问答', name: '线上线下销售占比', note: '' }, { type: '看板', name: '全渠道看板', note: '' }],
    history: [H('v2026.08.2', undefined, '吴佳', '2026-08-12 10:20', '创建', '生效中')],
  },
  {
    id: 'K-013', name: '到家业务', type: '术语', status: '已发布', since: 'v2026.09.4', updatedAt: '2026-09-28 21:30', owner: '吴佳',
    definition: '由门店拣货、骑手配送到家的订单，只含美团到家、京东到家和小程序配送单，不含自提。',
    calc: '订单.履约方式 = 配送到家', synonyms: ['到家', 'O2O'], applies: [O('order', '订单'), O('shipment', '出库单')],
    period: '长期有效', maps: '过滤条件「订单.履约方式 = 配送到家」',
    filters: [{ field: '订单.履约方式', cond: '= 配送到家', note: '' }],
    tries: ['到家订单出库及时率'],
    refs: [{ type: '指标', name: '出库及时率', note: '到家口径', hover: { kind: 'metric', id: 'ship_ontime' } }],
    history: [H('v2026.09.4', 'CR-0003', '吴佳', '2026-09-28 21:30', '创建', '生效中')],
  },
  {
    id: 'K-014', name: '动销', type: '术语', status: '已发布', since: 'v2026.08.2', updatedAt: '2026-08-12 10:20', owner: '王敏',
    definition: '统计期内有销量的商品记为动销。动销率 = 动销 SKU 数 / 在售 SKU 数。',
    calc: '商品.统计期销量 > 0', synonyms: ['动销率', '有销售'], applies: [O('product', '商品'), O('inventory', '库存快照')],
    period: '长期有效', maps: '过滤条件「商品.统计期销量 > 0」', filters: [{ field: '商品.统计期销量', cond: '> 0', note: '' }],
    tries: ['各品类上月动销率'], refs: [{ type: '看板', name: '商品健康度', note: '' }],
    history: [H('v2026.08.2', undefined, '王敏', '2026-08-12 10:20', '创建', '生效中')],
  },
  {
    id: 'K-015', name: '临期商品', type: '业务规则', status: '已发布', since: 'v2026.09.4', updatedAt: '2026-09-28 21:30', owner: '王敏',
    definition: '剩余保质期不足总保质期 1/3 的在库商品；保质期 ≤ 7 天的生鲜按剩余 ≤ 1 天判断。',
    calc: '商品.剩余保质期 ≤ 商品.保质期天数 × 1/3（生鲜：≤ 1 天）', synonyms: ['临期', '近效期'], applies: [O('product', '商品'), O('inventory', '库存快照')],
    period: '长期有效', maps: '过滤条件（库存）', filters: [{ field: '商品.剩余保质期', cond: '≤ 保质期天数 × 1/3', note: '' }],
    tries: ['华北仓临期商品金额'], refs: [{ type: '规则', name: 'R-014 临期商品库存积压', note: '判断条件' }, { type: '看板', name: '库存周报', note: '' }],
    history: [H('v2026.09.4', 'CR-0003', '刘婷', '2026-09-28 21:30', '创建（依赖商品.保质期天数）', '生效中')],
  },
  {
    id: 'K-016', name: '出库及时', type: '口径说明', status: '已发布', since: 'v2026.10.1', updatedAt: '2026-10-09 22:03', owner: '周晓峰',
    definition: '订单在支付后 30 分钟内状态变为“已出库”记为出库及时。原“已发货”状态已统一为“已出库”。',
    calc: '出库时间 − 支付时间 ≤ 30 分钟', synonyms: ['发货及时', '出库及时率'], applies: [O('shipment', '出库单'), M('ship_ontime', '出库及时率')],
    period: '2026-10-09 起（v2026.10.1）', maps: '计算说明', filters: [], tries: ['上周各仓出库及时率'],
    refs: [{ type: '指标', name: '出库及时率', note: '口径说明', hover: { kind: 'metric', id: 'ship_ontime' } }, { type: '规则', name: 'R-015 出库超 48 小时未签收', note: '', hover: { kind: 'rule', id: 'R-015' } }],
    history: [H('v2026.10.1', 'CR-0004', '王敏', '2026-10-09 22:03', '“已发货”统一为“已出库”', '生效中')],
  },
  {
    id: 'K-017', name: '客单价', type: '口径说明', status: '已发布', since: 'v2026.09.3', updatedAt: '2026-09-19 19:12', owner: '刘婷',
    definition: '客单价 = 不含税销售额 / 有效订单数，按订单算，不按人算。“人均消费”请用会员人均。',
    calc: '销售额（不含税） / 有效订单数', synonyms: ['客单', 'ATV'], applies: [M('atv', '客单价（不含税）'), M('gmv', '销售额（不含税）'), M('order_cnt', '有效订单数')],
    period: '长期有效', maps: '指标选择', filters: [], tries: ['华东门店上月客单价'],
    refs: [{ type: '指标', name: '客单价（不含税）', note: '口径', hover: { kind: 'metric', id: 'atv' } }],
    history: [H('v2026.09.3', 'CR-0002', '刘婷', '2026-09-19 19:12', '创建', '生效中')],
  },
  {
    id: 'K-018', name: '毛利口径说明', type: '口径说明', status: '已发布', since: 'v2026.08.2', updatedAt: '2026-10-24 18:40', owner: '何静', cr: 'CR-0011',
    definition: '毛利 = 不含税销售额 − 不含税成本；毛利率 = 毛利 / 不含税销售额。成本取移动加权平均成本。',
    calc: '毛利额 = SUM(订单行.不含税金额) − SUM(订单行.不含税成本)', synonyms: ['毛利', '毛利额', '毛利率'],
    applies: [M('gross_margin', '毛利率'), O('product', '商品'), O('order_line', '订单行')], period: '长期有效', maps: '计算说明（回答里注明）',
    filters: [], tries: ['各品类上月毛利率', '为什么毛利率和财务月报对不上'],
    refs: [
      { type: '指标', name: '毛利率', note: '口径说明', hover: { kind: 'metric', id: 'gross_margin' } }, { type: '规则', name: 'R-007 商品毛利率越线', note: '阈值按此口径', hover: { kind: 'rule', id: 'R-007' } },
      { type: '认证问答', name: '各品类毛利率排名', note: '需重新认证' }, { type: '看板', name: '品类毛利看板', note: '' },
    ],
    history: [
      H('v2026.10.4', 'CR-0011', '何静', '2026-10-24 18:40', '扣除促销补贴，与财务月报一致', '草稿'),
      H('v2026.08.2', undefined, '刘婷', '2026-08-12 10:20', '创建', '生效中'),
    ],
    draft: {
      definition: '毛利 = 不含税销售额 − 不含税成本 − 促销补贴；毛利率 = 毛利 / 不含税销售额。与财务月报一致。',
      calc: '毛利额 = SUM(订单行.不含税金额) − SUM(订单行.不含税成本) − SUM(订单行.促销补贴)',
      applies: [M('gross_margin', '毛利率'), O('product', '商品'), O('order_line', '订单行'), O('promotion', '促销活动')],
    },
  },
  {
    id: 'K-019', name: '大区', type: '术语', status: '已发布', since: 'v2026.10.2', updatedAt: '2026-10-17 20:45', owner: '陈立军',
    definition: '门店所属的经营大区，共 6 个：华东、华南、华北、华中、西南、西北。“区域”默认指大区，不指行政省份。',
    calc: '门店 → 大区（所属大区关系）', synonyms: ['区域', '战区'], applies: [O('region', '大区'), O('store', '门店')],
    period: '2026-10-17 起（v2026.10.2）', maps: '维度「大区」', filters: [], tries: ['各区域上周销售额'],
    refs: [{ type: '看板', name: '大区周报', note: '' }, { type: '规则', name: 'R-021 门店销售额环比下降', note: '按大区通知', hover: { kind: 'rule', id: 'R-021' } }],
    history: [H('v2026.10.2', 'CR-0005', '陈立军', '2026-10-17 20:45', '创建（随“门店 → 大区”关系）', '生效中')],
  },
  {
    id: 'K-020', name: '会员日', type: '财务日历', status: '已发布', since: 'v2026.09.3', updatedAt: '2026-09-19 19:12', owner: '吴佳',
    definition: '每月 8 日、18 日为会员日，会员双倍积分。',
    calc: 'DAY(订单.下单日期) ∈ {8, 18}', synonyms: ['会员日'], applies: [O('member', '会员'), O('order', '订单')],
    period: '长期有效', maps: '时间范围', filters: [{ field: 'DAY(订单.下单日期)', cond: '∈ {8, 18}', note: '' }],
    tries: ['上个会员日的会员销售额'],
    periods: [{ name: '10 月会员日', start: '2026-10-08', end: '2026-10-08', note: '' }, { name: '10 月会员日', start: '2026-10-18', end: '2026-10-18', note: '' }],
    refs: [{ type: '看板', name: '会员经营看板', note: '会员日对比' }],
    history: [H('v2026.09.3', 'CR-0002', '吴佳', '2026-09-19 19:12', '创建', '生效中')],
  },
  {
    id: 'K-021', name: '门店坪效', type: '口径说明', status: '已停用', since: 'v2026.08.2', stoppedIn: 'v2026.10.2', updatedAt: '2026-10-17 20:45', owner: '陈立军',
    definition: '门店销售额 / 营业面积（平方米）。门店主数据面积字段缺失较多，已停用，待面积数据治理后重新启用。',
    calc: '销售额（不含税） / 门店.营业面积', synonyms: ['坪效'], applies: [O('store', '门店'), M('gmv', '销售额（不含税）')],
    period: '2026-08-12 至 2026-10-17', maps: '指标选择', filters: [], tries: ['各门店坪效'],
    refs: [],
    history: [H('v2026.10.2', 'CR-0005', '陈立军', '2026-10-17 20:45', '停用：营业面积缺失 23%', '已停用'), H('v2026.08.2', undefined, '陈立军', '2026-08-12 10:20', '创建')],
  },
  {
    id: 'K-022', name: '高价值会员', type: '术语', status: '已停用', since: 'v2026.08.2', stoppedIn: 'v2026.09.3', updatedAt: '2026-09-19 19:12', owner: '孙浩然',
    definition: '近 12 个月消费金额前 10% 的会员。已由会员等级（金卡及以上）替代。',
    calc: 'PERCENT_RANK(会员.近12个月消费金额) ≥ 0.9', synonyms: ['高价值客户', 'VIP'], applies: [O('member', '会员')],
    period: '2026-08-12 至 2026-09-19', maps: '对象集', filters: [], tries: ['高价值会员复购率'], refs: [],
    history: [H('v2026.09.3', 'CR-0002', '孙浩然', '2026-09-19 19:12', '停用：改用会员等级', '已停用'), H('v2026.08.2', undefined, '孙浩然', '2026-08-12 10:20', '创建')],
  },
  {
    id: 'K-023', name: '闭店门店', type: '业务规则', status: '已发布', since: 'v2026.10.2', updatedAt: '2026-10-17 20:45', owner: '陈立军',
    definition: '门店状态为“闭店”且闭店日期在统计期之前的门店，默认不出现在门店排名里；问题里说“含闭店”时才计入。',
    calc: '门店.状态 = 闭店 AND 门店.闭店日期 < 统计期起始', synonyms: ['已关店', '撤店'], applies: [O('store', '门店')],
    period: '长期有效', maps: '默认过滤（门店范围）',
    filters: [{ field: '门店.状态', cond: '≠ 闭店（统计期前闭店）', note: '门店排名、门店列表默认过滤' }],
    tries: ['上月门店销售额排名'], refs: [{ type: '认证问答', name: '门店销售额排名', note: '默认过滤' }],
    history: [H('v2026.10.2', 'CR-0005', '陈立军', '2026-10-17 20:45', '创建', '生效中')],
  },
  {
    id: 'K-024', name: '促销补贴', type: '术语', status: '草稿', updatedAt: '2026-10-24 18:40', owner: '何静', cr: 'CR-0011',
    definition: '订单行上由平台券和品牌方承担的优惠金额之和，不含门店自担的折扣。毛利、毛利率按扣除后计算。',
    calc: '订单行.促销补贴 = 平台券分摊 + 品牌补贴分摊', synonyms: ['补贴', '平台补贴', '品牌补贴'],
    applies: [O('order_line', '订单行'), O('promotion', '促销活动'), M('gross_margin', '毛利率')], period: '随 v2026.10.4 生效', maps: '计算说明',
    filters: [], tries: ['上月促销补贴金额', '各品牌补贴占销售额比例'], refs: [{ type: '指标', name: '毛利率', note: '草稿口径 v3 引用', hover: { kind: 'metric', id: 'gross_margin' } }],
    history: [H('v2026.10.4', 'CR-0011', '何静', '2026-10-24 18:40', '新增', '草稿')],
  },
  {
    id: 'K-025', name: '自营与联营', type: '术语', status: '已发布', since: 'v2026.09.4', updatedAt: '2026-09-28 21:30', owner: '王敏',
    definition: '自营：公司采购入库、自负盈亏；联营：品牌方驻场经营、按扣点结算。销售额默认含联营，毛利只算自营。',
    calc: '商品.经营方式 ∈ {自营, 联营}', synonyms: ['自营', '联营', '扣点'], applies: [O('product', '商品'), O('supplier', '供应商'), M('gross_margin', '毛利率')],
    period: '长期有效', maps: '维度「经营方式」', filters: [{ field: '商品.经营方式', cond: '= 自营', note: '只对毛利、毛利率默认加' }],
    tries: ['自营商品毛利率'], refs: [{ type: '指标', name: '毛利率', note: '默认只算自营', hover: { kind: 'metric', id: 'gross_margin' } }],
    history: [H('v2026.09.4', 'CR-0003', '王敏', '2026-09-28 21:30', '创建', '生效中')],
  },
  {
    id: 'K-026', name: '日均销售额', type: '口径说明', status: '已发布', since: 'v2026.09.3', updatedAt: '2026-09-19 19:12', owner: '陈立军',
    definition: '统计期销售额 / 营业天数；门店停业日不计入天数。',
    calc: '销售额（不含税） / COUNT(DISTINCT 营业日)', synonyms: ['日均', '日均销售'], applies: [M('gmv', '销售额（不含税）'), O('store', '门店')],
    period: '长期有效', maps: '计算说明', filters: [], tries: ['大促期日均销售额'], refs: [{ type: '看板', name: '门店日报', note: '' }],
    history: [H('v2026.09.3', 'CR-0002', '陈立军', '2026-09-19 19:12', '创建', '生效中')],
  },
];

/** 同义词冲突：同一个词指向多条在用知识（不含已停用）；返回 词 → 条目 id 列表 */
export function synonymConflicts(list: KEntry[]) {
  const m = new Map<string, string[]>();
  list.filter((e) => e.status !== '已停用').forEach((e) => [e.name, ...e.synonyms].forEach((w) => m.set(w, [...(m.get(w) ?? []), e.id])));
  return new Map([...m].filter(([, ids]) => new Set(ids).size > 1));
}

/** 冲突词的默认处理（示例）：没写的按“先追问用户” */
export const CONFLICT_RULE: Record<string, string> = {
  老客: '默认按「复购客户」理解；问题里出现“注册”“积分”“权益”时按「老会员」',
  大促: '默认取离提问日期最近的一次大促（当前为双十一）',
  到家: '先追问：线上渠道全部，还是只看配送到家',
  毛利率: '口径说明与指标同名，以指标为准，回答里附口径说明',
  销售额: '指向指标选择说明，默认不含税',
};
