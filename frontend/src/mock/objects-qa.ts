/**
 * 对象搜索 / 对象详情（使用区）的 mock。负责人：智能问数项目调研员。
 * 不另建一套对象数据：标题、属性、指标、时间线、关联对象都从 objectRegistry（resolveObject）取，
 * 这里只补：
 *   1. 扩充门店到 100+ 家（OBJ_EXTRA_STORES），并追加进公共 ALL_STORES，保证抽屉、悬停卡能解析；
 *      建议 PDE 把这批门店并入 src/mock/stores.ts 的 ALL_STORES，之后删掉下面的追加代码。
 *   2. 搜索页的类型、分面定义；详情页的属性分组、关联分组、补充时间线、动作记录、可执行动作、快捷提问。
 */
import { ALL_STORES, storeById, type Store } from './stores';
import { PRODUCTS, productBySku } from './products';
import { ORDERS, orderById } from './orders';
import { metricById } from './metrics';
import { REGION_EC, QA_EC_STORES, QA_METRIC } from './qa';
import { seeded, fmtMoney, fmtInt } from './format';

/* ---------------- 1. 扩充门店 ---------------- */
const CITY: Record<string, { code: string; region: string; names: string[] }> = {
  上海: { code: 'SH', region: '华东一区', names: ['虹口北外滩', '黄浦新天地', '嘉定新城', '青浦赵巷', '奉贤南桥', '金山万达', '浦东世纪公园', '浦东金桥', '浦东周浦万达', '闵行莘庄', '徐汇田林', '长宁古北', '普陀真如', '杨浦新江湾'] },
  杭州: { code: 'HZ', region: '华东二区', names: ['上城湖滨', '西湖黄龙', '拱墅运河', '滨江龙湖天街', '余杭良渚', '临平新城', '萧山万象汇', '钱塘下沙'] },
  南京: { code: 'NJ', region: '华东三区', names: ['秦淮夫子庙', '栖霞仙林', '浦口江浦', '雨花台软件谷', '鼓楼龙江', '江宁九龙湖'] },
  苏州: { code: 'SZ', region: '华东三区', names: ['园区金鸡湖', '吴江太湖新城', '高新区狮山', '昆山花桥', '常熟虞山'] },
  宁波: { code: 'NB', region: '华东二区', names: ['北仑新碶', '镇海骆驼', '鄞州南部商务区', '慈溪新城'] },
  合肥: { code: 'HF', region: '华东四区', names: ['庐阳四牌楼', '瑶海万达', '高新区创新产业园', '滨湖新区'] },
  无锡: { code: 'WX', region: '华东三区', names: ['惠山万达', '锡山东亭', '江阴澄江'] },
  福州: { code: 'FZ', region: '华东五区', names: ['台江万象城', '晋安宝龙', '马尾快安', '闽侯上街'] },
  南昌: { code: 'NC', region: '华东五区', names: ['西湖万达', '青山湖高新', '新建望城', '红谷滩秋水广场'] },
  济南: { code: 'JN', region: '华东五区', names: ['市中万达', '天桥北园', '历城华山', '长清大学城', '高新区奥体'] },
};
const MANAGERS = ['顾晓彤', '方建华', '姚倩', '蒋志远', '沈佳怡', '陆海峰', '潘雪', '杜文涛', '叶思敏', '程立', '袁梦', '丁浩'];
export const OBJ_EXTRA_STORES: Store[] = (() => {
  const r = seeded(51207);
  const out: Store[] = [];
  let i = 0;
  for (const [city, c] of Object.entries(CITY)) {
    c.names.forEach((n, k) => {
      const fmt = n.includes('万达') || n.includes('万象') || n.includes('天街') || n.includes('宝龙') ? '商场店'
        : n.includes('软件谷') || n.includes('产业园') || n.includes('商务区') || n.includes('高新') ? '写字楼店' : '社区店';
      const roll = r();
      const id = `${c.code}${String(201 + k * 7).padStart(4, '0')}`;
      const y = 2017 + Math.floor(r() * 9);
      out.push({
        id, code: `${c.code}-${id.slice(2)}`, name: `${city}${n}店`, city, region: c.region, format: fmt,
        manager: MANAGERS[i % MANAGERS.length],
        area: roll < 0.05 ? null : Math.round(((fmt === '商场店' ? 430 : fmt === '写字楼店' ? 220 : 310) + r() * 200) * 10) / 10,
        openedAt: `${y}-${String(1 + Math.floor(r() * 12)).padStart(2, '0')}-${String(1 + Math.floor(r() * 28)).padStart(2, '0')}`,
        status: roll > 0.93 ? '已闭店' : roll > 0.85 ? '装修中' : '营业中',
      });
      i++;
    });
  }
  return out;
})();
// 追加进公共 ALL_STORES（只加不改，按 id 去重，热更新重复执行也不会重复追加）
OBJ_EXTRA_STORES.forEach((s) => { if (!ALL_STORES.some((x) => x.id === s.id)) ALL_STORES.push(s); });

/** 门店 9 月销售额（不含税，万元）与环比：问数结果表里有的用原值，其余按 id 生成 */
const hash = (s: string) => [...s].reduce((a, ch) => (a * 31 + ch.charCodeAt(0)) >>> 0, 7);
export function storeSales(id: string) {
  const q = QA_EC_STORES.find((x) => x.id === id);
  if (q) return { sales: q.sales, mom: q.mom };
  const r = seeded(hash(id));
  return { sales: Math.round((5 + r() * 14) * 10) / 10, mom: Math.round((-15 + r() * 22) * 10) / 10 };
}

/* ---------------- 2. 搜索页：类型与分面 ---------------- */
export type ObjKind = 'store' | 'product' | 'order';
export interface Facet { key: string; label: string; get: (row: any) => string; order?: string[] }
export interface SearchColumn { key: string; title: string; get: (row: any) => string | number | null; num?: boolean; width?: number }
export interface ObjType {
  kind: ObjKind; label: string; unit: string; rows: () => any[]; id: (row: any) => string; title: (row: any) => string;
  kw: (row: any) => string; kwPlaceholder: string; facets: Facet[]; columns: SearchColumn[];
}
export const OBJ_TYPES: ObjType[] = [
  {
    kind: 'store', label: '门店', unit: '家', rows: () => ALL_STORES, id: (s) => s.id, title: (s) => s.name,
    kw: (s) => `${s.name} ${s.code} ${s.id} ${s.manager}`, kwPlaceholder: '门店名称 / 编码 / 店长',
    facets: [
      { key: 'region', label: '区域', get: (s) => s.region, order: ['华东一区', '华东二区', '华东三区', '华东四区', '华东五区'] },
      { key: 'city', label: '城市', get: (s) => s.city },
      { key: 'format', label: '业态', get: (s) => s.format, order: ['社区店', '商场店', '写字楼店'] },
      { key: 'status', label: '状态', get: (s) => s.status, order: ['营业中', '装修中', '已闭店'] },
    ],
    columns: [
      { key: 'code', title: '门店编码', get: (s) => s.code, width: 120 },
      { key: 'city', title: '城市', get: (s) => s.city, width: 72 },
      { key: 'region', title: '区域', get: (s) => s.region, width: 96 },
      { key: 'format', title: '业态', get: (s) => s.format, width: 96 },
      { key: 'manager', title: '店长', get: (s) => s.manager, width: 88 },
      { key: 'area', title: '营业面积（㎡）', get: (s) => s.area, num: true, width: 128 },
      { key: 'sales', title: '9 月销售额（不含税，万元）', get: (s) => storeSales(s.id).sales, num: true, width: 200 },
      { key: 'openedAt', title: '开业日期', get: (s) => s.openedAt, width: 112 },
      { key: 'status', title: '状态', get: (s) => s.status, width: 88 },
    ],
  },
  {
    kind: 'product', label: '商品', unit: '个', rows: () => PRODUCTS, id: (p) => p.sku, title: (p) => p.name,
    kw: (p) => `${p.name} ${p.sku} ${p.barcode} ${p.brand}`, kwPlaceholder: '商品名称 / SKU / 条码',
    facets: [
      { key: 'cat', label: '品类', get: (p) => p.category.split('/')[0] },
      { key: 'brand', label: '品牌', get: (p) => p.brand },
    ],
    columns: [
      { key: 'sku', title: 'SKU', get: (p) => p.sku, width: 100 },
      { key: 'spec', title: '规格', get: (p) => p.spec, width: 160 },
      { key: 'category', title: '品类', get: (p) => p.category, width: 160 },
      { key: 'brand', title: '品牌', get: (p) => p.brand, width: 120 },
      { key: 'price', title: '零售价（元）', get: (p) => p.price, num: true, width: 120 },
    ],
  },
  {
    kind: 'order', label: '订单', unit: '单', rows: () => ORDERS, id: (o) => o.id, title: (o) => o.id,
    kw: (o) => `${o.id} ${storeById(o.storeId)?.name ?? ''} ${o.waybill ?? ''}`, kwPlaceholder: '订单号 / 门店 / 运单号',
    facets: [
      { key: 'channel', label: '渠道', get: (o) => o.channel },
      { key: 'ostatus', label: '状态', get: (o) => o.status, order: ['待支付', '待出库', '拣货中', '已出库', '已签收', '已取消'] },
    ],
    columns: [
      { key: 'store', title: '门店', get: (o) => storeById(o.storeId)?.name ?? '--', width: 220 },
      { key: 'channel', title: '渠道', get: (o) => o.channel, width: 112 },
      { key: 'amount', title: '实付金额（含税，元）', get: (o) => o.amount, num: true, width: 160 },
      { key: 'createdAt', title: '下单时间', get: (o) => o.createdAt, width: 168 },
      { key: 'status', title: '状态', get: (o) => o.status, width: 88 },
    ],
  },
];
export const objType = (k: string) => OBJ_TYPES.find((t) => t.kind === k) ?? OBJ_TYPES[0];
/** 当前用户可见范围（王敏：大区 = 华东）。无权限范围只说明“无权限”，不给数量 */
export const OBJ_SCOPE = { visible: '华东大区（华东一区至五区）', hidden: '其他大区' };
export const OBJ_CUTOFF = '2026-10-25 06:00';

/* ---------------- 3. 详情页补充数据 ---------------- */
export const KIND_LABEL: Record<string, string> = { store: '门店', product: '商品', order: '订单', region: '大区', metric: '指标' };

/** 属性分组：按字段名归组，没列到的进“其他” */
export const FIELD_GROUPS: Record<string, { title: string; labels: string[] }[]> = {
  store: [
    { title: '基本信息', labels: ['门店编码', '城市', '区域', '业态', '开业日期'] },
    { title: '经营', labels: ['店长', '营业面积', '近 7 日订单'] },
  ],
  region: [
    { title: '基本信息', labels: ['对象类型', '编码', '上级', '覆盖', '状态'] },
    { title: '经营', labels: ['负责人', '销售额', '复购客户', '最近修改'] },
  ],
  product: [
    { title: '基本信息', labels: ['SKU', '条码', '品牌', '规格', '品类'] },
    { title: '销售', labels: ['销售单位', '零售价'] },
  ],
  order: [
    { title: '基本信息', labels: ['订单号', '渠道', '门店', '会员手机'] },
    { title: '金额', labels: ['商品件数', '优惠金额', '实付金额'] },
    { title: '履约', labels: ['发货仓', '下单时间', '出库时间', '运单号'] },
  ],
};

/** 指标名 → 口径版本（公共 METRICS 里有的从公共取；复购客户销售额取问数登记的口径） */
export function caliberOf(label: string): string | null {
  const pick = (id: string) => { const m = metricById(id); return m ? `${m.name} ${m.version}` : null; };
  if (label.startsWith('复购客户销售额（不含税）')) return `${QA_METRIC.ex.name} ${QA_METRIC.ex.version}`;
  if (label.startsWith('复购客户销售额（含税）')) return `${QA_METRIC.in.name} ${QA_METRIC.in.version}`;
  if (label.startsWith('销售额（不含税）')) return pick('gmv');
  if (label.startsWith('销售额（含税）') || label.startsWith('实付金额（含税）')) return pick('gmv_tax');
  if (label.startsWith('订单数')) return pick('order_cnt');
  if (label.startsWith('客单价')) return pick('atv');
  if (label.startsWith('出库率')) return pick('ship_ontime');
  return null;
}
/** 门店额外的关键指标（9 月销售额，不含税） */
export function storeExtraKpi(id: string) {
  const s = storeSales(id); const m = metricById('gmv');
  return { label: '9 月销售额（不含税）', value: fmtMoney(s.sales, 1), unit: '万元', compare: `环比 ${s.mom > 0 ? '+' : ''}${s.mom.toFixed(1)}%`, tone: s.mom < 0 ? 'down' as const : 'up' as const, caliber: m ? `${m.name} ${m.version}` : null };
}

export interface RelItem { kind: string; id: string; label: string; note: string }
export interface RelGroup { key: string; title: string; items: RelItem[] }
/** 关联对象按关系类型分组：公共 related 按 kind 归组，门店再补“所属大区、同城门店、常售商品” */
export function relationGroups(kind: string, id: string, related: RelItem[] = []): RelGroup[] {
  const REL_TITLE: Record<string, string> = { order: '订单', product: '商品', store: '门店', region: '大区' };
  const groups: RelGroup[] = [];
  if (kind === 'store') {
    const s = storeById(id);
    if (s) {
      groups.push({ key: 'region', title: '所属大区', items: [{ kind: 'region', id: REGION_EC.id, label: REGION_EC.name, note: `${s.region} · 负责人 ${REGION_EC.owner}` }] });
      groups.push({ key: 'peer', title: '同城门店', items: ALL_STORES.filter((x) => x.city === s.city && x.id !== s.id && x.name !== s.name).map((x) => ({ kind: 'store', id: x.id, label: x.name, note: `${x.format} · ${x.status}` })) });
      const r = seeded(hash(id) + 3);
      const top = [...PRODUCTS].sort(() => r() - 0.5).slice(0, 8);
      groups.push({ key: 'product', title: '常售商品', items: top.map((p) => ({ kind: 'product', id: p.sku, label: p.name, note: `${p.spec} · 近 30 天 ${fmtInt(Math.round(200 + r() * 1800))} 件` })) });
    }
  }
  const byKind = new Map<string, RelItem[]>();
  related.forEach((x) => { byKind.set(x.kind, [...(byKind.get(x.kind) ?? []), x]); });
  byKind.forEach((items, k) => {
    const title = kind === 'region' && k === 'store' ? '下属门店' : kind === 'order' && k === 'store' ? '下单门店' : kind === 'order' && k === 'product' ? '订单商品' : REL_TITLE[k] ?? k;
    groups.push({ key: `rel-${k}`, title, items });
  });
  if (kind === 'product') {
    const sku = id;
    const orders = ORDERS.filter((o) => o.lines.some((l) => l.sku === sku));
    groups.push({ key: 'orders', title: '包含该商品的订单', items: orders.map((o) => ({ kind: 'order', id: o.id, label: o.id, note: `${storeById(o.storeId)?.name ?? '--'} · ${o.status}` })) });
  }
  return groups;
}

export type TlStatus = 'done' | 'warn' | 'error' | 'unknown' | undefined;
export interface TlItem { time: string; text: string; desc?: string; status?: TlStatus }
const TL_EVENTS: [TlStatus, string, string][] = [
  [undefined, '日结数据已同步', '系统'],
  ['warn', '正常 → 预警', '规则「客流环比预警」触发 · 系统'],
  [undefined, '预警 → 正常', '规则「客流环比预警」解除 · 系统'],
  [undefined, '动作成功：发放门店优惠券', '王敏 发起 · 赵磊 审批'],
  ['error', '动作失败：同步价签', '规则自动发起 · ESL 接口返回 500'],
  [undefined, '规则命中：库存低于安全线', '事件「缺货」· 系统'],
  ['unknown', '推送会员积分', '规则自动发起 · CRM 超时'],
  [undefined, '动作成功：指派巡店任务', '陈杰 发起'],
];
const fmtTime = (t: number) => {
  const d = new Date(t); const p = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
};
/** 门店、商品、订单的补充时间线（大区直接用 objectRegistry 的 26 条） */
export function extraTimeline(kind: string, id: string, base: TlItem[] = []): TlItem[] {
  if (kind === 'region' || kind === 'order') return base;
  const r = seeded(hash(id) + 11);
  const out: TlItem[] = [];
  let t = new Date('2026-10-24T22:00:00+08:00').getTime();
  for (let i = 0; i < 18; i++) {
    let [status, text, desc] = TL_EVENTS[Math.floor(r() * TL_EVENTS.length)];
    // “结果未知 · 待对账”只保留最近一条，更早的视为已对账
    while (status === 'unknown' && (out.some((o) => o.status === 'unknown') || base.some((o) => o.status === 'unknown'))) [status, text, desc] = TL_EVENTS[Math.floor(r() * TL_EVENTS.length)];
    out.push({ time: fmtTime(t), text, desc, status });
    t -= 86400000 * (1 + Math.floor(r() * 4)) + 3600000 * Math.floor(r() * 9);
  }
  // 公共时间线里的条目（开业、店长变更等）合并后按时间倒序
  return [...base, ...out].sort((a, b) => b.time.localeCompare(a.time));
}

export interface ActRecord { id: string; action: string; by: string; approver: string; time: string; result: '成功' | '失败' | '待审批' | '结果未知' | '执行中' }
export function actionRecords(kind: string, id: string): ActRecord[] {
  const NAMES: Record<string, string[]> = {
    store: ['发放门店优惠券', '指派巡店任务', '同步价签', '调拨补货', '推送会员积分'],
    region: ['发放复购召回优惠券', '指派大区经理跟进', '调整大区预算', '同步会员标签'],
    product: ['调整零售价', '同步价签', '设置安全库存'],
    order: ['补发出库单', '修改收货地址', '发起退款'],
  };
  const names = NAMES[kind] ?? NAMES.store;
  const who = ['王敏', '陈杰', '周婷', '规则自动发起'];
  const res: ActRecord['result'][] = ['成功', '成功', '成功', '成功', '失败', '结果未知', '成功', '待审批'];
  const r = seeded(hash(kind + id) + 23);
  const n = kind === 'order' ? 6 : 24 + Math.floor(r() * 10);
  const out: ActRecord[] = [];
  let t = new Date('2026-10-25T10:20:00+08:00').getTime();
  for (let i = 0; i < n; i++) {
    // 待审批只出现在最近几条；“结果未知”最多一条（更早的已对账）
    let result: ActRecord['result'] = i === 0 ? '待审批' : res[Math.floor(r() * res.length)];
    if (result === '待审批' && i > 2) result = '成功';
    if (result === '结果未知' && out.some((o) => o.result === '结果未知')) result = '成功';
    const by = who[Math.floor(r() * who.length)];
    const approver = result === '待审批' || (by !== '规则自动发起' && r() > 0.4) ? '赵磊' : '--';
    out.push({ id: `ACT-${fmtTime(t).slice(0, 10).replace(/-/g, '')}-${String(100 + Math.floor(r() * 900))}`, action: names[Math.floor(r() * names.length)],
      by, approver, time: fmtTime(t), result });
    t -= 3600000 * (6 + Math.floor(r() * 60));
  }
  return out;
}

export interface ObjAction { key: string; name: string; desc: string; fields: { label: string; value: string }[]; impact: string; irreversible?: boolean; blocked?: string; approver?: string }
export function objActions(kind: string, id: string): ObjAction[] {
  if (kind === 'store') {
    const s = storeById(id);
    const closed = s?.status === '已闭店';
    return [
      { key: 'coupon', name: '发放门店优惠券', desc: '满 99 减 10 · 约 1,200 位会员', fields: [{ label: '券模板', value: '满 99 减 10（门店券）' }, { label: '有效期', value: '7 天' }], impact: '向该门店近 90 天到店会员发券，约 1,200 人', approver: '赵磊', blocked: closed ? '已闭店门店不能发券' : undefined },
      { key: 'patrol', name: '指派巡店任务', desc: `指派给店长 ${s?.manager ?? '--'}`, fields: [{ label: '执行人', value: s?.manager ?? '--' }, { label: '截止', value: '2026-10-27' }], impact: '生成一条巡店任务，不改业务数据' },
      { key: 'status', name: '调整营业状态', desc: `当前：${s?.status ?? '--'}`, fields: [{ label: '调整为', value: s?.status === '营业中' ? '装修中' : '营业中' }], impact: '写回门店主数据，影响线上门店可见性', irreversible: true, approver: '赵磊' },
    ];
  }
  if (kind === 'region') {
    return [
      { key: 'coupon', name: '发放复购召回优惠券', desc: '影响 3,812 位会员', fields: [{ label: '券模板', value: '复购召回券 20 元' }, { label: '人群', value: '华东大区 60 天未复购会员' }], impact: '向 3,812 位会员发券', approver: '赵磊' },
      { key: 'follow', name: '指派大区经理跟进', desc: `指派给 ${REGION_EC.owner}`, fields: [{ label: '执行人', value: REGION_EC.owner }], impact: '生成跟进任务，不改业务数据' },
    ];
  }
  if (kind === 'product') {
    const p = productBySku(id);
    return [
      { key: 'price', name: '调整零售价', desc: `当前 ${p ? fmtMoney(p.price) : '--'} 元`, fields: [{ label: '新售价', value: p ? fmtMoney(Math.round(p.price * 95) / 100) : '--' }], impact: '写回全部门店价签', irreversible: true, approver: '赵磊' },
      { key: 'esl', name: '同步价签', desc: '推送到 107 家门店电子价签', fields: [], impact: '只同步现有价格' },
    ];
  }
  const o = orderById(id);
  return [
    { key: 'reship', name: '补发出库单', desc: o?.status === '已出库' || o?.status === '已签收' ? '订单已出库' : '重新下发到仓', fields: [{ label: '发货仓', value: o?.warehouse ?? '--' }], impact: '重新生成出库单', blocked: o?.status === '已出库' || o?.status === '已签收' ? '订单已出库，不能补发' : undefined },
    { key: 'refund', name: '发起退款', desc: `实付 ${o ? fmtMoney(o.amount) : '--'} 元`, fields: [{ label: '退款金额', value: o ? fmtMoney(o.amount) : '--' }], impact: '原路退回，不可撤销', irreversible: true, approver: '赵磊' },
  ];
}

export function quickQuestions(kind: string, title: string): string[] {
  if (kind === 'store') return [`${title} 9 月销售额和环比`, `${title} 在同城门店里排第几`, `${title} 近 7 天订单出库情况`];
  if (kind === 'region') return [`${title} 复购客户销售额为什么下降`, `${title} 哪些门店拖累最大`, `${title} 近 6 个月复购占比`];
  if (kind === 'product') return [`${title} 近 30 天在哪些门店卖得最好`, `${title} 售价调整前后销量对比`];
  return [`${title} 为什么还没出库`, `${title} 的会员近 90 天下单记录`];
}

/* ---------------- 4. 带范围提问的回答（mock） ---------------- */
export interface AskScope { kind: string; label: string; conds: string[]; ids: string[]; single?: { kind: string; id: string; title: string } }
export interface AskAnswer { text: string; basis: string; rows: { id: string; kind: string; name: string; value: string; note: string }[]; rowsTitle?: string }
export function answerFor(scope: AskScope, question: string): AskAnswer {
  const gmv = metricById('gmv'); const gmvLabel = gmv ? `${gmv.name} ${gmv.version}` : '销售额（不含税）';
  const basis = `范围：${scope.label}${scope.conds.length ? ` · ${scope.conds.join('、')}` : ''} · 已按行级权限过滤 · 数据截至 ${OBJ_CUTOFF}`;
  if (scope.single) {
    const { kind, id, title } = scope.single;
    if (kind === 'store') {
      const s = storeById(id)!; const ss = storeSales(id);
      const peers = ALL_STORES.filter((x) => x.city === s.city).map((x) => ({ x, v: storeSales(x.id).sales })).sort((a, b) => b.v - a.v);
      const rank = peers.findIndex((p) => p.x.id === id) + 1;
      return {
        text: `2026 年 9 月，${title} ${gmvLabel} 为 ${fmtMoney(ss.sales, 1)} 万元，环比 ${ss.mom > 0 ? '+' : ''}${ss.mom.toFixed(1)}%，在${s.city} ${peers.length} 家门店中排第 ${rank}。`,
        basis, rowsTitle: `${s.city}门店 · ${gmvLabel}（万元）`,
        rows: peers.slice(0, 5).map((p) => ({ id: p.x.id, kind: 'store', name: p.x.name, value: fmtMoney(p.v, 1), note: p.x.id === id ? '当前门店' : p.x.format })),
      };
    }
    return { text: `已限定在${KIND_LABEL[kind] ?? ''}「${title}」上回答：${question}。这类问题需要在问数工作台按明细展开，这里先给出对象的关键指标，见上方概览。`, basis, rows: [] };
  }
  if (scope.kind === 'store') {
    const rows = scope.ids.map((id) => ({ s: storeById(id)!, v: storeSales(id) })).filter((r) => r.s);
    const sum = rows.reduce((a, r) => a + r.v.sales, 0);
    const down = rows.filter((r) => r.v.mom < 0).length;
    const top = [...rows].sort((a, b) => b.v.sales - a.v.sales).slice(0, 5);
    return {
      text: `这 ${fmtInt(rows.length)} 家门店 2026 年 9 月${gmvLabel}合计 ${fmtMoney(sum, 1)} 万元，其中 ${fmtInt(down)} 家环比下降。销售额最高的 5 家见下表。`,
      basis, rowsTitle: `${gmvLabel}（万元）· 前 5`,
      rows: top.map((r) => ({ id: r.s.id, kind: 'store', name: r.s.name, value: fmtMoney(r.v.sales, 1), note: `${r.s.city} · 环比 ${r.v.mom > 0 ? '+' : ''}${r.v.mom.toFixed(1)}%` })),
    };
  }
  if (scope.kind === 'order') {
    const os = scope.ids.map((id) => orderById(id)!).filter(Boolean);
    const sum = os.reduce((a, o) => a + o.amount, 0);
    const tax = metricById('gmv_tax');
    return { text: `这 ${fmtInt(os.length)} 单实付金额合计 ${fmtMoney(sum)} 元（${tax ? `${tax.name} ${tax.version}` : '含税'}，未扣退款），已出库或签收 ${fmtInt(os.filter((o) => o.shippedAt).length)} 单。`, basis, rows: [] };
  }
  const ps = scope.ids.map((id) => productBySku(id)!).filter(Boolean);
  return { text: `这 ${fmtInt(ps.length)} 个商品覆盖 ${new Set(ps.map((p) => p.category.split('/')[0])).size} 个一级品类，平均零售价 ${fmtMoney(ps.reduce((a, p) => a + p.price, 0) / Math.max(1, ps.length))} 元。`, basis, rows: [] };
}

