/**
 * 情景推演（示例数据 + 示例计算）。
 * 范围：华东大区内的区域 + 品类 + 推演周数；驱动：价格、库存（补货量 / 安全库存天数）、促销（类型 / 力度 / 周数）。
 * 计算是示意模型：价格弹性 −1.6、促销拉动、补货降低缺货；只为界面演示，数字不追求与其他页对齐。
 */
import { reactive } from 'vue';
import { seeded } from './format';
import { PRODUCTS } from './products';
import { STORES } from './stores';
import type { ActionName, ObjKind } from './actions';

export const SC_AREAS = ['华东大区全部', '华东一区', '华东二区', '华东三区', '华东四区'] as const;
export const SC_CATEGORIES = ['全部品类', '乳品', '粮油调味', '酒水饮料', '日化家清', '休闲食品', '生鲜'] as const;
export const PROMO_TYPES = ['无', '满减', '折扣', '会员券'] as const;
export type ScStatus = '草稿' | '运行中' | '已完成' | '失败';

export interface ScScope { area: string; category: string; weeks: 4 | 8 | 12 }
export interface ScDrivers {
  pricePct: number;                 // 价格调整 %，−20 ~ +10
  replenishPct: number;             // 补货量调整 %，−30 ~ +50
  safetyDays: number;               // 安全库存天数 3 ~ 14
  promo: (typeof PROMO_TYPES)[number];
  promoDepth: number;               // 促销力度 %，0 ~ 30
  promoWeeks: number;               // 促销周数 0 ~ 4
}
export interface ScKpi { key: string; label: string; unit: string; base: number; value: number; better: 'up' | 'down' }
export interface ScImpact { sku: string; name: string; category: string; storeId: string; storeName: string; baseSales: number; sales: number; stockoutBase: number; stockout: number; suggest: string }
export interface ScResult { ranAt: string; weeks: string[]; baseSales: number[]; sales: number[]; baseMargin: number[]; margin: number[]; kpis: ScKpi[]; impacts: ScImpact[] }
export interface Scenario {
  id: string; name: string; owner: string; createdAt: string; baseVer: string;
  status: ScStatus; scope: ScScope; drivers: ScDrivers;
  /** 推演模板编号（mock/scenarioTemplates.ts）；缺省为演示模板 */
  templateCode?: string;
  result?: ScResult; failReason?: string;
  /** 已转为暂存动作的动作编号（动作中心 待确认） */
  staged: string[];
}

export const BASE_DRIVERS = (): ScDrivers => ({ pricePct: 0, replenishPct: 0, safetyDays: 7, promo: '无', promoDepth: 0, promoWeeks: 0 });
const catOf = (c: string) => c.split('/')[0];
const ELASTICITY = -1.6;

/** 示意模型：返回基线与方案的周序列、KPI 和 SKU × 门店影响 */
export function simulate(scope: ScScope, d: ScDrivers, ranAt: string): ScResult {
  const r = seeded(scope.area.length * 31 + scope.category.length * 7 + scope.weeks);
  const stores = STORES.filter((s) => s.status !== '已闭店' && (scope.area === '华东大区全部' || s.region === scope.area));
  const prods = PRODUCTS.filter((p) => scope.category === '全部品类' || catOf(p.category) === scope.category);
  const priceVol = Math.pow(1 + d.pricePct / 100, ELASTICITY);
  const promoShare = Math.min(d.promoWeeks, scope.weeks) / scope.weeks;
  const promoLift = d.promo === '无' ? 0 : (d.promoDepth / 100) * (d.promo === '会员券' ? 1.4 : d.promo === '折扣' ? 1.9 : 1.6) * promoShare;
  const stockGain = Math.max(-0.5, d.replenishPct / 100) * 0.35 + (d.safetyDays - 7) * 0.012;
  const baseStockout = 0.064;
  const stockout = Math.max(0.008, baseStockout * (1 - stockGain * 1.6));
  const volFactor = priceVol * (1 + promoLift) * ((1 - stockout) / (1 - baseStockout));
  const salesFactor = volFactor * (1 + d.pricePct / 100);
  const baseRate = 0.226;
  const rate = baseRate + (d.pricePct / 100) * 0.8 - (d.promo === '无' ? 0 : (d.promoDepth / 100) * 0.55 * promoShare) - Math.max(0, d.replenishPct) / 100 * 0.012;
  const scale = stores.length * prods.length * 820;
  const weeks: string[] = []; const baseSales: number[] = []; const sales: number[] = []; const baseMargin: number[] = []; const margin: number[] = [];
  for (let i = 0; i < scope.weeks; i++) {
    weeks.push(`W${i + 1}`);
    const b = Math.round(scale * (0.92 + r() * 0.16) * (i === 2 || i === 6 ? 1.12 : 1));
    const promoOn = d.promo !== '无' && i < d.promoWeeks;
    const f = promoOn ? salesFactor * (1 + (d.promoDepth / 100) * 0.9) / (1 + promoLift) : salesFactor / (1 + promoLift) * (1 + promoLift * 0.15);
    const s = Math.round(b * f);
    baseSales.push(b); sales.push(s);
    baseMargin.push(+(baseRate * 100).toFixed(1));
    margin.push(+((rate + (promoOn ? -(d.promoDepth / 100) * 0.4 : (d.promo === '无' ? 0 : (d.promoDepth / 100) * 0.2 * promoShare))) * 100).toFixed(1));
  }
  const sum = (a: number[]) => a.reduce((x, y) => x + y, 0);
  const bS = sum(baseSales); const sS = sum(sales);
  const bGp = bS * baseRate; const sGp = sales.reduce((acc, v, i) => acc + v * margin[i] / 100, 0);
  const promoCost = d.promo === '无' ? 0 : sS * (d.promoDepth / 100) * promoShare * 0.35;
  const turnBase = 28.4;
  const turn = turnBase * (1 + d.replenishPct / 100 * 0.45 + (d.safetyDays - 7) * 0.03) / Math.max(0.6, volFactor);
  const kpis: ScKpi[] = [
    { key: 'sales', label: '销售额', unit: '万元', base: bS / 1e4, value: sS / 1e4, better: 'up' },
    { key: 'gp', label: '毛利额', unit: '万元', base: bGp / 1e4, value: sGp / 1e4, better: 'up' },
    { key: 'rate', label: '毛利率', unit: '%', base: baseRate * 100, value: (sGp / sS) * 100, better: 'up' },
    { key: 'stockout', label: '缺货率', unit: '%', base: baseStockout * 100, value: stockout * 100, better: 'down' },
    { key: 'turn', label: '库存周转天数', unit: '天', base: turnBase, value: turn, better: 'down' },
    { key: 'promo', label: '促销成本', unit: '万元', base: 0, value: promoCost / 1e4, better: 'down' },
  ];
  const impacts: ScImpact[] = [];
  for (const p of prods) for (const s of stores) {
    const bs = Math.round(scale / (stores.length * prods.length) * scope.weeks * (0.5 + r()));
    const k = salesFactor * (0.85 + r() * 0.3);
    const so0 = +(baseStockout * 100 * (0.4 + r() * 1.6)).toFixed(1);
    const so1 = +(so0 * stockout / baseStockout).toFixed(1);
    const suggest = d.pricePct !== 0 && Math.abs(k - 1) > 0.04 ? (d.pricePct < 0 ? '发起调价审批（降价）' : '发起调价审批（提价）')
      : so1 > 6 || (d.replenishPct > 0 && so0 - so1 > 2) ? '补货申请' : '—';
    impacts.push({ sku: p.sku, name: p.name, category: catOf(p.category), storeId: s.id, storeName: s.name, baseSales: bs, sales: Math.round(bs * k), stockoutBase: so0, stockout: so1, suggest });
  }
  impacts.sort((a, b) => Math.abs(b.sales - b.baseSales) - Math.abs(a.sales - a.baseSales));
  return { ranAt, weeks, baseSales, sales, baseMargin, margin, kpis, impacts };
}

/** 转为暂存动作的建议清单（按驱动生成；只暂存，不执行） */
export interface StagePlan { key: string; action: ActionName; objKind: ObjKind; objId: string; objLabel: string; params: string; reason: string }
export function stagePlanOf(sc: Scenario): StagePlan[] {
  const out: StagePlan[] = [];
  const res = sc.result; if (!res) return out;
  const d = sc.drivers;
  const byProduct = new Map<string, ScImpact[]>();
  res.impacts.forEach((x) => byProduct.set(x.sku, [...(byProduct.get(x.sku) ?? []), x]));
  const area = sc.scope.area === '华东大区全部' ? '华东大区' : sc.scope.area;
  if (d.pricePct !== 0) {
    [...byProduct.entries()].slice(0, 6).forEach(([sku, rows]) => out.push({
      key: `price:${sku}`, action: '发起调价审批', objKind: 'product', objId: sku, objLabel: rows[0].name,
      params: `建议${d.pricePct < 0 ? '降价' : '提价'} ${Math.abs(d.pricePct)}% · ${area} ${rows.length} 家门店 · ${sc.scope.weeks} 周`,
      reason: `方案销售额 ${fmtPct(rows.reduce((a, x) => a + x.sales, 0) / rows.reduce((a, x) => a + x.baseSales, 0) - 1)}`,
    }));
  }
  if (d.replenishPct > 0 || d.safetyDays > 7) {
    [...byProduct.entries()].filter(([, rows]) => rows.some((x) => x.suggest === '补货申请')).slice(0, 6).forEach(([sku, rows]) => {
      const n = rows.filter((x) => x.suggest === '补货申请').length;
      out.push({ key: `stock:${sku}`, action: '补货申请', objKind: 'product', objId: sku, objLabel: rows[0].name,
        params: `补货量 +${d.replenishPct}% · 安全库存 ${d.safetyDays} 天 · ${n} 家门店`,
        reason: `缺货率 ${avg(rows.map((x) => x.stockoutBase)).toFixed(1)}% → ${avg(rows.map((x) => x.stockout)).toFixed(1)}%` });
    });
  }
  if (d.promo !== '无' && d.promoDepth > 0) {
    out.push({ key: 'promo', action: '发放复购召回优惠券', objKind: 'region', objId: 'REG-EC', objLabel: '华东大区',
      params: `${d.promo} ${d.promoDepth}% · ${d.promoWeeks} 周 · 范围 ${area} · ${sc.scope.category}`,
      reason: `促销成本约 ${res.kpis.find((k) => k.key === 'promo')!.value.toFixed(1)} 万元` });
  }
  return out;
}
const avg = (a: number[]) => (a.length ? a.reduce((x, y) => x + y, 0) / a.length : 0);
export const fmtPct = (v: number) => `${v >= 0 ? '+' : '−'}${Math.abs(v * 100).toFixed(1)}%`;

/* ───── 示例方案 ───── */
const S = (id: number, name: string, owner: string, createdAt: string, status: ScStatus, scope: Partial<ScScope>, drivers: Partial<ScDrivers>, extra: Partial<Scenario> = {}): Scenario => {
  const sc: Scenario = { id: `SCN-${String(id).padStart(4, '0')}`, name, owner, createdAt, baseVer: 'v2026.10.3', status,
    scope: { area: '华东大区全部', category: '全部品类', weeks: 8, ...scope }, drivers: { ...BASE_DRIVERS(), ...drivers }, staged: [], ...extra };
  if (status === '已完成') sc.result = simulate(sc.scope, sc.drivers, createdAt.replace(/:\d\d$/, ':40'));
  return sc;
};
export const SCENARIOS: Scenario[] = reactive([
  S(18, '双 11 乳品折扣 15% 两周', '王敏', '2026-10-25 15:12:08', '已完成', { category: '乳品' }, { pricePct: 0, promo: '折扣', promoDepth: 15, promoWeeks: 2, replenishPct: 20 }),
  S(17, '乳品降价 5% + 补货 20%', '王敏', '2026-10-25 14:40:51', '已完成', { category: '乳品' }, { pricePct: -5, replenishPct: 20, safetyDays: 9 }),
  S(16, '乳品维持原价 · 只加大补货', '王敏', '2026-10-25 14:21:30', '已完成', { category: '乳品' }, { replenishPct: 35, safetyDays: 10 }),
  S(15, '粮油会员券 10% 四周', '陈立军', '2026-10-25 11:05:17', '已完成', { category: '粮油调味' }, { promo: '会员券', promoDepth: 10, promoWeeks: 4 }),
  S(14, '华东二区日化提价 3%', '周晓峰', '2026-10-24 17:48:02', '已完成', { area: '华东二区', category: '日化家清' }, { pricePct: 3 }),
  S(13, '生鲜满减 20% 一周（试算）', '吴佳', '2026-10-24 16:30:44', '失败', { category: '生鲜' }, { promo: '满减', promoDepth: 20, promoWeeks: 1 },
    { failReason: '库存快照截止 2026-10-23 23:00，早于推演起点 10-26；生鲜称重商品缺少 10-24 以后的库存，无法计算缺货率' }),
  S(12, '休闲食品安全库存 12 天', '刘婷', '2026-10-24 10:02:19', '已完成', { category: '休闲食品' }, { safetyDays: 12, replenishPct: 10 }),
  S(11, '酒水饮料降价 8%', '孙浩然', '2026-10-23 19:22:55', '已完成', { category: '酒水饮料' }, { pricePct: -8 }),
  S(10, '华东一区全品类补货 −15%', '赵蕾', '2026-10-23 15:11:40', '已完成', { area: '华东一区' }, { replenishPct: -15, safetyDays: 5 }),
  S(9, '华东三区乳品折扣 10% 三周', '王敏', '2026-10-22 18:40:12', '已完成', { area: '华东三区', category: '乳品' }, { promo: '折扣', promoDepth: 10, promoWeeks: 3 }),
  S(8, '粮油降价 3%（12 周）', '陈立军', '2026-10-21 09:35:28', '已完成', { category: '粮油调味', weeks: 12 }, { pricePct: -3 }),
  S(7, '日化满减 12% 两周', '周晓峰', '2026-10-20 14:16:03', '已完成', { category: '日化家清' }, { promo: '满减', promoDepth: 12, promoWeeks: 2 }),
  S(6, '生鲜补货 +30%', '吴佳', '2026-10-19 11:48:39', '已完成', { category: '生鲜', weeks: 4 }, { replenishPct: 30, safetyDays: 4 }),
  S(5, '华东四区新店会员券 15%', '何静', '2026-10-18 16:57:21', '草稿', { area: '华东四区' }, { promo: '会员券', promoDepth: 15, promoWeeks: 2 }),
  S(4, '乳品提价 2%（成本上涨）', '王敏', '2026-10-17 10:20:45', '已完成', { category: '乳品' }, { pricePct: 2 }),
  S(3, '国庆后酒水补货 −20%', '孙浩然', '2026-10-15 09:12:30', '已完成', { category: '酒水饮料', weeks: 4 }, { replenishPct: -20 }),
  S(2, '华东二区休闲食品满减 8%', '周晓峰', '2026-10-12 15:40:02', '已完成', { area: '华东二区', category: '休闲食品' }, { promo: '满减', promoDepth: 8, promoWeeks: 2 }),
  S(1, '全品类安全库存 5 天（试算）', '赵蕾', '2026-10-10 14:05:51', '草稿', {}, { safetyDays: 5 }),
]);
export const scenarioById = (id: string) => SCENARIOS.find((s) => s.id === id);
export const nextScenarioId = () => `SCN-${String(Math.max(...SCENARIOS.map((s) => Number(s.id.slice(4)))) + 1).padStart(4, '0')}`;
