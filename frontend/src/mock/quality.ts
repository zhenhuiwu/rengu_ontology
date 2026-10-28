/** 数据质量示例数据：按对象类型的检查项（新鲜度 / 空值率 / 唯一性 / 引用完整性）+ 每项的运行历史。固定种子。 */
import { seeded } from './format';

/** 对象类型清单（名称、API 名、分组、数量与 pages/ontology/ObjectTypes.vue 的 TYPES 一致；那边是页面内常量，这里单独一份给数据质量用） */
export interface QObjType { key: string; name: string; api: string; group: string; source: string; count: number; owner: string; pk: string; cutoff: string }
export const Q_OBJECT_TYPES: QObjType[] = [
  { key: 'order', name: '订单', api: 'SalesOrder', group: '交易', source: 'POS 交易库（华东）', count: 1286402, owner: '陈立军', pk: 'order_id', cutoff: '2026-10-24 23:00' },
  { key: 'order_line', name: '订单行', api: 'SalesOrderLine', group: '交易', source: 'POS 交易库（华东）', count: 4917355, owner: '陈立军', pk: 'line_id', cutoff: '2026-10-24 23:00' },
  { key: 'refund', name: '退款单', api: 'Refund', group: '交易', source: 'POS 交易库（华东）', count: 38914, owner: '陈立军', pk: 'refund_id', cutoff: '2026-10-24 23:00' },
  { key: 'refund_line', name: '退款行', api: 'RefundLine', group: '交易', source: 'POS 交易库（华东）', count: 52307, owner: '陈立军', pk: 'line_id', cutoff: '2026-10-24 23:00' },
  { key: 'member', name: '会员', api: 'Member', group: '客户', source: '会员中心', count: 863027, owner: '刘婷', pk: 'member_id', cutoff: '2026-10-24 23:00' },
  { key: 'member_level', name: '会员等级', api: 'MemberLevel', group: '客户', source: '会员中心', count: 5, owner: '刘婷', pk: 'level_code', cutoff: '2026-10-24 23:00' },
  { key: 'store', name: '门店', api: 'Store', group: '组织', source: '门店主数据', count: 486, owner: '陈立军', pk: 'store_id', cutoff: '2026-10-24 23:00' },
  { key: 'region', name: '大区', api: 'Region', group: '组织', source: '门店主数据', count: 6, owner: '陈立军', pk: 'region_code', cutoff: '2026-10-24 23:00' },
  { key: 'province', name: '省份', api: 'Province', group: '组织', source: '门店主数据', count: 31, owner: '陈立军', pk: 'province_code', cutoff: '2026-10-24 23:00' },
  { key: 'product', name: '商品', api: 'Product', group: '商品', source: '商品主数据', count: 48213, owner: '王敏', pk: 'sku_code', cutoff: '2026-10-24 23:00' },
  { key: 'category', name: '品类', api: 'Category', group: '商品', source: '商品主数据', count: 1204, owner: '王敏', pk: 'category_code', cutoff: '2026-10-24 23:00' },
  { key: 'promotion', name: '促销活动', api: 'Promotion', group: '商品', source: '促销引擎', count: 2671, owner: '刘婷', pk: 'promo_id', cutoff: '2026-10-24 20:00' },
  { key: 'warehouse', name: '仓库', api: 'Warehouse', group: '供应链', source: '供应链 WMS 库', count: 4, owner: '孙浩然', pk: 'wh_code', cutoff: '2026-10-24 23:00' },
  { key: 'inventory', name: '库存快照', api: 'InventorySnapshot', group: '供应链', source: '供应链 WMS 库', count: 9412880, owner: '孙浩然', pk: 'wh_code + sku_code', cutoff: '2026-10-24 17:30' },
  { key: 'supplier', name: '供应商', api: 'Supplier', group: '供应链', source: '用友 NC 采购', count: 1148, owner: '孙浩然', pk: 'supplier_code', cutoff: '2026-10-24 22:40' },
  { key: 'purchase', name: '采购单', api: 'PurchaseOrder', group: '供应链', source: '用友 NC 采购', count: 76530, owner: '孙浩然', pk: 'po_no', cutoff: '2026-10-24 14:28' },
  { key: 'shipment', name: '出库单', api: 'Shipment', group: '供应链', source: '物流运单', count: 1197226, owner: '孙浩然', pk: 'shipment_no', cutoff: '2026-10-24 23:00' },
  { key: 'employee', name: '员工', api: 'Employee', group: '组织', source: 'HR 人事库', count: 7382, owner: '王敏', pk: 'emp_no', cutoff: '2026-10-24 03:30' },
];
export const qObjType = (key?: string | null) => Q_OBJECT_TYPES.find((t) => t.key === key);

export type CheckKind = '新鲜度' | '记录时效' | '空值率' | '唯一性' | '引用完整性' | '格式';
export type CheckResult = '通过' | '失败';
export interface QRun { time: string; result: CheckResult; actual: string; ms: number }
export interface QCheck { id: string; typeKey: string; typeName: string; kind: CheckKind; rule: string; threshold: string; actual: string; result: CheckResult; lastRun: string; failSince: string | null; history: QRun[]; /** 补充说明（容易和别的问题混淆时） */ note?: string }

/** 全局数据截至时间，和指标、问数页面一致 */
export const DATA_CUTOFF = '2026-10-24 23:00';

// 新鲜度阈值（小时）：数据截至时间不能早于 DATA_CUTOFF 减去阈值
const FRESH_H: Record<string, number> = { order: 1, order_line: 1, refund: 1, refund_line: 1, member: 2, inventory: 2, promotion: 6, purchase: 4, shipment: 1, employee: 24 };
const NULL_PROP: Record<string, [string, number]> = {
  order: ['门店', 0], order_line: ['商品', 0], refund: ['原订单', 0], member: ['手机号', 0.02], store: ['大区', 0], product: ['品类', 0],
  inventory: ['可用量', 0], shipment: ['运单号', 0.01], employee: ['所属门店', 0.05], promotion: ['结束时间', 0], supplier: ['统一社会信用代码', 0],
};
const REFS: Record<string, string> = {
  order: '订单.门店 → 门店', order_line: '订单行.商品 → 商品', refund: '退款单.原订单 → 订单', refund_line: '退款行.原订单行 → 订单行',
  member: '会员.等级 → 会员等级', store: '门店.大区 → 大区', province: '省份.大区 → 大区', product: '商品.品类 → 品类',
  inventory: '库存快照.仓库 → 仓库', purchase: '采购单.供应商 → 供应商', shipment: '出库单.订单 → 订单', employee: '员工.所属门店 → 门店',
};
// 当前失败的检查（其余通过）
const FAILING: Record<string, string> = {
  'inventory:新鲜度': '数据截至 2026-10-24 17:30，晚 5.5 小时',
  'purchase:新鲜度': '数据截至 2026-10-24 14:28，晚 8.5 小时',
  'member:空值率': '2.61%（22,580 条）',
  'shipment:空值率': '1.37%（16,307 条）',
  'order_line:唯一性': '重复 14 条',
  'employee:引用完整性': '83 条找不到门店',
};
const FAIL_SINCE: Record<string, string> = {
  'inventory:新鲜度': '2026-10-24 19:30', 'purchase:新鲜度': '2026-10-24 18:30', 'member:空值率': '2026-10-21 23:30',
  'shipment:空值率': '2026-10-24 23:30', 'order_line:唯一性': '2026-10-23 23:30', 'employee:引用完整性': '2026-10-15 23:30',
};
// 补充说明：出库单运单号为空 16,307 条，数量接近“订单 → 履约门店”的孤儿记录 16,284 条（前置仓未建档），两者无关
const NOTES: Record<string, string> = {
  'shipment:空值率': '与“订单 → 履约门店”的 16,284 条孤儿记录（前置仓未在门店主数据建档）无关：这里是出库单已出库但承运商未回传运单号',
};
const pad = (n: number) => String(n).padStart(2, '0');
const dayAt = (daysAgo: number, hhmm: string) => {
  const t = new Date(Date.UTC(2026, 9, 24 - daysAgo));
  return `${t.getUTCFullYear()}-${pad(t.getUTCMonth() + 1)}-${pad(t.getUTCDate())} ${hhmm}`;
};

export const QUALITY_CHECKS: QCheck[] = (() => {
  const out: QCheck[] = [];
  let n = 0;
  Q_OBJECT_TYPES.forEach((t) => {
    const kinds: [CheckKind, string, string][] = [];
    if (FRESH_H[t.key]) kinds.push(['新鲜度', `数据截至 ≥ ${DATA_CUTOFF} − ${FRESH_H[t.key]} 小时`, `≤ ${FRESH_H[t.key]} 小时`]);
    if (NULL_PROP[t.key]) kinds.push(['空值率', `${t.name}.${NULL_PROP[t.key][0]} 为空的比例`, `≤ ${(NULL_PROP[t.key][1] * 100).toFixed(0)}%`]);
    kinds.push(['唯一性', `${t.name}.${t.pk} 不重复`, '重复 0 条']);
    if (REFS[t.key]) kinds.push(['引用完整性', REFS[t.key], '找不到 0 条']);
    kinds.forEach(([kind, rule, threshold]) => {
      n += 1;
      const r = seeded(4400 + n);
      const fk = `${t.key}:${kind}`;
      const failMsg = FAILING[fk];
      const since = FAIL_SINCE[fk] ?? null;
      const okActual = kind === '新鲜度' ? `数据截至 ${t.cutoff}` : kind === '空值率' ? `${(NULL_PROP[t.key][1] * r() * 50).toFixed(2)}%` : kind === '唯一性' ? '重复 0 条' : '找不到 0 条';
      // 历史：近 45 天每天 23:30 一次；新鲜度每 2 小时一次（只保留近 4 天）
      const runs: QRun[] = [];
      const total = kind === '新鲜度' ? 48 : 45;
      for (let k = 0; k < total; k += 1) {
        const time = kind === '新鲜度'
          ? (() => { const h = 22 - (k % 12) * 2; return dayAt(Math.floor(k / 12) - (h >= 0 ? 0 : 1), `${pad((h + 24) % 24)}:30`); })()
          : dayAt(k, '23:30');
        const failing = !!since && time >= since;
        const blip = !failing && r() < 0.05;
        runs.push({
          time, result: failing || blip ? '失败' : '通过',
          actual: failing ? failMsg : blip ? (kind === '新鲜度' ? '晚 1.5 小时' : kind === '唯一性' ? '重复 3 条' : kind === '空值率' ? '0.4%' : '找不到 2 条') : okActual,
          ms: Math.round(300 + r() * 9000),
        });
      }
      runs.sort((a, b) => (a.time < b.time ? 1 : -1));
      out.push({
        id: `QC-${String(n).padStart(3, '0')}`, typeKey: t.key, typeName: t.name, kind, rule, threshold,
        actual: failMsg ?? okActual, result: failMsg ? '失败' : '通过', lastRun: runs[0].time, failSince: since, history: runs, note: NOTES[fk],
      });
    });
  });
  return out;
})();
