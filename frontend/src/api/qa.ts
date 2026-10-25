/**
 * 问数（qa）工作台接口。开关键 qa，前缀 /api/qa/v1（docs/backend/qa-api.md 第 3 节 W1–W17）。
 * 开关没切到真接口时页面照旧走 src/mock，这里的函数不会被调用。
 */
import type { QaSession, QaFavorite, QaPage, QaDemoTurn } from '@/mock/qa';
import { apiClient, currentApiContext, ApiError, API_BASE, type ApiErrorBody, type PageResult } from './http';

const api = apiClient('qa');
export const qaReal = () => api.isReal();

/**
 * apiClient 目前只开放 get / post。W4（PATCH）、W5 / W15（DELETE）先在这里按 http.ts request 的同样规则发：
 * 同一开关把关、X-Demo-As、自动带 ?ver / ?previewAs、统一错误体 → ApiError。
 * 待 http.ts 的 apiClient 补上 patch / del 后改回 api.patch / api.del（已报 PDE）。
 */
async function write<T>(method: 'PATCH' | 'DELETE', path: string, body?: unknown): Promise<T> {
  if (!api.isReal()) throw new ApiError(0, { code: 'MOCK_ONLY', message: '开关 qa 未切到真接口', details: { key: 'qa' } });
  const ctx = currentApiContext();
  const qs = new URLSearchParams();
  if (ctx.ver) qs.set('ver', ctx.ver);
  if (ctx.previewAs) qs.set('previewAs', ctx.previewAs);
  const headers: Record<string, string> = { Accept: 'application/json' };
  if (ctx.demoAs) headers['X-Demo-As'] = ctx.demoAs;
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  const url = `${API_BASE}/${api.prefix}/v1${path}${qs.size ? `?${qs}` : ''}`;
  let res: Response;
  try {
    res = await fetch(url, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
  } catch (e) {
    throw new ApiError(0, { code: 'NETWORK_ERROR', message: `无法连接后台：${(e as Error)?.message ?? e}` });
  }
  if (!res.ok) {
    let b: Partial<ApiErrorBody> | null = null;
    try { b = (await res.json()) as Partial<ApiErrorBody>; } catch { /* 非 JSON */ }
    throw new ApiError(res.status, { code: typeof b?.code === 'string' ? b.code : `HTTP_${res.status}`, message: String(b?.message ?? res.statusText), details: b?.details, requestId: b?.requestId ?? null });
  }
  return (res.status === 204 ? undefined : await res.json()) as T;
}

/** W3 会话详情：轮次快照形状同 QA_DEMO_TURNS；链路新生成的回答是 card='table' + text（后台 chain.py） */
export interface QaChainTable { id: string; title: string; columns: { key: string; title: string; align?: string }[]; items: Record<string, unknown>[]; total: number; summary: string; paged: boolean }
export interface QaChainAnswer { card: 'table'; text: string; dataAsOf?: string; basis?: Record<string, string>; table: QaChainTable }
export type QaRealTurn =
  | QaDemoTurn
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
  write<QaSessionDetail>('PATCH', `/sessions/${encodeURIComponent(sid)}`, body);
export const deleteSession = (sid: string) => write<void>('DELETE', `/sessions/${encodeURIComponent(sid)}`);
export const askTurn = (sid: string, question: string) =>
  api.post<QaAskOut>(`/sessions/${encodeURIComponent(sid)}/turns`, { question, refs: [] });
export const listFavorites = (q: { q?: string; page: number; size: number }) => api.get<PageResult<QaFavorite>>('/favorites', { query: q });
export const deleteFavorite = (id: string) => write<void>('DELETE', `/favorites/${encodeURIComponent(id)}`);
/** P1 简版（左栏贴底、「我的页面 · 全部」抽屉） */
export const listPagesBrief = (q: { kw?: string; page: number; size: number }) =>
  api.get<PageResult<QaPage>>('/pages', { query: { ...q, view: 'brief' } });
export const getSessionOptions = () => api.get<QaSessionOptions>('/session-options');
