/**
 * 静态本体（开关键 static，前缀 ontology）：对象类型、关系类型、问数路径、指标、维度与字典、业务知识。
 * 接口见 docs/backend/static-api.md §3.2–§3.6。
 *
 * 可翻页页面使用 fetchStaticPage；StaticGate paged 模式只隔离上下文。
 * loadStatic 保留给明确需要完整定义的编辑候选、元数据目录，不用作页面分页数据源。
 */
import { apiClient, currentApiContext, type PageResult, type RequestOptions } from './http';
import { LINKS, LINK_OBJECTS, PATH_GROUPS, type LinkType, type LinkObject, type PathGroup } from '@/mock/links';
import { OBJECT_PROPS, type ObjProp } from '@/mock/objectProps';
import { METRIC_LIST, METRIC_DEF, METRIC_DIMS, METRIC_VERSIONS, METRIC_REFS, type MetricListItem } from '@/mock/metricCatalog';
import { DIM_ITEMS, type DimItem } from '@/mock/dimensions';
import { KNOWLEDGE, type KEntry } from '@/mock/knowledge';
import type { ChangeRequest, CRDetail, CRLevel, ReviewEvent } from '@/mock/changes';
import { DRAFTS, type TargetKind } from '@/mock/releases';

const api = apiClient('static');
export const isStaticReal = (): boolean => api.isReal();
export const fetchMetricGroups = () => api.get<string[]>('/metrics/groups');
export const fetchDimensionUsage = (id: string) => all<import('@/mock/dimensions').DimUsage>(loadContext(), `/dimensions/${encodeURIComponent(id)}/usage`);
export interface DimensionMatchResult { exact: { item: string; itemName: string; value: string }[]; contains: { item: string; itemName: string; value: string }[]; more: number; rule: { resolve?: string } | null; groups: string[]; crossDim: boolean }
export interface KnowledgeTryRow { k: string; v: string; cls?: string; by?: string }
export const matchDimension = (body: { text: string; dimension: string; all: boolean; crId?: string }) => api.post<DimensionMatchResult>('/dimensions:match', body, loadContext().options);
export const resolveKnowledge = (body: { text: string; knowledge: string; crId?: string }) => api.post<{ rows: KnowledgeTryRow[] }>('/knowledge:resolve', body, loadContext().options);
export const fetchObjectCrHistory = (id: string) => all<{ cr: CRRow; parts: string[] }>(loadContext(), `/object-types/${encodeURIComponent(id)}/usage`, { kind: 'cr' });
export async function rollbackMetric(id: string, toVersion: string, opts: { join?: string; title?: string; desc?: string }) {
  const ctx = loadContext();
  const head = opts.join ? await fetchCR(opts.join, ctx.options) : undefined;
  if (!isCurrent(ctx)) return;
  const result = await api.post<{ crId: string; lockVersion: number }>(`/metrics/${encodeURIComponent(id)}:rollback`, {
    toVersion, ...(head ? { crId: head.id, lockVersion: head.lockVersion } : { title: opts.title }), reason: opts.desc,
  }, ctx.options);
  if (!isCurrent(ctx)) return;
  return result;
}
export async function fetchKnowledgeChoices() {
  const ctx = loadContext();
  const [objects, metrics] = await Promise.all([all<ObjectTypeItem>(ctx, '/object-types'), all<MetricListItem>(ctx, '/metrics')]);
  if (!isCurrent(ctx)) return null;
  return [
    ...objects.filter((o) => o.status === '已发布').map((o) => ({ kind: 'object-type' as const, id: o.id, name: o.name })),
    ...metrics.filter((m) => m.status === '已发布').map((m) => ({ kind: 'metric' as const, id: m.id, name: m.name })),
  ];
}
export const fetchKnowledgeConflicts = (crId?: string) => read<{ conflicts: { word: string; ids: string[] }[]; rule: Record<string, string> }>(loadContext(), '/knowledge/synonym-conflicts', crId ? { crId } : {});
export const fetchKnowledgeConflictRules = async () => {
  const result = await read<{ rule: Record<string, string> }>(loadContext(), '/knowledge/synonym-conflicts');
  return result.rule;
};

export const fetchObjectViews = (id: string) => all<import('@/mock/objectViews').ObjectView>(loadContext(), `/object-types/${encodeURIComponent(id)}/usage`, { kind: 'view' });
export async function fetchObjectMetrics(id: string) {
  const rows = await all<import('@/mock/metrics').Metric & { base: string[]; via: string[] }>(loadContext(), `/object-types/${encodeURIComponent(id)}/usage`, { kind: 'metric' });
  return rows.map(({ base, via, ...m }) => ({ m, base, via }));
}
export function staticContextKey(): string {
  const { ontologyId, demoAs, previewAs, ver } = currentApiContext();
  return JSON.stringify([isStaticReal(), ontologyId, demoAs, previewAs, ver]);
}
interface LoadContext { key: string; generation: number; options: RequestOptions }
let activeKey = '';
let generation = 0;
function isCurrent(ctx: LoadContext) { return ctx.generation === generation && ctx.key === staticContextKey(); }
function read<T>(ctx: LoadContext, path: string, query: Record<string, string | number | boolean> = {}) {
  return api.get<T>(path, { ...ctx.options, query: { ...ctx.options.query, ...query } });
}

/** One visible page; capture identity/history before dispatch. Never turns a page into an all-table fetch. */
export function fetchStaticPage<T>(path: string, query: Record<string, string | number | boolean | undefined> = {}, signal?: AbortSignal) {
  const ctx = loadContext();
  return api.get<PageResult<T> & Record<string, unknown>>(path, { ...ctx.options, signal, query: { ...ctx.options.query, ...query } });
}
export function fetchStaticDetail<T>(path: string, query: Record<string, string | number | boolean> = {}) {
  return read<T>(loadContext(), path, query);
}
/** 单页上限（后端 MAX_PAGE_SIZE=200）；超过时按页拉全 */
const MAX = 200;
async function all<T>(ctx: LoadContext, path: string, query: Record<string, string | number | boolean> = {}): Promise<T[]> {
  const out: T[] = [];
  for (let page = 1; ; page += 1) {
    const r = await read<PageResult<T>>(ctx, path, { ...query, page, size: MAX });
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

const initial = {
  objects: [...LINK_OBJECTS], props: { ...OBJECT_PROPS }, links: [...LINKS], paths: [...PATH_GROUPS],
  metrics: [...METRIC_LIST], def: [...METRIC_DEF], dims: [...METRIC_DIMS], versions: [...METRIC_VERSIONS], refs: [...METRIC_REFS],
  dimensions: [...DIM_ITEMS], knowledge: [...KNOWLEDGE],
};

/** 对象类型列表项（ObjectTypes.vue TYPES + LINK_OBJECTS.table） */
export interface ObjectTypeItem { id: string; name: string; api: string; group: string; table?: string; props: number; count: string; status: string }
/** real 模式下对象类型页的 TYPES（mock 模式为空，页面用页内常量） */
export const OBJECT_TYPE_LIST: ObjectTypeItem[] = [];

interface ObjectDetailDto { id: string; props: ObjProp[] }
interface MetricDetailDto extends MetricListItem { formula?: string; dimNames?: string[]; variants?: { code: string; label: string; default: boolean; api: string; version: string }[]; def: typeof METRIC_DEF; dims: typeof METRIC_DIMS }

async function loadObjects(ctx: LoadContext) {
  const list = await all<ObjectTypeItem>(ctx, '/object-types');
  if (!isCurrent(ctx)) return;
  replace(OBJECT_TYPE_LIST, list);
  replace(LINK_OBJECTS, list.map((o): LinkObject => ({ id: o.id, name: o.name, api: o.api, group: o.group, table: o.table ?? '--' })));
}
async function loadObjectProps(ctx: LoadContext) {
  const details = await pmap(OBJECT_TYPE_LIST, (o) => read<ObjectDetailDto>(ctx, `/object-types/${encodeURIComponent(o.id)}`));
  if (!isCurrent(ctx)) return;
  replaceRecord(OBJECT_PROPS, Object.fromEntries(details.map((d) => [d.id, d.props])));
}
async function loadLinks(ctx: LoadContext) {
  const [links, paths] = await Promise.all([all<LinkType>(ctx, '/link-types'), read<PathGroup[]>(ctx, '/link-types/paths')]);
  if (!isCurrent(ctx)) return;
  replace(LINKS, links); replace(PATH_GROUPS, paths);
}
async function loadMetrics(ctx: LoadContext) {
  const list = await all<MetricListItem>(ctx, '/metrics');
  if (isCurrent(ctx)) replace(METRIC_LIST, list);
}
/** 指标详情（定义 / 允许的维度 / 口径版本 / 引用），mock 里只有一份，按选中指标原地替换 */
let metricSeq = 0;
export async function loadMetricDetail(id: string) {
  const my = ++metricSeq;
  const ctx = loadContext();
  const enc = encodeURIComponent(id);
  const [d, versions, refs] = await Promise.all([
    read<MetricDetailDto>(ctx, `/metrics/${enc}`),
    all<(typeof METRIC_VERSIONS)[number]>(ctx, `/metrics/${enc}/versions`),
    all<(typeof METRIC_REFS)[number]>(ctx, `/metrics/${enc}/refs`),
  ]);
  if (!isCurrent(ctx) || my !== metricSeq) return;
  replace(METRIC_DEF, d.def ?? []);
  replace(METRIC_DIMS, d.dims ?? []);
  replace(METRIC_VERSIONS, versions);
  replace(METRIC_REFS, refs);
  return d;
}
async function loadDimensions(ctx: LoadContext) {
  const heads = await all<{ id: string }>(ctx, '/dimensions');
  // full=1：完整 DimItem（含 values / mappings / usage / history，草稿行原样），页面自己算视图
  const details = await pmap(heads, (h) => read<DimItem>(ctx, `/dimensions/${encodeURIComponent(h.id)}`, { full: true }));
  if (isCurrent(ctx)) replace(DIM_ITEMS, details);
}
async function loadKnowledge(ctx: LoadContext) {
  const list = await all<KEntry & { shownStatus?: string }>(ctx, '/knowledge');
  const refs = await pmap(list, (e) => all<KEntry['refs'][number]>(ctx, `/knowledge/${encodeURIComponent(e.id)}/refs`));
  if (!isCurrent(ctx)) return;
  replace(KNOWLEDGE, list.map(({ shownStatus: _s, ...e }, i) => ({ ...e, refs: refs[i] }) as KEntry));
}

export type StaticDataset = 'objects' | 'objectProps' | 'links' | 'metrics' | 'dimensions' | 'knowledge';
const LOADERS: Record<StaticDataset, (ctx: LoadContext) => Promise<void>> = {
  objects: loadObjects, objectProps: loadObjectProps, links: loadLinks, metrics: loadMetrics, dimensions: loadDimensions, knowledge: loadKnowledge,
};
/** 依赖：属性按对象列表逐个取 */
const DEPS: Partial<Record<StaticDataset, StaticDataset[]>> = { objectProps: ['objects'] };

const cache = new Map<string, Promise<void>>();
function loadContext(): LoadContext {
  const { ontologyId, demoAs, previewAs, ver } = currentApiContext();
  const key = staticContextKey();
  if (key !== activeKey) {
    activeKey = key; generation++; cache.clear();
    const demo = !isStaticReal();
    replace(OBJECT_TYPE_LIST, []); replace(LINK_OBJECTS, demo ? initial.objects : []); replaceRecord(OBJECT_PROPS, demo ? initial.props : {});
    replace(LINKS, demo ? initial.links : []); replace(PATH_GROUPS, demo ? initial.paths : []); replace(METRIC_LIST, demo ? initial.metrics : []);
    replace(METRIC_DEF, demo ? initial.def : []); replace(METRIC_DIMS, demo ? initial.dims : []); replace(METRIC_VERSIONS, demo ? initial.versions : []); replace(METRIC_REFS, demo ? initial.refs : []);
    replace(DIM_ITEMS, demo ? initial.dimensions : []); replace(KNOWLEDGE, demo ? initial.knowledge : []);
  }
  return { key, generation, options: { readonlyParams: false, query: { previewAs, ver }, headers: { 'X-Demo-As': demoAs ?? '', 'X-Ontology-Id': ontologyId ?? '' } } };
}
function loadOne(ds: StaticDataset, ctx: LoadContext): Promise<void> {
  const key = JSON.stringify([ctx.key, ds]);
  let p = cache.get(key);
  if (!p) {
    p = Promise.all((DEPS[ds] ?? []).map((dep) => loadOne(dep, ctx))).then(() => { if (isCurrent(ctx)) return LOADERS[ds](ctx); });
    p.catch(() => { if (cache.get(key) === p) cache.delete(key); });
    cache.set(key, p);
  }
  return p;
}
/** 切到当前版本 / 历史版本时调用：同一版本只拉一次；mock 模式直接返回 */
export async function loadStatic(sets: readonly StaticDataset[]): Promise<void> {
  const ctx = loadContext();
  if (!isStaticReal()) return;
  for (const ds of sets) { if (!isCurrent(ctx)) return; await loadOne(ds, ctx); }
}

// ---------------------------------------------------------------- 整版发布 / 回滚（Releases.vue 两个按钮，static-api.md §3.1）
/** 合并校验行（mock/changes.ts CheckRow） */
export interface MergeCheckRow { code: string; name: string; result: string; note: string }
/** 版本层 ReleaseOut（GET /releases 的每项同形） */
export interface ReleaseResult { ver: string; publishedAt: string; by: string; note: string; parentVer: string | null; kind: string; rollbackOf: string | null; changeOrderIds: string[]; skipped?: string[] }
/** POST /releases:merge-check：已通过单子的合并校验（不落库，和 mock mergeCheck 一致） */
export const mergeCheckApi = () => api.post<{ rows: MergeCheckRow[]; blocked: boolean }>('/releases:merge-check');
/** POST /releases：整版发布（本体管理员；合并校验阻断 → 409 MERGE_BLOCKED） */
async function writeRelease(path: string, body: object): Promise<ReleaseResult> {
  const key = staticContextKey();
  const result = await api.post<ReleaseResult>(path, body);
  if (key === staticContextKey()) { generation++; cache.clear(); }
  return result;
}
export const publishRelease = (note?: string, lockVersion?: number) => writeRelease('/releases', { note, lockVersion });
/** POST /releases/{ver}:rollback：回滚最近一次发布（否则 409 ROLLBACK_NOT_LATEST） */
export const rollbackRelease = (ver: string) => writeRelease(`/releases/${encodeURIComponent(ver)}:rollback`, {});
export interface MetricTrialResult {
  id: string; version: string; formula: string; crId: string | null;
  rows: { date: string; value: string | null }[];
  dataCutoff: string | null; scope: string;
}
export interface MetricTrialReadiness {
  id: string; ready: boolean;
  issues: { reason: string; message: string; reference?: string; candidates?: { reference: string; name: string; mapped: boolean }[] }[];
}
export const fetchMetricReadiness = (id: string, crId?: string) => {
  const ctx = loadContext();
  return api.get<MetricTrialReadiness>(`/metrics/${encodeURIComponent(id)}/trial-readiness`, { ...ctx.options, query: { ...ctx.options.query, crId } });
};
/** Pure trial: persisted definition and governed real rows; errors never fall back to mock. */
export const trialMetric = (id: string, crId?: string, options?: RequestOptions) =>
  api.post<MetricTrialResult>(`/metrics/${encodeURIComponent(id)}:trial`, { crId, range: '30d' }, options);
/** 发布 / 回滚成功后刷新顶栏版本号（同一后端前缀；versioning 不在真接口开关里时也能取到） */
export const fetchCurrentAfterWrite = () => api.get<{ current: string | null; next: string; lockVersion: number; release: ReleaseResult | null }>('/releases/current');

// ---------------------------------------------------------------- 变更单（Releases.vue 列表 / 详情按钮，static-api.md §3.1）
/** GET /change-requests?full=true 每行：mock ChangeRequest 原样 + detail（CRDetail）+ events（评审追加记录，新的在前） */
export type CRRow = ChangeRequest & { ownerId?: string; reviewerId?: string; detail: Partial<CRDetail>; events: ReviewEvent[] };
export type CRAction = 'submit' | 'withdraw' | 'approve' | 'reject' | 'unapprove' | 'reopen';
/** 整表载入（不分页，前端本地筛选），刷新页面后状态以库为准 */
export const fetchChangeRequests = (options?: RequestOptions, full = true) => api.get<CRRow[]>('/change-requests', { ...options, query: { ...options?.query, full } });
/** Complete lightweight workflow directory. Events are intentionally not fetched or used by badge consumers. */
export async function fetchWorkflowRecords(options?: RequestOptions): Promise<CRRow[]> {
  const rows = await api.get<Omit<CRRow, 'events'>[]>('/change-requests', { ...options, query: { ...options?.query, workflow: true } });
  if (rows.some((row) => !row.detail || !Array.isArray(row.detail.touches))) throw new Error('工作流目录响应无效，请稍后重试');
  return rows.map((row) => ({ ...row, events: [] }));
}
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
export type FacetFilterValue = (string | number | boolean)[] | { min?: string | number | null; max?: string | number | null };
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

// ---------------------------------------------------------------- 变更单写接口（handoff/static.md「变更单写接口契约」，static-r3）
/** 目标通用：kind + 业务编码（静态 / 动态 / Palantir 共用） */
export interface CRTargetIn { kind: TargetKind; id: string; isNew?: boolean; name?: string }
export type CRItemOp = 'add' | 'update' | 'delete';
export interface CRItemIn { key: string; op: CRItemOp; before?: unknown; after?: unknown; type?: string; field?: string; beforeText?: string; afterText?: string }
export interface CRItemOut extends CRItemIn { target: { kind: TargetKind; id: string }; section: string; updatedBy?: string; updatedAt: string }
/** 详情 = GET 列表一行 + 登记的目标 + 乐观锁版本号 */
export type CRFull = CRRow & { targets: CRTargetIn[]; lockVersion: number };
export interface CRCreateIn { title: string; reason?: string; target?: CRTargetIn; level?: CRLevel; reviewer?: string }
export interface CRPatchIn { title?: string; reason?: string; reviewer?: string; level?: CRLevel; migration?: string; lockVersion?: number }
export interface CRValidateOut { id: string; checkedAt: string; checks: MergeCheckRow[]; summary: string; blocked: boolean }

const crPath = (id: string, rest = '') => `/change-requests/${encodeURIComponent(id)}${rest}`;
/** POST /change-requests → 201 新草稿（默认审核人 = 本体管理员；占用他单目标 409 TARGET_IN_OTHER_CR） */
/** GET /change-requests/{id}：详情（带 targets、lockVersion） */
export const fetchCR = (id: string, options?: RequestOptions) => api.get<CRFull>(crPath(id), options);

export const createCR = (body: CRCreateIn, options?: RequestOptions) => api.post<CRFull>('/change-requests', body, options);
export const allocateCRTarget = (id: string, kind: 'link' | 'knowledge', name?: string, options?: RequestOptions) => api.post<{ id: string; target: CRTargetIn }>(crPath(id, '/targets:allocate'), { kind, name }, options);
/** Allocate and register together in the CR transaction; failed allocation retains the real CR for retry. */
export async function startAllocatedTarget(kind: 'link' | 'knowledge', opts: { join?: string; title?: string; desc?: string }, name: string, resume?: string) {
  const key = staticContextKey(), ctx = currentApiContext();
  const options = { readonlyParams: false, query: { ver: ctx.ver, previewAs: ctx.previewAs }, headers: { 'X-Demo-As': ctx.demoAs ?? '', 'X-Ontology-Id': ctx.ontologyId ?? '' } };
  const check = () => { if (key !== staticContextKey()) throw new Error('身份或版本已切换，请重新打开新建'); };
  const existing = opts.join ?? resume;
  const header = existing ? await fetchCR(existing, options) : await createCR({ title: opts.title || `新建${kind === 'link' ? '关系' : '业务知识'}`, reason: opts.desc }, options);
  check(); registerDraft(header);
  try {
    const result = await allocateCRTarget(header.id, kind, name, options); check();
    return { crId: header.id, target: result.target, owner: header.owner, updatedAt: header.updatedAt };
  } catch (e) { throw Object.assign(e instanceof Error ? e : new Error(String(e)), { crId: header.id }); }
}
/** PATCH /change-requests/{id}：改单头（仅草稿；lockVersion 不符 409 VERSION_CONFLICT） */
export const patchCR = (id: string, body: CRPatchIn, options?: RequestOptions) => api.patch<CRFull>(crPath(id), body, options);
export const fetchReviewerOptions = (id: string, options?: RequestOptions) => api.get<{ id: string; name: string }[]>(crPath(id, '/reviewer-options'), options);
/** POST /change-requests/{id}/targets：加入已有变更单（幂等） */
export const addCRTarget = (id: string, target: CRTargetIn, options?: RequestOptions) => api.post<CRFull>(crPath(id, '/targets'), target, options);
/** DELETE /change-requests/{id}/targets/{kind}/{targetId}：放弃本对象草稿（删草稿行 + 登记），204 */
export const discardCRTarget = (id: string, kind: TargetKind, targetId: string) =>
  api.delete<void>(crPath(id, `/targets/${kind}/${encodeURIComponent(targetId)}`));
/** PUT /change-requests/{id}/items：同一 (目标, section) 整段替换 */
export const saveCRItems = (id: string, body: { target: CRTargetIn; section: string; rows: CRItemIn[]; lockVersion?: number }, options?: RequestOptions) =>
  api.put<{ id: string; changes: number; lockVersion: number; items: CRItemOut[] }>(crPath(id, '/items'), body, options);
/** GET /change-requests/{id}/items：读回草稿行（可按目标 / section 筛） */
export const fetchCRItems = (id: string, q: { kind?: TargetKind; targetId?: string; section?: string } = {}, options?: RequestOptions) =>
  api.get<CRItemOut[]>(crPath(id, '/items'), { ...options, query: { ...options?.query, ...q } });
/** POST /change-requests/{id}:validate：同步重新校验，整单重写 V1–V17 */
export const validateCR = (id: string) => api.post<CRValidateOut>(crPath(id, ':validate'), {});
/** GET /change-requests/{id}/diffs：改动列表分页（默认 10 条一页） */
export const fetchCRDiffs = (id: string, page = 1, size = 10) =>
  api.get<PageResult<CRDetail['diffs'][number]>>(crPath(id, '/diffs'), { query: { page, size } });
/** GET /change-requests/draft-targets：{ 单号: { kind: [id…] } }（= mock DRAFT_TARGETS 结构） */
export const fetchDraftTargets = (status = '草稿') =>
  api.get<Record<string, Partial<Record<TargetKind, string[]>>>>('/change-requests/draft-targets', { query: { status } });

/** 把库里的单子登记进 DRAFTS（useDraftView 的 allDrafts / draftOf 读它；同号替换）。
 *  共享（PDE）：动态 / 动作模块登记变更单也用它，只要单头这 5 个字段（CRFull、CrFullDto 都满足） */
export function registerDraft(cr: Pick<CRFull, 'id' | 'title' | 'owner' | 'updatedAt' | 'changes'>) {
  const row = { id: cr.id, title: cr.title, owner: cr.owner, updatedAt: cr.updatedAt, changes: cr.changes };
  const i = DRAFTS.findIndex((d) => d.id === cr.id);
  if (i >= 0) DRAFTS.splice(i, 1, row); else DRAFTS.unshift(row);
}
const bumpDraft = (id: string, changes: number) => { const d = DRAFTS.find((x) => x.id === id); if (d) d.changes = changes; };

/**
 * 定义页“发起变更”：real 模式先在库里新建 / 加入（失败抛 ApiError，页面提示后中止），
 * 返回交给 useDraftView.start 的参数（real 一律 { join: 库里单号 }，单号以库为准）；mock 原样返回。
 */
export async function crStart(target: CRTargetIn, opts: { join?: string; title?: string; desc?: string }): Promise<{ join?: string; title?: string }> {
  if (!isStaticReal()) return opts;
  const key = staticContextKey(), ctx = currentApiContext();
  const options = { readonlyParams: false, query: { ver: ctx.ver, previewAs: ctx.previewAs }, headers: { 'X-Demo-As': ctx.demoAs ?? '', 'X-Ontology-Id': ctx.ontologyId ?? '' } };
  const cr = opts.join ? await addCRTarget(opts.join, target, options) : await createCR({ title: opts.title || '未命名变更', reason: opts.desc, target }, options);
  if (key !== staticContextKey()) throw new Error('身份或版本已切换，请重新发起变更');
  registerDraft(cr);
  return { join: cr.id };
}

/** 页面一次保存里的改动（同一 key 再次保存时保留最早的 before，改回原值的行去掉） */
export interface DraftChange { key: string; before?: unknown; after?: unknown; op?: CRItemOp; type?: string; field?: string; beforeText?: string; afterText?: string }
const same = (a: unknown, b: unknown) => JSON.stringify(a ?? null) === JSON.stringify(b ?? null);

/**
 * 定义页“保存到草稿”：real 模式读回该 (目标, section) 已有的草稿行，合并本次改动后整段 PUT；mock 什么都不做。
 * 失败抛 ApiError（CR_LOCKED / TARGET_IN_OTHER_CR / VERSION_CONFLICT…），页面提示。
 */
export async function crSave(crId: string | undefined, target: CRTargetIn, section: string, changes: DraftChange[]): Promise<void> {
  if (!isStaticReal() || !crId) return;
  // 读回时记下 lockVersion，PUT 带上：中间有人改过这张单子就 409 VERSION_CONFLICT，不覆盖
  const key = staticContextKey(), ctx = currentApiContext();
  const options = { readonlyParams: false, query: { ver: ctx.ver, previewAs: ctx.previewAs }, headers: { 'X-Demo-As': ctx.demoAs ?? '', 'X-Ontology-Id': ctx.ontologyId ?? '' } };
  const checkContext = () => { if (key !== staticContextKey()) throw new Error('身份或版本已切换，请重新加载草稿'); };
  const { lockVersion } = await fetchCR(crId, options);
  checkContext();
  const old = await fetchCRItems(crId, { kind: target.kind, targetId: target.id, section }, options);
  checkContext();
  const rows = new Map<string, CRItemIn>(old.map(({ target: _t, section: _s, updatedBy: _u, updatedAt: _a, ...r }) => [r.key, r]));
  for (const c of changes) {
    const prev = rows.get(c.key);
    if (c.op === 'delete' && prev?.op === 'add') { rows.delete(c.key); continue; }
    const op: CRItemOp = c.op ?? (prev?.op === 'add' || target.isNew ? 'add' : 'update');
    const before = prev && prev.op !== 'add' ? prev.before : op === 'add' ? undefined : c.before;
    if (op === 'update' && same(before, c.after)) { rows.delete(c.key); continue; }
    rows.set(c.key, { ...c, op, before });
  }
  const r = await saveCRItems(crId, { target, section, rows: [...rows.values()], lockVersion }, options);
  checkContext();
  bumpDraft(crId, r.changes);
}

/** DraftBanner“提交审核”：real 模式走 :submit（无审核人 409 CR_NO_REVIEWER、阻断 409 CR_BLOCKED）；mock 什么都不做 */
export async function crSubmit(crId: string): Promise<void> {
  if (isStaticReal()) await crAction(crId, 'submit');
}

/** DraftBanner“放弃草稿”：real 模式删掉本对象在该单里的草稿行和登记；mock 什么都不做。返回是否动了库 */
export async function crDiscard(crId: string, kind: TargetKind, targetId: string): Promise<boolean> {
  if (!isStaticReal()) return false;
  await discardCRTarget(crId, kind, targetId);
  return true;
}

/** 错误提示文字 */
export const crErr = (e: unknown) => (e instanceof Error ? e.message : String(e));
