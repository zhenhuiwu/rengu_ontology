/**
 * 情景推演模板（元数据）：页面的范围、驱动、输出指标、影响明细列、建议动作全部按模板渲染。
 * 后端同一份：backend/seeds/json/dynamic/SC_TEMPLATES.json（GET /scenarios/meta 的 templates）。
 * 引擎按模板 engine 键插拔；演示模板 demo_retail 的引擎即 mock/scenarios.ts simulate / stagePlanOf（原样，值不变）。
 */
import { SC_AREAS, SC_CATEGORIES, PROMO_TYPES, simulate, stagePlanOf, type Scenario, type ScResult, type ScScope, type ScDrivers, type StagePlan } from './scenarios';

export type ScValue = string | number;
export type ScCfg = Record<string, ScValue>;
/** 一个输入项（范围或驱动）。kind：select 下拉 / radio 单选 / slider 滑块 / number 数字框 */
export interface ScField {
  key: string; label: string; kind: 'select' | 'radio' | 'slider' | 'number';
  default: ScValue; options?: ScValue[]; min?: number; max?: number; step?: number;
  /** 显示单位（滑块值后缀 / 数字框后缀 / 单选项后缀） */
  unit?: string; signed?: boolean; width?: number;
  /** 取值来源“对象类型.属性”：真接口且 /object-types/{id}/facets 有数据时用分面取值，否则用 options */
  propRef?: string;
  /** propRef 取到分面值时排在最前的“全部”项 */
  allOption?: ScValue;
  /** 只有 when.key ≠ when.not 时可填；否则置 offValue；切到可填且为 offValue 时置 onDefault */
  when?: { key: string; not: ScValue }; offValue?: ScValue; onDefault?: ScValue;
  /** 该项不是默认值也不算“调整了驱动”，除非这些项都不为 0（如促销类型要配力度） */
  effectNeeds?: string[];
  invalidMsg?: string;
}
export interface ScRow { label: string; fields: ScField[] }
export interface ScKpiDef { key: string; label: string; short?: string; fmt: 'pct' | 'days' | 'wan'; delta: 'ratio' | 'pt' | 'abs'; baseTip?: string; chart?: boolean }
export interface ScImpCol {
  key: string; label: string; kind: 'obj' | 'money' | 'delta' | 'range' | 'text';
  objKind?: string; idKey?: string; labelKey?: string; valueKey?: string; baseKey?: string; unit?: string; width?: number; align?: 'right';
}
export interface ScTemplate {
  code: string; name: string; objectType: string; engine: string;
  scope: ScField[];
  driverGroups: { label: string; rows: ScRow[] }[];
  noDriverHint: string;
  /** 引擎说明（标题提示里展示） */
  modelNote: string;
  kpis: ScKpiDef[];
  series: { label: string; unit: string; scale: number; baseKey: string; valueKey: string };
  impact: { rowKey: string[]; sortNote: string; dims: string[]; columns: ScImpCol[] };
  /** 对比表的驱动行：text 里 {key} / {key:signed} 取驱动值；offKey 为默认值时显示 offText */
  compareRows: { label: string; text: string; offKey?: string; offText?: string }[];
  suggestions: { action: string; key: string }[];
}

const f = (x: ScField) => x;
export const DEMO_TEMPLATE: ScTemplate = {
  code: 'demo_retail', name: '门店商品经营推演', objectType: 'product', engine: 'demo_retail',
  scope: [
    f({ key: 'area', label: '区域', kind: 'select', default: '华东大区全部', options: [...SC_AREAS], propRef: 'store.region_code', allOption: '华东大区全部', invalidMsg: '范围不在可选列表里' }),
    f({ key: 'category', label: '品类', kind: 'select', default: '全部品类', options: [...SC_CATEGORIES], propRef: 'product.category_id', allOption: '全部品类', invalidMsg: '品类不在可选列表里' }),
    f({ key: 'weeks', label: '推演周数', kind: 'radio', default: 8, options: [4, 8, 12], unit: '周', min: 1, max: 52 }),
  ],
  driverGroups: [
    { label: '驱动 · 价格', rows: [{ label: '价格调整', fields: [f({ key: 'pricePct', label: '价格调整', kind: 'slider', default: 0, min: -20, max: 10, step: 1, unit: '%', signed: true })] }] },
    { label: '驱动 · 库存', rows: [
      { label: '补货量', fields: [f({ key: 'replenishPct', label: '补货量', kind: 'slider', default: 0, min: -30, max: 50, step: 5, unit: '%', signed: true })] },
      { label: '安全库存', fields: [f({ key: 'safetyDays', label: '安全库存', kind: 'number', default: 7, min: 3, max: 14, unit: '天', width: 120 })] },
    ] },
    { label: '驱动 · 促销', rows: [
      { label: '促销类型', fields: [f({ key: 'promo', label: '促销类型', kind: 'select', default: '无', options: [...PROMO_TYPES], effectNeeds: ['promoDepth'], invalidMsg: `促销方式只能是：${PROMO_TYPES.join('、')}` })] },
      { label: '力度 / 周数', fields: [
        f({ key: 'promoDepth', label: '促销力度', kind: 'number', default: 0, min: 0, max: 30, unit: '%', width: 110, when: { key: 'promo', not: '无' }, offValue: 0, onDefault: 10 }),
        f({ key: 'promoWeeks', label: '促销周数', kind: 'number', default: 0, min: 0, max: 4, unit: '周', width: 110, when: { key: 'promo', not: '无' }, offValue: 0, onDefault: 2 }),
      ] },
    ] },
  ],
  noDriverHint: '至少调整一个驱动（价格 / 库存 / 促销）',
  modelNote: '价格弹性 −1.6（示意模型）',
  kpis: [
    { key: 'sales', label: '销售额', fmt: 'wan', delta: 'ratio', chart: true },
    { key: 'gp', label: '毛利额', fmt: 'wan', delta: 'ratio', chart: true },
    { key: 'rate', label: '毛利率', fmt: 'pct', delta: 'pt', chart: true },
    { key: 'stockout', label: '缺货率', fmt: 'pct', delta: 'pt', chart: true },
    { key: 'turn', label: '库存周转天数', short: '周转天数', fmt: 'days', delta: 'ratio', chart: true },
    { key: 'promo', label: '促销成本', fmt: 'wan', delta: 'abs', baseTip: '基线无促销成本' },
  ],
  series: { label: '销售额', unit: '万元', scale: 1e4, baseKey: 'baseSales', valueKey: 'sales' },
  impact: {
    rowKey: ['sku', 'storeId'], sortNote: '按销售额变化绝对值排序', dims: ['store'],
    columns: [
      { key: 'sku', label: '商品', kind: 'obj', objKind: 'product', idKey: 'sku', labelKey: 'name' },
      { key: 'store', label: '门店', kind: 'obj', objKind: 'store', idKey: 'storeId', labelKey: 'storeName' },
      { key: 'base', label: '基线销售额', kind: 'money', valueKey: 'baseSales', align: 'right', width: 110 },
      { key: 'sales', label: '方案销售额', kind: 'money', valueKey: 'sales', align: 'right', width: 110 },
      { key: 'delta', label: '变化', kind: 'delta', valueKey: 'sales', baseKey: 'baseSales', align: 'right', width: 84 },
      { key: 'so', label: '缺货率', kind: 'range', valueKey: 'stockout', baseKey: 'stockoutBase', unit: '%', align: 'right', width: 132 },
      { key: 'suggest', label: '建议动作', kind: 'text', valueKey: 'suggest', width: 170 },
    ],
  },
  compareRows: [
    { label: '价格 / 补货', text: '价格 {pricePct:signed}% · 补货 {replenishPct:signed}% · 安全库存 {safetyDays} 天' },
    { label: '促销', text: '{promo} {promoDepth}% × {promoWeeks} 周', offKey: 'promo', offText: '无' },
  ],
  suggestions: [
    { key: 'price', action: '发起调价审批' },
    { key: 'stock', action: '补货申请' },
    { key: 'promo', action: '发放复购召回优惠券' },
  ],
};
export const SC_TEMPLATES: ScTemplate[] = [DEMO_TEMPLATE];
export const templateOf = (code?: string, list: ScTemplate[] = SC_TEMPLATES) => list.find((t) => t.code === code) ?? list[0];

/** 引擎注册表（按模板 engine 键）：演示引擎的类型化入参在这里收口，页面只见通用 ScCfg */
export interface ScEngine { simulate: (scope: ScCfg, drivers: ScCfg, ranAt: string) => ScResult; stagePlan: (s: Scenario) => StagePlan[] }
export const SC_ENGINES: Record<string, ScEngine> = {
  demo_retail: {
    simulate: (scope, drivers, ranAt) => simulate(scope as unknown as ScScope, drivers as unknown as ScDrivers, ranAt),
    stagePlan: stagePlanOf,
  },
};

export const allFields = (t: ScTemplate) => t.driverGroups.flatMap((g) => g.rows.flatMap((r) => r.fields));
export const defaultsOf = (fs: ScField[]): ScCfg => Object.fromEntries(fs.map((x) => [x.key, x.default]));
export const signed = (v: ScValue) => (typeof v === 'number' && v > 0 ? `+${v}` : String(v));
/** 没有调整任何驱动（与后端 no_driver 同一判定） */
export function noDriver(t: ScTemplate, d: ScCfg): boolean {
  return allFields(t).every((x) => d[x.key] === x.default || (x.effectNeeds ?? []).some((k) => d[k] === 0));
}
export const scopeText = (t: ScTemplate, s: ScCfg) => t.scope.map((x) => `${s[x.key]}${x.unit ? ` ${x.unit}` : ''}`).join(' · ');
export function fillText(text: string, d: ScCfg): string {
  return text.replace(/\{(\w+)(:signed)?\}/g, (_, k: string, sg?: string) => (sg ? signed(d[k]) : String(d[k] ?? '')));
}
