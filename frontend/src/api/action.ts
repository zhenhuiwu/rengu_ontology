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
 * 执行记录 / 统计：dynamic 开关开着时取 GET /api/ontology/v1/action-runs?actionType=&scope=runs（dynamic 的接口），按动作类型整份缓存，
 * 统计按 mock runStats() 同样的口径在前端算；取不到 / 开关关着时用 mock runsOf()。
 * 后台没起来（fallback.ts 的 backend.down）时以下取数函数都回退 mock，loadSnapshot / loadRuns / loadWritebackSystems 不再发请求。
 * 运行数据（action 开关，palantir-r2，文档 §12）：近期事件 GET /event-types/{id}/events、运行健康 + 近 14 天事件数
 * GET /event-types/{id}/stats、动作近 14 天执行量 GET /action-types/{id}/stats/daily；页面按选中类型取，取不到 / 后台没起来用 mock。
 * 变更单写库（palantir-cr，静态 static-r3 handoff/static.md「变更单写接口契约」）：VITE_PALANTIR_CR_WRITE=1 且 action 为真接口时，
 * 动作 / 事件类型页的“发起变更 / 加入”“保存草稿”“提交审核”“放弃草稿”先写库（目标 kind = action / event，id = 类型编码），
 * 失败弹错误、页面不变，不回退 mock、不调 fallBackIfDown。开关关着时页面行为同以前（不发请求）。
 */
import { reactive } from 'vue';
import {
  ACTION_TYPES, EVENT_TYPES, ACTION_VERSION_NOTES, EVENT_VERSION_NOTES, paramsOf as mockParams, condsOf as mockConds,
  writebackOf as mockWriteback, payloadOf as mockPayload, subscribersOf as mockSubs, versionsOf as mockVersions,
  type ActionType, type ActionParam, type SubmitCond, type OntologyEdit, type SideEffect, type EventType, type PayloadField,
  type Subscriber, type TypeVersion, writebackSystems as mockWbSystems, runsOf as mockRuns, type ActionRun, type RunStatus,
  type EventRecord,
} from '@/mock/actionEvent';
import type { ExtSystem } from '@/mock/extsys';
import { DRAFTS, type DraftCR } from '@/mock/releases';
import type { DraftWriter, SectionChange } from '@/pages/ontology/useDraftEdits';
import { integrationApi, listExtSystems } from './integration';
import { apiClient, currentApiContext, isApiError, type PageResult } from './http';
import { backend } from './fallback';

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
const on = () => api.isReal() && !backend.down && snap.state === 'ready';

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
  if (!api.isReal() || backend.down) return Promise.resolve();
  if (snap.ver === ver && (snap.state === 'ready' || (snap.state === 'loading' && pending))) return pending ?? Promise.resolve();
  snap.state = 'loading'; snap.ver = ver;
  pending = Promise.all([loadActions(), loadEvents()]).then(() => { if (snap.ver === ver) snap.state = 'ready'; }, (e) => { snap.state = 'error'; pending = null; throw e; });
  return pending;
}

// ---------------- 页面用的取数函数（真接口快照 / mock） ----------------

export const actionTypes = (): ActionType[] => (api.isReal() && !backend.down ? snap.actions : ACTION_TYPES);
export const paramsOf = (a: ActionType): ActionParam[] => (on() ? snap.params[a.id] ?? [] : mockParams(a));
export const condsOf = (a: ActionType): SubmitCond[] => (on() ? snap.conds[a.id] ?? [] : mockConds(a));
export const writebackOf = (a: ActionType): { edits: OntologyEdit[]; effects: SideEffect[] } =>
  (on() ? snap.writeback[a.id] ?? { edits: [], effects: [] } : mockWriteback(a));
export const actionVersionsOf = (a: ActionType): TypeVersion[] => (on() ? snap.actionVersions[a.id] ?? [] : mockVersions(a.version, a.owner, ACTION_VERSION_NOTES));

export const eventTypes = (): EventType[] => (api.isReal() && !backend.down ? snap.events : EVENT_TYPES);
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
  if (!integrationApi.isReal() || backend.down || wbSys.loading) return Promise.resolve();
  wbSys.loading = true;
  return listExtSystems({ writeback: true }).then(
    (items) => { wbSys.items = items; wbSys.loaded = true; },
  ).finally(() => { wbSys.loading = false; });
}
export const writebackSystems = (): ExtSystem[] => (integrationApi.isReal() && !backend.down && wbSys.loaded ? wbSys.items : mockWbSystems());

// ---------------- 执行记录 / 统计（dynamic 模块：GET /api/ontology/v1/action-runs） ----------------

export const dynamicApi = apiClient('dynamic');
/** mock ActionRun 的字段；后台多出来的（action、typeVersion、submittedAt、sysId …）和 null 不要 */
const RUN_KEYS = ['id', 'typeId', 'at', 'initiator', 'confirmer', 'approver', 'source', 'objKind', 'objId', 'objLabel', 'summary', 'status', 'ms', 'code', 'retries', 'idemKey', 'ruleId', 'alertId', 'extRef', 'reconcile'];
const pickRun = (r: Record<string, unknown>): ActionRun =>
  Object.fromEntries(RUN_KEYS.filter((k) => r[k] !== null && r[k] !== undefined).map((k) => [k, r[k]])) as unknown as ActionRun;

/** 按动作类型缓存全部执行记录（每个类型 100 多条，一次取完；顺序 = 后台顺序，和 mock 一致） */
export const runsSnap = reactive<{ byType: Record<string, ActionRun[]>; loading: Record<string, boolean> }>({ byType: {}, loading: {} });
export async function loadRuns(typeId: string, force = false): Promise<void> {
  if (!dynamicApi.isReal() || backend.down || runsSnap.loading[typeId] || (!force && runsSnap.byType[typeId])) return;
  runsSnap.loading[typeId] = true;
  try {
    const rows: ActionRun[] = [];
    for (let page = 1; ; page++) {
      const r = await dynamicApi.get<PageResult<Record<string, unknown>>>('/action-runs', { query: { actionType: typeId, scope: 'runs', page, size: ALL } });
      rows.push(...r.items.map(pickRun));
      if (rows.length >= r.total || !r.items.length) break;
    }
    runsSnap.byType[typeId] = rows;
  } finally { runsSnap.loading[typeId] = false; }
}
export const runsOf = (typeId: string): ActionRun[] =>
  (dynamicApi.isReal() && !backend.down && runsSnap.byType[typeId] ? runsSnap.byType[typeId] : mockRuns(typeId));
/** 同 mock runStats() 的口径（只是换成上面的 runsOf） */
export function runStats(typeId: string) {
  const rows = runsOf(typeId);
  const c = (s: RunStatus) => rows.filter((r) => r.status === s).length;
  const ok = c('成功') + c('已对账');
  const pending = c('待确认') + c('审批中') + c('执行中');
  const done = rows.length - pending - c('已撤回');
  return { total: rows.length, ok, failed: c('失败'), unknown: c('结果未知'), pending, confirm: c('待确认'), rate: done ? ok / done : 0,
    avgMs: rows.length ? Math.round(rows.reduce((s, r) => s + r.ms, 0) / rows.length) : 0 };
}

// ---------------- 运行数据（action 模块：近期事件 / 运行健康 / 趋势 / 动作每日执行量） ----------------

export interface EventQuery { range: '24h' | '7d'; outcome?: string; page: number; size: number }
/** 近期事件一页（range=7d 后台不设时间下限，同 mock「近 7 天」= 全部样本） */
export const listEvents = async (typeId: string, q: EventQuery) =>
  clean(await api.get<PageResult<EventRecord>>(`/event-types/${enc(typeId)}/events`, { query: { range: q.range, outcome: opt(q.outcome), page: q.page, size: q.size } }));

export interface EventStats {
  weekly: number; total: number; dedup: number; failed: number; partial: number; p95: number; asOf: string;
  daily: { date: string; value: number }[];
}
export const getEventStats = (typeId: string) => api.get<EventStats>(`/event-types/${enc(typeId)}/stats`);

export interface RunDaily { date: string; ok: number; failed: number; unknown: number }
export const getRunDaily = async (typeId: string, days = 14) =>
  (await api.get<{ items: RunDaily[] }>(`/action-types/${enc(typeId)}/stats/daily`, { query: { days } })).items;


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
/** GET /change-requests/{id}/items：读回该目标该段的草稿行 */
export const fetchCRItems = (id: string, q: { kind: CRKind; targetId: string; section: string }) => api.get<CRItemOut[]>(crPath(id, '/items'), { query: q });
/** PUT /change-requests/{id}/items：同一 (目标, section) 整段替换；不是草稿 409 CR_LOCKED */
export const saveCRItems = (id: string, body: { target: CRTargetIn; section: string; rows: CRItemIn[] }) =>
  api.put<{ id: string; changes: number; lockVersion: number; items: CRItemOut[] }>(crPath(id, '/items'), body);
/** POST /change-requests/{id}:submit：改动 0 → CR_EMPTY、阻断 → CR_BLOCKED、没有审核人 → CR_NO_REVIEWER */
export const submitCR = (id: string) => api.post<unknown>(crPath(id, ':submit'), {});
/** DELETE /change-requests/{id}/targets/{kind}/{targetId}：放弃本对象草稿（204） */
export const discardCRTarget = (id: string, kind: CRKind, targetId: string) => api.delete<void>(crPath(id, `/targets/${kind}/${enc(targetId)}`));

/** 库里新建 / 加入的单子登记进 DRAFTS（运行时，同 static-r3 的做法；useDraftView 的 allDrafts 读它） */
function registerDraft(cr: CRFull) {
  const row: DraftCR = { id: cr.id, title: cr.title, owner: cr.owner, updatedAt: cr.updatedAt, changes: DRAFTS.find((d) => d.id === cr.id)?.changes ?? 0 };
  const i = DRAFTS.findIndex((d) => d.id === cr.id);
  if (i >= 0) DRAFTS.splice(i, 1, row); else DRAFTS.unshift(row);
}

/**
 * “发起变更”弹窗确认：开关开着时先在库里新建 / 加入，返回交给 useDraftView.start 的参数（单号以库为准，一律 { join }）；
 * 开关关着原样返回。失败抛 ApiError，页面提示后中止。
 */
export async function crStart(target: CRTargetIn, opts: { join?: string; title?: string }): Promise<{ join?: string; title?: string }> {
  if (!crWriteOn()) return opts;
  const cr = opts.join ? await addCRTarget(opts.join, target) : await createCR({ title: opts.title || '未命名变更', target });
  registerDraft(cr);
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
        const old = await fetchCRItems(crId, { kind, targetId: tg.id, section: c.section });
        const rows = new Map<string, CRItemIn>(old.map(({ target: _t, section: _s, updatedBy: _u, updatedAt: _a, ...r }) => [r.key, r]));
        for (const r of c.rows as SectionChange[]) {
          if (r.op === 'revert') rows.delete(r.key);
          else rows.set(r.key, { key: r.key, op: r.op, before: r.before, after: r.after, type: SECTION_TYPE[c.section], field: r.key });
        }
        await saveCRItems(crId, { target: tg, section: c.section, rows: [...rows.values()] });
      }
    },
    submit: async (crId) => { await submitCR(crId); },
    discard: (crId) => discardCRTarget(crId, kind, target().id),
    errText: crErrText,
  };
}
