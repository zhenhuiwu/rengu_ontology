/**
 * 规则与自动化（本体 · 构建）示例数据。页面：pages/ontology/Rules.vue。
 * 5 条核心规则（R-007 / R-012 / R-015 / R-021 / SM-ORD）来自 dynamic.ts 的 RULES，命中历史取 ALERTS；
 * 另补 10 条规则让列表超过一页，它们的命中只发通知、不生成告警，命中记录在本文件里生成。
 * 规则版本、指标口径版本都是整数。
 */
import { seeded } from './format';
import { RULES, ALERTS, type Alert } from './dynamic';
import { PRODUCTS } from './products';
import { STORES } from './stores';
import { ORDERS } from './orders';
import { ACTIONS } from './actions';
import type { DraftCR } from './releases';

export type RuleStatus = '已发布' | '变更中' | '已停用';
export type ObjKind = 'store' | 'product' | 'order';
export interface RuleCond { metric?: string; metricVer?: number; field: string; op: string; value: string }
export interface RuleTrigger { kind: '定时' | '事件'; cron?: string; text: string; eventId?: string; filter?: string }
export interface AutoStage { enabled: boolean; actionTypeId: string; mapping: { param: string; from: string }[]; confirmer: string }
export interface RuleDef {
  id: string; name: string; version: number; status: RuleStatus; owner: string; desc: string;
  objKind: ObjKind; level: '高' | '中' | '低'; output: '告警' | '通知';
  conds: RuleCond[]; logic: '且' | '或'; expr: string; dedupe: string; cooldown: string;
  trigger: RuleTrigger; actionTypeId?: string; autoStage: AutoStage; weekly: number; core: boolean;
}

const OBJ_LABEL: Record<ObjKind, string> = { store: '门店', product: '商品', order: '订单' };
export const objKindLabel = (k: ObjKind) => OBJ_LABEL[k];

const AS = (actionTypeId: string, mapping: [string, string][], confirmer: string, enabled = true): AutoStage =>
  ({ enabled, actionTypeId, confirmer, mapping: mapping.map(([param, from]) => ({ param, from })) });
const NO_STAGE = (actionTypeId = ''): AutoStage => ({ enabled: false, actionTypeId, mapping: [], confirmer: '' });

export const RULE_DEFS: RuleDef[] = [
  { id: 'R-007', name: RULES['R-007'].name, version: 3, status: '变更中', owner: '刘婷', core: true, objKind: 'product', level: '高', output: '告警', weekly: 54,
    desc: '商品毛利率从 ≥ 18% 跌破 18% 时产生告警，只在越线那一次触发。',
    conds: [{ metric: 'gross_margin', metricVer: 2, field: '毛利率', op: '<', value: '18.00%' }, { metric: 'gross_margin', metricVer: 2, field: '上次计算的毛利率', op: '≥', value: '18.00%' }],
    logic: '且', expr: 'gross_margin@v2(sku, store, day) < 0.18 AND prev(gross_margin@v2) >= 0.18', dedupe: '规则 + 商品 + 门店 + 日期', cooldown: '7 天',
    trigger: { kind: '定时', cron: '0 8 * * *', text: '每天 08:00（数据截至前一日 23:00）' }, actionTypeId: 'start_price_approval',
    autoStage: AS('start_price_approval', [['商品', '告警.对象'], ['原因', '规则.命中说明'], ['备注', '规则.建议值（毛利率回到 18%）'], ['关联告警', '告警.ID']], '品类经理 王敏') },
  { id: 'R-012', name: RULES['R-012'].name, version: 2, status: '已发布', owner: '周晓峰', core: true, objKind: 'product', level: '高', output: '告警', weekly: 386,
    desc: '收到库存变动事件后，可用库存低于门店安全库存时产生告警。',
    conds: [{ field: '可用库存', op: '<', value: '安全库存' }, { field: '商品状态', op: '=', value: '在售' }],
    logic: '且', expr: 'event.available < product.safetyStock(store) AND product.status = "在售"', dedupe: '规则 + 商品 + 门店', cooldown: '24 小时',
    trigger: { kind: '事件', eventId: 'inventory_changed', text: '订阅“库存变动”（InventoryChanged）', filter: 'changeType ∈ {出库, 调拨出, 盘亏}' }, actionTypeId: 'request_replenish',
    autoStage: AS('request_replenish', [['商品', '事件.SKU'], ['门店', '事件.门店'], ['补货数量', '安全库存 × 1.5 − 可用库存'], ['补货方式', '固定值：仓库调拨'], ['关联告警', '告警.ID']], '门店店长') },
  { id: 'R-015', name: RULES['R-015'].name, version: 1, status: '已发布', owner: '陈立军', core: true, objKind: 'order', level: '中', output: '告警', weekly: 212,
    desc: '已出库超过 48 小时仍未签收的订单，每单只产生一次告警。',
    conds: [{ field: '订单状态', op: '=', value: '已出库' }, { field: '出库时长', op: '>', value: '48 小时' }],
    logic: '且', expr: 'order.status = "已出库" AND hours_since(order.shippedAt) > 48', dedupe: '规则 + 订单', cooldown: '不重复',
    trigger: { kind: '定时', cron: '10 * * * *', text: '每小时第 10 分' }, actionTypeId: 'urge_logistics',
    autoStage: AS('urge_logistics', [['订单', '告警.对象'], ['原因', '规则.命中说明'], ['关联告警', '告警.ID']], '订单运营组 陈立军') },
  { id: 'R-021', name: RULES['R-021'].name, version: 2, status: '已发布', owner: '陈立军', core: true, objKind: 'store', level: '中', output: '告警', weekly: 9,
    desc: '每周一按销售额（不含税）周环比计算，下降超过 15% 的门店产生告警。',
    conds: [{ metric: 'gmv', metricVer: 3, field: '销售额（不含税）周环比', op: '<', value: '−15.0%' }],
    logic: '且', expr: 'wow(gmv@v3(store, week)) < -0.15', dedupe: '规则 + 门店 + 周', cooldown: '7 天',
    trigger: { kind: '定时', cron: '0 9 * * 1', text: '每周一 09:00' }, actionTypeId: 'assign_region_manager', autoStage: NO_STAGE('assign_region_manager') },
  { id: 'SM-ORD', name: RULES['SM-ORD'].name, version: 4, status: '已发布', owner: '孙浩然', core: true, objKind: 'order', level: '高', output: '告警', weekly: 31,
    desc: '订单状态变更不在生命周期 v4 允许的迁移里时产生告警。',
    conds: [{ field: '状态迁移（原 → 新）', op: '∉', value: '生命周期 v4 合法迁移' }],
    logic: '且', expr: '(event.fromStatus, event.toStatus) NOT IN lifecycle("SalesOrder", 4).transitions', dedupe: '事件 ID', cooldown: '不重复',
    trigger: { kind: '事件', eventId: 'order_status_changed', text: '订阅“订单状态变更”（OrderStatusChanged）', filter: '全部' }, actionTypeId: 'correct_order_status',
    autoStage: AS('correct_order_status', [['订单', '事件.订单号'], ['目标状态', '生命周期上一个合法状态'], ['修正原因', '固定值：系统同步乱序'], ['关联告警', '告警.ID']], '订单运营组 孙浩然') },
  /* 以下 10 条：命中只发通知 */
  { id: 'R-003', name: '门店出库及时率低于 95%', version: 2, status: '已发布', owner: '周晓峰', core: false, objKind: 'store', level: '中', output: '通知', weekly: 17,
    desc: '门店前一日出库及时率低于 95% 时通知区域运营。', conds: [{ metric: 'ship_ontime', metricVer: 1, field: '出库及时率', op: '<', value: '95.00%' }],
    logic: '且', expr: 'ship_ontime@v1(store, day) < 0.95', dedupe: '规则 + 门店 + 日期', cooldown: '24 小时',
    trigger: { kind: '定时', cron: '30 7 * * *', text: '每天 07:30' }, autoStage: NO_STAGE() },
  { id: 'R-009', name: '大额退款复核', version: 1, status: '已发布', owner: '孙浩然', core: false, objKind: 'order', level: '中', output: '通知', weekly: 42,
    desc: '单笔退款超过 500 元时通知客服主管复核。', conds: [{ field: '退款金额', op: '>', value: '500.00 元' }],
    logic: '且', expr: 'event.amount > 500', dedupe: '退款单号', cooldown: '不重复',
    trigger: { kind: '事件', eventId: 'refund_created', text: '订阅“退款单创建”（RefundCreated）', filter: '全部' }, actionTypeId: 'approve_refund',
    autoStage: AS('approve_refund', [['订单', '事件.订单号'], ['原因', '事件.退款原因'], ['备注', '固定值：大额退款复核']], '客服主管 何静') },
  { id: 'R-010', name: '运单超 72 小时未签收升级', version: 1, status: '已发布', owner: '陈立军', core: false, objKind: 'order', level: '高', output: '通知', weekly: 23,
    desc: '已催派仍超过 72 小时未签收的订单升级给物流经理。', conds: [{ field: '订单状态', op: '=', value: '已出库' }, { field: '出库时长', op: '>', value: '72 小时' }],
    logic: '且', expr: 'order.status = "已出库" AND hours_since(order.shippedAt) > 72', dedupe: '规则 + 订单', cooldown: '不重复',
    trigger: { kind: '定时', cron: '10 */4 * * *', text: '每 4 小时第 10 分' }, actionTypeId: 'reissue_shipment', autoStage: NO_STAGE('reissue_shipment') },
  { id: 'R-014', name: '临期商品库存积压', version: 3, status: '已发布', owner: '王敏', core: false, objKind: 'product', level: '中', output: '通知', weekly: 66,
    desc: '距保质期不足 7 天且库存超过 7 天销量时，提醒门店做临期促销。', conds: [{ field: '剩余保质天数', op: '<', value: '7 天' }, { field: '库存 / 近 7 天日均销量', op: '>', value: '7' }],
    logic: '且', expr: 'days_to_expire(batch) < 7 AND stock / avg_daily_sales(7d) > 7', dedupe: '规则 + 商品 + 门店 + 批次', cooldown: '3 天',
    trigger: { kind: '定时', cron: '0 6 * * *', text: '每天 06:00' }, actionTypeId: 'adjust_store_price',
    autoStage: AS('adjust_store_price', [['商品', '命中.对象'], ['原因', '固定值：临期促销'], ['生效时间', '次日 0 点']], '品类经理 王敏', false) },
  { id: 'R-017', name: '门店有效订单数骤降', version: 1, status: '已发布', owner: '陈立军', core: false, objKind: 'store', level: '高', output: '通知', weekly: 5,
    desc: '营业时段内门店近 1 小时有效订单数比上周同时段下降超过 40%。', conds: [{ metric: 'order_cnt', metricVer: 1, field: '有效订单数 同比上周同时段', op: '<', value: '−40.0%' }],
    logic: '且', expr: 'yoy_week(order_cnt@v1(store, hour)) < -0.40 AND store.isOpen(now)', dedupe: '规则 + 门店 + 小时', cooldown: '2 小时',
    trigger: { kind: '定时', cron: '5 9-22 * * *', text: '营业时段每小时第 5 分（09:05–22:05）' }, autoStage: NO_STAGE() },
  { id: 'R-018', name: '商品毛利率为负', version: 2, status: '已发布', owner: '刘婷', core: false, objKind: 'product', level: '高', output: '通知', weekly: 8,
    desc: '商品毛利率低于 0 时立即通知定价委员会。', conds: [{ metric: 'gross_margin', metricVer: 2, field: '毛利率', op: '<', value: '0.00%' }],
    logic: '且', expr: 'gross_margin@v2(sku, store, day) < 0', dedupe: '规则 + 商品 + 门店 + 日期', cooldown: '24 小时',
    trigger: { kind: '定时', cron: '0 8 * * *', text: '每天 08:00' }, actionTypeId: 'start_price_approval',
    autoStage: AS('start_price_approval', [['商品', '命中.对象'], ['原因', '固定值：负毛利'], ['关联告警', '—']], '定价委员会') },
  { id: 'R-019', name: '调价后毛利率越线', version: 1, status: '已发布', owner: '王敏', core: false, objKind: 'product', level: '中', output: '通知', weekly: 29,
    desc: '门店新价生效后重新计算毛利率，低于 18% 时提醒品类经理。', conds: [{ metric: 'gross_margin', metricVer: 2, field: '新价下的毛利率', op: '<', value: '18.00%' }],
    logic: '且', expr: 'gross_margin@v2(sku, store, price = event.newPrice) < 0.18', dedupe: '门店 + SKU + 生效日期', cooldown: '24 小时',
    trigger: { kind: '事件', eventId: 'store_price_changed', text: '订阅“门店价格变更”（StorePriceChanged）', filter: 'newPrice < oldPrice' }, autoStage: NO_STAGE() },
  { id: 'R-023', name: '订单支付超时未取消', version: 2, status: '已发布', owner: '孙浩然', core: false, objKind: 'order', level: '低', output: '通知', weekly: 140,
    desc: '待支付超过 30 分钟仍未自动取消的订单，提示订单运营核查。', conds: [{ field: '订单状态', op: '=', value: '待支付' }, { field: '下单时长', op: '>', value: '30 分钟' }],
    logic: '且', expr: 'order.status = "待支付" AND minutes_since(order.createdAt) > 30', dedupe: '规则 + 订单', cooldown: '不重复',
    trigger: { kind: '定时', cron: '*/15 * * * *', text: '每 15 分钟' }, actionTypeId: 'correct_order_status',
    autoStage: AS('correct_order_status', [['订单', '命中.对象'], ['目标状态', '固定值：已取消'], ['修正原因', '固定值：系统同步乱序']], '订单运营组 孙浩然') },
  { id: 'R-025', name: '门店销售额连续 3 天低于目标', version: 1, status: '已停用', owner: '陈立军', core: false, objKind: 'store', level: '中', output: '通知', weekly: 0,
    desc: '已停用：与 R-021 重复，保留历史命中记录。', conds: [{ metric: 'gmv', metricVer: 3, field: '销售额（不含税）/ 日目标', op: '<', value: '90.0%（连续 3 天）' }],
    logic: '且', expr: 'consecutive_days(gmv@v3(store, day) / target(store, day) < 0.9) >= 3', dedupe: '规则 + 门店', cooldown: '7 天',
    trigger: { kind: '定时', cron: '0 9 * * *', text: '每天 09:00' }, autoStage: NO_STAGE() },
  { id: 'R-026', name: '安全库存设置偏低', version: 1, status: '已发布', owner: '周晓峰', core: false, objKind: 'product', level: '低', output: '通知', weekly: 12,
    desc: '近 14 天缺货超过 2 次的商品，提示供应链计划员上调安全库存。', conds: [{ field: '近 14 天缺货次数', op: '>', value: '2' }],
    logic: '且', expr: 'count(stockout, 14d) > 2', dedupe: '规则 + 商品 + 门店', cooldown: '14 天',
    trigger: { kind: '定时', cron: '0 5 * * 1', text: '每周一 05:00' }, actionTypeId: 'set_safety_stock',
    autoStage: AS('set_safety_stock', [['商品', '命中.对象'], ['原因', '固定值：频繁缺货']], '供应链计划员 周晓峰') },
];
export const ruleDefById = (id: string) => RULE_DEFS.find((r) => r.id === id);
export const isCoreRule = (id: string) => id in RULES;

/** 变更中的规则：R-007 随 CR-0011（毛利率口径扣除促销补贴，口径 v2 → v3）一起改 */
export const RULE_DRAFTS: Record<string, { cr: DraftCR; patch: Partial<RuleDef> }> = {
  'R-007': {
    cr: { id: 'CR-0011', title: '毛利率口径扣除促销补贴', owner: '何静', updatedAt: '2026-10-24 18:40', changes: 3 },
    patch: {
      version: 4,
      conds: [{ metric: 'gross_margin', metricVer: 3, field: '毛利率', op: '<', value: '18.00%' }, { metric: 'gross_margin', metricVer: 3, field: '上次计算的毛利率', op: '≥', value: '18.00%' }],
      expr: 'gross_margin@v3(sku, store, day) < 0.18 AND prev(gross_margin@v3) >= 0.18',
    },
  },
};

/* ───────── 命中记录 ───────── */
export interface Hit {
  id: string; ruleId: string; at: string; objKind: ObjKind; objId: string; objLabel: string;
  value: string; threshold: string; alertId: string | null; result: string; alert?: Alert;
}
const SAMPLE_VALUE: Record<string, (r: () => number) => string> = {
  'R-003': (r) => `${(88 + r() * 6.9).toFixed(2)}%`,
  'R-009': (r) => `${(500 + r() * 1800).toFixed(2)} 元`,
  'R-010': (r) => `${(72 + r() * 30).toFixed(1)} 小时`,
  'R-014': (r) => `剩 ${1 + Math.floor(r() * 6)} 天 · 库存 ${(7 + r() * 9).toFixed(1)} 天销量`,
  'R-017': (r) => `−${(40 + r() * 35).toFixed(1)}%`,
  'R-018': (r) => `−${(r() * 6).toFixed(2)}%`,
  'R-019': (r) => `${(12 + r() * 5.9).toFixed(2)}%`,
  'R-023': (r) => `待支付 ${31 + Math.floor(r() * 90)} 分钟`,
  'R-026': (r) => `缺货 ${3 + Math.floor(r() * 4)} 次`,
};
const pad = (n: number, w = 2) => String(n).padStart(w, '0');
function synthHits(rule: RuleDef): Hit[] {
  const r = seeded(rule.id.split('').reduce((h, c) => h * 31 + c.charCodeAt(0), 7) % 100000);
  const out: Hit[] = [];
  const perDay = Math.max(0, Math.round(rule.weekly / 7));
  const days = rule.status === '已停用' ? [] : Array.from({ length: 25 }, (_, i) => 25 - i);
  for (const day of days) {
    const n = Math.min(6, perDay + (r() < 0.3 ? 1 : 0));
    for (let k = 0; k < n; k++) {
      const time = `2026-10-${pad(day)} ${pad(day === 25 ? 6 + Math.floor(r() * 9) : 6 + Math.floor(r() * 16))}:${pad(Math.floor(r() * 60))}:${pad(Math.floor(r() * 60))}`;
      let objId: string; let objLabel: string;
      if (rule.objKind === 'store') { const s = STORES[Math.floor(r() * STORES.length)]; objId = s.id; objLabel = s.name; }
      else if (rule.objKind === 'product') { const p = PRODUCTS[Math.floor(r() * PRODUCTS.length)]; objId = p.sku; objLabel = `${p.name} ${p.spec}`; }
      else { objId = ORDERS[Math.floor(r() * ORDERS.length)].id; objLabel = objId; }
      const c = rule.conds[0];
      const v = SAMPLE_VALUE[rule.id]?.(r) ?? c.field;
      out.push({ id: `HIT-${rule.id.replace('-', '')}-${time.slice(5, 10).replace('-', '')}${pad(k)}`, ruleId: rule.id, at: time, objKind: rule.objKind, objId, objLabel,
        value: v, threshold: `${c.op} ${c.value}`, alertId: null, result: r() < 0.9 ? '已通知' : '已去重' });
    }
  }
  return out.sort((a, b) => b.at.localeCompare(a.at));
}
const HIT_CACHE = new Map<string, Hit[]>();
export function hitsOf(ruleId: string): Hit[] {
  if (HIT_CACHE.has(ruleId)) return HIT_CACHE.get(ruleId)!;
  const rule = ruleDefById(ruleId)!;
  const hits = rule.core
    ? ALERTS.filter((a) => a.ruleId === ruleId).map((a) => ({ id: a.id, ruleId, at: a.triggeredAt, objKind: a.objKind, objId: a.objId, objLabel: a.objLabel,
      value: a.value, threshold: a.threshold, alertId: a.id, result: a.status, alert: a }))
      .sort((a, b) => b.at.localeCompare(a.at))
    : synthHits(rule);
  HIT_CACHE.set(ruleId, hits);
  return hits;
}

/** 规则暂存到动作中心的动作（来源 = 该规则的告警） */
export const stagedOf = (ruleId: string) => ACTIONS.filter((a) => a.source.ruleId === ruleId);

/* ───────── 回放试跑（示例计算） ───────── */
export interface ReplaySample { key: string; at: string; objKind: ObjKind; objId: string; objLabel: string; value: string; diff: '一致' | '新增命中' | '不再命中' }
export function replay(rule: RuleDef, range: '7d' | '30d', useDraft: boolean) {
  const cut = range === '7d' ? '2026-10-18 23:59:59' : '2026-09-25 00:00:00';
  const actual = hitsOf(rule.id).filter((h) => h.at > cut);
  const r = seeded(actual.length * 13 + (useDraft ? 7 : 1) + (range === '7d' ? 3 : 5));
  const samples: ReplaySample[] = actual.map((h) => ({ key: h.id, at: h.at, objKind: h.objKind, objId: h.objId, objLabel: h.objLabel, value: h.value,
    diff: useDraft && r() < 0.18 ? '不再命中' : '一致' }));
  if (useDraft) {
    const extra = Math.max(2, Math.round(actual.length * 0.12));
    for (let i = 0; i < extra; i++) {
      const h = actual[Math.floor(r() * Math.max(1, actual.length))];
      if (!h) break;
      const p = rule.objKind === 'product' ? PRODUCTS[Math.floor(r() * PRODUCTS.length)] : null;
      samples.push({ key: `NEW-${i}`, at: h.at, objKind: h.objKind, objId: p ? p.sku : h.objId, objLabel: p ? `${p.name} ${p.spec}` : h.objLabel,
        value: rule.objKind === 'product' ? `${(17 + r()).toFixed(2)}%` : h.value, diff: '新增命中' });
    }
  }
  samples.sort((a, b) => b.at.localeCompare(a.at));
  const would = samples.filter((s) => s.diff !== '不再命中').length;
  return { actual: actual.length, would, added: samples.filter((s) => s.diff === '新增命中').length, removed: samples.filter((s) => s.diff === '不再命中').length, samples };
}

/* ───────── 版本历史 ───────── */
export interface RuleVersion { ver: number; status: '生效中' | '已替代' | '草稿'; by: string; at: string; change: string }
const VERSION_NOTES = ['阈值由 15% 调整为 18%', '去重键加入门店，冷却改为 7 天', '条件改用指标口径引用（不再写死 SQL）', '新增自动暂存动作', '触发时间由 07:00 改为 08:00'];
export function ruleVersions(rule: RuleDef, draftVer?: number): RuleVersion[] {
  const out: RuleVersion[] = [];
  if (draftVer) out.push({ ver: draftVer, status: '草稿', by: '何静', at: '2026-10-24 18:40', change: '条件引用的毛利率口径升到 v3（扣除促销补贴）' });
  for (let v = rule.version; v >= 1; v--) {
    out.push({ ver: v, status: v === rule.version ? '生效中' : '已替代', by: rule.owner, at: `2026-${pad(10 - (rule.version - v) * 2)}-${pad(8 + v * 3)} 1${v}:2${v}`,
      change: v === 1 ? '首次发布' : VERSION_NOTES[(v + rule.id.length) % VERSION_NOTES.length] });
  }
  return out;
}

export const CONFIRMERS = ['品类经理 王敏', '门店店长', '订单运营组 陈立军', '订单运营组 孙浩然', '客服主管 何静', '供应链计划员 周晓峰', '定价委员会'];
export const MAPPING_SOURCES = ['告警.对象', '规则.命中说明', '告警.门店', '告警.ID', '事件.SKU', '事件.门店', '事件.订单号', '命中.对象', '规则.建议值（毛利率回到 18%）', '安全库存 × 1.5 − 可用库存', '固定值', '—'];
