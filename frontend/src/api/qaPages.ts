/**
 * 问数「我的页面 / 模板库」接口（qa-api.md 第 5 节 P1–P12）。开关键同 qa（apiClient('qa')）。
 * 单独成文件，不动工作台 / 调优共用的 qa.ts。形状同 src/mock/qa-pages.ts（QaPageFull / QaTemplate）。
 */
import type { PageCard, QaPageFull, QaTemplate, RefreshPlan, ShareSetting, Subscription } from '@/mock/qa-pages';
import { apiClient, type PageResult } from './http';

const api = apiClient('qa');

export interface PageListQ { kw?: string; src?: string; share?: string; status?: string; page: number; size: number }
export type PageList = PageResult<QaPageFull> & { fromWorkbenchCount: number };
/** P1 我的页面（完整形状） */
export const listPages = (q: PageListQ) => api.get<PageList>('/pages', { query: { ...q, view: 'full' } });
/** P3 页面详情 */
export const getPage = (id: string) => api.get<QaPageFull>(`/pages/${id}`);
/** P2 新建（空白 / 用模板）；template.refresh 是模板气泡里选的计划 */
export interface PageCreate { name: string; scope: string; template?: { id: string; caliber?: string; refresh?: Omit<RefreshPlan, 'onFail'> & { onFail?: RefreshPlan['onFail'] } } }
export const createPage = (b: PageCreate) => api.post<QaPageFull>('/pages', b);
/** P4 重命名 */
export const renamePage = (id: string, name: string) => api.patch<QaPageFull>(`/pages/${id}`, { name });
/** P5 删除 */
export const deletePage = (id: string) => api.delete<void>(`/pages/${id}`);
/** P6 立即刷新 */
export interface RefreshResult { id: string; status: QaPageFull['status']; statusNote?: string; lastRefresh: string; dataAsOf: string }
export const refreshPage = (id: string) => api.post<RefreshResult>(`/pages/${id}:refresh`);
/** P7 固定到页面（工作台） */
export const pinToPage = (id: string, b: { turnId: string; title: string; tableId?: string; chartId?: string }) => api.post<QaPageFull>(`/pages/${id}/cards`, b);
/** P8 调整卡片（顺序 + 移除） */
export const saveCardOrder = (id: string, order: string[]) => api.put<{ cards: PageCard[]; removed: string[] }>(`/pages/${id}/cards`, { order });
/** P9 刷新与订阅 */
export const saveSchedule = (id: string, refresh: RefreshPlan, sub: Subscription) =>
  api.put<{ refresh: RefreshPlan; sub: Subscription; nextRun: string }>(`/pages/${id}/schedule`, { refresh, sub });
/** P10 共享 */
export const saveShare = (id: string, s: ShareSetting) =>
  api.put<ShareSetting & { linkUrl?: string; newViewers: string[]; newGrants: string[] }>(`/pages/${id}/share`, s);
/** P11 模板库 / P12 模板详情 */
export const listTemplates = (q: { kw?: string; scene?: string; page: number; size: number }) => api.get<PageResult<QaTemplate>>('/templates', { query: q });

/** 模板气泡里的刷新文案（“每周一 08:00”等）→ 刷新计划，同 MyPages.fromTemplate 的 mock 逻辑 */
export function planOf(text: string): RefreshPlan {
  if (text.startsWith('每小时')) return { freq: '每小时', time: '整点', onFail: '保留上次结果' };
  if (text.startsWith('每周')) return { freq: '每周', weekday: '周一', time: '08:00', onFail: '保留上次结果' };
  if (text.startsWith('手动')) return { freq: '手动', time: '--', onFail: '保留上次结果' };
  return { freq: '每天', time: '08:00', onFail: '保留上次结果' };
}
