/**
 * 对象注册表：悬停卡（HoverCard）和详情抽屉（DetailDrawer）共用同一份数据解析。
 * 新增对象类型：在 RESOLVERS 里加一个 kind。
 */
import {
  storeById, productBySku, orderById, metricById, ORDERS, REGION_EC, QA_TOTAL_CUSTOMERS, QA_TOTAL_SALES, QA_REGION_TIMELINE, QA_REGION_RELATED,
  fmtMoney, fmtInt, fmtPct, EMPTY,
} from '@/mock';
import { ACTION_EVENT_RESOLVERS } from './actionEventResolvers';

export interface ObjField { label: string; value: string }
/** 时间线状态 → 颜色见 theme.ts timelineTone：done/无=灰，warn/unknown=橙（unknown 带问号），error=红 */
export interface ObjTimeline { time: string; text: string; desc?: string; status?: 'done' | 'warn' | 'error' | 'unknown' }
/** 指标行：抽屉“指标”页签用，2–4 行即可 */
export interface ObjKpi { label: string; value: string; unit?: string; compare?: string; tone?: 'up' | 'down' }
/**
 * 额外页签（可选）。默认抽屉只有 属性 + 时间线 (+ 关联对象)；resolver 返回 tabs 时追加在“属性”之后。
 * pinToProps: true 时，这组指标同时显示在“属性”页签下方（避免属性页大片空白）。
 */
export interface ObjTab { key: string; title: string; kpis?: ObjKpi[]; fields?: ObjField[]; pinToProps?: boolean }
export interface ObjView {
  kind: string; kindLabel: string; id: string; title: string; code: string;
  status?: { text: string; color: 'default' | 'processing' | 'success' | 'warning' | 'error' };
  brief: ObjField[];       // 悬停卡显示（≤4 项）
  fields: ObjField[];      // 抽屉“属性”页签
  timeline?: ObjTimeline[];
  tabs?: ObjTab[];         // 可选额外页签，如“指标”
  related?: { kind: string; id: string; label: string; note: string }[];
}

const orderStatusColor = (s: string): NonNullable<ObjView['status']>['color'] =>
  s === '已出库' || s === '已签收' ? 'success' : s === '已取消' ? 'default' : s === '待支付' ? 'warning' : 'processing';

const RESOLVERS: Record<string, (id: string) => ObjView | null> = {
  // 大区（问数工作台右栏“打开对象详情”）
  region(id) {
    if (id !== REGION_EC.id) return null;
    const r = REGION_EC;
    return {
      kind: 'region', kindLabel: '大区', id: r.id, title: r.name, code: r.id,
      status: { text: r.status, color: 'warning' },
      brief: [
        { label: '负责人', value: r.owner }, { label: '覆盖', value: `${r.provinces} 省 · ${r.stores} 家门店` },
        { label: '销售额', value: `${r.sales}（9 月）` }, { label: '状态', value: `${r.status}（自 ${r.since}）` },
      ],
      fields: [
        { label: '对象类型', value: '大区' }, { label: '编码', value: r.id },
        { label: '负责人', value: r.owner }, { label: '上级', value: r.parent },
        { label: '覆盖', value: `${r.provinces} 省 · ${r.stores} 家门店` }, { label: '状态', value: `${r.status}（自 ${r.since}）` },
        { label: '销售额', value: `${r.sales}（9 月）` }, { label: '复购客户', value: `${fmtInt(QA_TOTAL_CUSTOMERS)} 人` },
        { label: '最近修改', value: `${r.modifiedBy} · ${r.modifiedAt}` },
      ],
      tabs: [{
        key: 'kpi', title: '指标', pinToProps: true,
        kpis: [
          { label: '销售额（不含税）', value: '2,074.2', unit: '万元', compare: '9 月' },
          { label: '复购客户销售额（不含税）', value: fmtMoney(QA_TOTAL_SALES, 1), unit: '万元', compare: '环比 -8.4%（8 月 1,404.0）', tone: 'down' },
          { label: '复购客户', value: fmtInt(QA_TOTAL_CUSTOMERS), unit: '人' },
          { label: '复购占比', value: '62.0%', compare: '8 月 63.6%', tone: 'down' },
        ],
      }],
      // 完整时间线 26 条、关联门店 38 家（超过一页，供抽屉分页演示；抽屉分页由 PDE 在公共组件中加）
      timeline: QA_REGION_TIMELINE.map((t) => ({
        time: t.time, text: `${t.title} · ${t.desc}`,
        status: t.level === 'warn' ? 'warn' : t.level === 'error' ? 'error' : t.level === 'unknown' ? 'unknown' : undefined,
      })),
      related: QA_REGION_RELATED,
    };
  },
  store(id) {
    const s = storeById(id); if (!s) return null;
    const orders = ORDERS.filter((o) => o.storeId === s.id);
    return {
      kind: 'store', kindLabel: '门店', id: s.id, title: s.name, code: s.code,
      status: { text: s.status, color: s.status === '营业中' ? 'success' : s.status === '装修中' ? 'warning' : 'default' },
      brief: [
        { label: '区域', value: s.region }, { label: '店长', value: s.manager },
        { label: '业态', value: s.format }, { label: '营业面积', value: s.area == null ? EMPTY : `${fmtMoney(s.area, 1)} ㎡` },
      ],
      fields: [
        { label: '门店编码', value: s.code }, { label: '城市', value: s.city }, { label: '区域', value: s.region },
        { label: '业态', value: s.format }, { label: '店长', value: s.manager },
        { label: '营业面积', value: s.area == null ? EMPTY : `${fmtMoney(s.area, 1)} ㎡` },
        { label: '开业日期', value: s.openedAt }, { label: '近 7 日订单', value: fmtInt(orders.length) },
      ],
      tabs: (() => {
        const paid = orders.filter((o) => o.status !== '已取消' && o.status !== '待支付');
        const sum = paid.reduce((a, o) => a + o.amount, 0);
        const shipped = orders.filter((o) => o.shippedAt).length;
        return [{
          key: 'kpi', title: '指标', pinToProps: true,
          kpis: [
            { label: '实付金额（含税）', value: fmtMoney(sum), unit: '元', compare: `${fmtInt(paid.length)} 单已支付` },
            { label: '订单数', value: fmtInt(orders.length), unit: '单' },
            { label: '客单价', value: paid.length ? fmtMoney(sum / paid.length) : EMPTY, unit: '元' },
            { label: '出库率', value: orders.length ? fmtPct(shipped / orders.length) : EMPTY, compare: `${fmtInt(shipped)} 单已出库或签收` },
          ],
        }];
      })(),
      timeline: [
        { time: '2026-10-25 06:00', text: '日结数据已同步' },
        { time: '2026-10-21 14:32', text: '店长由 张卫国 变更为 ' + s.manager },
        { time: s.openedAt + ' 09:00', text: '门店开业' },
      ],
      related: orders.map((o) => ({ kind: 'order', id: o.id, label: o.id, note: `${o.status} · ${fmtMoney(o.amount)}` })),
    };
  },
  product(id) {
    const p = productBySku(id); if (!p) return null;
    return {
      kind: 'product', kindLabel: '商品', id: p.sku, title: p.name, code: p.sku,
      brief: [
        { label: '规格', value: p.spec }, { label: '品类', value: p.category },
        { label: '零售价', value: `${fmtMoney(p.price)} 元/${p.unit}` }, { label: '条码', value: p.barcode },
      ],
      fields: [
        { label: 'SKU', value: p.sku }, { label: '条码', value: p.barcode }, { label: '品牌', value: p.brand },
        { label: '规格', value: p.spec }, { label: '品类', value: p.category }, { label: '销售单位', value: p.unit },
        { label: '零售价', value: `${fmtMoney(p.price)} 元` },
      ],
    };
  },
  order(id) {
    const o = orderById(id); if (!o) return null;
    const s = storeById(o.storeId);
    const timeline: ObjTimeline[] = [{ time: o.createdAt, text: `下单（${o.channel}）` }];
    if (o.status !== '待支付' && o.status !== '已取消') timeline.push({ time: o.createdAt, text: '支付成功' });
    if (o.shippedAt) timeline.push({ time: o.shippedAt, text: `已出库（${o.warehouse}）`, status: 'done' });
    if (o.status === '已取消') timeline.push({ time: o.createdAt, text: '用户取消', status: 'error' });
    return {
      kind: 'order', kindLabel: '订单', id: o.id, title: o.id, code: o.channel,
      status: { text: o.status, color: orderStatusColor(o.status) },
      brief: [
        { label: '门店', value: s?.name ?? EMPTY }, { label: '实付金额', value: `${fmtMoney(o.amount)} 元` },
        { label: '下单时间', value: o.createdAt }, { label: '运单号', value: o.waybill ?? EMPTY },
      ],
      fields: [
        { label: '订单号', value: o.id }, { label: '渠道', value: o.channel }, { label: '门店', value: s?.name ?? EMPTY },
        { label: '发货仓', value: o.warehouse }, { label: '会员手机', value: o.member ?? EMPTY },
        { label: '商品件数', value: fmtInt(o.lines.length) }, { label: '优惠金额', value: o.discount ? fmtMoney(o.discount) : EMPTY },
        { label: '实付金额', value: fmtMoney(o.amount) }, { label: '下单时间', value: o.createdAt },
        { label: '出库时间', value: o.shippedAt ?? EMPTY }, { label: '运单号', value: o.waybill ?? EMPTY },
      ],
      timeline,
      related: [
        ...(s ? [{ kind: 'store', id: s.id, label: s.name, note: s.code }] : []),
        ...o.lines.map((l) => {
          const p = productBySku(l.sku);
          return { kind: 'product', id: l.sku, label: p?.name ?? l.sku, note: `× ${l.qty} · ${fmtMoney(l.price)}` };
        }),
      ],
    };
  },
  metric(id) {
    const m = metricById(id); if (!m) return null;
    const v = m.unit === '%' ? fmtPct(m.value) : m.unit === '元' ? fmtMoney(m.value) : fmtInt(m.value);
    return {
      kind: 'metric', kindLabel: '指标口径', id: m.id, title: m.name, code: m.id,
      status: { text: m.status, color: m.status === '已发布' ? 'success' : 'default' },
      brief: [
        { label: '口径', value: m.formula }, { label: '负责人', value: m.owner },
        { label: '版本', value: m.version }, { label: '当前值', value: v },
      ],
      fields: [
        { label: '编码', value: m.id }, { label: '口径', value: m.formula }, { label: '单位', value: m.unit },
        { label: '允许的维度', value: m.dims.join('、') }, { label: '负责人', value: m.owner },
        { label: '版本', value: m.version }, { label: '数据更新', value: m.updatedAt },
      ],
      timeline: [
        { time: '2026-10-18 10:12', text: `${m.version} 发布（${m.owner}）`, status: 'done' },
        { time: '2026-09-02 16:40', text: '新增维度：渠道' },
      ],
    };
  },
  // 规则、动作执行记录、事件（动作类型 / 事件类型页面新增）
  ...ACTION_EVENT_RESOLVERS,
};

const warned = new Set<string>();
/** 开发环境：引用了解析不到的对象时警告一次（scripts/shot.py 把这条警告当作 FAIL） */
export function warnUnresolved(kind: string, id: string, where = '') {
  if (!import.meta.env.DEV) return;
  const k = `${kind}:${id}`;
  if (warned.has(k)) return;
  warned.add(k);
  console.warn(`[objectRegistry] 无法解析对象 ${k}${where ? `（${where}）` : ''}：请在 src/mock 里补数据或在 RESOLVERS 里加 kind`);
}
export const resolveObject = (kind: string, id: string): ObjView | null => {
  const v = RESOLVERS[kind]?.(id) ?? null;
  if (!v) warnUnresolved(kind, id);
  return v;
};
/** 只检查能否解析（开发环境下解析不到会警告） */
export const checkObject = (kind: string, id: string, where?: string) => {
  const ok = !!RESOLVERS[kind]?.(id);
  if (!ok) warnUnresolved(kind, id, where);
  return ok;
};
