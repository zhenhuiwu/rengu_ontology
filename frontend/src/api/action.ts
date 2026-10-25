/**
 * 动作类型 / 事件类型（action 模块，开关键 action，前缀 /api/ontology/v1；docs/backend/action-event-api.md §3.1、§3.2，
 * 后台 backend/app/modules/action|event/router.py）。
 *
 * 页面（pages/ontology/ActionTypes.vue、EventTypes.vue）的模板和草稿编辑（useDraftEdits）都是同步读 mock 的
 * paramsOf()/condsOf()/… ，所以真接口这里做成「快照」：actionApi.isReal() 时，进页面（以及 ?ver= 变化时）把
 * 全部类型的详情 + 各页签数据一次取回放进响应式缓存，页面换成调这里同名的取数函数。开关关着时这些函数直接返回 mock。
 * 左侧列表的分组 / 关键字筛选和分页另走列表接口（listActionTypes / listEventTypes）。
 * 字段名、取值已和 mock 对齐（ActionType / EventType / ActionParam …），这里只把 null 收成可选字段。
 *
 * 不在本文件：执行记录 / 统计（GET /action-runs 归 dynamic）、近期事件（/events）、草稿写入（PUT /change-requests/…，versioning）。
 */
import { reactive } from 'vue';
import {
  ACTION_TYPES, EVENT_TYPES, ACTION_VERSION_NOTES, EVENT_VERSION_NOTES, paramsOf as mockParams, condsOf as mockConds,
  writebackOf as mockWriteback, payloadOf as mockPayload, subscribersOf as mockSubs, versionsOf as mockVersions,
  type ActionType, type ActionParam, type SubmitCond, type OntologyEdit, type SideEffect, type EventType, type PayloadField,
  type Subscriber, type TypeVersion, writebackSystems as mockWbSystems,
} from '@/mock/actionEvent';
import type { ExtSystem } from '@/mock/extsys';
import { integrationApi, listExtSystems } from './integration';
import { apiClient, currentApiContext, type PageResult } from './http';

export const actionApi = apiClient('action');
const api = actionApi;
const enc = encodeURIComponent;
const ALL = 200; // 后台 size 上限（MAX_PAGE_SIZE）；动作 17 个、事件 15 个，一页取完

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

interface Snap {
  /** 'idle' | 'loading' | 'ready' | 'error'；ver = 取数时的 ?ver= */
  state: 'idle' | 'loading' | 'ready' | 'error';
  ver: string;
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
export const snap = reactive<Snap>({ state: 'idle', ver: '', actions: [], params: {}, conds: {}, writeback: {}, actionVersions: {}, events: [], payload: {}, subs: {}, eventVersions: {} });
const on = () => api.isReal() && snap.state === 'ready';

const items = async <T>(path: string, query: Record<string, number> = {}) => clean((await api.get<{ items: T[] }>(path, { query })).items);
const allIds = async (path: string) => (await api.get<PageResult<{ id: string }>>(path, { query: { page: 1, size: ALL } })).items.map((x) => x.id);

async function loadActions() {
  const ids = await allIds('/action-types');
  const rows = await Promise.all(ids.map(async (id) => {
    const p = `/action-types/${enc(id)}`;
    const [detail, params, conds, wb, vers] = await Promise.all([
      api.get<ActionType>(p), items<ActionParam>(`${p}/params`, { page: 1, size: ALL }), items<SubmitCond>(`${p}/submission-criteria`),
      api.get<WritebackDto>(`${p}/writeback`), items<TypeVersion>(`${p}/versions`),
    ]);
    // mock 里本体内部动作写的是 sysId: null（草稿编辑表单也用 null），这里保留 null，不收成 undefined
    return { id, detail: { ...clean(detail), sysId: detail.sysId ?? null }, params, conds, wb: clean(wb), vers };
  }));
  snap.actions = rows.map((r) => r.detail);
  snap.params = Object.fromEntries(rows.map((r) => [r.id, r.params]));
  snap.conds = Object.fromEntries(rows.map((r) => [r.id, r.conds]));
  snap.writeback = Object.fromEntries(rows.map((r) => [r.id, r.wb]));
  snap.actionVersions = Object.fromEntries(rows.map((r) => [r.id, r.vers]));
}

async function loadEvents() {
  const ids = await allIds('/event-types');
  const rows = await Promise.all(ids.map(async (id) => {
    const p = `/event-types/${enc(id)}`;
    const [detail, payload, subs, vers] = await Promise.all([
      api.get<EventType>(p), items<PayloadField & { envelope?: boolean }>(`${p}/payload-fields`, { page: 1, size: ALL }), items<Subscriber>(`${p}/subscribers`), items<TypeVersion>(`${p}/versions`),
    ]);
    return { id, detail: clean(detail), payload: payload.map(({ envelope: _e, ...f }) => f), subs, vers };
  }));
  snap.events = rows.map((r) => r.detail);
  snap.payload = Object.fromEntries(rows.map((r) => [r.id, r.payload]));
  snap.subs = Object.fromEntries(rows.map((r) => [r.id, r.subs]));
  snap.eventVersions = Object.fromEntries(rows.map((r) => [r.id, r.vers]));
}

let pending: Promise<void> | null = null;
/** 取快照（动作 + 事件一起，两个页面共用）。?ver= 变了重取（请求本身由 http.ts 自动带 ver）；失败抛 ApiError，页面自己提示。 */
export function loadSnapshot(ver = currentApiContext().ver ?? ''): Promise<void> {
  if (!api.isReal()) return Promise.resolve();
  if (snap.ver === ver && (snap.state === 'ready' || (snap.state === 'loading' && pending))) return pending ?? Promise.resolve();
  snap.state = 'loading'; snap.ver = ver;
  pending = Promise.all([loadActions(), loadEvents()]).then(() => { if (snap.ver === ver) snap.state = 'ready'; }, (e) => { snap.state = 'error'; pending = null; throw e; });
  return pending;
}

// ---------------- 页面用的取数函数（真接口快照 / mock） ----------------

export const actionTypes = (): ActionType[] => (api.isReal() ? snap.actions : ACTION_TYPES);
export const paramsOf = (a: ActionType): ActionParam[] => (on() ? snap.params[a.id] ?? [] : mockParams(a));
export const condsOf = (a: ActionType): SubmitCond[] => (on() ? snap.conds[a.id] ?? [] : mockConds(a));
export const writebackOf = (a: ActionType): { edits: OntologyEdit[]; effects: SideEffect[] } =>
  (on() ? snap.writeback[a.id] ?? { edits: [], effects: [] } : mockWriteback(a));
export const actionVersionsOf = (a: ActionType): TypeVersion[] => (on() ? snap.actionVersions[a.id] ?? [] : mockVersions(a.version, a.owner, ACTION_VERSION_NOTES));

export const eventTypes = (): EventType[] => (api.isReal() ? snap.events : EVENT_TYPES);
export const payloadOf = (e: EventType): PayloadField[] => (on() ? snap.payload[e.id] ?? [] : mockPayload(e));
export const subscribersOf = (e: EventType): Subscriber[] => (on() ? snap.subs[e.id] ?? [] : mockSubs(e));
export const eventVersionsOf = (e: EventType): TypeVersion[] => (on() ? snap.eventVersions[e.id] ?? [] : mockVersions(e.version, e.owner, EVENT_VERSION_NOTES));

// ---------------- 写回 tab 的外部系统下拉（integration 模块：GET /api/data/v1/ext-systems?writeback=1） ----------------

/**
 * 可写回的外部系统（生产环境且未停用）。integration 开关开着时取真接口（每次进动作类型页重取，外部系统页新建 / 编辑后能看到），
 * 第一次取到之前 / 开关关着时用 mock writebackSystems()。
 */
export const wbSys = reactive<{ loading: boolean; loaded: boolean; items: ExtSystem[] }>({ loading: false, loaded: false, items: [] });
export function loadWritebackSystems(): Promise<void> {
  if (!integrationApi.isReal() || wbSys.loading) return Promise.resolve();
  wbSys.loading = true;
  return listExtSystems({ writeback: true }).then(
    (items) => { wbSys.items = items; wbSys.loaded = true; },
  ).finally(() => { wbSys.loading = false; });
}
export const writebackSystems = (): ExtSystem[] => (integrationApi.isReal() && wbSys.loaded ? wbSys.items : mockWbSystems());
