/**
 * 问数（qa）工作台接口。开关键 qa，前缀 /api/qa/v1（docs/backend/qa-api.md 第 3 节 W1–W17）。
 * 开关没切到真接口时页面照旧走 src/mock，这里的函数不会被调用。
 */
import type { QaSession, QaFavorite, QaPage, QaDemoTurn } from '@/mock/qa';
import { apiClient, currentApiContext, ApiError, API_BASE, type ApiErrorBody, type PageResult } from './http';

const api = apiClient('qa');
export const qaReal = () => api.isReal();

/**
 * PATCH / PUT / DELETE 走 apiClient（PDE 215819d 补的 put / patch / delete）。
 * W11 导出要拿文件（Blob + Content-Disposition 文件名），apiClient 只解析 JSON，暂留这个 GET 下载小工具；
 * 规则同 http.ts request：同一开关把关、X-Demo-As、自动带 ?ver / ?previewAs、统一错误体 → ApiError（待 apiClient 有下载方法后改回，已报 PDE）。
 */
async function download(path: string, query?: Record<string, string>): Promise<Response> {
  if (!api.isReal()) throw new ApiError(0, { code: 'MOCK_ONLY', message: '开关 qa 未切到真接口', details: { key: 'qa' } });
  const ctx = currentApiContext();
  const qs = new URLSearchParams(query);
  if (ctx.ver) qs.set('ver', ctx.ver);
  if (ctx.previewAs) qs.set('previewAs', ctx.previewAs);
  const headers: Record<string, string> = {};
  if (ctx.demoAs) headers['X-Demo-As'] = ctx.demoAs;
  const url = `${API_BASE}/${api.prefix}/v1${path}${qs.size ? `?${qs}` : ''}`;
  let res: Response;
  try {
    res = await fetch(url, { headers });
  } catch (e) {
    throw new ApiError(0, { code: 'NETWORK_ERROR', message: `无法连接后台：${(e as Error)?.message ?? e}` });
  }
  if (!res.ok) {
    let b: Partial<ApiErrorBody> | null = null;
    try { b = (await res.json()) as Partial<ApiErrorBody>; } catch { /* 非 JSON */ }
    throw new ApiError(res.status, { code: typeof b?.code === 'string' ? b.code : `HTTP_${res.status}`, message: String(b?.message ?? res.statusText), details: b?.details, requestId: b?.requestId ?? null });
  }
  return res;
}

/** W3 会话详情：轮次快照形状同 QA_DEMO_TURNS；链路新生成的回答是 card='table' + text（后台 chain.py） */
export interface QaChainTable { id: string; title: string; columns: { key: string; title: string; align?: string }[]; items: Record<string, unknown>[]; total: number; summary: string; paged: boolean }
export interface QaChainAnswer { card: 'table'; text: string; dataAsOf?: string; basis?: Record<string, string>; table: QaChainTable }
/** applied：越权轮次已提交待审批的申请（W16 之后 W3 返回）；vote：这一轮的赞踩（W12）；主卡 answer.caliber：W9 切过的口径 */
export type QaRealTurn =
  | (QaDemoTurn & { applied?: boolean; vote?: '' | 'up' | 'down' })
  | { id: string; kind: 'answer'; time?: string; question: string; answer: QaChainAnswer }
  | { id: string; kind: 'empty'; time?: string; question: string; lead: string; bold: string; tail: string; tag: string; retry?: undefined };
export interface QaSessionDetail extends QaSession { scope: string | null; caliber: string; ownerId: string; turns: QaRealTurn[] }
export interface QaOption { value: string; label: string }
export interface QaSessionOptions { scopes: QaOption[]; calibers: QaOption[] }
export interface QaAskOut { turnId: string; streamUrl: string; turn: QaRealTurn }

export const listSessions = (q: { q?: string; range?: string; page: number; size: number }) =>
  api.get<PageResult<QaSession>>('/sessions', { query: { ...q, range: q.range === 'all' ? undefined : q.range } });
export const getSession = (sid: string) => api.get<QaSessionDetail>(`/sessions/${encodeURIComponent(sid)}`);
export const createSession = (body: { scope: string; caliber: string }) => api.post<QaSessionDetail>('/sessions', body);
export const patchSession = (sid: string, body: { title?: string; scope?: string; caliber?: string }) =>
  api.patch<QaSessionDetail>(`/sessions/${encodeURIComponent(sid)}`, body);
export const deleteSession = (sid: string) => api.delete<void>(`/sessions/${encodeURIComponent(sid)}`);
export const askTurn = (sid: string, question: string) =>
  api.post<QaAskOut>(`/sessions/${encodeURIComponent(sid)}/turns`, { question, refs: [] });
export const listFavorites = (q: { q?: string; page: number; size: number }) => api.get<PageResult<QaFavorite>>('/favorites', { query: q });
export const deleteFavorite = (id: string) => api.delete<void>(`/favorites/${encodeURIComponent(id)}`);
/** P1 简版（左栏贴底、「我的页面 · 全部」抽屉） */
export const listPagesBrief = (q: { kw?: string; page: number; size: number }) =>
  api.get<PageResult<QaPage>>('/pages', { query: { ...q, view: 'brief' } });
/** W16 申请数据权限；duration 为天数 30 | 90 | 180（前端单选文字转数字） */
export interface QaPermRequestIn { scope: string; object: string; reason: string; duration: number; sourceTurnId: string | null }
export const createPermissionRequest = (body: QaPermRequestIn) =>
  api.post<{ id: string; status: string; approver: string | null }>('/permission-requests', body);
export const getSessionOptions = () => api.get<QaSessionOptions>('/session-options');

/** W9 澄清条切换口径：返回这一轮的 Answer（带 caliber） */
export type QaCaliberKey = 'ex' | 'in' | 'both';
export const recalcTurn = (tid: string, caliber: QaCaliberKey) =>
  api.post<Record<string, unknown> & { caliber: QaCaliberKey }>(`/turns/${encodeURIComponent(tid)}:recalc`, { caliber });
/** W12 赞踩：'' = 取消；踩会在问数调优生成一条待处理反馈 */
export type QaVote = '' | 'up' | 'down';
export const voteTurn = (tid: string, vote: QaVote) => api.put<{ id: string; vote: QaVote }>(`/turns/${encodeURIComponent(tid)}/vote`, { vote });
/** W10 结果表翻页：summary 按全部数据算 */
export interface QaTurnResult<R = Record<string, unknown>> extends PageResult<R> { columns: { key: string; title: string; align?: string }[]; summary: Record<string, number | string | null> }
export const getTurnResult = <R = Record<string, unknown>>(tid: string, q: { page: number; size: number }) =>
  api.get<QaTurnResult<R>>(`/turns/${encodeURIComponent(tid)}/result`, { query: q });
/** W11 导出全部行：浏览器下载，返回文件名 */
export async function exportTurn(tid: string, format: 'xlsx' | 'csv' = 'xlsx'): Promise<string> {
  const res = await download(`/turns/${encodeURIComponent(tid)}/export`, { format });
  const cd = res.headers.get('Content-Disposition') ?? '';
  const m = /filename\*=UTF-8''([^;]+)/.exec(cd);
  const name = m ? decodeURIComponent(m[1]) : `${tid}.${format}`;
  const url = URL.createObjectURL(await res.blob());
  const a = document.createElement('a');
  a.href = url; a.download = name; document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 0);
  return name;
}

/**
 * X1 / X2 / X2b 动作（动态模块 /api/ontology/v1/action-runs，开关 `dynamic`）：问数只发起和引用。
 * 工作台「建议动作」/ 右栏「发起」：stage 得到待确认的 action_run → 确认卡 → confirm；确认卡取消 → withdraw。
 */
const dyn = apiClient('dynamic');
export const actionsReal = () => dyn.isReal();
export interface QaActionRun { id: string; action: string; typeVersion: string | null; objLabel: string | null; approver: string | null; system: string | null; status: string; idemKey: string; params: string | null }
export interface QaStageIn { action: string; source: { type: '问数'; session?: string; sessionTitle?: string }; objKind: string; objId: string; objLabel: string; params?: string }
export const stageAction = (body: QaStageIn) => dyn.post<QaActionRun>('/action-runs:stage', body);
export const confirmAction = (code: string) => dyn.post<QaActionRun>(`/action-runs/${encodeURIComponent(code)}:confirm`, {});
export const withdrawAction = (code: string, reason = '确认卡取消') => dyn.post<QaActionRun>(`/action-runs/${encodeURIComponent(code)}:withdraw`, { reason });
