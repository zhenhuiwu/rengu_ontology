/**
 * 首页看板（Home.vue）。开关键 biz，前缀 biz（后台 app/modules/dashboard：
 * GET /api/biz/v1/dashboard/kpis、GET /api/biz/v1/stores/ranking）。
 *
 * - mock 模式：页面照旧用 src/mock 现算，这里的函数不会被调用（apiClient 把关，直接抛 MOCK_ONLY）；
 * - real 模式：KPI 条和门店排行改读接口；接口失败时页面显示错误 / 空数据，不回退演示值。
 */
import type { Metric } from '@/mock/metrics';
import { apiClient, type PageResult } from './http';

const api = apiClient('biz');

/** KPI 条用到的指标字段（后台 KpiMetricOut）。value/prev 后台可空，页面按 0 兜底 */
export type KpiMetric = Pick<Metric, 'id' | 'name' | 'unit' | 'value' | 'prev'>;
interface KpiMetricDto { id: string; name: string; unit: string; value: number | null; prev: number | null }
export interface KpisDto { items: KpiMetricDto[]; dataAsOf: string }

/** 门店排行一行（Home.vue fullRanking；amount 是不含税金额） */
export interface StoreRank { rank: number; id: string; name: string; city: string; cnt: number; amount: number }
export interface StoreRankingDto extends PageResult<StoreRank> { dataAsOf: string }

export const isDashboardReal = () => api.isReal();

export async function fetchKpis(ids: string[], cutoff?: string): Promise<{ items: KpiMetric[]; dataAsOf: string }> {
  const r = await api.get<KpisDto>('/dashboard/kpis', { query: { ids: ids.join(','), cutoff } });
  return { dataAsOf: r.dataAsOf, items: r.items.map((m) => ({ id: m.id, name: m.name, unit: m.unit, value: m.value ?? 0, prev: m.prev ?? 0 })) };
}

export function fetchStoreRanking(q: { from?: string; to?: string; page: number; size: number }): Promise<StoreRankingDto> {
  return api.get<StoreRankingDto>('/stores/ranking', { query: { ...q } });
}


export interface SalesDaily { items: { date: string; value: number }[]; dataAsOf: string; taxMode: string; taxRate: number }
export const fetchSalesDaily = (q: { cutoff?: string; days?: number } = {}): Promise<SalesDaily> =>
  api.get<SalesDaily>('/dashboard/sales/daily', { query: { ...q } });
