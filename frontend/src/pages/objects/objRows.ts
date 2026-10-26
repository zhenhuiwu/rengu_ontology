/**
 * 对象行的取数层：对象搜索 / 详情只调这里，不关心是真接口还是演示数据。
 * - 真接口（qaLive）：qa 通用搜索 X1 / 详情 X2（后台按 seeds/extra/qa/objects/bindings.json 把属性映射到列）；
 *   分面优先静态 /facets，没有就用 X1 返回的过渡分面。
 * - 演示（mock）：下面 MOCK_BINDINGS 把属性 api 名映射到 mock 行字段（与后台 bindings.json 同一份对照，只是换成 mock 字段名），
 *   搜索 / 筛选 / 分面 / 排序 / 分页在本地算；没有绑定的类型 = 未接入数据。预览按 usePreview().scopeOf 过滤行。
 * 绑定是数据，不是页面逻辑：加一个类型只加一项映射，页面不用改。
 */
import { ALL_STORES } from '@/mock/stores';
import { PRODUCTS } from '@/mock/products';
import { ORDERS } from '@/mock/orders';
import { inScope } from '@/mock/objects-qa';
import { exploreGet, exploreSearch, type ExploreRow } from '@/api/qaObjects';
import { isRange, loadStaticFacets, type FacetMeta, type Filters, type TypeMeta } from './objMeta';

interface MockBinding { rows: () => Record<string, any>[]; id: string; cols: Record<string, string> }
const MOCK_BINDINGS: Record<string, MockBinding> = {
  store: {
    rows: () => ALL_STORES, id: 'id',
    cols: { store_id: 'id', store_name: 'name', format: 'format', city: 'city', area_sqm: 'area', opened_at: 'openedAt', status: 'status' },
  },
  product: {
    rows: () => PRODUCTS, id: 'sku',
    cols: { sku: 'sku', sku_name: 'name', barcode: 'barcode', category_id: 'category', brand: 'brand', spec: 'spec', unit: 'unit', retail_price: 'price' },
  },
  order: {
    rows: () => ORDERS, id: 'id',
    cols: {
      order_id: 'id', order_no_display: 'id', channel: 'channel', status: 'status', store_id: 'storeId', member_id: 'member',
      warehouse_id: 'warehouse', created_at: 'createdAt', shipped_at: 'shippedAt', discount_amount: 'discount', paid_amount: 'amount',
    },
  },
};

export interface RowsQuery { kw: string; filters: Filters; sort: string; page: number; size: number }
export interface RowsResult { items: ExploreRow[]; total: number; bound: boolean; boundProps: string[]; facets: FacetMeta[] }
/** 预览的行级范围（mock 用）：null = 不限 */
export type ScopeFn = (typeName: string) => string | null;

function mockRow(m: TypeMeta, b: MockBinding, r: Record<string, any>): ExploreRow {
  const values = Object.fromEntries(m.properties.map((p) => [p.key, p.key in b.cols ? r[b.cols[p.key]] ?? null : null]));
  return { id: String(r[b.id]), title: String(values[m.titleProp] ?? r[b.id]), values };
}
function mockAll(m: TypeMeta, scope: ScopeFn): ExploreRow[] | null {
  const b = MOCK_BINDINGS[m.id];
  if (!b) return null;
  const s = scope(m.name);
  return b.rows().filter((r) => inScope(m.id, r, s)).map((r) => mockRow(m, b, r));
}
const cmp = (a: unknown, b: unknown) => (typeof a === 'number' && typeof b === 'number' ? a - b : String(a ?? '').localeCompare(String(b ?? ''), 'zh'));
/** 一行是否满足筛选（skip = 算某个分面时不计它自己） */
function hit(m: TypeMeta, r: ExploreRow, q: Pick<RowsQuery, 'kw' | 'filters'>, skip?: string): boolean {
  const kw = q.kw.trim().toLowerCase();
  if (kw && !m.properties.some((p) => p.searchable && String(r.values[p.key] ?? '').toLowerCase().includes(kw))) return false;
  return Object.entries(q.filters).every(([k, f]) => {
    if (k === skip) return true;
    const v = r.values[k];
    if (Array.isArray(f)) return !f.length || f.includes(String(v));
    if (v == null) return f.min == null && f.max == null;
    const date = typeof v === 'string';
    const max = date && f.max != null && String(f.max).length === 10 ? `${f.max}\uffff` : f.max;
    return (f.min == null || cmp(v, f.min) >= 0) && (max == null || cmp(v, max) <= 0);
  });
}
function mockFacets(m: TypeMeta, all: ExploreRow[], q: RowsQuery): FacetMeta[] {
  return m.properties.filter((p) => p.filterable && !p.sensitive).map((p): FacetMeta => {
    const base = all.filter((r) => hit(m, r, q, p.key));
    if (isRange(p)) {
      const vs = base.map((r) => r.values[p.key]).filter((v) => v != null).sort(cmp) as (string | number)[];
      return { key: p.key, kind: 'range', min: vs[0] ?? null, max: vs[vs.length - 1] ?? null };
    }
    const cnt = new Map<string, number>();
    all.forEach((r) => { const v = r.values[p.key]; if (v != null) cnt.set(String(v), 0); });
    base.forEach((r) => { const v = r.values[p.key]; if (v != null) cnt.set(String(v), (cnt.get(String(v)) ?? 0) + 1); });
    return { key: p.key, kind: 'enum', values: [...cnt].map(([value, count]) => ({ value, count })).sort((a, b) => b.count - a.count || cmp(a.value, b.value)).slice(0, 50) };
  });
}
function sortRows(rows: ExploreRow[], sort: string) {
  if (!sort) return rows;
  const desc = sort.startsWith('-'); const k = desc ? sort.slice(1) : sort;
  return [...rows].sort((a, b) => (desc ? -1 : 1) * cmp(a.values[k], b.values[k]));
}

/** 搜索一页；live=true 走 X1，出错由调用方处理（qaFallBackIfDown 后可再以 live=false 调） */
export async function searchRows(m: TypeMeta, q: RowsQuery, live: boolean, scope: ScopeFn): Promise<RowsResult> {
  if (live) {
    // 分面：静态 /facets（真数据，随其他筛选联动，不带关键词）；有关键词或静态取不到时用 E1 顺带的过渡分面（计数含关键词）[推断]
    const sf = q.kw.trim() ? Promise.resolve(null) : loadStaticFacets(m.id, q.filters);
    const r = await exploreSearch(m.id, { kw: q.kw, filters: q.filters, sort: q.sort || undefined, page: q.page, size: q.size });
    const f = await sf;
    return { items: r.items, total: r.total, bound: r.bound, boundProps: r.boundProps, facets: f ?? r.facets };
  }
  const all = mockAll(m, scope);
  if (!all) return { items: [], total: 0, bound: false, boundProps: [], facets: [] };
  const rows = sortRows(all.filter((r) => hit(m, r, q)), q.sort);
  return {
    items: rows.slice((q.page - 1) * q.size, q.page * q.size), total: rows.length, bound: true,
    boundProps: Object.keys(MOCK_BINDINGS[m.id].cols), facets: mockFacets(m, all, q),
  };
}
/** 演示模式下当前筛选的全部行（导出 / 提问用） */
export function mockMatched(m: TypeMeta, q: Pick<RowsQuery, 'kw' | 'filters'>, scope: ScopeFn): ExploreRow[] {
  return (mockAll(m, scope) ?? []).filter((r) => hit(m, r, q));
}
export type GetResult = { row: ExploreRow | null; bound: boolean; denied: boolean };
/** 单个对象；不存在 row=null，范围外 denied=true，未接入 bound=false */
export async function getRow(m: TypeMeta, id: string, live: boolean, scope: ScopeFn): Promise<GetResult> {
  if (live) {
    try { return { row: await exploreGet(m.id, id), bound: true, denied: false }; } catch (e: any) {
      if (e?.status === 403) return { row: null, bound: true, denied: true };
      if (e?.status === 404) {
        const probe = await exploreSearch(m.id, { page: 1, size: 1, withFacets: false }).catch(() => null);
        return { row: null, bound: probe ? probe.bound : true, denied: false };
      }
      throw e;
    }
  }
  const b = MOCK_BINDINGS[m.id];
  if (!b) return { row: null, bound: false, denied: false };
  const r = b.rows().find((x) => String(x[b.id]) === id);
  if (!r) return { row: null, bound: true, denied: false };
  if (!inScope(m.id, r, scope(m.name))) return { row: null, bound: true, denied: true };
  return { row: mockRow(m, b, r), bound: true, denied: false };
}
export const isMockBound = (type: string) => type in MOCK_BINDINGS;
