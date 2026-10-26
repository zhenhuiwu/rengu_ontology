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
import type { ChangeRequest, CRDetail, CRLevel, ReviewEvent } from '@/mock/changes';
import { DRAFTS, type TargetKind } from '@/mock/releases';

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
export const fetchCR = (id: string) => api.get<CRFull>(crPath(id));

export const createCR = (body: CRCreateIn) => api.post<CRFull>('/change-requests', body);
/** PATCH /change-requests/{id}：改单头（仅草稿；lockVersion 不符 409 VERSION_CONFLICT） */
export const patchCR = (id: string, body: CRPatchIn) => api.patch<CRFull>(crPath(id), body);
/** POST /change-requests/{id}/targets：加入已有变更单（幂等） */
export const addCRTarget = (id: string, target: CRTargetIn) => api.post<CRFull>(crPath(id, '/targets'), target);
/** DELETE /change-requests/{id}/targets/{kind}/{targetId}：放弃本对象草稿（删草稿行 + 登记），204 */
export const discardCRTarget = (id: string, kind: TargetKind, targetId: string) =>
  api.delete<void>(crPath(id, `/targets/${kind}/${encodeURIComponent(targetId)}`));
/** PUT /change-requests/{id}/items：同一 (目标, section) 整段替换 */
export const saveCRItems = (id: string, body: { target: CRTargetIn; section: string; rows: CRItemIn[]; lockVersion?: number }) =>
  api.put<{ id: string; changes: number; lockVersion: number; items: CRItemOut[] }>(crPath(id, '/items'), body);
/** GET /change-requests/{id}/items：读回草稿行（可按目标 / section 筛） */
export const fetchCRItems = (id: string, q: { kind?: TargetKind; targetId?: string; section?: string } = {}) =>
  api.get<CRItemOut[]>(crPath(id, '/items'), { query: q });
/** POST /change-requests/{id}:validate：同步重新校验，整单重写 V1–V17 */
export const validateCR = (id: string) => api.post<CRValidateOut>(crPath(id, ':validate'), {});
/** GET /change-requests/{id}/diffs：改动列表分页（默认 10 条一页） */
export const fetchCRDiffs = (id: string, page = 1, size = 10) =>
  api.get<PageResult<CRDetail['diffs'][number]>>(crPath(id, '/diffs'), { query: { page, size } });
/** GET /change-requests/draft-targets：{ 单号: { kind: [id…] } }（= mock DRAFT_TARGETS 结构） */
export const fetchDraftTargets = (status = '草稿') =>
  api.get<Record<string, Partial<Record<TargetKind, string[]>>>>('/change-requests/draft-targets', { query: { status } });

/** 把库里的单子登记进 DRAFTS（useDraftView 的 allDrafts / draftOf 读它；同号替换） */
function registerDraft(cr: CRFull) {
  const row = { id: cr.id, title: cr.title, owner: cr.owner, updatedAt: cr.updatedAt, changes: cr.changes };
  const i = DRAFTS.findIndex((d) => d.id === cr.id);
  if (i >= 0) DRAFTS.splice(i, 1, row); else DRAFTS.unshift(row);
}
const bumpDraft = (id: string, changes: number) => { const d = DRAFTS.find((x) => x.id === id); if (d) d.changes = changes; };

/**
 * 定义页“发起变更”：real 模式先在库里新建 / 加入（失败抛 ApiError，页面提示后中止），
 * 返回交给 useDraftView.start 的参数（real 一律 { join: 库里单号 }，单号以库为准）；mock 原样返回。
 */
export async function crStart(target: CRTargetIn, opts: { join?: string; title?: string }): Promise<{ join?: string; title?: string }> {
  if (!isStaticReal()) return opts;
  const cr = opts.join ? await addCRTarget(opts.join, target) : await createCR({ title: opts.title || '未命名变更', target });
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
  const [{ lockVersion }, old] = await Promise.all([fetchCR(crId), fetchCRItems(crId, { kind: target.kind, targetId: target.id, section })]);
  const rows = new Map<string, CRItemIn>(old.map(({ target: _t, section: _s, updatedBy: _u, updatedAt: _a, ...r }) => [r.key, r]));
  for (const c of changes) {
    const prev = rows.get(c.key);
    const op: CRItemOp = c.op ?? (prev?.op === 'add' || target.isNew ? 'add' : 'update');
    const before = prev && prev.op !== 'add' ? prev.before : op === 'add' ? undefined : c.before;
    if (op === 'update' && same(before, c.after)) { rows.delete(c.key); continue; }
    rows.set(c.key, { ...c, op, before });
  }
  const r = await saveCRItems(crId, { target, section, rows: [...rows.values()], lockVersion });
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
