/** 函数示例数据：Python 函数，入参 / 出参用本体对象类型标注；被动作、规则引用；带版本历史。固定种子。 */
import { seeded } from './format';

export interface FnParam { name: string; type: string; objType?: string; desc: string }
export interface FnVersion { ver: string; time: string; author: string; note: string; status: '当前' | '历史' }
export interface FnUse { kind: '动作' | '规则'; name: string }
export interface OntFunction {
  id: string; name: string; title: string; runtime: string; version: string; status: '已发布' | '草稿' | '已停用';
  owner: string; updatedAt: string; timeoutS: number; desc: string;
  inputs: FnParam[]; output: FnParam; usedBy: FnUse[];
  sampleIn: Record<string, unknown>; sampleOut: unknown; avgMs: number; calls7d: number;
  code: string;
}

const P = 'Python 3.11';
const base: Omit<OntFunction, 'version' | 'updatedAt' | 'code'>[] = [
  { id: 'FN-001', name: 'calc_reorder_qty', title: '计算建议补货量', runtime: P, status: '已发布', owner: '孙浩然', timeoutS: 5, desc: '按近 14 天日均销量、在途量和安全库存计算建议补货量，向上取整到箱规。',
    inputs: [{ name: 'inventory', type: 'Inventory', objType: '库存', desc: '要补货的库存记录（仓库 + SKU）' }, { name: 'product', type: 'Product', objType: '商品', desc: '商品，取箱规' }, { name: 'days', type: 'int', desc: '覆盖天数，默认 7' }],
    output: { name: 'qty', type: 'int', desc: '建议补货量（件），已按箱规取整' }, usedBy: [{ kind: '动作', name: '补货申请' }, { kind: '规则', name: 'R-012 可用库存低于安全线' }],
    sampleIn: { inventory: 'WH-SH01 / SKU 6901028', product: 'SKU 6901028 农夫山泉 550ml（箱规 24）', days: 7 }, sampleOut: { qty: 312 }, avgMs: 38, calls7d: 1842 },
  { id: 'FN-002', name: 'region_sales_mom', title: '大区销售额环比', runtime: P, status: '已发布', owner: '陈立军', timeoutS: 10, desc: '计算大区本月至今销售额（不含税）与上月同期的环比。',
    inputs: [{ name: 'region', type: 'Region', objType: '大区', desc: '大区' }, { name: 'as_of', type: 'datetime', desc: '数据截至时间' }],
    output: { name: 'result', type: 'MomResult', desc: '{ current, previous, rate }，金额单位元' }, usedBy: [{ kind: '规则', name: 'R-021 门店销售额环比下降' }, { kind: '动作', name: '指派大区经理跟进' }],
    sampleIn: { region: '华东大区', as_of: '2026-10-24 23:00' }, sampleOut: { current: 16832510.42, previous: 18377204.9, rate: -0.084 }, avgMs: 412, calls7d: 96 },
  { id: 'FN-003', name: 'gross_margin', title: '商品毛利率', runtime: P, status: '已发布', owner: '王敏', timeoutS: 5, desc: '毛利率 =（销售额 − 成本 − 促销补贴）÷ 销售额，均为不含税口径。',
    inputs: [{ name: 'product', type: 'Product', objType: '商品', desc: '商品' }, { name: 'store', type: 'Store', objType: '门店', desc: '门店，可为空表示全部门店' }],
    output: { name: 'margin', type: 'float', desc: '毛利率，0–1' }, usedBy: [{ kind: '规则', name: 'R-007 商品毛利率越线' }, { kind: '动作', name: '发起调价审批' }, { kind: '动作', name: '门店调价' }],
    sampleIn: { product: 'SKU 6920152 维达抽纸 3 层', store: '上海徐汇店' }, sampleOut: { margin: 0.1832 }, avgMs: 54, calls7d: 6230 },
  { id: 'FN-004', name: 'member_churn_score', title: '会员流失评分', runtime: P, status: '已发布', owner: '刘婷', timeoutS: 8, desc: '按最近购买间隔、频次、金额（RFM）给出 0–100 的流失风险分。',
    inputs: [{ name: 'member', type: 'Member', objType: '会员', desc: '会员' }, { name: 'orders', type: 'list[Order]', objType: '订单', desc: '该会员近 180 天订单' }],
    output: { name: 'score', type: 'int', desc: '流失风险分，越高越可能流失' }, usedBy: [{ kind: '规则', name: '会员流失预警' }, { kind: '动作', name: '发放复购召回优惠券' }],
    sampleIn: { member: 'M2024031877（金卡）', orders: '近 180 天 9 单' }, sampleOut: { score: 72, level: '高' }, avgMs: 120, calls7d: 15420 },
  { id: 'FN-005', name: 'coupon_budget_left', title: '优惠券预算余额', runtime: P, status: '已发布', owner: '刘婷', timeoutS: 3, desc: '活动预算减去已发放未过期面额，供发券动作前置校验。',
    inputs: [{ name: 'promotion', type: 'Promotion', objType: '促销活动', desc: '促销活动' }],
    output: { name: 'left', type: 'Decimal', desc: '剩余预算（元）' }, usedBy: [{ kind: '动作', name: '发放复购召回优惠券' }],
    sampleIn: { promotion: 'PROMO-2026-1018 双十一预热' }, sampleOut: { left: 128400.0 }, avgMs: 22, calls7d: 410 },
  { id: 'FN-006', name: 'shipment_overdue_hours', title: '出库超时小时数', runtime: P, status: '已发布', owner: '孙浩然', timeoutS: 3, desc: '出库到签收的时长；未签收按数据截至时间计算。',
    inputs: [{ name: 'shipment', type: 'Shipment', objType: '出库单', desc: '出库单' }],
    output: { name: 'hours', type: 'float', desc: '已用小时数' }, usedBy: [{ kind: '规则', name: 'R-015 出库超 48 小时未签' }, { kind: '动作', name: '发起物流催派' }],
    sampleIn: { shipment: 'SH20261022008817' }, sampleOut: { hours: 51.5, signed: false }, avgMs: 9, calls7d: 28810 },
  { id: 'FN-007', name: 'order_state_transition', title: '订单状态迁移校验', runtime: P, status: '已发布', owner: '陈立军', timeoutS: 2, desc: '校验订单状态迁移是否合法（例如 已取消 → 已出库 不允许）。',
    inputs: [{ name: 'order', type: 'Order', objType: '订单', desc: '订单' }, { name: 'to_status', type: 'str', desc: '目标状态' }],
    output: { name: 'ok', type: 'bool', desc: '是否允许，附原因' }, usedBy: [{ kind: '动作', name: '订单状态修正' }, { kind: '规则', name: '订单状态非法迁移' }],
    sampleIn: { order: 'SO202610230905721（已取消）', to_status: '已出库' }, sampleOut: { ok: false, reason: '已取消的订单不能出库' }, avgMs: 4, calls7d: 96320 },
  { id: 'FN-008', name: 'refund_risk', title: '退款风险判断', runtime: P, status: '已发布', owner: '陈立军', timeoutS: 5, desc: '大额、短时间多次、跨门店退款标记为需人工审核。',
    inputs: [{ name: 'refund', type: 'Refund', objType: '退款', desc: '退款单' }, { name: 'member', type: 'Member', objType: '会员', desc: '会员，可为空' }],
    output: { name: 'risk', type: 'RiskResult', desc: '{ level, reasons[] }' }, usedBy: [{ kind: '动作', name: '退款审核' }],
    sampleIn: { refund: 'RF20261024000913（¥1,280.00）', member: 'M2025071203' }, sampleOut: { level: '需人工审核', reasons: ['单笔 > ¥1,000', '24 小时内第 3 笔'] }, avgMs: 31, calls7d: 3920 },
  { id: 'FN-009', name: 'member_level_eval', title: '会员等级评定', runtime: P, status: '已发布', owner: '刘婷', timeoutS: 5, desc: '按近 12 个月消费额评定会员等级。',
    inputs: [{ name: 'member', type: 'Member', objType: '会员', desc: '会员' }, { name: 'levels', type: 'list[MemberLevel]', objType: '会员等级', desc: '等级定义' }],
    output: { name: 'level', type: 'MemberLevel', objType: '会员等级', desc: '应得等级' }, usedBy: [{ kind: '动作', name: '调整会员等级' }],
    sampleIn: { member: 'M2024031877', levels: '6 个等级' }, sampleOut: { level: '金卡', spend_12m: 18240.5 }, avgMs: 47, calls7d: 2210 },
  { id: 'FN-010', name: 'safety_stock', title: '安全库存计算', runtime: P, status: '已发布', owner: '孙浩然', timeoutS: 10, desc: '按需求波动和补货周期计算安全库存（95% 服务水平）。',
    inputs: [{ name: 'inventory', type: 'Inventory', objType: '库存', desc: '库存记录' }, { name: 'lead_days', type: 'int', desc: '补货周期（天）' }],
    output: { name: 'safety', type: 'int', desc: '安全库存（件）' }, usedBy: [{ kind: '动作', name: '调整安全库存' }, { kind: '规则', name: 'R-012 可用库存低于安全线' }],
    sampleIn: { inventory: 'WH-SH01 / SKU 6901028', lead_days: 3 }, sampleOut: { safety: 186 }, avgMs: 260, calls7d: 730 },
  { id: 'FN-011', name: 'store_price_check', title: '门店调价校验', runtime: P, status: '已发布', owner: '王敏', timeoutS: 3, desc: '调价后毛利率不得低于品类底线，降价超 20% 需审批。',
    inputs: [{ name: 'store', type: 'Store', objType: '门店', desc: '门店' }, { name: 'product', type: 'Product', objType: '商品', desc: '商品' }, { name: 'new_price', type: 'Decimal', desc: '新售价（含税）' }],
    output: { name: 'check', type: 'CheckResult', desc: '{ ok, need_approval, margin_after }' }, usedBy: [{ kind: '动作', name: '门店调价' }, { kind: '动作', name: '发起调价审批' }],
    sampleIn: { store: '杭州西湖店', product: 'SKU 6920152', new_price: 15.9 }, sampleOut: { ok: true, need_approval: false, margin_after: 0.152 }, avgMs: 18, calls7d: 1260 },
  { id: 'FN-012', name: 'alert_assignee', title: '告警处理人推荐', runtime: P, status: '已发布', owner: '陈立军', timeoutS: 3, desc: '按告警对象所属大区和角色推荐处理人。',
    inputs: [{ name: 'store', type: 'Store', objType: '门店', desc: '告警对象所属门店' }, { name: 'role', type: 'str', desc: '处理角色' }],
    output: { name: 'assignee', type: 'Employee', objType: '员工', desc: '推荐处理人' }, usedBy: [{ kind: '动作', name: '认领告警' }, { kind: '动作', name: '转派告警' }],
    sampleIn: { store: '上海徐汇店', role: '大区经理' }, sampleOut: { assignee: '王敏（E10023）' }, avgMs: 12, calls7d: 540 },
  { id: 'FN-013', name: 'region_budget_check', title: '大区预算校验', runtime: P, status: '草稿', owner: '陈立军', timeoutS: 5, desc: '调整大区预算时校验是否超出年度总额（草稿，尚未发布）。',
    inputs: [{ name: 'region', type: 'Region', objType: '大区', desc: '大区' }, { name: 'delta', type: 'Decimal', desc: '调整金额（元）' }],
    output: { name: 'ok', type: 'bool', desc: '是否在额度内' }, usedBy: [],
    sampleIn: { region: '华东大区', delta: 500000 }, sampleOut: { ok: true, left_after: 2350000 }, avgMs: 0, calls7d: 0 },
  { id: 'FN-014', name: 'supplier_score', title: '供应商评分', runtime: P, status: '草稿', owner: '孙浩然', timeoutS: 10, desc: '按准时率、质检合格率、价格稳定性给供应商打分。',
    inputs: [{ name: 'supplier', type: 'Supplier', objType: '供应商', desc: '供应商' }, { name: 'pos', type: 'list[PurchaseOrder]', objType: '采购单', desc: '近 90 天采购单' }],
    output: { name: 'score', type: 'float', desc: '0–100' }, usedBy: [],
    sampleIn: { supplier: 'SUP-0218 上海光明乳业', pos: '近 90 天 46 单' }, sampleOut: { score: 87.5 }, avgMs: 0, calls7d: 0 },
  { id: 'FN-015', name: 'legacy_margin_v1', title: '毛利率（旧口径）', runtime: 'Python 3.9', status: '已停用', owner: '王敏', timeoutS: 5, desc: '含税口径毛利率，已被 gross_margin 替代（R-0010 销售额改为不含税口径）。',
    inputs: [{ name: 'product', type: 'Product', objType: '商品', desc: '商品' }],
    output: { name: 'margin', type: 'float', desc: '毛利率（含税）' }, usedBy: [],
    sampleIn: { product: 'SKU 6920152' }, sampleOut: { margin: 0.2013 }, avgMs: 51, calls7d: 0 },
];

const AUTHORS = ['王敏', '陈立军', '孙浩然', '刘婷', '何静'];
const NOTES = ['调整默认参数', '修复空值时报错', '补充单元测试', '入参改为本体对象类型', '性能优化：批量读取', '口径对齐不含税', '增加返回原因', '超时从 10 秒改为 5 秒', '按箱规取整', '日志脱敏'];
const pad = (n: number) => String(n).padStart(2, '0');

function versionsOf(f: typeof base[number], i: number): FnVersion[] {
  const r = seeded(7100 + i);
  const n = f.status === '草稿' ? 3 + Math.floor(r() * 4) : 14 + Math.floor(r() * 18);
  const out: FnVersion[] = [];
  let major = 1, minor = 0, patch = 0;
  let t = Date.UTC(2026, 9, 24, 18, 0) - Math.floor(r() * 30) * 86400000;
  const times: number[] = [];
  for (let k = 0; k < n; k += 1) { times.push(t); t -= Math.floor((1 + r() * 9) * 86400000 + r() * 36000000); }
  times.reverse();
  times.forEach((tm, k) => {
    if (k > 0) { if (r() < 0.15) { minor += 1; patch = 0; } else patch += 1; if (k === Math.floor(n * 0.6) && f.status !== '草稿') { major += 1; minor = 0; patch = 0; } }
    const d = new Date(tm + 8 * 3600000);
    out.push({
      ver: f.status === '草稿' ? `v0.${minor}.${patch}` : `v${major}.${minor}.${patch}`,
      time: `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())} ${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}`,
      author: k === 0 ? f.owner : AUTHORS[Math.floor(r() * AUTHORS.length)], note: k === 0 ? '首次创建' : NOTES[Math.floor(r() * NOTES.length)], status: '历史',
    });
  });
  out.reverse();
  out[0].status = '当前';
  return out;
}

const codeOf = (f: typeof base[number]) => [
  `from ontology import ${[...new Set([...f.inputs, f.output].map((p) => p.type.replace(/^list\[(.*)\]$/, '$1')).filter((t) => /^[A-Z]/.test(t) && !['Decimal', 'MomResult', 'RiskResult', 'CheckResult'].includes(t)))].join(', ') || 'types'}`,
  '',
  `def ${f.name}(${f.inputs.map((p) => `${p.name}: ${p.type}`).join(', ')}) -> ${f.output.type}:`,
  `    """${f.desc}"""`,
  '    ...',
].join('\n');

const VERSIONS: Record<string, FnVersion[]> = {};
export const FUNCTIONS: OntFunction[] = base.map((f, i) => {
  const vs = versionsOf(f, i);
  VERSIONS[f.id] = vs;
  return { ...f, version: vs[0].ver, updatedAt: vs[0].time, code: codeOf(f) };
});
export const fnVersions = (id: string) => VERSIONS[id] ?? [];
export const fnSignature = (f: OntFunction) => `def ${f.name}(${f.inputs.map((p) => `${p.name}: ${p.type}`).join(', ')}) -> ${f.output.type}`;
