/**
 * 我的页面与模板库（/use/qa/pages）的 mock。
 * - 页面 id / 名称 / 卡片数与 QA_PAGE_LIST（工作台“固定到页面”、左栏“我的页面”）一致，前 13 个就是那 13 个页面。
 * - 卡片口径用指标完整名称 + 版本，与指标口径页一致；退款率卡片受 CR-0010（v2 → v3 审核中）影响，与 changes.ts 一致。
 * - 数据截至统一用公共 DATA_CUTOFF。
 */
import { DATA_CUTOFF } from './quality';
import { QA_PAGE_LIST, QA_METRIC } from './qa';
import { seeded } from './format';

export type CardViz = '指标卡' | '折线图' | '柱状图' | '表格';
export interface PageCard {
  key: string; title: string; viz: CardViz;
  metric: string; version: string;
  value?: string; unit?: string; sub?: string;
  series?: number[]; labels?: string[];
  rows?: [string, string][];
  /** 口径有待发布的变更 */
  pending?: string;
}
export type RefreshFreq = '每小时' | '每天' | '每周' | '每月' | '手动';
export interface RefreshPlan { freq: RefreshFreq; time: string; weekday?: string; monthday?: number; onFail: '保留上次结果' | '标记失败并通知' }
export interface Subscription { on: boolean; channel: '邮件' | '企业微信' | '站内信'; receivers: string[]; when: '每次刷新后' | '每周一 09:00' | '每天 09:00'; onlyChanged: boolean }
export type SharePerm = '可查看' | '可编辑';
export interface ShareMember { name: string; dept: string; perm: SharePerm }
export interface ShareSetting { mode: '仅自己' | '指定成员' | '所在部门'; members: ShareMember[]; link: boolean; allowExport: boolean }
export type PageStatus = '正常' | '刷新失败' | '口径待更新';
export interface QaPageFull {
  id: string; name: string; owner: string; scope: string; source: string; sourceKind: '问数工作台' | '模板' | '手动新建';
  cards: PageCard[]; dataAsOf: string; lastRefresh: string; updatedAt: string;
  refresh: RefreshPlan; sub: Subscription; share: ShareSetting; status: PageStatus; statusNote?: string; views: number;
}

export const PAGES_CUTOFF = DATA_CUTOFF;
export const PEOPLE: { name: string; dept: string }[] = [
  { name: '陈杰', dept: '华东事业部 / 运营中心' }, { name: '赵磊', dept: '华东事业部 / 运营中心' }, { name: '周婷', dept: '华东事业部 / 会员中心' },
  { name: '刘婷', dept: '华东事业部 / 财务数据组' }, { name: '陈立军', dept: '华东事业部 / 财务数据组' }, { name: '周晓峰', dept: '华东事业部 / 供应链' },
  { name: '何静', dept: '华东事业部 / 财务数据组' }, { name: '刘洋', dept: '集团总部 / 数据平台' }, { name: '张伟', dept: '华东事业部 / 运营中心' },
  { name: '孙浩然', dept: '华东事业部 / 运营中心' }, { name: '钱一鸣', dept: '华东事业部 / 供应链' }, { name: '马晨', dept: '华东事业部 / 会员中心' },
];

const MONTHS = ['4月', '5月', '6月', '7月', '8月', '9月'];
const WEEKS = ['W38', 'W39', 'W40', 'W41', 'W42', 'W43'];
const EX = `${QA_METRIC.ex.name}`;
/** 卡片库：[标题, 类型, 指标, 版本, 取值, 单位, 说明] */
const CARD_LIB: Record<string, Omit<PageCard, 'key'>> = {
  sales: { title: '华东大区销售额（不含税）', viz: '指标卡', metric: '销售额（不含税）', version: 'v3', value: '2,074.2', unit: '万元', sub: '9 月 · 环比 -3.1%' },
  salesTrend: { title: '近 6 周销售额趋势', viz: '折线图', metric: '销售额（不含税）', version: 'v3', labels: WEEKS, series: [482, 495, 470, 503, 488, 476] },
  repeat: { title: '复购客户销售额', viz: '指标卡', metric: EX, version: QA_METRIC.ex.version, value: '1,286.0', unit: '万元', sub: '9 月 · 环比 -8.4%' },
  repeatTrend: { title: '复购客户销售额月度趋势', viz: '折线图', metric: EX, version: QA_METRIC.ex.version, labels: MONTHS, series: [1302.5, 1338.0, 1366.4, 1421.8, 1404.0, 1286.0] },
  repeatTax: { title: '复购客户销售额（含税）', viz: '指标卡', metric: QA_METRIC.in.name, version: QA_METRIC.in.version, value: '1,453.2', unit: '万元', sub: '9 月' },
  province: { title: '各省复购客户销售额', viz: '柱状图', metric: EX, version: QA_METRIC.ex.version, labels: ['上海', '浙江', '江苏', '安徽', '福建', '江西', '山东'], series: [412, 318, 276, 98, 72, 51, 59] },
  refund: { title: '退款率', viz: '指标卡', metric: '退款率', version: 'v2', value: '2.84', unit: '%', sub: '上周', pending: 'CR-0010 审核中：发布后按 v3（退款完成时间）重算' },
  storeRank: { title: '门店销售额 Top 5', viz: '表格', metric: '销售额（不含税）', version: 'v3', rows: [['上海静安大悦城店', '588.9'], ['上海闵行七宝万科广场B1店', '655.3'], ['杭州滨江星光大道店', '520.0'], ['宁波鄞州万达店', '498.2'], ['上海浦东张江高科店', '412.6']] },
  ship: { title: '出库及时率', viz: '指标卡', metric: '出库及时率', version: 'v1', value: '93.6', unit: '%', sub: '近 7 天 · 同义词“发货及时率”' },
  shipTrend: { title: '出库及时率趋势', viz: '折线图', metric: '出库及时率', version: 'v1', labels: ['10-19', '10-20', '10-21', '10-22', '10-23', '10-24'], series: [94.1, 92.8, 93.5, 95.0, 92.2, 93.6] },
  overdue: { title: '出库超 48 小时未签收', viz: '表格', metric: '有效订单数', version: 'v1', rows: [['南京建邺河西店', '14 单'], ['杭州拱墅大关店', '9 单'], ['上海宝山顾村店', '7 单']] },
  orders: { title: '有效订单数', viz: '指标卡', metric: '有效订单数', version: 'v1', value: '18.42', unit: '万单', sub: '9 月' },
  atv: { title: '客单价（不含税）', viz: '指标卡', metric: '客单价（不含税）', version: 'v1', value: '112.6', unit: '元', sub: '9 月 · 环比 +1.2%' },
  margin: { title: '品类毛利率', viz: '柱状图', metric: '毛利率', version: 'v2', labels: ['生鲜', '粮油', '日化', '酒饮', '休食'], series: [18.2, 12.4, 26.8, 31.5, 28.0], pending: 'CR-0011 草稿：毛利率拟扣除促销补贴（v2 → v3）' },
  ordersTrend: { title: '订单数趋势', viz: '柱状图', metric: '有效订单数', version: 'v1', labels: MONTHS, series: [16.8, 17.2, 17.9, 18.8, 19.1, 18.4] },
};
/** 每个页面用哪些卡片（按 QA_PAGE_LIST 的顺序；卡片数与 QA_PAGE_LIST.cards 一致） */
const PAGE_CARDS: Record<string, string[]> = {
  华东大区销售周报: ['sales', 'salesTrend', 'refund', 'storeRank', 'atv', 'orders', 'ordersTrend'],
  门店履约看板: ['ship', 'shipTrend', 'overdue', 'orders', 'storeRank', 'ordersTrend', 'sales', 'atv', 'refund', 'salesTrend'],
  复购客户月度分析: ['repeat', 'repeatTrend', 'province', 'repeatTax', 'atv', 'storeRank'],
};
const POOL = Object.keys(CARD_LIB);

const SCOPES = ['华东大区', '华东一区', '华东二区', '华东三区', '上海', '杭州'];
const FREQS: RefreshPlan[] = [
  { freq: '每周', weekday: '周一', time: '08:00', onFail: '标记失败并通知' },
  { freq: '每小时', time: '整点', onFail: '保留上次结果' },
  { freq: '每天', time: '07:30', onFail: '保留上次结果' },
  { freq: '每月', monthday: 1, time: '09:00', onFail: '标记失败并通知' },
  { freq: '每天', time: '08:00', onFail: '标记失败并通知' },
  { freq: '手动', time: '--', onFail: '保留上次结果' },
];
const EXTRA_NAMES = ['会员流失预警看板', '门店人效对比', '自有品牌销售占比', '线上订单履约时长', '新品动销跟踪', '退货率（按品类）', '大区周会材料', '上海门店日报', '杭州门店日报', '供应商到货准时率', '促销券核销看板', '生鲜损耗周报', '华东二区门店排名', '9 月经营复盘'];

const freqLabel = (r: RefreshPlan) => (r.freq === '手动' ? '手动刷新' : r.freq === '每周' ? `每周${(r.weekday ?? '').slice(1)} ${r.time}` : r.freq === '每月' ? `每月 ${r.monthday} 日 ${r.time}` : r.freq === '每小时' ? '每小时整点' : `每天 ${r.time}`);
export { freqLabel };

function build(): QaPageFull[] {
  const r = seeded(7031);
  const base = [...QA_PAGE_LIST.map((p) => ({ id: p.id, name: p.name, n: p.cards, shared: p.shared, updated: p.updatedAt })),
    ...EXTRA_NAMES.map((n, i) => ({ id: `P${String(QA_PAGE_LIST.length + i + 1).padStart(3, '0')}`, name: n, n: 3 + ((i * 5) % 6), shared: i % 4 === 1, updated: `10-${String(Math.max(1, 12 - i)).padStart(2, '0')}` }))];
  return base.map((p, i) => {
    const keys = PAGE_CARDS[p.name] ?? Array.from({ length: p.n }, (_, k) => POOL[(i * 3 + k * 5) % POOL.length]).filter((k, j, a) => a.indexOf(k) === j);
    while (keys.length < p.n) { const k = POOL.find((x) => !keys.includes(x)); if (!k) break; keys.push(k); }
    const cards = keys.slice(0, p.n).map((k, j) => ({ key: `${p.id}-C${j + 1}`, ...CARD_LIB[k] }));
    const refresh = { ...FREQS[i % FREQS.length] };
    const members = p.shared ? PEOPLE.slice(i % 5, i % 5 + 2 + (i % 3)).map((m, j) => ({ ...m, perm: (j === 0 && i % 2 === 0 ? '可编辑' : '可查看') as SharePerm })) : [];
    const pending = cards.find((c) => c.pending);
    const fail = i === 4 || i === 17;
    const fromWb = i < 8 || i % 3 === 0;
    return {
      id: p.id, name: p.name, owner: '王敏', scope: SCOPES[i % SCOPES.length],
      sourceKind: fromWb ? '问数工作台' : i % 3 === 1 ? '模板' : '手动新建',
      source: fromWb ? `问数工作台 · 会话「${['华东大区复购下降原因', '门店出库超时排查', '9 月复购客户分析', '双 11 备货节奏', '会员运营周会', '品类毛利对比', '缺货门店盘点', '促销复盘'][i % 8]}」` : i % 3 === 1 ? `模板「${TEMPLATE_NAMES[i % TEMPLATE_NAMES.length]}」` : '手动新建',
      cards, dataAsOf: fail ? '2026-10-23 23:00' : DATA_CUTOFF,
      lastRefresh: fail ? '2026-10-25 08:00' : refresh.freq === '手动' ? `2026-10-${String(20 - (i % 5)).padStart(2, '0')} 10:12` : '2026-10-25 08:00',
      updatedAt: `2026-${p.updated} ${String(9 + (i % 9)).padStart(2, '0')}:${String((i * 13) % 60).padStart(2, '0')}`,
      refresh,
      sub: { on: i % 3 === 0, channel: (['邮件', '企业微信', '站内信'] as const)[i % 3], receivers: i % 3 === 0 ? ['王敏', ...members.slice(0, 2).map((m) => m.name)] : [], when: i % 2 ? '每次刷新后' : '每周一 09:00', onlyChanged: i % 2 === 0 },
      share: { mode: p.shared ? (i % 6 === 0 ? '所在部门' : '指定成员') : '仅自己', members, link: p.shared && i % 2 === 0, allowExport: i % 4 !== 3 },
      status: fail ? '刷新失败' : pending ? '口径待更新' : '正常',
      statusNote: fail ? '08:00 刷新失败：订单中心同步延迟，已保留 10-23 的结果' : pending?.pending,
      views: Math.floor(20 + r() * 300),
    } as QaPageFull;
  });
}

/* ───────────── 模板库 ───────────── */
export interface QaTemplate { id: string; name: string; scene: string; cards: PageCard[]; provider: string; uses: number; updatedAt: string; official: boolean; desc: string }
const TEMPLATE_NAMES = ['大区经营周报', '门店履约日报', '复购分析', '品类毛利分析', '会员运营周报', '促销效果复盘', '缺货预警', '新店爬坡', '生鲜损耗', '供应商绩效', '预算执行', '门店排名', '订单结构分析', '客单价分析', '退款监控', '履约时效', '渠道对比', '月度经营复盘', '双 11 作战看板', '店长晨会看板', '区域对比', '库存周转'];
const SCENES = ['经营分析', '履约', '会员', '财务', '营销', '供应链'];
function buildTemplates(): QaTemplate[] {
  return TEMPLATE_NAMES.map((n, i) => {
    const keys = Array.from({ length: 4 + (i % 4) }, (_, k) => POOL[(i * 2 + k * 3) % POOL.length]).filter((k, j, a) => a.indexOf(k) === j);
    return {
      id: `T${String(i + 1).padStart(3, '0')}`, name: n, scene: SCENES[i % SCENES.length],
      cards: keys.map((k, j) => ({ key: `T${i + 1}-C${j + 1}`, ...CARD_LIB[k] })),
      provider: i % 3 === 2 ? ['陈杰', '周婷', '刘婷'][i % 3] : '数据平台（官方）', official: i % 3 !== 2,
      uses: 320 - i * 13, updatedAt: `2026-10-${String(Math.max(1, 24 - i)).padStart(2, '0')}`,
      desc: `${n}：按所选范围和口径生成 ${keys.length} 张卡片，新建后可增删卡片、调整顺序。`,
    };
  });
}

export const MY_PAGES: QaPageFull[] = build();
export const PAGE_TEMPLATES: QaTemplate[] = buildTemplates();
export const PAGE_SCOPES = SCOPES;
export interface TplCreate { tpl: QaTemplate; name: string; scope: string; caliber: string; refresh: string }

/* ───────────── 其他人的页面（权限预览用：被预览用户自己的 + 共享给他的） ───────────── */
/** 页面所有者的部门（“所在部门”共享按此判断） */
export const OWNER_DEPT: Record<string, string> = { 王敏: '华东事业部 / 运营中心', 苏刚: '华东事业部 / 运营中心', 陈杰: '华东事业部 / 运营中心', 周婷: '华东事业部 / 会员中心' };
const OTHER_DEFS: [string, string, string, '仅自己' | '指定成员' | '所在部门', string[]][] = [
  ['P901', '华东门店日巡看板', '苏刚', '仅自己', []],
  ['P902', '华东二区出库跟踪', '苏刚', '指定成员', ['陈杰']],
  ['P903', '华东订单周报', '苏刚', '仅自己', []],
  ['P904', '运营中心门店排名', '陈杰', '指定成员', ['苏刚', '赵磊']],
  ['P905', '会员复购周报（华东）', '周婷', '指定成员', ['苏刚']],
];
export const OTHER_PAGES: QaPageFull[] = OTHER_DEFS.map(([id, name, owner, mode, who], i) => {
  const b = MY_PAGES[(i * 5 + 2) % MY_PAGES.length];
  return {
    ...b, id, name, owner, scope: ['华东大区', '华东二区', '华东一区'][i % 3], sourceKind: '手动新建', source: '手动新建', status: b.status === '刷新失败' ? '正常' : b.status,
    cards: b.cards.map((c, j) => ({ ...c, key: `${id}-C${j + 1}` })),
    sub: { ...b.sub, on: false, receivers: [] },
    share: { mode, members: who.map((n) => ({ name: n, dept: PEOPLE.find((p) => p.name === n)?.dept ?? OWNER_DEPT[n] ?? '', perm: '可查看' as SharePerm })), link: false, allowExport: true },
  };
});
