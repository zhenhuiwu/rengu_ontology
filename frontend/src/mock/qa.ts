/**
 * 问数工作台示例：2026 年 9 月华东大区复购客户销售额（不含税）。
 * 与评审稿 A.html 一致：合计 1,286.0 万元，环比 -8.4%（8 月 1,404.0 万元），复购客户 48,210 人。
 */
import { seeded } from './format';

export interface QaProvinceRow { key: string; province: string; sales: number; mom: number; customers: number }

/** 万元；环比为百分数（-6.1 = -6.1%） */
export const QA_PROVINCES: QaProvinceRow[] = [
  { key: 'sh', province: '上海', sales: 352.4, mom: -6.1, customers: 12840 },
  { key: 'js', province: '江苏', sales: 298.6, mom: -9.8, customers: 11260 },
  { key: 'zj', province: '浙江', sales: 276.3, mom: -7.2, customers: 10380 },
  { key: 'ah', province: '安徽', sales: 128.9, mom: -12.5, customers: 5120 },
  { key: 'fj', province: '福建', sales: 102.5, mom: -4.3, customers: 3960 },
  { key: 'jx', province: '江西', sales: 68.7, mom: -10.9, customers: 2730 },
  { key: 'sd', province: '山东', sales: 58.6, mom: -11.4, customers: 1920 },
];
export const QA_TOTAL_SALES = 1286.0;
export const QA_TOTAL_CUSTOMERS = 48210;
export const QA_TREND = { months: ['4月', '5月', '6月', '7月', '8月', '9月'], values: [1302.5, 1338.0, 1366.4, 1421.8, 1404.0, 1286.0] };

export const QA_SESSIONS = {
  today: ['华东大区复购客户销售额', '本周门店客流 Top 10'],
  week: ['10 月退货率（按品类）', '库存低于安全线的商品', '已出库未签收订单', '会员新增与流失', '双 11 备货门店清单', '9 月毛利率按品类', '促销券核销率（按渠道）', '缺货门店与影响销售额'],
  earlier: ['Q3 华东门店坪效排名', '国庆期间客流同比', '8 月新品动销率', '江苏门店人效对比', '会员积分兑换成本', '上海门店复购率周趋势'],
};

/** 大区对象（抽屉 ?drawer=region:REG-EC 与右栏共用） */
export const REGION_EC = {
  id: 'REG-EC', name: '华东大区', owner: '陈杰', parent: '华东事业部', provinces: 7, stores: 486,
  sales: '¥2,074.2 万元', status: '预警', since: '10-08', modifiedBy: '张伟', modifiedAt: '2026-10-12 14:20',
};

/**
 * 状态时间线：只放状态迁移和动作结果（属性变更如“负责人变更”不放）。
 * level 配色（群内已定）：normal 灰色＝正常；warn 橙色＝需要关注；unknown 结果未知也用橙色（警告色）+ 问号图标，
 * 标题写动作名，“结果未知 · 待对账”由 StatusTimeline 自动补一行（右栏自绘时间线同样补一行），不再用紫色；error 红色＝失败；蓝色只用于当前选中。
 */
export type QaTimelineLevel = 'warn' | 'error' | 'unknown' | 'normal';
export const QA_TIMELINE: { level: QaTimelineLevel; title: string; time: string; desc: string }[] = [
  { level: 'warn', title: '正常 → 预警', time: '2026-10-08 08:05', desc: '规则「复购环比预警」触发 · 环比 -8.4%' },
  { level: 'normal', title: '规则命中：复购率连降', time: '2026-09-21 09:40', desc: '事件「复购率下降」· 上海 12 店 · 系统' },
  { level: 'normal', title: '动作成功：调整促销预算', time: '2026-09-03 17:45', desc: '王敏 发起 · 赵磊 审批 · +¥200,000' },
  { level: 'unknown', title: '同步会员标签', time: '2026-08-29 22:10', desc: '规则自动发起 · CRM 超时' },
  { level: 'error', title: '动作失败：门店调拨补货', time: '2026-08-21 10:30', desc: '周婷 发起 · 赵磊 审批 · WMS 缺货' },
  { level: 'normal', title: '预警 → 正常', time: '2026-08-02 08:05', desc: '规则「复购环比预警」解除 · 系统' },
];

/** “结果未知”的统一文案（问数页面内所有出现处共用） */
export const QA_UNKNOWN_LABEL = '结果未知 · 待对账';

/* ───────────── 分页演示数据（左栏历史会话 / 历史轮次门店表 / 大区抽屉） ───────────── */

/** 历史会话：约 100 条，按时间倒序；date 为 YYYY-MM-DD（今天 = 2026-10-25） */
export interface QaSession { id: string; title: string; date: string }
const SESSION_TOPICS = [
  '门店客流 Top 10', '退货率（按品类）', '库存低于安全线的商品', '已出库未签收订单', '会员新增与流失', '备货门店清单',
  '毛利率按品类', '促销券核销率（按渠道）', '缺货门店与影响销售额', '门店坪效排名', '客流同比', '新品动销率',
  '门店人效对比', '会员积分兑换成本', '复购率周趋势', '客单价排名', '生鲜损耗率', '线上订单履约时长',
  '品类销售额环比', '自有品牌占比', '门店销售额同比', '大区预算执行率', '会员复购间隔', '供应商到货准时率',
];
const SESSION_SCOPES = ['华东', '上海', '江苏', '浙江', '安徽', '华东一区', '华东二区', '杭州', '南京', '苏州'];
const SESSION_PERIODS = ['本周', '上周', '10 月', '9 月', 'Q3', '近 30 天', '国庆期间', '8 月'];
function buildSessions(): QaSession[] {
  const rnd = seeded(20261025);
  const out: QaSession[] = [
    { id: 'S0001', title: '华东大区复购客户销售额', date: '2026-10-25' },
    { id: 'S0002', title: '本周门店客流 Top 10', date: '2026-10-25' },
    { id: 'S0003', title: '华东各重点门店销售额', date: '2026-10-25' },
  ];
  let day = new Date('2026-10-25T00:00:00');
  for (let i = out.length; i < 103; i++) {
    if (rnd() < 0.55) day = new Date(day.getTime() - 86400000 * (1 + Math.floor(rnd() * 2)));
    const t = SESSION_TOPICS[Math.floor(rnd() * SESSION_TOPICS.length)];
    const sc = SESSION_SCOPES[Math.floor(rnd() * SESSION_SCOPES.length)];
    const p = SESSION_PERIODS[Math.floor(rnd() * SESSION_PERIODS.length)];
    const y = day.getFullYear(), m = String(day.getMonth() + 1).padStart(2, '0'), d = String(day.getDate()).padStart(2, '0');
    out.push({ id: `S${String(i + 1).padStart(4, '0')}`, title: `${p}${sc}${t}`, date: `${y}-${m}-${d}` });
  }
  return out;
}
export const QA_SESSION_LIST: QaSession[] = buildSessions();
export const QA_TODAY = '2026-10-25';

/** 历史轮次：“上个月华东各重点门店的销售额？”—— 38 家门店，演示结果表分页、导出 */
export interface QaStoreRow { key: string; id: string; store: string; city: string; sales: number; mom: number; orders: number; /** 区县缩写 */ dist: string }
/** [城市, 城市码, [店名片段, 区县缩写][]]；区县缩写供公共门店编码“城市-区县-编号”使用（QaStoreRow.dist） */
const EC_CITIES: [string, string, [string, string][]][] = [
  ['上海', 'SH', [['浦东张江', 'PD'], ['徐汇漕河泾', 'XH'], ['闵行七宝', 'MH'], ['静安大悦城', 'JA'], ['长宁中山公园', 'CN'], ['杨浦五角场', 'YP'], ['普陀长风', 'PT'], ['宝山顾村', 'BS'], ['松江万达', 'SJ']]],
  ['杭州', 'HZ', [['西湖文三路', 'XH'], ['滨江星光', 'BJ'], ['拱墅大关', 'GS'], ['余杭未来科技城', 'YH'], ['萧山市心', 'XS']]],
  ['南京', 'NJ', [['鼓楼湖南路', 'GL'], ['江宁百家湖', 'JN'], ['建邺河西', 'JY'], ['玄武新街口', 'XW']]],
  ['苏州', 'SZ', [['园区湖西', 'GY'], ['姑苏观前', 'GS'], ['吴中石湖', 'WZ'], ['相城高铁新城', 'XC']]],
  ['宁波', 'NB', [['鄞州万达', 'YZ'], ['海曙天一', 'HS'], ['江北来福士', 'JB']]],
  ['合肥', 'HF', [['政务区天鹅湖', 'ZW'], ['蜀山大溪地', 'SS'], ['包河万达', 'BH']]],
  ['无锡', 'WX', [['梁溪崇安寺', 'LX'], ['滨湖万达', 'BH'], ['新吴硕放', 'XW']]],
  ['福州', 'FZ', [['鼓楼东街口', 'GL'], ['仓山万达', 'CS']]],
  ['南昌', 'NC', [['红谷滩万达', 'HG'], ['东湖八一广场', 'DH']]],
  ['济南', 'JN', [['历下泉城路', 'LX'], ['槐荫万达', 'HY'], ['高新区汉峪金谷', 'GX']]],
];
/** 与公共 STORES（stores.ts）是同一家店的：沿用公共 id 和全称，避免出现同名不同 id 的门店 */
const SAME_AS_SHARED: Record<string, [string, string]> = {
  上海浦东张江: ['SH0031', '上海浦东张江高科店'], 上海徐汇漕河泾: ['SH0047', '上海徐汇漕河泾店'], 上海闵行七宝: ['SH0102', '上海闵行七宝万科广场B1店'],
  上海静安大悦城: ['SH0133', '上海静安大悦城店'], 杭州西湖文三路: ['HZ0012', '杭州西湖文三路店'], 杭州滨江星光: ['HZ0058', '杭州滨江星光大道店'],
  南京鼓楼湖南路: ['NJ0021', '南京鼓楼湖南路店'], 南京江宁百家湖: ['NJ0076', '南京江宁百家湖店'], 苏州园区湖西: ['SZ0009', '苏州工业园区湖西CBD店'],
  宁波鄞州万达: ['NB0015', '宁波鄞州万达店'], 合肥政务区天鹅湖: ['HF0004', '合肥政务区天鹅湖店'], 无锡梁溪崇安寺: ['WX0011', '无锡梁溪崇安寺店'],
};
function buildStores(): QaStoreRow[] {
  const rnd = seeded(9038);
  const rows: QaStoreRow[] = [];
  for (const [city, code, names] of EC_CITIES) {
    names.forEach(([n, dist], i) => {
      const same = SAME_AS_SHARED[`${city}${n}`];
      const id = same?.[0] ?? `${code}${String(11 + i * 17).padStart(4, '0')}`;
      rows.push({ key: id, id, dist, store: same?.[1] ?? `${city}${n}店`, city, sales: Math.round((6 + rnd() * 10) * 10) / 10,
        mom: Math.round((-14 + rnd() * 16) * 10) / 10, orders: Math.round(1800 + rnd() * 3200) });
    });
  }
  return rows.sort((a, b) => b.sales - a.sales);
}
export const QA_EC_STORES: QaStoreRow[] = buildStores();

/** 大区抽屉：关联门店（38 家，与上表一致）与完整状态时间线（右栏只展示前 6 条） */
export const QA_REGION_RELATED = QA_EC_STORES.map((s) => ({
  kind: 'store', id: s.id, label: s.store, note: `${s.city} · 9 月 ${s.sales.toFixed(1)} 万元`,
}));
function buildRegionTimeline() {
  const older: { level: QaTimelineLevel; title: string; time: string; desc: string }[] = [];
  const evts: [QaTimelineLevel, string, string][] = [
    ['normal', '动作成功：发放会员日优惠券', '王敏 发起 · 赵磊 审批'],
    ['warn', '正常 → 预警', '规则「客流环比预警」触发 · 系统'],
    ['normal', '预警 → 正常', '规则「客流环比预警」解除 · 系统'],
    ['normal', '规则命中：库存低于安全线', '事件「缺货」· 杭州 3 店 · 系统'],
    ['error', '动作失败：同步价签', '规则自动发起 · ESL 接口返回 500'],
    ['normal', '动作成功：调拨补货', '周婷 发起 · 赵磊 审批'],
    ['unknown', '推送会员积分', '规则自动发起 · CRM 超时'],
  ];
  let t = new Date('2026-07-30T09:00:00').getTime();
  for (let i = 0; i < 20; i++) {
    const [level, title, desc] = evts[i % evts.length];
    const d = new Date(t);
    const s = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')} ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
    older.push({ level, title, time: s, desc });
    t -= 86400000 * 3 + 3600000 * (i % 5);
  }
  return [...QA_TIMELINE, ...older];
}
export const QA_REGION_TIMELINE = buildRegionTimeline();

/* ───────────── 左下抽屉：常用问题 / 我的页面（各超过一页，每页 10 条） ───────────── */
export interface QaFavorite { id: string; question: string; uses: number; lastUsed: string }
export const QA_FAVORITE_LIST: QaFavorite[] = [
  '本月销售额同比', '已出库超 48 小时未签收的订单', '各门店客单价排名', '华东大区复购客户销售额', '本周门店客流 Top 10',
  '库存低于安全线的商品', '促销券核销率（按渠道）', '9 月毛利率按品类', '会员新增与流失', '缺货门店与影响销售额',
  '生鲜损耗率（按门店）', '线上订单履约时长', '自有品牌销售占比', '大区预算执行率', '供应商到货准时率',
  '新品动销率', '门店人效对比', '退货率（按品类）',
].map((q, i) => ({ id: `F${String(i + 1).padStart(3, '0')}`, question: q, uses: 86 - i * 4, lastUsed: `10-${String(25 - Math.floor(i * 1.3)).padStart(2, '0')}` }));

export interface QaPage { id: string; name: string; cards: number; updatedAt: string; shared: boolean }
export const QA_PAGE_LIST: QaPage[] = [
  '华东大区销售周报', '门店履约看板', '复购客户月度分析', '双 11 备货跟踪', '会员运营周报', '品类毛利看板',
  '缺货预警看板', '促销效果复盘', '华东一区门店排名', '新店爬坡跟踪', '生鲜损耗日报', '供应商绩效月报', '大区预算执行',
].map((n, i) => ({ id: `P${String(i + 1).padStart(3, '0')}`, name: n, cards: 4 + ((i * 3) % 7), updatedAt: `10-${String(25 - i * 2 > 0 ? 25 - i * 2 : 1).padStart(2, '0')}`, shared: i % 3 === 0 }));

/** 会话共用的范围与口径（输入框下方）；中途修改会在对话里插一条系统提示 */
export const QA_SCOPE_OPTIONS = [
  { value: '华东', label: '范围：华东大区' },
  { value: '华东一区', label: '范围：华东一区' },
  { value: '华东二区', label: '范围：华东二区' },
];
export const QA_CALIBER_OPTIONS = [
  { value: '不含税', label: '口径：不含税' },
  { value: '含税', label: '口径：含税' },
];

/** 申请权限抽屉的审批人（按申请范围路由到数据负责人；不展示该范围是否有数据） */
/**
 * 指标完整名称 + 版本，和指标口径页（静态负责人提交 1aaa55b）一致。
 * 公共 METRICS 里暂无 repeat_sales / repeat_sales_tax，先在这里登记；PDE 补进公共 mock 后改为从 metricById 读。
 */
export const QA_METRIC = {
  ex: { id: 'repeat_sales', name: '复购客户销售额（不含税）', version: 'v3' },
  in: { id: 'repeat_sales_tax', name: '复购客户销售额（含税）', version: 'v1' },
} as const;
export const qaMetricLabel = (k: 'ex' | 'in') => `${QA_METRIC[k].name} ${QA_METRIC[k].version}`;

export const QA_APPLY = { scope: '大区 = 华南', object: `${QA_METRIC.ex.name} ${QA_METRIC.ex.version}`, approver: '刘洋（数据权限管理员）', durations: ['30 天', '90 天', '180 天'] };

/** 左栏贴底只露前几条 */
export const QA_FAVORITES = QA_FAVORITE_LIST.slice(0, 3).map((f) => f.question);
export const QA_MY_PAGES = QA_PAGE_LIST.slice(0, 2).map((p) => p.name);
