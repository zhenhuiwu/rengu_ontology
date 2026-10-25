/** 数据连接（数据接入）示例数据：16 个数据源，每个带库表清单和同步记录。固定种子，每次一样。 */
import { seeded } from './format';

export type DsType = 'PostgreSQL' | '达梦 DM8' | '人大金仓 KingbaseES' | 'MySQL' | 'ERP';
export type DsStatus = '正常' | '异常' | '未测试';
export interface DsTest { time: string; ok: boolean; ms: number; msg: string }
export interface DsSync { mode: '定时增量' | '定时全量' | '实时（CDC）' | '手动'; cron: string; cronText: string; window: string; cursorField: string; nextRun: string | null }
export interface DsRun { time: string; result: '成功' | '延迟' | '失败'; rows: number; secs: number; msg: string }
export interface Datasource {
  id: string; name: string; type: DsType; host: string; port: number; database: string; user: string;
  /** 达梦：兼容模式；金仓：database_mode；ERP：系统 */
  compatMode?: string; databaseMode?: string; erpSystem?: string; appId?: string;
  ssl: boolean; status: DsStatus; lastTest: DsTest | null; owner: string; objectTypes: string[];
  sync: DsSync; runs: DsRun[]; tableCount: number; createdAt: string;
  /** 详情顶部的说明（如：只是数据源、不是外部系统） */
  note?: string;
  /** 这些对象类型的同名表只作参考（对账用），不映射；对象以 refSource 为准 */
  refOnly?: { objects: string[]; source: string };
}
export interface DsTable { schema: string; name: string; comment: string; rows: number; cols: number; objectType: string | null; updatedAt: string; /** 参考表：说明以哪张表为准 */ ref?: string }

export const DS_TYPES: DsType[] = ['PostgreSQL', '达梦 DM8', '人大金仓 KingbaseES', 'MySQL', 'ERP'];
export const DS_DEFAULT_PORT: Record<DsType, number> = { PostgreSQL: 5432, '达梦 DM8': 5236, '人大金仓 KingbaseES': 54321, MySQL: 3306, ERP: 443 };
export const DM_COMPAT = ['Oracle', 'MySQL', 'PostgreSQL', 'SQL Server', '不兼容'];
export const KB_MODES = ['oracle', 'pg', 'mysql'];
export const ERP_SYSTEMS = ['用友 NC Cloud', '金蝶云·星空', 'SAP S/4HANA'];

type Seed = [string, DsType, string, number, string, string, string, string[], DsSync['mode'], string, string, Partial<Datasource>?];
const SEEDS: Seed[] = [
  ['POS 交易库（华东）', 'PostgreSQL', '10.12.8.21', 5432, 'pos_prod', 'sync_ro', '陈立军', ['订单', '订单行', '退款', '退款行'], '定时增量', '*/15 * * * *', '每 15 分钟', { note: '只读数据源，不是外部系统：订单数据从这里同步；写回订单走 外部系统 → 订单 OMS' }],
  ['财务总账', '达梦 DM8', '10.12.20.5', 5236, 'FIN_GL', 'BI_READER', '何静', [], '定时增量', '0 2 * * *', '每天 02:00', { compatMode: 'Oracle', refOnly: { objects: ['门店', '大区'], source: '门店主数据（ORG.T_STORE / ORG.T_REGION）' }, note: '门店、大区表只作财务对账参考，未映射；门店、大区以 门店主数据（ORG.T_STORE / ORG.T_REGION）为准' }],
  ['供应链 WMS 库', '人大金仓 KingbaseES', '10.12.31.40', 54321, 'wms', 'wms_ro', '孙浩然', ['仓库', '库存', '出库单'], '定时增量', '*/30 * * * *', '每 30 分钟', { databaseMode: 'oracle' }],
  ['会员中心', 'MySQL', '10.12.9.77', 3306, 'member_center', 'bi_sync', '刘婷', ['会员', '会员等级'], '实时（CDC）', '-', '持续同步'],
  ['用友 NC 采购', 'ERP', 'nc.xianlin.local', 443, 'NCC-PUR', 'api_bi', '孙浩然', ['采购单', '供应商'], '定时增量', '0 */2 * * *', '每 2 小时', { erpSystem: '用友 NC Cloud', appId: 'ncc-bi-0192' }],
  ['商品主数据', 'PostgreSQL', '10.12.8.35', 5432, 'mdm_product', 'sync_ro', '王敏', ['商品', '品类'], '定时增量', '0 * * * *', '每小时整点'],
  ['促销引擎', 'MySQL', '10.12.9.102', 3306, 'promo', 'bi_sync', '刘婷', ['促销活动'], '定时增量', '0 */4 * * *', '每 4 小时'],
  ['HR 人事库', '达梦 DM8', '10.12.20.18', 5236, 'HR_CORE', 'BI_READER', '王敏', ['员工'], '定时全量', '30 3 * * *', '每天 03:30', { compatMode: 'MySQL' }],
  ['门店主数据', 'PostgreSQL', '10.12.8.36', 5432, 'mdm_store', 'sync_ro', '陈立军', ['门店', '大区', '省份'], '定时增量', '0 * * * *', '每小时整点'],
  ['冷链温控', '人大金仓 KingbaseES', '10.12.31.52', 54321, 'coldchain', 'cc_ro', '孙浩然', ['仓库'], '定时增量', '*/10 * * * *', '每 10 分钟', { databaseMode: 'pg' }],
  ['到家订单（美团 / 饿了么）', 'MySQL', '10.12.9.140', 3306, 'o2o_order', 'bi_sync', '陈立军', ['订单', '订单行'], '定时增量', '*/5 * * * *', '每 5 分钟'],
  ['金蝶费用报销', 'ERP', 'k3cloud.xianlin.local', 443, 'K3-EXP', 'api_bi', '何静', ['门店'], '定时增量', '0 1 * * *', '每天 01:00', { erpSystem: '金蝶云·星空', appId: 'k3c-298113' }],
  ['物流运单', 'PostgreSQL', '10.12.8.60', 5432, 'tms', 'sync_ro', '孙浩然', ['出库单'], '定时增量', '*/15 * * * *', '每 15 分钟'],
  ['社群团购', 'MySQL', '10.12.9.150', 3306, 'groupbuy', 'bi_sync', '刘婷', ['订单'], '手动', '-', '手动触发'],
  ['财务报表（测试）', '达梦 DM8', '10.12.70.3', 5236, 'FIN_RPT_UAT', 'BI_READER', '何静', [], '手动', '-', '手动触发', { compatMode: 'Oracle' }],
  ['SAP 集团合并', 'ERP', 's4.group.local', 443, 'S4-FI', 'RFC_BI', '何静', ['大区'], '定时全量', '0 4 * * 1', '每周一 04:00', { erpSystem: 'SAP S/4HANA', appId: 'S4P-800' }],
];
const pad = (n: number) => String(n).padStart(2, '0');
const at = (minsAgo: number) => {
  // 以 2026-10-25 16:30 为“现在”
  const t = new Date(Date.UTC(2026, 9, 25, 16, 30) - minsAgo * 60000);
  return `${t.getUTCFullYear()}-${pad(t.getUTCMonth() + 1)}-${pad(t.getUTCDate())} ${pad(t.getUTCHours())}:${pad(t.getUTCMinutes())}`;
};

export const DATASOURCES: Datasource[] = SEEDS.map(([name, type, host, port, database, user, owner, objectTypes, mode, cron, cronText, extra], i) => {
  const r = seeded(9100 + i);
  const status: DsStatus = i === 4 ? '异常' : i === 14 ? '未测试' : '正常';
  const lastTest: DsTest | null = status === '未测试' ? null : status === '异常'
    ? { time: at(38), ok: false, ms: 10000, msg: '连接超时（10 秒）：nc.xianlin.local:443 无响应' }
    : { time: at(Math.round(r() * 2000) + 20), ok: true, ms: Math.round(18 + r() * 160), msg: '连接成功' };
  const runs: DsRun[] = mode === '手动' && i === 14 ? [] : Array.from({ length: 8 }, (_, k) => {
    const x = r();
    const result: DsRun['result'] = i === 4 && k < 2 ? '失败' : x < 0.1 ? '延迟' : '成功';
    return {
      time: at(k * (mode === '实时（CDC）' ? 60 : 90) + Math.round(r() * 30) + 5), result,
      rows: result === '失败' ? 0 : Math.round(200 + r() * 48000), secs: Math.round(4 + r() * 140),
      msg: result === '失败' ? '连接超时，已重试 3 次' : result === '延迟' ? '源库负载高，比计划晚 12 分钟' : '完成',
    };
  });
  return {
    id: `DS-${String(i + 1).padStart(3, '0')}`, name, type, host, port, database, user, owner, objectTypes, status, lastTest,
    ssl: type !== 'MySQL' || i % 2 === 0,
    sync: { mode, cron, cronText, window: mode === '定时全量' ? '00:00—06:00' : '全天', cursorField: mode === '定时全量' || mode === '手动' ? '-' : type === 'ERP' ? 'ts' : 'updated_at', nextRun: mode === '手动' || mode === '实时（CDC）' ? null : at(-Math.round(5 + r() * 50)) },
    runs, tableCount: 18 + Math.round(r() * 50), createdAt: `2026-0${1 + (i % 9)}-${pad(3 + i)}`, ...extra,
  };
});
export const datasourceById = (id?: string | null) => DATASOURCES.find((d) => d.id === id);

const TBL = ['order_header', 'order_line', 'refund', 'refund_line', 'member', 'member_level', 'store', 'region', 'sku', 'category', 'promo_rule', 'warehouse', 'stock_snapshot', 'supplier', 'purchase_order', 'shipment', 'employee', 'payment', 'coupon', 'price_change', 'pos_shift', 'cashier', 'device', 'daily_close'];
const TBL_CN: Record<string, [string, string | null]> = {
  order_header: ['订单头', '订单'], order_line: ['订单明细', '订单行'], refund: ['退款单', '退款'], refund_line: ['退款明细', '退款行'],
  member: ['会员', '会员'], member_level: ['会员等级', '会员等级'], store: ['门店', '门店'], region: ['大区', '大区'], sku: ['商品 SKU', '商品'],
  category: ['品类', '品类'], promo_rule: ['促销规则', '促销活动'], warehouse: ['仓库', '仓库'], stock_snapshot: ['库存快照', '库存'],
  supplier: ['供应商', '供应商'], purchase_order: ['采购单', '采购单'], shipment: ['出库单', '出库单'], employee: ['员工', '员工'],
  payment: ['支付流水', null], coupon: ['优惠券', null], price_change: ['调价记录', null], pos_shift: ['收银班次', null],
  cashier: ['收银员', null], device: ['收银设备', null], daily_close: ['门店日结', null],
};
const cache = new Map<string, DsTable[]>();
/** 某个数据源的库表清单（按 schema、表名排序） */
export function dsTables(ds: Datasource): DsTable[] {
  const hit = cache.get(ds.id);
  if (hit) return hit;
  const r = seeded(Number(ds.id.slice(3)) * 131);
  const schemas = ds.type === 'ERP' ? ['api'] : ds.type === '达梦 DM8' || (ds.type === '人大金仓 KingbaseES' && ds.databaseMode === 'oracle') ? [ds.user, 'ODS'] : ['public', 'ods', 'dim'];
  const out: DsTable[] = Array.from({ length: ds.tableCount }, (_, k) => {
    const base = TBL[k % TBL.length];
    const name = k < TBL.length ? base : `${base}_h${Math.floor(k / TBL.length)}`;
    const mapped = k < TBL.length && TBL_CN[base] && ds.objectTypes.includes(TBL_CN[base][1] ?? '') ? TBL_CN[base][1] : null;
    const ref = k < TBL.length && ds.refOnly?.objects.includes(TBL_CN[base]?.[1] ?? '') ? ds.refOnly.source : undefined;
    const upper = ds.type === '达梦 DM8' || (ds.type === '人大金仓 KingbaseES' && ds.databaseMode === 'oracle');
    // 门店主数据：门店 / 大区落在 ORG schema（对象类型 门店 → ORG.T_STORE，大区 → ORG.T_REGION）
    const org = ds.name === '门店主数据' && mapped && (base === 'store' || base === 'region');
    return {
      ref,
      schema: org ? 'ORG' : schemas[k % schemas.length], name: org ? `T_${base.toUpperCase()}` : upper ? name.toUpperCase() : name,
      comment: (TBL_CN[base]?.[0] ?? base) + (k < TBL.length ? '' : '（历史）'),
      rows: Math.round(r() * (base.includes('line') ? 9e6 : 4e5)), cols: 6 + Math.round(r() * 40),
      objectType: mapped, updatedAt: at(Math.round(r() * 600)),
    };
  });
  out.sort((a, b) => Number(!!b.objectType) - Number(!!a.objectType) || (a.schema + a.name).localeCompare(b.schema + b.name));
  cache.set(ds.id, out);
  return out;
}
