/**
 * 变更与发布（静态本体负责人）。变更单状态：草稿 → 审核中 → 已通过 → 整版发布；可驳回。
 * 草稿三张（CR-0012 / 0011 / 0009）与 releases.ts 的 DRAFTS 一致；已发布的与 RELEASES 一致。
 * 校验项编号与《静态本体-后台表结构与接口定义》V1–V17 一致。
 */
export type CRStatus = '草稿' | '审核中' | '已通过' | '已发布' | '已驳回';
export type CRLevel = '重大' | '一般' | '修订'; // MAJOR / MINOR / PATCH
export interface ChangeRequest {
  id: string; title: string; owner: string; reviewer: string; status: CRStatus; level: CRLevel;
  changes: number; updatedAt: string; release?: string; reason: string;
}

export const NEXT_VERSION = 'v2026.10.4';

export const CHANGE_REQUESTS: ChangeRequest[] = [
  { id: 'CR-0012', title: '会员对象拆分“储值卡”', owner: '王敏', reviewer: '陈立军', status: '草稿', level: '重大', changes: 7, updatedAt: '2026-10-25 15:02', reason: '储值卡余额和会员积分分开管理，问数需要能单独查储值卡' },
  { id: 'CR-0011', title: '毛利率口径扣除促销补贴', owner: '何静', reviewer: '刘婷', status: '草稿', level: '重大', changes: 3, updatedAt: '2026-10-24 18:40', reason: '财务要求毛利按扣除平台和品牌促销补贴后计算，与月报一致' },
  { id: 'CR-0010', title: '退款率改按退款完成时间统计', owner: '刘婷', reviewer: '陈立军', status: '审核中', level: '重大', changes: 5, updatedAt: '2026-10-25 11:20', reason: '按申请时间统计会把后来被拒的退款也算进去，与财务口径不一致' },
  { id: 'CR-0009', title: '门店新增“业态”枚举值“前置仓”', owner: '陈立军', reviewer: '王敏', status: '草稿', level: '一般', changes: 1, updatedAt: '2026-10-21 10:26', reason: '新开 6 家前置仓，需要按业态单独统计' },
  { id: 'CR-0008', title: '出库及时率补充同义词“发货及时率”', owner: '周晓峰', reviewer: '王敏', status: '已通过', level: '修订', changes: 2, updatedAt: '2026-10-24 16:05', reason: '门店运营常说“发货及时率”，问数召回不到' },
  { id: 'CR-0007', title: '销售额改为不含税口径；新增 gmv_tax', owner: '王敏', reviewer: '陈立军', status: '已发布', level: '重大', changes: 6, updatedAt: '2026-10-24 21:17', release: 'v2026.10.3', reason: '与财务月报口径统一' },
  { id: 'CR-0006', title: '门店新增“店长”关系（关联员工）', owner: '孙浩然', reviewer: '王敏', status: '已驳回', level: '一般', changes: 2, updatedAt: '2026-10-22 14:48', reason: '门店运营想按店长查业绩' },
  { id: 'CR-0005', title: '门店新增“大区”关系', owner: '陈立军', reviewer: '王敏', status: '已发布', level: '一般', changes: 3, updatedAt: '2026-10-17 20:45', release: 'v2026.10.2', reason: '组织调整后按大区考核' },
  { id: 'CR-0004', title: '订单状态“已发货”统一为“已出库”', owner: '王敏', reviewer: '周晓峰', status: '已发布', level: '一般', changes: 4, updatedAt: '2026-10-09 22:03', release: 'v2026.10.1', reason: 'ERP 与 WMS 叫法不一致，问数出现两个状态' },
  { id: 'CR-0003', title: '商品新增保质期属性', owner: '刘婷', reviewer: '周晓峰', status: '已发布', level: '修订', changes: 2, updatedAt: '2026-09-28 21:30', release: 'v2026.09.4', reason: '临期商品预警需要保质期' },
  { id: 'CR-0002', title: '复购客户定义改为 90 天内 ≥2 单', owner: '孙浩然', reviewer: '刘婷', status: '已发布', level: '重大', changes: 3, updatedAt: '2026-09-19 19:12', release: 'v2026.09.3', reason: '原定义 30 天过短，复购率波动大' },
  { id: 'CR-0001', title: '会员等级新增“黑金”', owner: '吴佳', reviewer: '王敏', status: '已驳回', level: '修订', changes: 1, updatedAt: '2026-09-12 10:02', reason: '会员体系调整' },
];

export type DiffKind = '新增' | '修改' | '删除';
export interface DiffRow { key: string; type: string; target: string; field: string; kind: DiffKind; before: string; after: string }
export type CheckResult = '通过' | '警告' | '阻断' | '不适用';
export interface CheckRow { code: string; name: string; result: CheckResult; note: string }
export interface ImpactItem { kind: string; name: string; note: string; hover?: { kind: string; id: string } }
export interface TrialRow { period: string; before: string; after: string; delta: string }

const CHECK_NAMES: [string, string][] = [
  ['V1', '主键与映射完整'], ['V2', '主键唯一性抽检'], ['V3', '关系两端与类型兼容'], ['V4', '枚举挂取值字典'],
  ['V5', '指标引用的属性已映射'], ['V6', '允许的维度可到达'], ['V7', '已发布 API 名不删不改'], ['V8', '改公式或过滤须升版本'],
  ['V9', '同义词冲突'], ['V10', '层级成员唯一父级'], ['V11', '自然语言描述齐全'], ['V12', '影响分析'],
  ['V13', '多路径须指定默认路径'], ['V14', '各目标库 EXPLAIN 通过'], ['V15', '重大改动写明迁移方式'], ['V16', '冲突策略时间字段'],
  ['V17', '同名词唯一默认'],
];
const checks = (over: Record<string, [CheckResult, string]>): CheckRow[] =>
  CHECK_NAMES.map(([code, name]) => ({ code, name, result: over[code]?.[0] ?? '通过', note: over[code]?.[1] ?? '' }));

export interface CRDetail { diffs: DiffRow[]; checks: CheckRow[]; impact: { direct: ImpactItem[]; defs: ImpactItem[]; usage: ImpactItem[] }; trial?: { title: string; rows: TrialRow[] }; migration?: string }

const D_0010: CRDetail = {
  diffs: [
    { key: '1', type: '指标', target: '退款率', field: '时间字段', kind: '修改', before: '退款单.申请时间', after: '退款单.退款完成时间' },
    { key: '2', type: '指标', target: '退款率', field: '默认过滤', kind: '修改', before: '退款状态 ∈ {已申请, 已完成}', after: '退款状态 = 已完成' },
    { key: '3', type: '指标', target: '退款率', field: '口径版本', kind: '修改', before: 'v2', after: 'v3' },
    { key: '4', type: '属性', target: '退款单', field: '退款完成时间 refund_done_at', kind: '新增', before: '', after: 'timestamp · 时间维度' },
    { key: '5', type: '字段映射', target: '退款单.退款完成时间', field: '源字段', kind: '新增', before: '', after: 'ERP.T_REFUND.finish_time（DATE，按 Asia/Shanghai 解释）' },
  ],
  checks: checks({
    V9: ['警告', '“退款完成率”已是“退款单完成率”的同义词，建议在业务知识里确认'],
    V13: ['不适用', '本次未改关系'],
    V14: ['通过', '达梦 DM8、金仓 KES（Oracle 兼容）均通过'],
    V15: ['通过', '已写明：历史告警和已保存的页面保留旧口径 v2'],
    V16: ['不适用', '退款单使用“用户编辑优先”'],
  }),
  impact: {
    direct: [{ kind: '指标', name: '退款率', note: 'v2 → v3' }, { kind: '属性', name: '退款单.退款完成时间', note: '新增' }],
    defs: [{ kind: '指标', name: '销售额（不含税）', note: '共用退款行，不受影响', hover: { kind: 'metric', id: 'gmv' } }],
    usage: [
      { kind: '看板', name: '华东大区销售周报', note: '退款率卡片按新口径重算' },
      { kind: '认证问答', name: '上个月各大区的退款率', note: '需重新认证' },
      { kind: '评测题', name: '退款相关 4 题', note: '发布前自动回放' },
      { kind: '规则', name: '无', note: '没有规则引用退款率' },
    ],
  },
  trial: { title: '试跑对比（华东大区，按新旧口径）', rows: [
    { period: '2026-07', before: '3.42%', after: '3.05%', delta: '−0.37 pp' },
    { period: '2026-08', before: '3.18%', after: '2.91%', delta: '−0.27 pp' },
    { period: '2026-09', before: '3.36%', after: '3.12%', delta: '−0.24 pp' },
  ] },
  migration: '历史告警和已保存的页面保留旧口径 v2；新提问按 v3 回答。',
};

const D_0011: CRDetail = {
  diffs: [
    { key: '1', type: '指标', target: '毛利额', field: '公式', kind: '修改', before: '不含税销售额 − 不含税成本', after: '不含税销售额 − 不含税成本 − 促销补贴' },
    { key: '2', type: '指标', target: '毛利率', field: '口径版本', kind: '修改', before: 'v2', after: 'v3' },
    { key: '3', type: '属性', target: '订单行', field: '促销补贴 promo_subsidy', kind: '新增', before: '', after: 'decimal · 元' },
  ],
  checks: checks({
    V5: ['阻断', '订单行.促销补贴 还没有字段映射'],
    V14: ['阻断', '金仓 KES：促销补贴子查询 EXPLAIN 失败（字段不存在）'],
    V15: ['警告', '还没写已有页面和告警的迁移方式'],
    V13: ['不适用', '本次未改关系'], V16: ['不适用', ''],
  }),
  impact: {
    direct: [{ kind: '指标', name: '毛利额', note: 'v2 → v3' }, { kind: '属性', name: '订单行.促销补贴', note: '新增' }],
    defs: [{ kind: '指标', name: '毛利率', note: '由毛利额派生，跟着升到 v3', hover: { kind: 'metric', id: 'gross_margin' } }],
    usage: [
      { kind: '规则', name: 'R-007 商品毛利率越线', note: '阈值 18.00% 需按新口径重估' },
      { kind: '看板', name: '品类毛利看板', note: '毛利卡片按新口径重算' },
      { kind: '认证问答', name: '各品类毛利率排名', note: '需重新认证' },
    ],
  },
};

const D_0008: CRDetail = {
  diffs: [
    { key: '1', type: '同义词', target: '出库及时率', field: '同义词', kind: '新增', before: '', after: '发货及时率' },
    { key: '2', type: '业务知识', target: '出库及时率', field: '说明', kind: '修改', before: '出库时长 ≤ 24 小时的订单占比', after: '出库时长 ≤ 24 小时的订单占比；门店常说的“发货及时率”即此指标' },
  ],
  checks: checks({ V13: ['不适用', ''], V15: ['不适用', '修订级改动'], V16: ['不适用', ''] }),
  impact: {
    direct: [{ kind: '同义词', name: '发货及时率', note: '指向出库及时率' }],
    defs: [{ kind: '指标', name: '出库及时率', note: '口径不变，版本不变', hover: { kind: 'metric', id: 'ship_ontime' } }],
    usage: [{ kind: '评测题', name: '履约相关 3 题', note: '召回率预计提升' }],
  },
};

export const CR_DETAILS: Record<string, CRDetail> = { 'CR-0010': D_0010, 'CR-0011': D_0011, 'CR-0008': D_0008 };
export const crDetail = (id: string) => CR_DETAILS[id] ?? D_0010;

export interface ReviewEvent { time: string; text: string; desc?: string; status?: string }
export const REVIEW_LOG: Record<string, ReviewEvent[]> = {
  'CR-0010': [
    { time: '2026-10-25 11:20', text: '刘婷 提交审核', desc: '5 处改动，发布校验无阻断' },
    { time: '2026-10-25 10:52', text: '系统 完成发布校验', desc: '通过 13 · 警告 1 · 不适用 3', status: 'warn' },
    { time: '2026-10-24 17:36', text: '刘婷 补充迁移方式', desc: '历史告警和已保存的页面保留旧口径 v2' },
    { time: '2026-10-24 15:10', text: '刘婷 创建变更单' },
  ],
};
export const reviewLog = (cr: ChangeRequest): ReviewEvent[] => REVIEW_LOG[cr.id] ?? [
  ...(cr.status === '已发布' ? [{ time: cr.updatedAt, text: `${cr.reviewer} 整版发布为 ${cr.release}` }] : []),
  ...(cr.status === '已驳回' ? [{ time: cr.updatedAt, text: `${cr.reviewer} 驳回`, desc: '员工是敏感对象，请先在权限里定好可见范围再提', status: 'error' }] : []),
  ...(cr.status === '已通过' ? [{ time: cr.updatedAt, text: `${cr.reviewer} 审核通过`, desc: `等待整版发布 ${NEXT_VERSION}` }] : []),
  { time: cr.updatedAt, text: `${cr.owner} 创建变更单` },
];
