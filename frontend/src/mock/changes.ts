/**
 * 变更与发布（静态本体负责人）。变更单状态：草稿 → 审核中 → 已通过 → 整版发布；审核中可驳回，驳回后可改回草稿。
 * 草稿 CR-0014 / 0013 / 0012 / 0011 / 0009 与 releases.ts 的 DRAFTS、parts/useDraftView 的 TARGETS 一致；
 * 已发布的单子与 RELEASES 一致。校验项编号与《静态本体-后台表结构与接口定义》V1–V17 一致。
 * 每张单子都有自己的改动、校验、影响分析；没有的数据（试跑、迁移方式）就不给，页面显示“暂无”。
 * 评审记录里的校验计数由 checks 现算，不写死。
 */
import { releaseByVer } from './releases';

export type CRStatus = '草稿' | '审核中' | '已通过' | '已发布' | '已驳回';
export type CRLevel = '重大' | '一般' | '修订'; // MAJOR / MINOR / PATCH
export interface ChangeRequest {
  id: string; title: string; owner: string; reviewer: string; status: CRStatus; level: CRLevel;
  changes: number; updatedAt: string; release?: string; reason: string;
  /** 已驳回：驳回原因 */
  rejectReason?: string;
}

/** 当前登录用户的兜底值；页面优先读 app store 的 user.name */
export const CURRENT_USER = '王敏';
export const NEXT_VERSION = 'v2026.10.4';
export const CURRENT_VERSION = 'v2026.10.3';

export const CHANGE_REQUESTS: ChangeRequest[] = [
  { id: 'CR-0016', title: '新增指标“客单价（不含税）”', owner: '刘婷', reviewer: '王敏', status: '审核中', level: '一般', changes: 3, updatedAt: '2026-10-25 14:10', reason: '门店运营周会要看客单价，目前问数只能现算' },
  { id: 'CR-0015', title: '有效订单数补充同义词“成交单量”', owner: '何静', reviewer: '刘婷', status: '已通过', level: '修订', changes: 2, updatedAt: '2026-10-25 10:18', reason: '财务和电商常说“成交单量”，问数召回不到' },
  { id: 'CR-0014', title: '调整大区预算：超 5% 增加二级审批', owner: '刘婷', reviewer: '陈立军', status: '草稿', level: '一般', changes: 3, updatedAt: '2026-10-25 11:26', reason: '预算调整幅度超过 5% 时需要财务 BP 一起审批' },
  { id: 'CR-0013', title: '门店价格变更：增加促销价字段', owner: '王敏', reviewer: '周晓峰', status: '草稿', level: '修订', changes: 2, updatedAt: '2026-10-24 17:52', reason: '用友 ERP 价格档案开始推送促销价，规则要按促销价判断毛利' },
  { id: 'CR-0012', title: '会员对象拆分“储值卡”', owner: '王敏', reviewer: '陈立军', status: '草稿', level: '重大', changes: 7, updatedAt: '2026-10-25 15:02', reason: '储值卡余额和会员积分分开管理，问数需要能单独查储值卡' },
  { id: 'CR-0011', title: '毛利率口径扣除促销补贴', owner: '何静', reviewer: '刘婷', status: '草稿', level: '重大', changes: 4, updatedAt: '2026-10-24 18:40', reason: '财务要求毛利按扣除平台和品牌促销补贴后计算，与月报一致' },
  { id: 'CR-0010', title: '退款率改按退款完成时间统计', owner: '刘婷', reviewer: '陈立军', status: '审核中', level: '重大', changes: 5, updatedAt: '2026-10-25 11:20', reason: '按申请时间统计会把后来被拒的退款也算进去，与财务口径不一致' },
  { id: 'CR-0009', title: '门店新增“业态”枚举值“前置仓”', owner: '陈立军', reviewer: '王敏', status: '草稿', level: '一般', changes: 1, updatedAt: '2026-10-21 10:26', reason: '新开 6 家前置仓，需要按业态单独统计' },
  { id: 'CR-0008', title: '出库及时率补充同义词“发货及时率”', owner: '周晓峰', reviewer: '王敏', status: '已通过', level: '修订', changes: 2, updatedAt: '2026-10-24 16:05', reason: '门店运营常说“发货及时率”，问数召回不到' },
  { id: 'CR-0007', title: '销售额改为不含税口径；新增 gmv_tax', owner: '王敏', reviewer: '陈立军', status: '已发布', level: '重大', changes: 6, updatedAt: '2026-10-24 21:17', release: 'v2026.10.3', reason: '与财务月报口径统一' },
  { id: 'CR-0006', title: '门店新增“店长”关系（关联员工）', owner: '孙浩然', reviewer: '王敏', status: '已驳回', level: '一般', changes: 2, updatedAt: '2026-10-22 14:48', reason: '门店运营想按店长查业绩', rejectReason: '员工是敏感对象，请先在权限里定好店长信息的可见范围再提' },
  { id: 'CR-0005', title: '门店新增“大区”关系', owner: '陈立军', reviewer: '王敏', status: '已发布', level: '一般', changes: 3, updatedAt: '2026-10-17 20:45', release: 'v2026.10.2', reason: '组织调整后按大区考核' },
  { id: 'CR-0004', title: '订单状态“已发货”统一为“已出库”', owner: '王敏', reviewer: '周晓峰', status: '已发布', level: '一般', changes: 4, updatedAt: '2026-10-09 22:03', release: 'v2026.10.1', reason: 'ERP 与 WMS 叫法不一致，问数出现两个状态' },
  { id: 'CR-0003', title: '商品新增保质期属性', owner: '刘婷', reviewer: '周晓峰', status: '已发布', level: '修订', changes: 2, updatedAt: '2026-09-28 21:30', release: 'v2026.09.4', reason: '临期商品预警需要保质期' },
  { id: 'CR-0002', title: '复购客户定义改为 90 天内 ≥2 单', owner: '孙浩然', reviewer: '刘婷', status: '已发布', level: '重大', changes: 3, updatedAt: '2026-09-19 19:12', release: 'v2026.09.3', reason: '原定义 30 天过短，复购率波动大' },
  { id: 'CR-0001', title: '会员等级新增“黑金”', owner: '吴佳', reviewer: '王敏', status: '已驳回', level: '修订', changes: 1, updatedAt: '2026-09-12 10:02', reason: '会员体系调整', rejectReason: 'CRM 还没有“黑金”等级码，源数据里取不到，等 CRM 上线后再提' },
];

export type DiffKind = '新增' | '修改' | '删除';
export interface DiffRow { key: string; type: string; target: string; field: string; kind: DiffKind; before: string; after: string }
export type CheckResult = '通过' | '警告' | '阻断' | '信息' | '不适用';
export interface CheckRow { code: string; name: string; result: CheckResult; note: string }
/** count：认证问答、评测题等可数的条数，用于影响摘要（不填按 1 条算） */
export interface ImpactItem { kind: string; name: string; note: string; count?: number; hover?: { kind: string; id: string } }
export interface TrialRow { period: string; before: string; after: string; delta: string }
/** 编辑草稿时跳到哪个页面的草稿视图 */
export interface EditTarget { kind: 'object' | 'metric' | 'action' | 'event' | 'rule' | 'link' | 'dimension'; id: string }

const CHECK_NAMES: [string, string][] = [
  ['V1', '主键与映射完整'], ['V2', '主键唯一性抽检'], ['V3', '关系两端与类型兼容'], ['V4', '枚举挂取值字典'],
  ['V5', '指标引用的属性已映射'], ['V6', '允许的维度可到达'], ['V7', '已发布 API 名不删不改'], ['V8', '改公式或过滤须升版本'],
  ['V9', '同义词冲突'], ['V10', '层级成员唯一父级'], ['V11', '自然语言描述齐全'], ['V12', '影响分析'],
  ['V13', '多路径须指定默认路径'], ['V14', '各目标库 EXPLAIN 通过'], ['V15', '重大改动写明迁移方式'], ['V16', '冲突策略时间字段'],
  ['V17', '同名词唯一默认'],
];
const checks = (over: Record<string, [CheckResult, string]>): CheckRow[] =>
  CHECK_NAMES.map(([code, name]) => ({ code, name, result: over[code]?.[0] ?? '通过', note: over[code]?.[1] ?? '' }));
const NA = (note = ''): [CheckResult, string] => ['不适用', note];

export interface CRDetail {
  diffs: DiffRow[]; checks: CheckRow[];
  impact: { direct: ImpactItem[]; defs: ImpactItem[]; usage: ImpactItem[] };
  trial?: { title: string; rows: TrialRow[] };
  migration?: string;
  /** 合并校验用：本单改到的对象 / 属性 / 指标 / 动作 / 事件（键形如 metric:order_cnt） */
  touches: string[];
  /** 合并校验用：本单新增的同义词 */
  synonyms?: { word: string; to: string }[];
  edit?: EditTarget;
  /** 评审记录的时间点；节点按状态生成 */
  times: { created: string; checked?: string; submitted?: string; reviewed?: string };
  /** 额外的评审记录节点（例如补充迁移方式） */
  notes?: ReviewEvent[];
}
const row = (key: number, type: string, target: string, field: string, kind: DiffKind, before: string, after: string): DiffRow =>
  ({ key: String(key), type, target, field, kind, before, after });

export const CR_DETAILS: Record<string, CRDetail> = {
  'CR-0016': {
    diffs: [
      row(1, '指标', '客单价（不含税）atv', '公式', '新增', '', '销售额（不含税） / 有效订单数'),
      row(2, '指标', '客单价（不含税）', '允许的维度', '新增', '', '门店、渠道、日期'),
      row(3, '指标', '有效订单数', '业务知识说明', '修改', '状态 ≠ 已取消的订单数', '状态 ≠ 已取消的订单数；也是客单价的分母'),
    ],
    checks: checks({ V3: NA('本次未改关系'), V4: NA(''), V7: NA('新指标'), V8: NA('新指标，从 v1 开始'), V10: NA(''), V13: NA('本次未改关系'), V15: NA('一般级改动'), V16: NA('') }),
    impact: {
      direct: [{ kind: '指标', name: '客单价（不含税）', note: '新增 v1', hover: { kind: 'metric', id: 'atv' } }, { kind: '指标', name: '有效订单数', note: '只改说明，口径不变', hover: { kind: 'metric', id: 'order_cnt' } }],
      defs: [{ kind: '指标', name: '销售额（不含税）', note: '作为分子引用，不受影响', hover: { kind: 'metric', id: 'gmv' } }],
      usage: [{ kind: '评测题', name: '客单价相关 3 题', note: '新增，发布前自动回放', count: 3 }, { kind: '看板', name: '门店经营日报', note: '可新增客单价卡片' }],
    },
    trial: { title: '试跑（全部门店，新口径）', rows: [
      { period: '2026-07', before: '--', after: '¥68.42', delta: '--' },
      { period: '2026-08', before: '--', after: '¥67.95', delta: '--' },
      { period: '2026-09', before: '--', after: '¥69.10', delta: '--' },
    ] },
    touches: ['metric:atv', 'metric:order_cnt'],
    synonyms: [{ word: '客单', to: '客单价（不含税）' }],
    edit: { kind: 'metric', id: 'atv' },
    times: { created: '2026-10-24 18:31', checked: '2026-10-25 14:02', submitted: '2026-10-25 14:10' },
  },
  'CR-0015': {
    diffs: [
      row(1, '同义词', '有效订单数', '同义词', '新增', '', '成交单量'),
      row(2, '业务知识', '有效订单数', '说明', '修改', '状态 ≠ 已取消的订单数', '状态 ≠ 已取消的订单数；财务和电商说的“成交单量”即此指标'),
    ],
    checks: checks({ V3: NA(''), V4: NA(''), V8: NA('口径不变'), V9: ['通过', '“成交单量”此前没有指向任何指标'], V10: NA(''), V13: NA(''), V15: NA('修订级改动'), V16: NA('') }),
    impact: {
      direct: [{ kind: '同义词', name: '成交单量', note: '指向有效订单数' }],
      defs: [{ kind: '指标', name: '有效订单数', note: '口径不变，版本不变', hover: { kind: 'metric', id: 'order_cnt' } }],
      usage: [{ kind: '评测题', name: '订单量相关 2 题', note: '召回率预计提升', count: 2 }],
    },
    touches: ['metric:order_cnt'],
    synonyms: [{ word: '成交单量', to: '有效订单数' }],
    times: { created: '2026-10-24 15:40', checked: '2026-10-24 15:52', submitted: '2026-10-24 16:00', reviewed: '2026-10-25 10:18' },
  },
  'CR-0014': {
    diffs: [
      row(1, '动作类型', '调整大区预算', '提交条件', '修改', '大区总监审批', '幅度 ≤ 5%：大区总监；> 5%：大区总监 + 财务 BP'),
      row(2, '动作类型', '调整大区预算', '参数 调整幅度 pct', '新增', '', '计算参数 · (新预算 − 原预算) / 原预算'),
      row(3, '动作类型', '调整大区预算', '写回 泛微 OA 流程', '修改', 'WF-BUD-01 一级审批', 'WF-BUD-02 按幅度分级审批'),
    ],
    checks: checks({
      V1: NA('未改对象'), V2: NA(''), V3: NA(''), V4: NA(''), V5: NA(''), V6: NA(''), V10: NA(''), V13: NA(''), V14: NA('不涉及查询'),
      V15: ['警告', '还没写在途审批单怎么处理：建议仍按一级审批走完'], V16: NA(''),
    }),
    impact: {
      direct: [{ kind: '动作类型', name: '调整大区预算', note: 'v2 → v3' }],
      defs: [{ kind: '对象类型', name: '大区', note: '动作挂在大区上，对象定义不变' }],
      usage: [{ kind: '外部系统', name: '泛微 OA', note: '需上线流程模板 WF-BUD-02' }, { kind: '动作中心', name: '在途预算调整 2 单', note: '按旧流程走完' }],
    },
    touches: ['action:adjust_region_budget'],
    edit: { kind: 'action', id: 'adjust_region_budget' },
    times: { created: '2026-10-25 09:12', checked: '2026-10-25 11:26' },
  },
  'CR-0013': {
    diffs: [
      row(1, '事件类型', '门店价格变更', '载荷字段 促销价 promoPrice', '新增', '', 'decimal · 元 · 可选'),
      row(2, '事件类型', '门店价格变更', '载荷字段 促销截止 promoEndAt', '新增', '', '日期时间 · 可选'),
    ],
    checks: checks({
      V1: NA('未改对象'), V2: NA(''), V3: NA(''), V4: NA(''), V5: NA(''), V6: NA(''), V7: ['通过', '只新增可选字段，已有字段不变'], V8: NA(''), V9: NA(''), V10: NA(''),
      V11: ['警告', '“促销截止”还没写自然语言描述'], V13: NA(''), V14: NA('不涉及查询'), V15: NA('修订级改动'), V16: NA(''), V17: NA(''),
    }),
    impact: {
      direct: [{ kind: '事件类型', name: '门店价格变更', note: '载荷新增 2 个可选字段' }],
      defs: [{ kind: '对象类型', name: '商品', note: '事件挂在商品上，对象定义不变' }],
      usage: [{ kind: '规则', name: 'R-007 商品毛利率越线', note: '可改为按促销价判断，需另提变更' }, { kind: '外部系统', name: '用友 ERP 价格档案', note: '已在测试环境推送促销价' }],
    },
    touches: ['event:store_price_changed'],
    edit: { kind: 'event', id: 'store_price_changed' },
    times: { created: '2026-10-24 17:20', checked: '2026-10-24 17:52' },
  },
  'CR-0012': {
    diffs: [
      row(1, '对象类型', '储值卡 StoredValueCard', '对象类型', '新增', '', '分组：客户 · 主键：卡号'),
      row(2, '属性', '储值卡', '卡号 card_no', '新增', '', 'string · 主键'),
      row(3, '属性', '储值卡', '余额 balance', '新增', '', 'decimal · 元'),
      row(4, '关系', '会员 → 储值卡', '持有', '新增', '', '1 : N，会员.会员号 = 储值卡.会员号'),
      row(5, '属性', '会员', '储值余额 stored_balance', '修改', '会员表上的余额字段', '标为弃用，值改为汇总名下储值卡余额'),
      row(6, '字段映射', '储值卡', '源表', '新增', '', 'CRM.T_SVC_CARD（每小时增量）'),
      row(7, '指标', '储值卡余额', '公式', '新增', '', 'SUM(储值卡.余额)'),
    ],
    checks: checks({
      V2: ['通过', '抽检 10 万条，卡号无重复'],
      V7: ['通过', '会员.stored_balance 保留并标为弃用，不删 API 名'],
      V10: NA(''), V13: ['通过', '会员 → 储值卡 只有一条路径'],
      V15: ['警告', '迁移方式还没写已保存的页面怎么处理'], V16: ['通过', '储值卡使用“源系统优先”，时间字段 updated_at'],
    }),
    impact: {
      direct: [{ kind: '对象类型', name: '储值卡', note: '新增' }, { kind: '属性', name: '会员.储值余额', note: '弃用，保留一个版本' }],
      defs: [{ kind: '指标', name: '储值卡余额', note: '新增 v1' }, { kind: '指标', name: '会员储值余额', note: '改为由储值卡汇总，结果不变' }],
      usage: [
        { kind: '看板', name: '会员资产看板', note: '储值余额卡片改读储值卡' },
        { kind: '认证问答', name: '各门店会员储值余额', note: '需重新认证' },
        { kind: '评测题', name: '会员储值相关 5 题', note: '发布前自动回放', count: 5 },
      ],
    },
    trial: { title: '试跑对比（会员储值余额 vs 储值卡余额汇总）', rows: [
      { period: '2026-07', before: '¥4,182.6 万', after: '¥4,182.6 万', delta: '0' },
      { period: '2026-08', before: '¥4,317.9 万', after: '¥4,317.9 万', delta: '0' },
      { period: '2026-09', before: '¥4,405.2 万', after: '¥4,405.2 万', delta: '0' },
    ] },
    migration: '会员.储值余额保留一个版本并标为弃用，值改为汇总名下储值卡余额；新提问按储值卡回答。',
    touches: ['object:stored_value_card', 'object:member', 'property:member.stored_balance', 'metric:svc_balance'],
    edit: { kind: 'object', id: 'member' },
    times: { created: '2026-10-25 09:41', checked: '2026-10-25 15:02' },
  },
  'CR-0011': {
    diffs: [
      row(1, '指标', '毛利额', '公式', '修改', '不含税销售额 − 不含税成本', '不含税销售额 − 不含税成本 − 促销补贴'),
      row(2, '指标', '毛利率', '口径版本', '修改', 'v2', 'v3'),
      row(3, '属性', '订单行', '促销补贴 promo_subsidy', '新增', '', 'decimal · 元'),
      row(4, '规则', 'R-007 商品毛利率越线', '条件引用口径', '修改', 'gross_margin@v2（规则 v3）', 'gross_margin@v3（规则 v4）'),
    ],
    checks: checks({
      V5: ['阻断', '订单行.促销补贴 还没有字段映射'],
      V14: ['阻断', '金仓 KES：促销补贴子查询 EXPLAIN 失败（字段不存在）'],
      V15: ['警告', '还没写已有页面和告警的迁移方式'],
      V13: NA('本次未改关系'), V16: NA(''),
    }),
    impact: {
      direct: [{ kind: '指标', name: '毛利额', note: 'v2 → v3' }, { kind: '属性', name: '订单行.促销补贴', note: '新增' }],
      defs: [{ kind: '指标', name: '毛利率', note: '由毛利额派生，跟着升到 v3', hover: { kind: 'metric', id: 'gross_margin' } }],
      usage: [
        { kind: '规则', name: 'R-007 商品毛利率越线', note: '随本单升到 v4，条件改引用毛利率 v3；近 7 天回放命中 3 → 5，阈值 18.00% 不变' },
        { kind: '看板', name: '品类毛利看板', note: '毛利卡片按新口径重算' },
        { kind: '认证问答', name: '各品类毛利率排名', note: '需重新认证' },
      ],
    },
    touches: ['metric:gross_profit', 'metric:gross_margin', 'property:order_line.promo_subsidy'],
    edit: { kind: 'metric', id: 'gross_margin' },
    times: { created: '2026-10-24 16:05', checked: '2026-10-24 18:40' },
  },
  'CR-0010': {
    diffs: [
      row(1, '指标', '退款率', '时间字段', '修改', '退款单.申请时间', '退款单.退款完成时间'),
      row(2, '指标', '退款率', '默认过滤', '修改', '退款状态 ∈ {已申请, 已完成}', '退款状态 = 已完成'),
      row(3, '指标', '退款率', '口径版本', '修改', 'v2', 'v3'),
      row(4, '属性', '退款单', '退款完成时间 refund_done_at', '新增', '', 'timestamp · 时间维度'),
      row(5, '字段映射', '退款单.退款完成时间', '源字段', '新增', '', 'ERP.T_REFUND.finish_time（DATE，按 Asia/Shanghai 解释）'),
    ],
    checks: checks({
      V9: ['警告', '“退款完成率”已是“退款单完成率”的同义词，建议在业务知识里确认'],
      V13: NA('本次未改关系'),
      V14: ['通过', '达梦 DM8、金仓 KES（Oracle 兼容）均通过'],
      V15: ['通过', '已写明：历史告警和已保存的页面保留旧口径 v2'],
      V16: NA('退款单使用“用户编辑优先”'),
    }),
    impact: {
      direct: [{ kind: '指标', name: '退款率', note: 'v2 → v3' }, { kind: '属性', name: '退款单.退款完成时间', note: '新增' }],
      defs: [{ kind: '指标', name: '销售额（不含税）', note: '共用退款行，不受影响', hover: { kind: 'metric', id: 'gmv' } }],
      usage: [
        { kind: '看板', name: '华东大区销售周报', note: '退款率卡片按新口径重算' },
        { kind: '认证问答', name: '上个月各大区的退款率', note: '需重新认证' },
        { kind: '评测题', name: '退款相关 4 题', note: '发布前自动回放', count: 4 },
        { kind: '规则', name: '无', note: '没有规则引用退款率', count: 0 },
      ],
    },
    trial: { title: '试跑对比（华东大区，按新旧口径）', rows: [
      { period: '2026-07', before: '3.42%', after: '3.05%', delta: '−0.37 pp' },
      { period: '2026-08', before: '3.18%', after: '2.91%', delta: '−0.27 pp' },
      { period: '2026-09', before: '3.36%', after: '3.12%', delta: '−0.24 pp' },
    ] },
    migration: '历史告警和已保存的页面保留旧口径 v2；新提问按 v3 回答。',
    touches: ['metric:refund_rate', 'object:refund', 'property:refund.refund_done_at'],
    synonyms: [{ word: '退款完成率', to: '退款率' }],
    times: { created: '2026-10-24 15:10', checked: '2026-10-25 10:52', submitted: '2026-10-25 11:20' },
    notes: [{ time: '2026-10-24 17:36', text: '刘婷 补充迁移方式', desc: '历史告警和已保存的页面保留旧口径 v2' }],
  },
  'CR-0009': {
    diffs: [row(1, '枚举值', '门店.业态', '取值', '新增', '', '前置仓（FWH）')],
    checks: checks({
      V4: ['通过', '取值字典“门店业态”已加 FWH'],
      V9: ['信息', '“前置仓”与仓库对象名称相近，问数会优先理解为门店业态'],
      V3: NA(''), V8: NA(''), V10: NA(''), V13: NA(''), V15: NA('一般级改动'), V16: NA(''),
    }),
    impact: {
      direct: [{ kind: '枚举值', name: '门店.业态 = 前置仓', note: '新增' }],
      defs: [{ kind: '指标', name: '销售额（不含税）', note: '可按业态拆出前置仓', hover: { kind: 'metric', id: 'gmv' } }],
      usage: [{ kind: '看板', name: '门店业态分布', note: '新增一项' }, { kind: '评测题', name: '业态相关 2 题', note: '发布前自动回放', count: 2 }],
    },
    touches: ['property:store.format'],
    edit: { kind: 'object', id: 'store' },
    times: { created: '2026-10-21 10:02', checked: '2026-10-21 10:26' },
  },
  'CR-0008': {
    diffs: [
      row(1, '同义词', '出库及时率', '同义词', '新增', '', '发货及时率'),
      row(2, '业务知识', '出库及时率', '说明', '修改', '30 分钟内变为“已出库”的订单占比', '30 分钟内变为“已出库”的订单占比；门店常说的“发货及时率”即此指标'),
    ],
    checks: checks({ V3: NA(''), V4: NA(''), V8: NA('口径不变'), V10: NA(''), V13: NA(''), V15: NA('修订级改动'), V16: NA('') }),
    impact: {
      direct: [{ kind: '同义词', name: '发货及时率', note: '指向出库及时率' }],
      defs: [{ kind: '指标', name: '出库及时率', note: '口径不变，版本不变', hover: { kind: 'metric', id: 'ship_ontime' } }],
      usage: [{ kind: '评测题', name: '履约相关 3 题', note: '召回率预计提升', count: 3 }],
    },
    touches: ['metric:ship_ontime'],
    synonyms: [{ word: '发货及时率', to: '出库及时率' }],
    times: { created: '2026-10-24 14:30', checked: '2026-10-24 14:41', submitted: '2026-10-24 14:45', reviewed: '2026-10-24 16:05' },
  },
  'CR-0007': {
    diffs: [
      row(1, '指标', '销售额 gmv', '名称', '修改', '销售额', '销售额（不含税）'),
      row(2, '指标', '销售额（不含税）', '公式', '修改', 'SUM(订单.实付金额) − SUM(退款.退款金额)', 'SUM(订单行.不含税金额) − SUM(退款行.不含税退款金额)'),
      row(3, '指标', '销售额（不含税）', '口径版本', '修改', 'v2', 'v3'),
      row(4, '指标', '销售额（含税）gmv_tax', '公式', '新增', '', 'SUM(订单.实付金额) − SUM(退款.退款金额)'),
      row(5, '属性', '订单行', '不含税金额 amount_ex_tax', '新增', '', 'decimal · 元'),
      row(6, '属性', '退款行', '不含税退款金额 refund_ex_tax', '新增', '', 'decimal · 元'),
    ],
    checks: checks({
      V8: ['通过', '销售额已升到 v3'],
      V9: ['通过', '“含税销售额”指向 gmv_tax，不与 gmv 冲突'],
      V13: NA('本次未改关系'), V16: NA(''),
      V17: ['通过', '同名词“销售额”唯一默认：销售额（不含税）'],
    }),
    impact: {
      direct: [{ kind: '指标', name: '销售额（不含税）', note: 'v2 → v3', hover: { kind: 'metric', id: 'gmv' } }, { kind: '指标', name: '销售额（含税）', note: '新增', hover: { kind: 'metric', id: 'gmv_tax' } }],
      defs: [{ kind: '指标', name: '毛利率', note: '分母改为不含税销售额', hover: { kind: 'metric', id: 'gross_margin' } }],
      usage: [
        { kind: '看板', name: '集团经营首页', note: '金额统一为不含税' },
        { kind: '认证问答', name: '上周各大区销售额', note: '已重新认证' },
        { kind: '认证问答', name: '本月销售额同比', note: '已重新认证' },
        { kind: '评测题', name: '销售额相关 9 题', note: '发布前已回放，全部通过', count: 9 },
      ],
    },
    trial: { title: '试跑对比（全部门店，含税 → 不含税）', rows: [
      { period: '2026-07', before: '¥9,418.3 万', after: '¥8,357.5 万', delta: '−11.3%' },
      { period: '2026-08', before: '¥9,602.7 万', after: '¥8,521.1 万', delta: '−11.3%' },
      { period: '2026-09', before: '¥9,355.0 万', after: '¥8,298.4 万', delta: '−11.3%' },
    ] },
    migration: '已保存的页面和告警保留含税口径 v2；新提问“销售额”按不含税回答，含税请问“含税销售额”。',
    touches: ['metric:gmv', 'metric:gmv_tax', 'property:order_line.amount_ex_tax', 'property:refund_line.refund_ex_tax'],
    synonyms: [{ word: '含税销售额', to: '销售额（含税）' }],
    times: { created: '2026-10-23 16:08', checked: '2026-10-24 11:20', submitted: '2026-10-24 11:26', reviewed: '2026-10-24 18:02' },
  },
  'CR-0006': {
    diffs: [
      row(1, '关系', '门店 → 员工', '店长', '新增', '', 'N : 1，门店.店长工号 = 员工.工号'),
      row(2, '属性', '门店', '店长工号 manager_emp_no', '新增', '', 'string · 映射 HR_CORE.T_STORE_MGR'),
    ],
    checks: checks({
      V3: ['通过', '门店、员工两端类型兼容'],
      V12: ['警告', '员工是敏感对象，影响分析没有写店长信息对谁可见'],
      V8: NA(''), V10: NA(''), V15: NA('一般级改动'), V16: NA(''),
    }),
    impact: {
      direct: [{ kind: '关系', name: '门店 → 员工（店长）', note: '新增' }, { kind: '属性', name: '门店.店长工号', note: '新增' }],
      defs: [{ kind: '对象类型', name: '员工', note: '敏感对象，可见范围未定' }],
      usage: [{ kind: '权限', name: '员工对象行级权限', note: '店长信息可见范围未定' }],
    },
    touches: ['relation:store.manager', 'property:store.manager_emp_no'],
    times: { created: '2026-10-21 16:30', checked: '2026-10-21 16:42', submitted: '2026-10-21 16:50', reviewed: '2026-10-22 14:48' },
  },
  'CR-0005': {
    diffs: [
      row(1, '关系', '门店 → 大区', '所属大区', '新增', '', 'N : 1，门店.大区编码 = 大区.编码'),
      row(2, '属性', '门店', '大区编码 region_code', '新增', '', 'string · 映射 ORG.T_STORE.region_code'),
      row(3, '层级', '组织层级', '层级', '修改', '省份 → 门店', '大区 → 门店（省份改为行政维度，不再是上级）'),
    ],
    checks: checks({
      V3: ['通过', '门店、大区两端类型兼容'],
      V10: ['通过', '486 家门店各属于一个大区'],
      V13: ['通过', '门店 → 大区 默认走“所属大区”，不经过省份'],
      V8: NA(''), V15: NA('一般级改动'), V16: NA(''),
    }),
    impact: {
      direct: [{ kind: '关系', name: '门店 → 大区', note: '新增' }, { kind: '层级', name: '组织层级', note: '增加大区一级' }],
      defs: [{ kind: '指标', name: '销售额（不含税）', note: '新增可用维度：大区', hover: { kind: 'metric', id: 'gmv' } }],
      usage: [
        { kind: '看板', name: '华东大区销售周报', note: '新增' },
        { kind: '规则', name: 'R-021 门店销售额环比下降', note: '按大区汇总告警' },
        { kind: '评测题', name: '大区相关 6 题', note: '发布前已回放，全部通过', count: 6 },
      ],
    },
    touches: ['relation:store.region', 'property:store.region_code', 'hierarchy:org'],
    times: { created: '2026-10-15 10:20', checked: '2026-10-16 15:12', submitted: '2026-10-16 15:30', reviewed: '2026-10-17 11:05' },
  },
  'CR-0004': {
    diffs: [
      row(1, '枚举值', '订单.状态', '取值', '修改', '已发货', '已出库'),
      row(2, '字段映射', '订单.状态', 'ERP 状态码 S3', '修改', '已发货', '已出库'),
      row(3, '字段映射', '订单.状态', 'WMS 状态 OUTBOUND', '新增', '', '已出库'),
      row(4, '同义词', '订单.状态 = 已出库', '同义词', '新增', '', '已发货'),
    ],
    checks: checks({
      V4: ['通过', '取值字典“订单状态”已合并'],
      V9: ['通过', '“已发货”改为“已出库”的同义词，不再是独立状态'],
      V15: ['通过', '已写明：历史订单状态统一改写为已出库'],
      V3: NA(''), V10: NA(''), V13: NA(''), V16: NA(''),
    }),
    impact: {
      direct: [{ kind: '枚举值', name: '订单.状态 = 已出库', note: '合并已发货' }],
      defs: [{ kind: '指标', name: '出库及时率', note: '分子统一按已出库计', hover: { kind: 'metric', id: 'ship_ontime' } }],
      usage: [
        { kind: '规则', name: '订单状态非法迁移', note: '状态机同步更新' },
        { kind: '评测题', name: '订单状态相关 4 题', note: '发布前已回放，全部通过', count: 4 },
      ],
    },
    trial: { title: '试跑对比（出库及时率，全部仓库）', rows: [
      { period: '2026-07', before: '91.24%', after: '96.80%', delta: '+5.56 pp' },
      { period: '2026-08', before: '90.87%', after: '96.95%', delta: '+6.08 pp' },
      { period: '2026-09', before: '91.52%', after: '97.03%', delta: '+5.51 pp' },
    ] },
    migration: '历史订单的“已发货”统一改写为“已出库”；“已发货”保留为同义词。',
    touches: ['property:order.status'],
    synonyms: [{ word: '已发货', to: '订单.状态 = 已出库' }],
    times: { created: '2026-10-08 10:12', checked: '2026-10-08 16:40', submitted: '2026-10-08 16:52', reviewed: '2026-10-09 15:30' },
  },
  'CR-0003': {
    diffs: [
      row(1, '属性', '商品', '保质期天数 shelf_life_days', '新增', '', 'integer · 天'),
      row(2, '字段映射', '商品.保质期天数', '源字段', '新增', '', 'MDM.T_PRODUCT.shelf_life'),
    ],
    checks: checks({ V3: NA(''), V4: NA(''), V8: NA(''), V9: NA(''), V10: NA(''), V13: NA(''), V15: NA('修订级改动'), V16: NA(''), V17: NA('') }),
    impact: {
      direct: [{ kind: '属性', name: '商品.保质期天数', note: '新增' }],
      defs: [{ kind: '对象类型', name: '商品', note: '属性 25 → 26' }],
      usage: [{ kind: '规则', name: '临期商品预警', note: '按保质期计算' }],
    },
    touches: ['property:product.shelf_life_days'],
    times: { created: '2026-09-26 14:00', checked: '2026-09-26 14:12', submitted: '2026-09-26 14:20', reviewed: '2026-09-27 10:40' },
  },
  'CR-0002': {
    diffs: [
      row(1, '对象集', '复购客户', '定义', '修改', '30 天内下单 ≥ 2 次的会员', '90 天内下单 ≥ 2 次的会员'),
      row(2, '指标', '复购率', '口径版本', '修改', 'v1', 'v2'),
      row(3, '业务知识', '复购客户', '说明', '修改', '30 天复购', '90 天复购；与会员中心口径一致'),
    ],
    checks: checks({
      V8: ['通过', '复购率已升到 v2'],
      V15: ['通过', '已写明：历史告警保留 v1'],
      V3: NA(''), V4: NA(''), V10: NA(''), V13: NA(''), V16: NA(''),
    }),
    impact: {
      direct: [{ kind: '对象集', name: '复购客户', note: '30 天 → 90 天' }, { kind: '指标', name: '复购率', note: 'v1 → v2' }],
      defs: [{ kind: '指标', name: '复购客户销售额', note: '跟着重算' }],
      usage: [
        { kind: '规则', name: '大区复购预警', note: '阈值按新口径重估' },
        { kind: '认证问答', name: '各大区复购率', note: '已重新认证' },
        { kind: '评测题', name: '复购相关 5 题', note: '发布前已回放，全部通过', count: 5 },
      ],
    },
    trial: { title: '试跑对比（复购率，全部会员）', rows: [
      { period: '2026-06', before: '18.42%', after: '31.65%', delta: '+13.23 pp' },
      { period: '2026-07', before: '16.90%', after: '31.08%', delta: '+14.18 pp' },
      { period: '2026-08', before: '19.37%', after: '31.92%', delta: '+12.55 pp' },
    ] },
    migration: '历史告警保留 v1；看板上的复购卡片标注口径从 v2026.09.3 起变化。',
    touches: ['objectset:repeat_customer', 'metric:repeat_rate'],
    times: { created: '2026-09-15 09:30', checked: '2026-09-17 11:00', submitted: '2026-09-17 11:10', reviewed: '2026-09-18 16:20' },
  },
  'CR-0001': {
    diffs: [row(1, '枚举值', '会员等级', '取值', '新增', '', '黑金（L5）')],
    checks: checks({
      V4: ['警告', 'CRM 等级码里没有 L5，取值字典映射不到源数据'],
      V3: NA(''), V8: NA(''), V10: NA(''), V13: NA(''), V15: NA('修订级改动'), V16: NA(''),
    }),
    impact: {
      direct: [{ kind: '枚举值', name: '会员等级 = 黑金', note: '新增' }],
      defs: [{ kind: '对象类型', name: '会员等级', note: '取值 5 → 6' }],
      usage: [{ kind: '看板', name: '会员等级分布', note: '新增一项' }],
    },
    touches: ['property:member_level.level'],
    times: { created: '2026-09-10 15:00', checked: '2026-09-10 15:08', submitted: '2026-09-10 15:20', reviewed: '2026-09-12 10:02' },
  },
};

/** 没有详情的单子（例如刚新建的）返回空内容，页面显示“暂无”；不借用其他单子的数据 */
const EMPTY: CRDetail = { diffs: [], checks: [], impact: { direct: [], defs: [], usage: [] }, touches: [], times: { created: '' } };
export const crDetail = (id: string): CRDetail => CR_DETAILS[id] ?? EMPTY;

/** 校验计数：阻断 / 警告 / 信息 / 通过 / 不适用 */
export function checkCounts(rows: CheckRow[]) {
  const c: Record<CheckResult, number> = { 阻断: 0, 警告: 0, 信息: 0, 通过: 0, 不适用: 0 };
  rows.forEach((r) => { c[r.result]++; });
  return c;
}
export function checkSummary(rows: CheckRow[]) {
  const c = checkCounts(rows);
  return (['阻断', '警告', '信息', '通过', '不适用'] as CheckResult[]).filter((k) => c[k]).map((k) => `${k} ${c[k]}`).join(' · ');
}

/** 影响摘要：影响 N 个指标、M 条认证问答、K 道评测题 */
export function impactSummary(d: CRDetail) {
  const all = [...d.impact.direct, ...d.impact.defs, ...d.impact.usage];
  const metrics = new Set(all.filter((i) => i.kind === '指标').map((i) => i.name)).size;
  const sum = (k: string) => all.filter((i) => i.kind === k).reduce((n, i) => n + (i.count ?? 1), 0);
  return { metrics, qa: sum('认证问答'), evals: sum('评测题') };
}

export interface ReviewEvent { time: string; text: string; desc?: string; status?: string }
/** 评审记录（新的在前），按状态和时间点生成；校验计数由 checks 现算 */
export function reviewLog(cr: ChangeRequest): ReviewEvent[] {
  const d = crDetail(cr.id);
  const t = d.times;
  const ev: ReviewEvent[] = [];
  if (t.created) ev.push({ time: t.created, text: `${cr.owner} 创建变更单`, desc: `${d.diffs.length} 处改动` });
  if (t.checked && d.checks.length) {
    const c = checkCounts(d.checks);
    ev.push({ time: t.checked, text: '系统 完成发布校验', desc: checkSummary(d.checks), status: c['阻断'] ? 'error' : c['警告'] ? 'warn' : undefined });
  }
  ev.push(...(d.notes ?? []));
  if (t.submitted) ev.push({ time: t.submitted, text: `${cr.owner} 提交审核`, desc: `审核人 ${cr.reviewer}` });
  if (t.reviewed && cr.status === '已驳回') ev.push({ time: t.reviewed, text: `${cr.reviewer} 驳回`, desc: cr.rejectReason, status: 'error' });
  else if (t.reviewed) ev.push({ time: t.reviewed, text: `${cr.reviewer} 审核通过`, desc: cr.release ? `进入 ${cr.release}` : `等待整版发布 ${NEXT_VERSION}` });
  if (cr.status === '已发布' && cr.release) ev.push({ time: cr.updatedAt, text: `${releaseByVer(cr.release)?.by ?? cr.reviewer} 整版发布为 ${cr.release}` });
  return ev.sort((a, b) => b.time.localeCompare(a.time));
}

/** 草稿编辑入口：跳到对应页面的草稿视图 */
export function editRoute(e?: EditTarget) {
  if (!e) return undefined;
  const map = { object: ['object-types', 'type'], metric: ['metrics', 'metric'], action: ['action-types', 'action'], event: ['event-types', 'event'], rule: ['rules', 'rule'], link: ['link-types', 'link'], dimension: ['dimensions', 'dim'] } as const;
  const [page, key] = map[e.kind];
  return { path: `/build/ontology/${page}`, query: { [key]: e.id, view: 'draft' } };
}

/**
 * 整版发布前的合并校验：把所有已通过的单子合起来再查一次。
 *   M1 同一对象 / 属性 / 指标只被一张单子改动；V9 同义词冲突；V17 同名词唯一默认；各单自身校验无阻断。
 * 结果：rows（结论）和 blocked（有阻断则不能发布）。
 */
export function mergeCheck(crs: ChangeRequest[]) {
  const rows: CheckRow[] = [];
  const owner = new Map<string, string[]>();
  crs.forEach((c) => crDetail(c.id).touches.forEach((k) => owner.set(k, [...(owner.get(k) ?? []), c.id])));
  const overlap = [...owner.entries()].filter(([, ids]) => ids.length > 1);
  rows.push(overlap.length
    ? { code: 'M1', name: '同一定义只被一张单子改动', result: '阻断', note: overlap.map(([k, ids]) => `${ids.join('、')} 都改了 ${k.split(':')[1]}`).join('；') }
    : { code: 'M1', name: '同一定义只被一张单子改动', result: '通过', note: `${crs.length} 张单子改动的定义互不重叠` });

  const words = new Map<string, { to: string; id: string }[]>();
  crs.forEach((c) => (crDetail(c.id).synonyms ?? []).forEach((s) => words.set(s.word, [...(words.get(s.word) ?? []), { to: s.to, id: c.id }])));
  const clash = [...words.entries()].filter(([, v]) => new Set(v.map((x) => x.to)).size > 1);
  rows.push(clash.length
    ? { code: 'V9', name: '同义词冲突', result: '阻断', note: clash.map(([w, v]) => `“${w}”被 ${v.map((x) => `${x.id} 指向 ${x.to}`).join('、')}`).join('；') }
    : { code: 'V9', name: '同义词冲突', result: '通过', note: words.size ? `新增同义词 ${[...words.keys()].map((w) => `“${w}”`).join('、')} 互不冲突` : '没有新增同义词' });
  const dup = [...words.entries()].filter(([, v]) => v.length > 1 && new Set(v.map((x) => x.to)).size === 1);
  rows.push(dup.length
    ? { code: 'V17', name: '同名词唯一默认', result: '警告', note: dup.map(([w]) => `“${w}”被重复添加，发布时只保留一条`).join('；') }
    : { code: 'V17', name: '同名词唯一默认', result: '通过', note: '每个词只有一个默认指向' });

  const selfBlocked = crs.filter((c) => checkCounts(crDetail(c.id).checks)['阻断'] > 0);
  rows.push(selfBlocked.length
    ? { code: 'V1–V17', name: '各单自身校验', result: '阻断', note: `${selfBlocked.map((c) => c.id).join('、')} 有阻断项` }
    : { code: 'V1–V17', name: '各单自身校验', result: '通过', note: '各单均无阻断' });

  const synCRs = crs.filter((c) => crDetail(c.id).synonyms?.length);
  if (synCRs.length > 1) rows.push({ code: '提示', name: '同义词索引', result: '信息', note: `${synCRs.map((c) => c.id).join('、')} 都改了同义词，发布后同义词索引一起重建，约 2 分钟生效` });
  const evals = crs.reduce((n, c) => n + impactSummary(crDetail(c.id)).evals, 0);
  if (evals) rows.push({ code: '提示', name: '评测回放', result: '信息', note: `已按 ${NEXT_VERSION} 草稿回放 ${evals} 道评测题，通过 ${evals} 题，失败 0 题` });
  return { rows, blocked: rows.some((r) => r.result === '阻断') };
}

/** 本体管理员：只有他们能整版发布。 */
export const ONTOLOGY_ADMINS = ['王敏'];

/**
 * 其他页面生成的变更单（例如问数调优“转为同义词”）登记到公共列表，“变更与发布”打开时就能看到同一张单。
 * 单号由公共 useDraftView().start 分配；这里只补列表行和详情（改动、影响、同义词），不改已有单子。
 */
export function registerChange(cr: ChangeRequest, detail?: Partial<CRDetail>) {
  if (!CHANGE_REQUESTS.some((c) => c.id === cr.id)) CHANGE_REQUESTS.unshift(cr);
  if (detail) CR_DETAILS[cr.id] = { ...EMPTY, ...detail, times: detail.times ?? { created: cr.updatedAt } };
}
/** 生成登记用的改动行 */
export const changeRow = row;
