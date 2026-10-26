/**
 * 静态本体（开关键 static，前缀 ontology）：对象类型、关系类型、问数路径、指标、维度与字典、业务知识。
 * 接口见 docs/backend/static-api.md §3.2–§3.6。
 *
 * 接法（联调第一版）：
 * - mock 模式：什么都不做，页面照旧读 src/mock，界面不变，不发请求。
 * - real 模式：进入静态页前（StaticGate）按页面需要把整表从接口拉下来，原地替换 src/mock 里对应的数组 / 对象，
 *   再挂载页面本体。页面本来就是“整表进来、页内筛选分页”，替换后筛选、分页、深链 focus、抽屉都照旧工作；
 *   mock 里的 objName / linkById 等辅助函数读的也是同一个数组，跟着变。
 * - 历史版本（?ver=）由 http.ts 自动带上，按版本分别缓存；切换版本时 StaticGate 重新加载。
 * 接口的服务端筛选 / 分页 / focusId 已由后端测试覆盖（tests/test_static*.py），页面这一版不逐页请求。
 */
import { apiClient, currentApiContext, type PageResult } from './http';
import { LINKS, LINK_OBJECTS, PATH_GROUPS, type LinkType, type LinkObject, type PathGroup } from '@/mock/links';
import { OBJECT_PROPS, type ObjProp } from '@/mock/objectProps';
import { METRIC_LIST, METRIC_DEF, METRIC_DIMS, METRIC_VERSIONS, METRIC_REFS, type MetricListItem } from '@/mock/metricCatalog';
import { DIM_ITEMS, type DimItem } from '@/mock/dimensions';
import { KNOWLEDGE, type KEntry } from '@/mock/knowledge';
import type { ChangeRequest, CRDetail, ReviewEvent } from '@/mock/changes';

const api = apiClient('static');
export const isStaticReal = (): boolean => api.isReal();

/** 单页上限（后端 MAX_PAGE_SIZE=200）；超过时按页拉全 */
const MAX = 200;
async function all<T>(path: string, query: Record<string, string | number | boolean> = {}): Promise<T[]> {
  const out: T[] = [];
  for (let page = 1; ; page += 1) {
    const r = await api.get<PageResult<T>>(path, { query: { ...query, page, size: MAX } });
    out.push(...r.items);
    if (out.length >= r.total || !r.items.length || r.page < page) return out;
  }
}
/** 并发数有限的 map（每个对象 / 维度一个请求） */
async function pmap<T, R>(xs: readonly T[], fn: (x: T) => Promise<R>, n = 6): Promise<R[]> {
  const out: R[] = new Array(xs.length);
  let i = 0;
  await Promise.all(Array.from({ length: Math.min(n, xs.length) }, async () => {
    while (i < xs.length) { const k = i++; out[k] = await fn(xs[k]); }
  }));
  return out;
}
const replace = <T>(arr: T[], items: readonly T[]) => { arr.splice(0, arr.length, ...items); };
const replaceRecord = <T>(rec: Record<string, T>, next: Record<string, T>) => {
  Object.keys(rec).forEach((k) => delete rec[k]);
  Object.assign(rec, next);
};

const MOCK_DRAFT_OBJECTS = LINK_OBJECTS.filter((o) => o.draft);

/** 对象类型列表项（ObjectTypes.vue TYPES + LINK_OBJECTS.table） */
export interface ObjectTypeItem { id: string; name: string; api: string; group: string; table?: string; props: number; count: string; status: string }
/** real 模式下对象类型页的 TYPES（mock 模式为空，页面用页内常量） */
export const OBJECT_TYPE_LIST: ObjectTypeItem[] = [];

interface ObjectDetailDto { id: string; props: ObjProp[] }
interface MetricDetailDto extends MetricListItem { def: typeof METRIC_DEF; dims: typeof METRIC_DIMS }

async function loadObjects() {
  const list = await all<ObjectTypeItem>('/object-types');
  replace(OBJECT_TYPE_LIST, list);
  // 草稿对象（如 CR-0012 的储值卡）只在变更单里、不是对象类型，静态接口不返回；变更单仍是 mock（versioning），先保留 mock 的草稿项 [推断]
  const ids = new Set(list.map((o) => o.id));
  replace(LINK_OBJECTS, [
    ...list.map((o): LinkObject => ({ id: o.id, name: o.name, api: o.api, group: o.group, table: o.table ?? '--' })),
    ...MOCK_DRAFT_OBJECTS.filter((o) => !ids.has(o.id)),
  ]);
}
async function loadObjectProps() {
  const details = await pmap(OBJECT_TYPE_LIST, (o) => api.get<ObjectDetailDto>(`/object-types/${encodeURIComponent(o.id)}`));
  replaceRecord(OBJECT_PROPS, Object.fromEntries(details.map((d) => [d.id, d.props])));
}
async function loadLinks() {
  replace(LINKS, await all<LinkType>('/link-types'));
  replace(PATH_GROUPS, await api.get<PathGroup[]>('/link-types/paths'));
}
async function loadMetrics() {
  replace(METRIC_LIST, await all<MetricListItem>('/metrics'));
}
/** 指标详情（定义 / 允许的维度 / 口径版本 / 引用），mock 里只有一份，按选中指标原地替换 */
export async function loadMetricDetail(id: string) {
  const enc = encodeURIComponent(id);
  const [d, versions, refs] = await Promise.all([
    api.get<MetricDetailDto>(`/metrics/${enc}`),
    all<(typeof METRIC_VERSIONS)[number]>(`/metrics/${enc}/versions`),
    all<(typeof METRIC_REFS)[number]>(`/metrics/${enc}/refs`),
  ]);
  replace(METRIC_DEF, d.def ?? []);
  replace(METRIC_DIMS, d.dims ?? []);
  replace(METRIC_VERSIONS, versions);
  replace(METRIC_REFS, refs);
}
async function loadDimensions() {
  const heads = await all<{ id: string }>('/dimensions');
  // full=1：完整 DimItem（含 values / mappings / usage / history，草稿行原样），页面自己算视图
  replace(DIM_ITEMS, await pmap(heads, (h) => api.get<DimItem>(`/dimensions/${encodeURIComponent(h.id)}`, { query: { full: true } })));
}
async function loadKnowledge() {
  const list = await all<KEntry & { shownStatus?: string }>('/knowledge');
  const refs = await pmap(list, (e) => all<KEntry['refs'][number]>(`/knowledge/${encodeURIComponent(e.id)}/refs`));
  replace(KNOWLEDGE, list.map(({ shownStatus: _s, ...e }, i) => ({ ...e, refs: refs[i] }) as KEntry));
}

export type StaticDataset = 'objects' | 'objectProps' | 'links' | 'metrics' | 'dimensions' | 'knowledge';
const LOADERS: Record<StaticDataset, () => Promise<void>> = {
  objects: loadObjects, objectProps: loadObjectProps, links: loadLinks, metrics: loadMetrics, dimensions: loadDimensions, knowledge: loadKnowledge,
};
/** 依赖：属性按对象列表逐个取 */
const DEPS: Partial<Record<StaticDataset, StaticDataset[]>> = { objectProps: ['objects'] };

const cache = new Map<string, Promise<void>>();
function loadOne(ds: StaticDataset): Promise<void> {
  const key = `${currentApiContext().ver ?? ''}|${ds}`;
  let p = cache.get(key);
  if (!p) {
    p = Promise.all((DEPS[ds] ?? []).map(loadOne)).then(() => LOADERS[ds]());
    p.catch(() => cache.delete(key));
    cache.set(key, p);
  }
  return p;
}
/** 切到当前版本 / 历史版本时调用：同一版本只拉一次；mock 模式直接返回 */
export async function loadStatic(sets: readonly StaticDataset[]): Promise<void> {
  if (!isStaticReal()) return;
  // 不同版本共用同一组 mock 数组：换版本时清掉别的版本的缓存，保证数组内容和当前版本一致
  const ver = currentApiContext().ver ?? '';
  for (const k of [...cache.keys()]) if (!k.startsWith(`${ver}|`)) cache.delete(k);
  for (const ds of sets) await loadOne(ds);
}

// ---------------------------------------------------------------- 整版发布 / 回滚（Releases.vue 两个按钮，static-api.md §3.1）
/** 合并校验行（mock/changes.ts CheckRow） */
export interface MergeCheckRow { code: string; name: string; result: string; note: string }
/** 版本层 ReleaseOut（GET /releases 的每项同形） */
export interface ReleaseResult { ver: string; publishedAt: string; by: string; note: string; parentVer: string | null; kind: string; rollbackOf: string | null; changeOrderIds: string[] }
/** POST /releases:merge-check：已通过单子的合并校验（不落库，和 mock mergeCheck 一致） */
export const mergeCheckApi = () => api.post<{ rows: MergeCheckRow[]; blocked: boolean }>('/releases:merge-check');
/** POST /releases：整版发布（本体管理员；合并校验阻断 → 409 MERGE_BLOCKED） */
export const publishRelease = (note?: string) => api.post<ReleaseResult>('/releases', note ? { note } : {});
/** POST /releases/{ver}:rollback：回滚最近一次发布（否则 409 ROLLBACK_NOT_LATEST） */
export const rollbackRelease = (ver: string) => api.post<ReleaseResult>(`/releases/${encodeURIComponent(ver)}:rollback`, {});
/** 发布 / 回滚成功后刷新顶栏版本号（同一后端前缀；versioning 不在真接口开关里时也能取到） */
export const fetchCurrentAfterWrite = () => api.get<{ current: string | null; next: string; release: ReleaseResult | null }>('/releases/current');

// ---------------------------------------------------------------- 变更单（Releases.vue 列表 / 详情按钮，static-api.md §3.1）
/** GET /change-requests?full=true 每行：mock ChangeRequest 原样 + detail（CRDetail）+ events（评审追加记录，新的在前） */
export type CRRow = ChangeRequest & { detail: Partial<CRDetail>; events: ReviewEvent[] };
export type CRAction = 'submit' | 'withdraw' | 'approve' | 'reject' | 'unapprove' | 'reopen';
/** 整表载入（不分页，前端本地筛选），刷新页面后状态以库为准 */
export const fetchChangeRequests = () => api.get<CRRow[]>('/change-requests', { query: { full: true } });
/** POST /change-requests/{id}:{action}：状态流转（非法流转 409 CR_STATE、人不对 403、阻断 409 CR_BLOCKED），返回更新后的一行 */
export const crAction = (id: string, action: CRAction, rejectReason?: string) =>
  api.post<CRRow>(`/change-requests/${encodeURIComponent(id)}:${action}`, action === 'reject' ? { rejectReason } : {});
/** DELETE /change-requests/{id}：删除草稿（owner + 草稿；非 owner 403、非草稿 409 CR_STATE），成功 204 */
export const deleteCR = (id: string) => api.delete<void>(`/change-requests/${encodeURIComponent(id)}`);

/** 通用对象浏览：GET /object-types/{id}/meta 的属性项（static-api.md §3.2.1） */
export interface ExplorerPropMeta {
  key: string; name: string; dataType: string;
  searchable: boolean; filterable: boolean; sortable: boolean; listed: boolean;
  order: number; visibility: 'prominent' | 'normal' | 'hidden';
  format: string | null; unit: string | null;
  listWidth?: number | null; facetKind?: 'enum' | 'range' | null;
}
export interface MetricVariant { code: string; label: string; api: string; version: string; default: boolean }
export interface ObjectTypeMeta {
  titleProp: string | null; properties: ExplorerPropMeta[];
  pkProp?: string | null; kwPlaceholder?: string; hasData?: boolean;
  metrics?: { id: string; name: string; unit: string | null; variants: MetricVariant[] }[];
}
export type FacetFilterValue = string[] | { min?: string | number; max?: string | number } | string | number | boolean;
export type Facet =
  | { key: string; name: string; kind: 'enum'; order: number; hasData: boolean; values: { value: string; count: number }[] }
  | { key: string; name: string; kind: 'range'; order: number; hasData: boolean; min: number | string | null; max: number | string | null };
/** GET /object-types/{id}/meta：结果列、分面、搜索字段的元数据 */
export const getObjectTypeMeta = (id: string) => api.get<ObjectTypeMeta>(`/object-types/${encodeURIComponent(id)}/meta`);
/** GET /object-types/{id}/facets：按实际数据算的分面取值 / 区间，随其他已选筛选联动 */
export const getObjectTypeFacets = (id: string, filters: Record<string, FacetFilterValue> = {}) =>
  api.get<Facet[]>(`/object-types/${encodeURIComponent(id)}/facets`, {
    query: Object.keys(filters).length ? { filters: JSON.stringify(filters) } : {},
  });
