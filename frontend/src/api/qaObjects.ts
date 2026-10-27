/**
 * 问数「对象搜索 / 对象详情」接口（qa-api.md 第 6 节 O2–O7、O9）。开关键同 qa（apiClient('qa')）。
 * 单独成文件，不动工作台 / 调优共用的 qa.ts。形状同 src/mock/objects-qa.ts（AskAnswer / RelItem / TlItem / ObjAction）。
 */
import type { AskAnswer, AskScope, ObjAction, RelItem, TlItem } from '@/mock/objects-qa';
import { apiClient, workspaceFetch, currentApiContext, ApiError, API_BASE, type ApiErrorBody, type PageResult } from './http';

const api = apiClient('qa');

export interface ObjFilter { kw: string; facets: Record<string, string[]> }
export interface ObjHit { id: string; title: string; cells: Record<string, string | number | null> }
export interface ObjFacet { key: string; label: string; vals: { v: string; n: number }[] }
export type ObjSearchResult = PageResult<ObjHit> & { facets: ObjFacet[]; scopeText: string };
/** O2 搜索（分面计数：本分面不参与自己的计数） */
export const searchObjects = (kind: string, b: ObjFilter & { page: number; size: number }) => api.post<ObjSearchResult>(`/objects/${kind}/search`, b);

/** O4 带范围提问：ids 优先；没有 ids 时按 filter 由后台重算范围 */
export type ScopedAskIn = Omit<AskScope, 'ids'> & { ids?: string[]; filter?: ObjFilter; query?: ExploreQuery };
/** 当前仅支持明确的订单/商品基表汇总；未配置销售/历史/预测返回503 DATA_NOT_CONFIGURED。 */
export const scopedAsk = (scope: ScopedAskIn, question: string) => api.post<AskAnswer>('/scoped-ask', { scope, question });

/** O5 详情：full 带关键指标（含口径）、快捷问题、数据范围 */
export interface ObjDetail {
  kind: string; kindLabel: string; id: string; code: string; title: string;
  status?: { text: string; color?: string }; brief: { label: string; value: string }[];
  groups: { title: string; fields: { label: string; value: string }[] }[]; fields: { label: string; value: string }[];
  kpis?: { label: string; value: string; unit?: string; compare?: string; tone?: string; caliber: string | null }[];
  analyticsConfigured?: boolean; analyticsNote?: string;
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

/* —— 通用对象搜索 / 详情（generic-object-explorer.md；qa-api.md 第 6.1 节 X1–X3）：按静态元数据 + qa 种子绑定取行 —— */
export type ExploreFilter = string[] | { min?: string | number | null; max?: string | number | null };
export interface ExploreQuery { kw?: string; filters?: Record<string, ExploreFilter> }
export interface ExploreRow { id: string; title: string; values: Record<string, unknown> }
export type ExploreFacet =
  | { key: string; kind: 'enum'; values: { value: string; count: number }[] }
  | { key: string; kind: 'range'; min: string | number | null; max: string | number | null };
/** bound=false：这个类型没在 qa 种子里绑定物理表（页面显示“未接入数据”）；boundProps：有列的属性 */
export type ExploreResult = PageResult<ExploreRow> & { titleProp: string; bound: boolean; boundProps: string[]; facets: ExploreFacet[]; canExport?: boolean; dataVersion?: 'current' };
/** X1 搜索：kw 只查可搜索属性；filters 枚举=数组、范围={min,max}；sort 前缀 '-' 降序；facets 是过渡分面（静态 /facets 上线后去掉） */
export const exploreSearch = (type: string, b: ExploreQuery & { sort?: string; page: number; size: number; withFacets?: boolean }) =>
  api.post<ExploreResult>(`/object-types/${encodeURIComponent(type)}/search`, b);
/** X2 详情（范围外 403 OUT_OF_SCOPE，不存在 / 未接入 404） */
export const exploreGet = (type: string, id: string) =>
  api.get<ExploreRow & { titleProp: string; bound: boolean; boundProps: string[] }>(`/object-types/${encodeURIComponent(type)}/objects/${encodeURIComponent(id)}`);

export interface ExploreRelation { linkId: string; label: string; other: string; items: ExploreRow[]; total: number; page: number; size: number; state: 'ok' | 'unbound' | 'unsupported' }
export const exploreRelated = (type: string, id: string, query: { link?: string; page?: number; size?: number } = {}) =>
  api.get<ExploreRelation[]>(`/object-types/${encodeURIComponent(type)}/objects/${encodeURIComponent(id)}/related`, { query });

/**
 * O3 导出 CSV（POST + 文件）：apiClient 只解析 JSON，这里同 qa.ts 的下载小工具，规则同 http.ts request
 * （同一开关把关、X-Demo-As、自动带 ?ver / ?previewAs、统一错误体 → ApiError）。返回文件名。
 */
export const exportObjects = (kind: string, b: ObjFilter & { ids?: string[] }) => download(`/objects/${kind}/export`, b, `${kind}.csv`);
/** X3 通用导出：当前筛选或所选 id，敏感属性不导出，预览不能导出 */
export const exploreExport = (type: string, b: ExploreQuery & { ids?: string[]; props?: string[] }) =>
  download(`/object-types/${encodeURIComponent(type)}/export`, b, `${type}.csv`);
async function download(path: string, b: unknown, fallbackName: string): Promise<string> {
  if (!api.isReal()) throw new ApiError(0, { code: 'MOCK_ONLY', message: '开关 qa 未切到真接口', details: { key: 'qa' } });
  const ctx = currentApiContext();
  const qs = new URLSearchParams();
  if (ctx.ver) qs.set('ver', ctx.ver);
  if (ctx.previewAs) qs.set('previewAs', ctx.previewAs);
  const headers: Record<string, string> = { 'Content-Type': 'application/json' };
  if (ctx.demoAs) headers['X-Demo-As'] = ctx.demoAs;
  if (ctx.ontologyId) headers['X-Ontology-Id'] = ctx.ontologyId;
  const url = `${API_BASE}/${api.prefix}/v1${path}${qs.size ? `?${qs}` : ''}`;
  let res: Response;
  try {
    res = await workspaceFetch(url, { method: 'POST', headers, body: JSON.stringify(b) });
  } catch (e) {
    if ((e as Error)?.name === 'AbortError') throw e;
    throw new ApiError(0, { code: 'NETWORK_ERROR', message: `无法连接后台：${(e as Error)?.message ?? e}` });
  }
  if (!res.ok) {
    let eb: Partial<ApiErrorBody> | null = null;
    try { eb = (await res.json()) as Partial<ApiErrorBody>; } catch { /* 非 JSON */ }
    throw new ApiError(res.status, { code: typeof eb?.code === 'string' ? eb.code : `HTTP_${res.status}`, message: String(eb?.message ?? res.statusText), details: eb?.details, requestId: eb?.requestId ?? null });
  }
  const cd = res.headers.get('Content-Disposition') ?? '';
  const m = /filename\*=UTF-8''([^;]+)/.exec(cd);
  const name = m ? decodeURIComponent(m[1]) : fallbackName;
  const href = URL.createObjectURL(await res.blob());
  const a = document.createElement('a');
  a.href = href; a.download = name; document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(href), 0);
  return name;
}
