/**
 * 动态本体：告警处理 + 动作中心。开关键 dynamic，前缀 ontology（docs/backend/dynamic-api.md §4，
 * 后台 backend/app/modules/dynamic/router.py，共 18 个接口）。
 *
 * 页面（pages/ops/Alerts.vue、Actions.vue）在 dynamicApi.isReal() 时走这里，否则保持原来的 mock 逻辑不变。
 * 字段名已和前端 mock 对齐（Alert / ActionRecord），这里只做类型收窄，不改值。
 */
import type { Alert, AlertStatus } from '@/mock/dynamic';
import type { ActionLog, ActionRecord, ActionStatus, SysId } from '@/mock/actions';
import { apiClient, currentApiContext, ApiError, API_BASE, type PageResult } from './http';

export const dynamicApi = apiClient('dynamic');
const api = dynamicApi;
const enc = encodeURIComponent;

// ---------------- 告警 ----------------

export type AlertTab = 'open' | 'doing' | 'closed' | 'all';
export interface AlertLogDto { time: string; text: string; who: string; abnormal?: boolean | null }
/** 后台 AlertOut：Alert + 关联动作（actionRef / actionStatus 同 Alerts.vue Row） */
export interface AlertDto extends Omit<Alert, 'storeId' | 'storeLabel'> {
  storeId?: string | null; storeLabel?: string | null;
  actionRef?: string | null; actionStatus?: string | null; actionId?: string | null;
}
export interface AlertRuleDto { id: string; name: string; version: string; trigger: string; condition: string; schedule: string; dedupe: string; action?: string; metric?: string; confirmer?: string }
export interface AlertCan { claim: boolean; transfer: boolean; close: boolean; startAction: boolean; reasons: Record<string, string> }
export interface AlertDetailDto extends AlertDto { rule: AlertRuleDto | null; recent7d: number; locked: boolean; log: AlertLogDto[]; can: AlertCan }
export interface AlertPageDto extends PageResult<AlertDto> { counts: Record<AlertTab, number> }
export interface AlertQuery { status: AlertTab; level?: string; ruleId?: string; kw?: string; page: number; size: number }

const opt = (v?: string) => (v && v !== '全部' ? v : undefined);

export const listAlerts = (q: AlertQuery) =>
  api.get<AlertPageDto>('/alerts', { query: { status: q.status, level: opt(q.level), ruleId: opt(q.ruleId), kw: q.kw?.trim() || undefined, page: q.page, size: q.size } });
export const getAlert = (id: string) => api.get<AlertDetailDto>(`/alerts/${enc(id)}`);
export const claimAlert = (id: string) => api.post<AlertDetailDto>(`/alerts/${enc(id)}:claim`);
export const batchClaimAlerts = (ids: string[]) => api.post<{ claimed: number; ids: string[] }>('/alerts:batch-claim', { ids });
export const transferAlert = (id: string, to: string) => api.post<AlertDetailDto>(`/alerts/${enc(id)}:transfer`, { to });
export const closeAlert = (id: string, as: '已解决' | '已忽略', reason: string) => api.post<AlertDetailDto>(`/alerts/${enc(id)}:close`, { as, reason });
export const batchCloseAlerts = (ids: string[], as: '已解决' | '已忽略', reason: string) =>
  api.post<{ closed: number; ids: string[] }>('/alerts:batch-close', { ids, as, reason });
export const startAlertAction = (id: string) => api.post<{ alert: AlertDetailDto; action: ActionRunDetailDto }>(`/alerts/${enc(id)}:start-action`);
export const listPeople = () => api.get<string[]>('/people');

// ---------------- 动作 ----------------

export type ActionTab = 'pending' | 'records' | 'unknown';
/** 后台 ActionRunOut（动作中心记录 source 为对象）。可空字段在页面上按 mock 的可选字段处理 */
export type ActionRunDto = Omit<ActionRecord, 'log' | 'failReason' | 'unknownReason' | 'reconcile' | 'retryOf' | 'retriedAs'> & {
  typeId: string; confirmer?: string | null;
  failReason?: string; unknownReason?: string; reconcile?: string; retryOf?: string; retriedAs?: string;
};
export interface ActionCan { confirm: boolean; withdraw: boolean; approve: boolean; resubmit: boolean; reconcile: boolean; reasons: Record<string, string> }
export interface ActionRunDetailDto extends ActionRunDto { log: ActionLog[]; can: ActionCan }
export interface ActionRunPageDto extends PageResult<ActionRunDto> { counts: Record<ActionTab, number>; statusCounts: Record<string, number> }
export interface ActionRunQuery { tab: ActionTab; status?: string; action?: string; sysId?: string; kw?: string; page: number; size: number }
export interface ActionMetaDto {
  actionNames: string[];
  actionSystems: { id: SysId; name: string }[];
  actionMeta: Record<string, { sysId: SysId; kind: string; version: string; approver: string; call: string }>;
}
export type ReconcileResult = '外部已成功' | '外部未执行' | '仍无法确认';

/** 后台 null → 前端可选字段 undefined（mock 里这些字段是 `?:`，页面用 `??` / 真假判断） */
const clean = <T extends object>(o: T): T => {
  const r = { ...o } as Record<string, unknown>;
  for (const k of ['failReason', 'unknownReason', 'reconcile', 'retryOf', 'retriedAs']) if (r[k] === null) r[k] = undefined;
  if (r.source && typeof r.source === 'object') {
    const s = { ...(r.source as Record<string, unknown>) };
    for (const k of Object.keys(s)) if (s[k] === null) s[k] = undefined;
    r.source = s;
  }
  return r as T;
};

export async function listActionRuns(q: ActionRunQuery): Promise<ActionRunPageDto> {
  const r = await api.get<ActionRunPageDto>('/action-runs', {
    query: { tab: q.tab, status: opt(q.status), action: opt(q.action), sysId: opt(q.sysId), kw: q.kw?.trim() || undefined, page: q.page, size: q.size },
  });
  return { ...r, items: r.items.map(clean) };
}
export const getActionRun = async (id: string) => clean(await api.get<ActionRunDetailDto>(`/action-runs/${enc(id)}`));
export const getActionMeta = () => api.get<ActionMetaDto>('/actions/meta');
export const confirmActionRun = async (id: string) => clean(await api.post<ActionRunDetailDto>(`/action-runs/${enc(id)}:confirm`));
export const withdrawActionRun = async (id: string, reason: string) => clean(await api.post<ActionRunDetailDto>(`/action-runs/${enc(id)}:withdraw`, { reason }));
export const approveActionRun = async (id: string) => clean(await api.post<ActionRunDetailDto>(`/action-runs/${enc(id)}:approve`));
export const resubmitActionRun = async (id: string) => {
  const r = await api.post<{ id: string; idemKey: string; action: ActionRunDetailDto; original: ActionRunDetailDto }>(`/action-runs/${enc(id)}:resubmit`);
  return { ...r, action: clean(r.action), original: clean(r.original) };
};
export const reconcileActionRun = async (id: string, result: ReconcileResult, note: string) =>
  clean(await api.post<ActionRunDetailDto>(`/action-runs/${enc(id)}:reconcile`, { result, note }));
/** 暂存动作（规则 / 问数 / 推演 / 手动入口用；动作中心、告警页本身没有暂存入口） */
export const stageActionRun = async (body: Record<string, unknown>) => clean(await api.post<ActionRunDetailDto>('/action-runs:stage', body));

export type { AlertStatus, ActionStatus };

// ---------------- 规则与自动化（pages/ontology/Rules.vue；后台 rules_router.py） ----------------

import type { RuleDef, AutoStage, Hit } from '@/mock/rules';

/** 后台 RuleDef + 显示状态（草稿中 / 审核中 / 已发布 / 已停用）+ 所在变更单；详情多带 draft（有草稿时） */
export interface RuleDto extends RuleDef { displayStatus: string; cr?: string | null; draft?: RuleDef | null }
export interface RuleQuery { trigger?: string; status?: string; kw?: string; page: number; size: number }
export interface RuleMetaDto { confirmers: string[]; mappingSources: string[]; triggers: string[]; statuses: string[] }
export interface RuleVersionDto { ver: number; status: string; by: string; at: string; change: string; cr?: string | null }
export interface TimelineDto { time: string; text: string; desc: string; status: string }
export interface RuleHitDetailDto extends Omit<Hit, 'alert'> { alert: AlertDetailDto | null; linkedAction: ActionRunDto | null; timeline: TimelineDto[] }
export interface ReplaySampleDto { key: string; at: string; objKind: string; objId: string; objLabel: string; storeId?: string | null; storeLabel?: string | null; value: string; diff: string }
export interface ReplayDto { actual: number; would: number; added: number; removed: number; deduped: number; dedupeReason: string; samples: ReplaySampleDto[] }

/** 后台 null → 前端可选字段 undefined（Hit.storeId / alertId 等在 mock 里是 `?:`） */
const undef = <T extends object>(o: T): T => {
  const r = { ...o } as Record<string, unknown>;
  for (const k of Object.keys(r)) if (r[k] === null) r[k] = undefined;
  return r as T;
};

/**
 * PUT / PATCH：apiClient（http.ts，共享核心）目前只有 get / post，这里按 request() 同样的约定补一个最小实现：
 * 同样受开关约束、带 X-Demo-As / ?ver / ?previewAs、错误体转 ApiError。PDE 给 apiClient 加 put / patch 后换成 api.put / api.patch。
 */
async function write<T>(method: 'PUT' | 'PATCH', path: string, body: unknown): Promise<T> {
  if (!api.isReal()) throw new ApiError(0, { code: 'MOCK_ONLY', message: '开关 dynamic 未切到真接口', details: { key: 'dynamic' } });
  const ctx = currentApiContext();
  const headers: Record<string, string> = { Accept: 'application/json', 'Content-Type': 'application/json' };
  if (ctx.demoAs) headers['X-Demo-As'] = ctx.demoAs;
  const qs = new URLSearchParams();
  if (ctx.ver) qs.set('ver', ctx.ver);
  if (ctx.previewAs) qs.set('previewAs', ctx.previewAs);
  const url = `${API_BASE}/${api.prefix}/v1${path}${qs.toString() ? `?${qs}` : ''}`;
  let res: Response;
  try { res = await fetch(url, { method, headers, body: JSON.stringify(body) }); } catch (e) {
    throw new ApiError(0, { code: 'NETWORK_ERROR', message: `无法连接后台：${(e as Error)?.message ?? e}` });
  }
  if (!res.ok) {
    let b: { code?: string; message?: string; details?: unknown; requestId?: string | null } | null = null;
    try { b = await res.json(); } catch { /* 非 JSON */ }
    throw new ApiError(res.status, { code: b?.code ?? `HTTP_${res.status}`, message: String(b?.message ?? res.statusText), details: b?.details, requestId: b?.requestId ?? res.headers.get('X-Request-ID') });
  }
  return (await res.json()) as T;
}

export const listRules = (q: RuleQuery) =>
  api.get<PageResult<RuleDto>>('/rules', { query: { trigger: opt(q.trigger), status: opt(q.status), kw: q.kw?.trim() || undefined, page: q.page, size: q.size } });
export const getRulesMeta = () => api.get<RuleMetaDto>('/rules/meta');
export const getRule = (id: string) => api.get<RuleDto>(`/rules/${enc(id)}`);
export const saveRuleDraft = (id: string, body: { cr?: string; autoStage: AutoStage; thresholds: string[] }) =>
  write<RuleDto>('PUT', `/rules/${enc(id)}/draft`, body);
export async function listRuleHits(id: string, range: '7d' | '30d', page: number, size: number) {
  const r = await api.get<PageResult<Hit>>(`/rules/${enc(id)}/hits`, { query: { range, page, size } });
  return { ...r, items: r.items.map(undef) };
}
export async function getRuleHit(id: string) {
  const r = await api.get<RuleHitDetailDto>(`/rule-hits/${enc(id)}`);
  return { ...undef(r), linkedAction: r.linkedAction ? clean(r.linkedAction) : null, alert: r.alert, timeline: r.timeline };
}
export const replayRule = (id: string, range: '7d' | '30d', version: 'current' | 'draft') =>
  api.post<ReplayDto>(`/rules/${enc(id)}:replay`, { range, version });
export const listRuleVersions = (id: string) => api.get<RuleVersionDto[]>(`/rules/${enc(id)}/versions`);
/** 规则暂存过的动作（动作页“已暂存的动作”）：动作中心全部记录按 ruleId 过滤 */
export async function listRuleActionRuns(ruleId: string) {
  const r = await api.get<ActionRunPageDto>('/action-runs', { query: { ruleId, page: 1, size: 100 } });
  return { ...r, items: r.items.map(clean) };
}

// ---------------- 生命周期（pages/ontology/parts/object/ObjBehaviorLifecycle.vue；后台 rules_router.py L1–L6） ----------------

import type { Lifecycle, LcDraft, LcIllegal, LcChange } from '@/mock/lifecycle';

/** L1：当前（或 ?ver= 历史版本发布时生效的）状态机；view=draft 时带 draft = { cr, edits } */
export interface LifecycleDto extends Lifecycle { draft: { cr: string | null; edits: LcDraft } | null; readonly?: boolean }

export const getLifecycle = (objectId: string, draftCr?: string) =>
  api.get<LifecycleDto>(`/object-types/${enc(objectId)}/lifecycle`, { query: draftCr ? { view: 'draft', cr: draftCr } : {} });
export function listLifecycleChanges(objectId: string, illegal: boolean, page: number, size: number) {
  return api.get<PageResult<LcChange>>(`/object-types/${enc(objectId)}/lifecycle/changes`, { query: { illegal: illegal ? 1 : 0, page, size } });
}
export const listLifecycleIllegal = (objectId: string) => api.get<LcIllegal[]>(`/object-types/${enc(objectId)}/lifecycle/illegal`);
/** L3–L6：整份草稿改动（新增状态 / 新增·修改·删除迁移）一起保存 */
export const saveLifecycleDraft = (objectId: string, cr: string, edits: LcDraft) =>
  write<{ cr: string; edits: LcDraft }>('PUT', `/object-types/${enc(objectId)}/lifecycle/draft`, { cr, ...edits });
