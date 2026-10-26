/**
 * 问数「对象搜索 / 对象详情」接口（qa-api.md 第 6 节 O2–O7、O9）。开关键同 qa（apiClient('qa')）。
 * 单独成文件，不动工作台 / 调优共用的 qa.ts。形状同 src/mock/objects-qa.ts（AskAnswer / RelItem / TlItem / ObjAction）。
 */
import type { AskAnswer, AskScope, ObjAction, RelItem, TlItem } from '@/mock/objects-qa';
import { apiClient, currentApiContext, ApiError, API_BASE, type ApiErrorBody, type PageResult } from './http';

const api = apiClient('qa');

export interface ObjFilter { kw: string; facets: Record<string, string[]> }
export interface ObjHit { id: string; title: string; cells: Record<string, string | number | null> }
export interface ObjFacet { key: string; label: string; vals: { v: string; n: number }[] }
export type ObjSearchResult = PageResult<ObjHit> & { facets: ObjFacet[]; scopeText: string };
/** O2 搜索（分面计数：本分面不参与自己的计数） */
export const searchObjects = (kind: string, b: ObjFilter & { page: number; size: number }) => api.post<ObjSearchResult>(`/objects/${kind}/search`, b);

/** O4 带范围提问：ids 优先；没有 ids 时按 filter 由后台重算范围 */
export type ScopedAskIn = Omit<AskScope, 'ids'> & { ids?: string[]; filter?: ObjFilter };
export const scopedAsk = (scope: ScopedAskIn, question: string) => api.post<AskAnswer>('/scoped-ask', { scope, question });

/** O5 详情：full 带关键指标（含口径）、快捷问题、数据范围 */
export interface ObjDetail {
  kind: string; kindLabel: string; id: string; code: string; title: string;
  status?: { text: string; color?: string }; brief: { label: string; value: string }[];
  groups: { title: string; fields: { label: string; value: string }[] }[]; fields: { label: string; value: string }[];
  kpis?: { label: string; value: string; unit?: string; compare?: string; tone?: string; caliber: string }[];
  quickQuestions?: string[]; scopeLabel?: string; cutoff?: string;
}
export const getObject = (kind: string, id: string, view: 'drawer' | 'full' = 'full') =>
  api.get<ObjDetail>(`/objects/${kind}/${encodeURIComponent(id)}`, { query: { view } });
/** O6 关联对象：每组各自分页；denied = 整组无权（预览） */
export interface ObjRelGroup { key: string; title: string; items: (RelItem & { denied?: boolean })[]; total?: number; page?: number; size?: number; denied?: boolean }
export const getRelated = (kind: string, id: string, q: { group?: string; page?: number; size?: number } = {}) =>
  api.get<ObjRelGroup[]>(`/objects/${kind}/${encodeURIComponent(id)}/related`, { query: q });
/** O7 时间线 */
export const getTimeline = (kind: string, id: string, q: { page: number; size: number }) =>
  api.get<PageResult<TlItem> & { unknownLabel: string }>(`/objects/${kind}/${encodeURIComponent(id)}/timeline`, { query: q });
/** O9 可执行动作 */
export const getActions = (kind: string, id: string) => api.get<ObjAction[]>(`/objects/${kind}/${encodeURIComponent(id)}/actions`);

/**
 * O3 导出 CSV（POST + 文件）：apiClient 只解析 JSON，这里同 qa.ts 的下载小工具，规则同 http.ts request
 * （同一开关把关、X-Demo-As、自动带 ?ver / ?previewAs、统一错误体 → ApiError）。返回文件名。
 */
export async function exportObjects(kind: string, b: ObjFilter & { ids?: string[] }): Promise<string> {
  if (!api.isReal()) throw new ApiError(0, { code: 'MOCK_ONLY', message: '开关 qa 未切到真接口', details: { key: 'qa' } });
  const ctx = currentApiContext();
  const qs = new URLSearchParams();
  if (ctx.ver) qs.set('ver', ctx.ver);
  if (ctx.previewAs) qs.set('previewAs', ctx.previewAs);
  const headers: Record<string, string> = { 'Content-Type': 'application/json' };
  if (ctx.demoAs) headers['X-Demo-As'] = ctx.demoAs;
  const url = `${API_BASE}/${api.prefix}/v1/objects/${kind}/export${qs.size ? `?${qs}` : ''}`;
  let res: Response;
  try {
    res = await fetch(url, { method: 'POST', headers, body: JSON.stringify(b) });
  } catch (e) {
    throw new ApiError(0, { code: 'NETWORK_ERROR', message: `无法连接后台：${(e as Error)?.message ?? e}` });
  }
  if (!res.ok) {
    let eb: Partial<ApiErrorBody> | null = null;
    try { eb = (await res.json()) as Partial<ApiErrorBody>; } catch { /* 非 JSON */ }
    throw new ApiError(res.status, { code: typeof eb?.code === 'string' ? eb.code : `HTTP_${res.status}`, message: String(eb?.message ?? res.statusText), details: eb?.details, requestId: eb?.requestId ?? null });
  }
  const cd = res.headers.get('Content-Disposition') ?? '';
  const m = /filename\*=UTF-8''([^;]+)/.exec(cd);
  const name = m ? decodeURIComponent(m[1]) : `${kind}.csv`;
  const href = URL.createObjectURL(await res.blob());
  const a = document.createElement('a');
  a.href = href; a.download = name; document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(href), 0);
  return name;
}
