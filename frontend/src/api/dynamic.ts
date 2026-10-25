/**
 * 动态本体：告警处理 + 动作中心。开关键 dynamic，前缀 ontology（docs/backend/dynamic-api.md §4，
 * 后台 backend/app/modules/dynamic/router.py，共 18 个接口）。
 *
 * 页面（pages/ops/Alerts.vue、Actions.vue）在 dynamicApi.isReal() 时走这里，否则保持原来的 mock 逻辑不变。
 * 字段名已和前端 mock 对齐（Alert / ActionRecord），这里只做类型收窄，不改值。
 */
import type { Alert, AlertStatus } from '@/mock/dynamic';
import type { ActionLog, ActionRecord, ActionStatus, SysId } from '@/mock/actions';
import { apiClient, type PageResult } from './http';

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
