/**
 * 动作类型 / 事件类型（action 模块，开关键 action，前缀 /api/ontology/v1；docs/backend/action-event-api.md §3.1、§3.2，
 * 后台 backend/app/modules/action|event/router.py）。
 *
 * 页面（pages/ontology/ActionTypes.vue、EventTypes.vue）的模板和草稿编辑（useDraftEdits）都是同步读 mock 的
 * paramsOf()/condsOf()/… ，所以真接口这里做成「快照」：actionApi.isReal() 时，进页面（以及 ?ver= 变化时）把
 * 完整轻目录缓存；仅选中类型懒加载详情，草稿才取完整参数/载荷基线。开关关着时同名函数直接返回 mock。
 * 左侧列表的分组 / 关键字筛选和分页另走列表接口（listActionTypes / listEventTypes）。
 * 字段名、取值已和 mock 对齐（ActionType / EventType / ActionParam …），这里只把 null 收成可选字段。
 *
 * 执行记录由 /action-runs 按页查询；总体统计由 /action-types/{id}/stats 聚合，只有显式 mock 模式计算演示值。
 * 真模式错误和空数据不回演示；快照、运行记录和写回候选按身份 / 预览 / 版本隔离。
 * 运行数据（action 开关，palantir-r2，文档 §12）：近期事件 GET /event-types/{id}/events、运行健康 + 近 14 天事件数
 * GET /event-types/{id}/stats、动作近 14 天执行量 GET /action-types/{id}/stats/daily；页面按选中类型取，真模式加载 / 失败为空态。
 * 变更单写库（palantir-cr，静态 static-r3 handoff/static.md「变更单写接口契约」）：VITE_PALANTIR_CR_WRITE=1 且 action 为真接口时，
 * 动作 / 事件类型页的“发起变更 / 加入”“保存草稿”“提交审核”“放弃草稿”先写库（目标 kind = action / event，id = 类型编码），
 * 失败弹错误、页面不变，不回退 mock、不调 fallBackIfDown。开关关着时页面行为同以前（不发请求）。
 * palantir-r3：改动数以库为准（crChanges，取自 CRFull.changes / PUT items 返回的 changes）；刷新后 crRestore 按
 * GET /change-requests/draft-targets + GET /change-requests?status=草稿 恢复登记，crWriter.load 读回本对象的草稿行。
 */
import { reactive } from 'vue';
import {
  ACTION_TYPES, EVENT_TYPES, ACTION_VERSION_NOTES, EVENT_VERSION_NOTES, paramsOf as mockParams, condsOf as mockConds,
  writebackOf as mockWriteback, payloadOf as mockPayload, subscribersOf as mockSubs, versionsOf as mockVersions,
  type ActionType, type ActionParam, type SubmitCond, type OntologyEdit, type SideEffect, type EventType, type PayloadField,
  type Subscriber, type TypeVersion, writebackSystems as mockWbSystems, runsOf as mockRuns, type ActionRun, type RunStatus,
  type EventRecord as MockEventRecord,
} from '@/mock/actionEvent';
import type { ExtSystem } from '@/mock/extsys';
import { DRAFTS } from '@/mock/releases';
import type { DraftWriter, SectionChange } from '@/pages/ontology/useDraftEdits';
import { integrationApi } from './integration';
import { apiClient, currentApiContext, isApiError, type PageResult, type ApiContext, type QueryValue, type RequestOptions } from './http';
import { registerDraft } from './static';

export const actionApi = apiClient('action');
const api = actionApi;
const enc = encodeURIComponent;
const ALL = 200; // 后台每页上限；轻目录及选中草稿基线按此上限完整翻页。
export const actionContextKey = (ctx: ApiContext = currentApiContext()) => JSON.stringify([ctx.demoAs ?? '', ctx.previewAs ?? '', ctx.ver ?? '']);
const fixed = (ctx: ApiContext, query: Record<string, QueryValue> = {}) => ({ headers: { 'X-Demo-As': ctx.demoAs ?? '' }, readonlyParams: false, query: { ...query, ver: ctx.ver ?? undefined, previewAs: ctx.previewAs ?? undefined } });

/** 后台 null → 前端可选字段 undefined */
const clean = <T>(o: T): T => {
  if (Array.isArray(o)) return o.map(clean) as T;
  if (!o || typeof o !== 'object') return o;
  const r: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(o)) if (v !== null) r[k] = clean(v);
  return r as T;
};

// ---------------- 列表（左侧，分页 + 筛选走后台） ----------------

export type ActionTypeRow = Pick<ActionType, 'id' | 'name' | 'api' | 'group' | 'status' | 'version' | 'target' | 'targetApi' | 'targetKind'>;
export type EventTypeRow = Pick<EventType, 'id' | 'name' | 'api' | 'source' | 'status' | 'version' | 'object'>;
const opt = (v?: string) => (v && v !== '全部' ? v : undefined);

export const listActionTypes = async (q: { group?: string; kw?: string; page: number; size: number }) =>
  clean(await api.get<PageResult<ActionTypeRow> & { groups: string[] }>('/action-types', { query: { group: opt(q.group), kw: q.kw?.trim() || undefined, page: q.page, size: q.size } }));
export const listEventTypes = async (q: { source?: string; kw?: string; page: number; size: number }) =>
  clean(await api.get<PageResult<EventTypeRow> & { sources: string[] }>('/event-types', { query: { source: opt(q.source), kw: q.kw?.trim() || undefined, page: q.page, size: q.size } }));

// ---------------- 快照（详情 + 页签） ----------------

export interface WritebackDto { config: { sysId: string | null; endpoint: string; idemKey: string }; edits: OntologyEdit[]; effects: SideEffect[]; flow: { text: string; desc: string; status: string }[] }

export interface ActionExecutionOptions {
  objects: { id: string; name: string; api: string; properties: { api: string; name: string; type: string }[] }[];
  relations: { id: string; name: string; from: string; to: string }[];
  functions: { id: string; name: string; version: string; inputs: { name: string; type: string }[] }[];
  events: { id: string; name: string; version: string; objectKind: string; payload: { api: string; name: string; type: string; required: boolean }[] }[];
  targets: { id: string; name: string; actions: string[] }[];
}
export const getActionExecutionOptions = () => api.get<ActionExecutionOptions>('/action-execution-options');

interface Snap {
  /** 'idle' | 'loading' | 'ready' | 'error'；ver = 取数时的 ?ver= */
  state: 'idle' | 'loading' | 'ready' | 'error';
  ver: string;
  key: string;
  details: Record<string, number>;
  actions: ActionType[];
  params: Record<string, ActionParam[]>;
  conds: Record<string, SubmitCond[]>;
  writeback: Record<string, WritebackDto>;
  actionVersions: Record<string, TypeVersion[]>;
  events: EventType[];
  payload: Record<string, PayloadField[]>;
  subs: Record<string, Subscriber[]>;
  eventVersions: Record<string, TypeVersion[]>;
}
export const snap = reactive<Snap>({ state: 'idle', ver: '', key: '', details: {}, actions: [], params: {}, conds: {}, writeback: {}, actionVersions: {}, events: [], payload: {}, subs: {}, eventVersions: {} });
const on = () => api.isReal();

export const snapshotReady = () => snap.state === 'ready' && snap.key === actionContextKey();
const emptySnapshot = () => ({ details: {}, actions: [], params: {}, conds: {}, writeback: {}, actionVersions: {}, events: [], payload: {}, subs: {}, eventVersions: {} });
async function allItems<T>(path: string, ctx: ApiContext): Promise<T[]> {
  const rows: T[] = [];
  for (let page = 1; ; page++) {
    const r = await api.get<{ items: T[]; total?: number }>(path, fixed(ctx, { page, size: ALL }));
    rows.push(...clean(r.items));
    if (r.total === undefined || rows.length >= r.total || !r.items.length) return rows;
  }
}
async function loadActions(ctx: ApiContext) { return { actions: await allItems<ActionType>('/action-types', ctx) }; }
async function loadEvents(ctx: ApiContext) { return { events: await allItems<EventType>('/event-types', ctx) }; }
const detailPending = new Map<string, Promise<void>>();
export const actionDetailReady = (id: string, editing = false) => snapshotReady() && (snap.details[`action:${id}`] ?? 0) >= (editing ? 2 : 1);
export const eventDetailReady = (id: string, editing = false) => snapshotReady() && (snap.details[`event:${id}`] ?? 0) >= (editing ? 2 : 1);
/** 完整参数仅供所选动作的草稿基线或规则参数映射，目录加载不预取。 */
export async function loadActionParams(id: string): Promise<void> {
  if (!api.isReal() || !id) return;
  const ctx = { ...currentApiContext() }, key = actionContextKey(ctx);
  const directory = loadSnapshot(), seq = snapSeq;
  await directory;
  if (seq !== snapSeq || key !== actionContextKey() || !snapshotReady()) return;
  const rows = await allItems<ActionParam>(`/action-types/${enc(id)}/params`, ctx);
  if (seq === snapSeq && key === actionContextKey()) snap.params[id] = rows;
}
export async function loadEventSubscribers(id: string): Promise<void> {
  if (!api.isReal() || !id) return;
  const ctx = { ...currentApiContext() }, key = actionContextKey(ctx);
  const directory = loadSnapshot(), seq = snapSeq;
  await directory;
  if (seq !== snapSeq || key !== actionContextKey() || !snapshotReady()) return;
  const rows = await allItems<Subscriber>(`/event-types/${enc(id)}/subscribers`, ctx);
  if (seq === snapSeq && key === actionContextKey()) snap.subs[id] = rows;
}
export function loadActionDetail(id: string, editing = false): Promise<void> { return loadDetail('action', id, editing); }
export function loadEventDetail(id: string, editing = false): Promise<void> { return loadDetail('event', id, editing); }
async function loadDetail(kind: 'action' | 'event', id: string, editing: boolean): Promise<void> {
  if (!api.isReal() || !id) return;
  const ctx = { ...currentApiContext() }, key = actionContextKey(ctx);
  const directory = loadSnapshot(), seq = snapSeq;
  await directory;
  if (seq !== snapSeq || key !== actionContextKey() || !snapshotReady()) return;
  if ((kind === 'action' ? actionDetailReady : eventDetailReady)(id, editing)) return;
  const token = `${seq}|${kind}|${id}|${editing}`;
  if (detailPending.has(token)) return detailPending.get(token);
  const path = `/${kind}-types/${enc(id)}`;
  const task = (async () => {
    if (kind === 'action') {
      const [detail, conds, wb, versions, params] = await Promise.all([
        api.get<ActionType>(path, fixed(ctx)), allItems<SubmitCond>(`${path}/submission-criteria`, ctx),
        api.get<WritebackDto>(`${path}/writeback`, fixed(ctx)), allItems<TypeVersion>(`${path}/versions`, ctx),
        editing ? allItems<ActionParam>(`${path}/params`, ctx) : Promise.resolve(null),
      ]);
      if (seq !== snapSeq || key !== actionContextKey()) return;
      const index = snap.actions.findIndex(row => row.id === id);
      const row = { ...clean(detail), sysId: detail.sysId ?? null };
      if (index < 0) snap.actions.push(row); else snap.actions[index] = row;
      snap.conds[id] = conds; snap.writeback[id] = clean(wb); snap.actionVersions[id] = versions;
      if (params) snap.params[id] = params;
    } else {
      const [detail, subs, versions, payload] = await Promise.all([
        api.get<EventType>(path, fixed(ctx)), allItems<Subscriber>(`${path}/subscribers`, ctx),
        allItems<TypeVersion>(`${path}/versions`, ctx), editing ? allItems<PayloadField>(`${path}/payload-fields`, ctx) : Promise.resolve(null),
      ]);
      if (seq !== snapSeq || key !== actionContextKey()) return;
      const index = snap.events.findIndex(row => row.id === id);
      if (index < 0) snap.events.push(clean(detail)); else snap.events[index] = clean(detail);
      snap.subs[id] = subs; snap.eventVersions[id] = versions;
      if (payload) snap.payload[id] = payload;
    }
    snap.details[`${kind}:${id}`] = Math.max(snap.details[`${kind}:${id}`] ?? 0, editing ? 2 : 1);
  })().finally(() => { detailPending.delete(token); });
  detailPending.set(token, task); return task;
}
export const listActionParams = async (id: string, page: number, size: number) => clean(await api.get<PageResult<ActionParam>>(`/action-types/${enc(id)}/params`, { query: { page, size } }));
export const listEventPayload = async (id: string, page: number, size: number) => clean(await api.get<PageResult<PayloadField & { envelope: boolean }>>(`/event-types/${enc(id)}/payload-fields`, { query: { page, size } }));
let pending: Promise<void> | null = null;
let snapSeq = 0;
/** 发布或回滚成功后使当前定义缓存失效，迟到的旧请求不能重新填回。 */
export function invalidateActionSnapshot(): void {
  if (!api.isReal()) return;
  ++snapSeq;
  pending = null;
  detailPending.clear();
  Object.assign(snap, emptySnapshot(), { state: 'idle', key: '', ver: '' });
}
/** 两类定义全部成功后原子发布；每个异步子请求固定调用时身份、预览和版本。 */
export function loadSnapshot(ver = currentApiContext().ver ?? ''): Promise<void> {
  if (!api.isReal()) return Promise.resolve();
  const ctx = { ...currentApiContext(), ver };
  const key = actionContextKey(ctx);
  if (snap.key === key && (snap.state === 'ready' || (snap.state === 'loading' && pending))) return pending ?? Promise.resolve();
  const seq = ++snapSeq;
  Object.assign(snap, emptySnapshot(), { state: 'loading', key, ver });
  pending = Promise.all([loadActions(ctx), loadEvents(ctx)]).then(([actions, events]) => {
    if (seq === snapSeq && key === actionContextKey()) Object.assign(snap, actions, events, { state: 'ready' });
  }, (e) => {
    if (seq === snapSeq && key === actionContextKey()) Object.assign(snap, emptySnapshot(), { state: 'error' });
    throw e;
  }).finally(() => { if (seq === snapSeq) pending = null; });
  return pending;
}

// ---------------- 页面用的取数函数（真接口快照 / mock） ----------------

export const actionTypes = (): ActionType[] => (api.isReal() ? snapshotReady() ? snap.actions : [] : ACTION_TYPES);
export const paramsOf = (a: ActionType): ActionParam[] => (on() ? !snapshotReady() ? [] : snap.params[a.id] ?? [] : mockParams(a));
export const condsOf = (a: ActionType): SubmitCond[] => (on() ? !snapshotReady() ? [] : snap.conds[a.id] ?? [] : mockConds(a));
export const writebackOf = (a: ActionType): { edits: OntologyEdit[]; effects: SideEffect[] } =>
  (on() ? !snapshotReady() ? { edits: [], effects: [] } : snap.writeback[a.id] ?? { edits: [], effects: [] } : mockWriteback(a));
export const actionVersionsOf = (a: ActionType): TypeVersion[] => (on() ? !snapshotReady() ? [] : snap.actionVersions[a.id] ?? [] : mockVersions(a.version, a.owner, ACTION_VERSION_NOTES));

export const eventTypes = (): EventType[] => (api.isReal() ? snapshotReady() ? snap.events : [] : EVENT_TYPES);
export const payloadOf = (e: EventType): PayloadField[] => (on() ? !snapshotReady() ? [] : snap.payload[e.id] ?? [] : mockPayload(e));
export const subscribersOf = (e: EventType): Subscriber[] => (on() ? !snapshotReady() ? [] : snap.subs[e.id] ?? [] : mockSubs(e));
export const eventVersionsOf = (e: EventType): TypeVersion[] => (on() ? !snapshotReady() ? [] : snap.eventVersions[e.id] ?? [] : mockVersions(e.version, e.owner, EVENT_VERSION_NOTES));

// ---------------- 写回 tab 的外部系统下拉（integration 模块：GET /api/data/v1/ext-systems?writeback=1） ----------------

/**
 * 可写回的外部系统（生产环境且未停用）。integration 开关开着时取真接口（每次进动作类型页重取，外部系统页新建 / 编辑后能看到），
 * 真模式未加载时为空；只有显式 mock 模式使用演示候选。
 */
export const wbSys = reactive<{ key: string; loading: boolean; loaded: boolean; items: ExtSystem[] }>({ key: '', loading: false, loaded: false, items: [] });
let wbPending: Promise<void> | null = null;
let wbSeq = 0;
export function loadWritebackSystems(): Promise<void> {
  if (!integrationApi.isReal()) return Promise.resolve();
  const ctx = { ...currentApiContext() }; const key = actionContextKey(ctx);
  if (wbSys.key === key && wbSys.loading && wbPending) return wbPending;
  const seq = ++wbSeq;
  Object.assign(wbSys, { key, loading: true, loaded: false, items: [] });
  wbPending = integrationApi.get<{ items: ExtSystem[] }>('/ext-systems', fixed(ctx, { writeback: 1 })).then((r) => {
    if (seq === wbSeq && key === actionContextKey()) Object.assign(wbSys, { items: clean(r.items), loaded: true });
  }).finally(() => { if (seq === wbSeq) { wbSys.loading = false; wbPending = null; } });
  return wbPending;
}
export const writebackSystems = (): ExtSystem[] => (integrationApi.isReal() ? wbSys.key === actionContextKey() && wbSys.loaded ? wbSys.items : [] : mockWbSystems());

// ---------------- 执行记录 / 统计（dynamic 模块：GET /api/ontology/v1/action-runs） ----------------

export const dynamicApi = apiClient('dynamic');
/** mock ActionRun 的字段；后台多出来的（action、typeVersion、submittedAt、sysId …）和 null 不要 */
const RUN_KEYS = ['id', 'typeId', 'at', 'initiator', 'confirmer', 'approver', 'source', 'objKind', 'objId', 'objLabel', 'summary', 'status', 'ms', 'code', 'retries', 'idemKey', 'ruleId', 'alertId', 'extRef', 'reconcile'];
const pickRun = (r: Record<string, unknown>): ActionRun =>
  Object.fromEntries(RUN_KEYS.filter((k) => r[k] !== null && r[k] !== undefined).map((k) => [k, r[k]])) as unknown as ActionRun;

/** 真实执行记录由页面分页读取；仅显式 mock 模式提供演示记录。 */
export const runsOf = (typeId: string): ActionRun[] => api.isReal() ? [] : mockRuns(typeId);
/** 仅供 mock 页计算演示口径；真页使用后台聚合统计。 */
export function runStats(typeId: string) {
  const rows = runsOf(typeId);
  const c = (s: RunStatus) => rows.filter((r) => r.status === s).length;
  const ok = c('成功') + c('已对账');
  const pending = c('待确认') + c('审批中') + c('执行中');
  const done = rows.length - pending - c('已撤回');
  return { total: rows.length, ok, failed: c('失败'), unknown: c('结果未知'), pending, confirm: c('待确认'), rate: done ? ok / done : 0,
    avgMs: rows.length ? Math.round(rows.reduce((s, r) => s + r.ms, 0) / rows.length) : 0 };
}

export type ActionRunStats = ReturnType<typeof runStats> & { asOf?: string };
export const getActionRunStats = (typeId: string) => api.get<ActionRunStats>(`/action-types/${enc(typeId)}/stats`);
export async function listActionRuns(typeId: string, q: { page: number; size: number; status?: string }) {
  if (!dynamicApi.isReal()) throw new Error('执行记录需要启用真实动态接口');
  const ctx = { ...currentApiContext() };
  const result = await dynamicApi.get<PageResult<Record<string, unknown>>>('/action-runs', fixed(ctx, { actionType: typeId, scope: 'runs', page: q.page, size: q.size, status: opt(q.status) }));
  return { ...result, items: result.items.map(pickRun) };
}

// ---------------- 运行数据（action 模块：近期事件 / 运行健康 / 趋势 / 动作每日执行量） ----------------

export type EventRecord = Omit<MockEventRecord, 'outcome' | 'latencyMs'> & {
  outcome: MockEventRecord['outcome'] | '待投递'; latencyMs: number | null;
  source?: string; producerType?: string; producerObjectType?: string; traceId?: string;
  ingestedAt?: string; ontologyVersion?: string;
};
export interface EventQuery { range: '24h' | '7d'; outcome?: string; page: number; size: number }
/** 近期事件一页（range=7d 后台不设时间下限，同 mock「近 7 天」= 全部样本） */
export const listEvents = async (typeId: string, q: EventQuery) =>
  clean(await api.get<PageResult<EventRecord>>(`/event-types/${enc(typeId)}/events`, { query: { range: q.range, outcome: opt(q.outcome), page: q.page, size: q.size } }));
export const getEvent = async (id: string) => clean(await api.get<EventRecord>(`/events/${enc(id)}`));

export interface EventSimulation {
  testEventId: string | null; deliveredTo: string[]; payloadValid: boolean; message: string;
  checks: { name: string; matched: boolean | null; message: string }[];
}
export const simulateEvent = (typeId: string, view: string, cr?: string) =>
  api.post<EventSimulation>(`/event-types/${enc(typeId)}:simulate`, {}, { query: { view, cr } });
export interface EventDeliveryStatus {
  eventId: string; outcome: string; summary: string; total: number; page: number; size: number;
  items: EventDelivery[];
}
export interface EventDelivery {
  id: string; eventId: string; name: string; state: string; attempts: number; error: string | null;
  version: number; startedAt: string | null; finishedAt: string | null; isTest: boolean; objectId: string; idempotencyKey: string;
  result: {
    httpStatus?: number; actionId?: string; ruleId?: string; ruleVersion?: string; hitId?: string; alertId?: string;
    status?: string; actionStatus?: string; actionReason?: string; blockedByEventId?: string;
  } | null;
}
export const getEventDeliveries = (id: string, page = 1) =>
  api.get<EventDeliveryStatus>(`/events/${enc(id)}/deliveries`, { query: { page, size: 10 } });
export interface DeliveryHistory {
  id: string; kind: string; attempt: number; state: string; error: string | null; result: EventDelivery['result']; at: string; actor: string;
  detail: { decision?: string; reason?: string; evidence?: string; before?: { state: string; error: string | null }; previousError?: string | null };
}
export interface DeliveryDetail extends EventDelivery { history: PageResult<DeliveryHistory>; canManage: boolean; decisions: DeliveryDecision[] }
export type DeliveryDecision = 'received' | 'skip' | 'retry' | 'supplement';
export const listDeliveryExceptions = (typeId: string, state: string, kw: string, page = 1) =>
  api.get<PageResult<EventDelivery>>(`/event-types/${enc(typeId)}/delivery-exceptions`, { query: { state: state || undefined, kw, page, size: 10 } });
export const getDeliveryDetail = (id: string, page = 1) =>
  api.get<DeliveryDetail>(`/event-deliveries/${enc(id)}`, { query: { page, size: 10 } });
export const disposeDelivery = (id: string, body: { version: number; decision: DeliveryDecision; reason: string; evidence: string; authorized: true }) =>
  api.post<EventDelivery>(`/event-deliveries/${enc(id)}:dispose`, body);

export interface EventStats {
  weekly: number; total: number; dedup: number; failed: number; partial: number; pending: number; p95: number | null; asOf: string;
  daily: { date: string; value: number }[];
}
export const getEventStats = (typeId: string) => api.get<EventStats>(`/event-types/${enc(typeId)}/stats`);

export interface RunDaily { date: string; ok: number; failed: number; unknown: number }
export const getRunDaily = async (typeId: string, days = 14) =>
  (await api.get<{ items: RunDaily[] }>(`/action-types/${enc(typeId)}/stats/daily`, { query: { days } })).items;

export interface ActionTrialContext { objKind?: string; objId?: string; alertId?: string }
export const dryRunAction = (typeId: string, params: Record<string, unknown>, view: string, cr: string | undefined, options: RequestOptions, context: ActionTrialContext = {}) =>
  api.post<{ passed: boolean; checked: number; failed: { name: string; fail: string }[] }>(`/action-types/${enc(typeId)}:dry-run`, { params, ...context }, { ...options, query: { ...options.query, view, cr } });


// ---------------- 变更单写接口（static-r3 契约；VITE_PALANTIR_CR_WRITE=1 才用） ----------------

/** 前端开关：静态 static-r3 合并前保持关闭（src/env.d.ts 的声明由 PDE 补，这里先用类型断言读） */
export const CR_WRITE_FLAG = (import.meta.env as unknown as Record<string, string | undefined>).VITE_PALANTIR_CR_WRITE === '1';
/** 写库生效 = 开关开 + action 真接口。后台没起来（backend.down）也照样发请求、照样报错，不假装成功 */
export const crWriteOn = (): boolean => CR_WRITE_FLAG && api.isReal();

export type CRKind = 'action' | 'event';
export interface CRTargetIn { kind: CRKind; id: string; isNew?: boolean; name?: string }
type CROp = 'add' | 'update' | 'delete';
interface CRItemIn { key: string; op: CROp; before?: unknown; after?: unknown; type?: string; field?: string }
interface CRItemOut extends CRItemIn { target: { kind: string; id: string }; section: string; updatedBy?: string; updatedAt: string }
interface CRFull { id: string; title: string; owner: string; updatedAt: string; changes: number; lockVersion: number }
const crPath = (id: string, rest = '') => `/change-requests/${enc(id)}${rest}`;

/** POST /change-requests：发起（新建草稿，201）；目标在别的单里 409 TARGET_IN_OTHER_CR */
export const createCR = (body: { title: string; reason?: string; target: CRTargetIn }) => api.post<CRFull>('/change-requests', body);
/** POST /change-requests/{id}/targets：加入已有（幂等）；不是草稿 409 CR_STATE */
export const addCRTarget = (id: string, t: CRTargetIn) => api.post<CRFull>(crPath(id, '/targets'), t);
/** GET /change-requests/{id}/items：读回该目标（该段）的草稿行；section 省略时取该目标全部段 */
export const fetchCRItems = (id: string, q: { kind: CRKind; targetId: string; section?: string }) => api.get<CRItemOut[]>(crPath(id, '/items'), { query: q });
/** GET /change-requests/{id}：单头（这里只用 changes） */
export const fetchCR = (id: string) => api.get<CRFull>(crPath(id));
/** GET /change-requests/draft-targets：{单号: {kind: [目标编码]}} */
export const fetchDraftTargets = () => api.get<Record<string, Partial<Record<string, string[]>>>>('/change-requests/draft-targets');
/** GET /change-requests?status=草稿：不带分页参数时返回整个数组 */
export const fetchDraftCRs = () => api.get<CRFull[]>('/change-requests', { query: { status: '草稿' } });
/** PUT /change-requests/{id}/items：同一 (目标, section) 整段替换；不是草稿 409 CR_LOCKED */
export const saveCRItems = (id: string, body: { target: CRTargetIn; section: string; rows: CRItemIn[]; lockVersion: number }) =>
  api.put<{ id: string; changes: number; lockVersion: number; items: CRItemOut[] }>(crPath(id, '/items'), body);
/** POST /change-requests/{id}:submit：改动 0 → CR_EMPTY、阻断 → CR_BLOCKED、没有审核人 → CR_NO_REVIEWER */
export const submitCR = (id: string) => api.post<unknown>(crPath(id, ':submit'), {});
/** DELETE /change-requests/{id}/targets/{kind}/{targetId}：放弃本对象草稿（204） */
export const discardCRTarget = (id: string, kind: CRKind, targetId: string) => api.delete<void>(crPath(id, `/targets/${kind}/${enc(targetId)}`));

/** 每张单子库里的改动数（写库模式下横幅 / 提交确认用它，不再按前端计）；没有记录时用 DraftCR.changes + 本对象已保存改动 */
export const crChanges = reactive<Record<string, number>>({});

/** 库里新建 / 加入的单子登记进 DRAFTS（运行时，同 static-r3 的做法；useDraftView 的 allDrafts 读它） */
function registerCR(cr: CRFull) {
  if (typeof cr.changes === 'number') crChanges[cr.id] = cr.changes;
  registerDraft(cr);
}

/**
 * “发起变更”弹窗确认：开关开着时先在库里新建 / 加入，返回交给 useDraftView.start 的参数（单号以库为准，一律 { join }）；
 * 开关关着原样返回。失败抛 ApiError，页面提示后中止。
 */
export async function crStart(target: CRTargetIn, opts: { join?: string; title?: string; desc?: string }): Promise<{ join?: string; title?: string }> {
  if (!crWriteOn()) return opts;
  const cr = opts.join ? await addCRTarget(opts.join, target) : await createCR({ title: opts.title || '未命名变更', reason: opts.desc, target });
  registerCR(cr);
  return { join: cr.id };
}

/** 变更单写接口的错误提示：message（code），422 VALIDATION_FAILED 时带 details.fields 的字段名 */
export function crErrText(e: unknown): string {
  if (!isApiError(e)) return e instanceof Error ? e.message : String(e);
  const f = (e.details as { fields?: unknown } | null)?.fields;
  const names = Array.isArray(f) ? f.map((x) => (typeof x === 'string' ? x : (x as { field?: string; loc?: string })?.field ?? (x as { loc?: string })?.loc ?? '')).filter(Boolean)
    : f && typeof f === 'object' ? Object.keys(f) : [];
  return `${e.message || e.code}（${e.code}）${names.length ? `：${names.join('、')}` : ''}`;
}

/** 改动列表「类型」列【推断】：契约只定了 section 名，中文名按页签取 */
const SECTION_TYPE: Record<string, string> = { params: '参数', conds: '提交条件', edits: '本体修改', effects: '副作用', config: '写回配置', payload: '载荷字段', subs: '订阅方' };

/**
 * 给 useDraftEdits 的写库实现。保存：每个变了的 section 先 GET 读回库里的行，按 key 合并本次改动（revert 去掉），再整段 PUT
 * （不读回直接 PUT 会把刷新页面前保存的行冲掉）。
 */
export function crWriter(kind: CRKind, target: () => { id: string; name: string }): DraftWriter {
  const t = (): CRTargetIn => ({ kind, ...target() });
  return {
    on: crWriteOn,
    async save(crId, changes) {
      for (const c of changes) {
        const tg = t();
        const { lockVersion } = await fetchCR(crId);
        const old = await fetchCRItems(crId, { kind, targetId: tg.id, section: c.section });
        const rows = new Map<string, CRItemIn>(old.map(({ target: _t, section: _s, updatedBy: _u, updatedAt: _a, ...r }) => [r.key, r]));
        for (const r of c.rows as SectionChange[]) {
          if (r.op === 'revert') rows.delete(r.key);
          else rows.set(r.key, { key: r.key, op: r.op, before: r.before, after: r.after, type: SECTION_TYPE[c.section], field: r.key });
        }
        const r = await saveCRItems(crId, { target: tg, section: c.section, rows: [...rows.values()], lockVersion });
        crChanges[crId] = r.changes;
        const draft = DRAFTS.find((d) => d.id === crId);
        if (draft) draft.changes = r.changes;
      }
    },
    submit: async (crId) => { await submitCR(crId); },
    async discard(crId) {
      try { await discardCRTarget(crId, kind, target().id); }
      catch (e) { if (!isApiError(e) || e.status !== 404) throw e; }
      // 放弃后单子的改动数变了：读一次单头；读失败不影响放弃本身（已删库），只是改动数不更新
      try { crChanges[crId] = (await fetchCR(crId)).changes; } catch { delete crChanges[crId]; }
    },
    changes: (crId) => crChanges[crId],
    load: (crId, targetId) => fetchCRItems(crId, { kind, targetId }),
    errText: crErrText,
  };
}

/**
 * 刷新后恢复草稿登记（写库模式，进页面调一次）：库里草稿单的单头进 DRAFTS，返回本种类的 [单号, 目标编码]，页面逐个 start({ join, target }) 登记。
 * stale：前端登记表（mock DRAFT_TARGETS 复制）里有、库里已没有的（比如上次放弃过），页面用 forgetTarget 去掉。
 * 失败抛 ApiError（页面提示，不回退 mock 的登记以外的任何东西）。
 */
export async function crRestore(kind: CRKind): Promise<{ pairs: [string, string][]; inDb: (cr: string, id: string) => boolean }> {
  const [map, crs] = await Promise.all([fetchDraftTargets(), fetchDraftCRs()]);
  const pairs: [string, string][] = [];
  for (const cr of crs) {
    const ids = map[cr.id]?.[kind] ?? [];
    if (!ids.length) continue;
    registerCR(cr);
    ids.forEach((id) => pairs.push([cr.id, id]));
  }
  return { pairs, inDb: (cr, id) => !!map[cr]?.[kind]?.includes(id) };
}


export interface ActionUsedBy { ruleId: string; ruleName: string; trigger: string; mode: string; alerts: number; openAlerts: number }
export const getActionUsedBy = async (typeId: string) =>
  (await api.get<{ items: ActionUsedBy[] }>(`/action-types/${encodeURIComponent(typeId)}/used-by`)).items;
