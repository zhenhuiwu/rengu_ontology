/**
 * 数据连接。开关键 data，前缀 data（docs/backend/datasources-api.md，api-index §3.1）。
 *
 * Connections.vue 从这里取数：开关没切到真接口时（isReal() = false）页面照旧用 src/mock/datasources.ts；
 * 切到真接口后列表、库表、增改删、测试连接都走接口，失败交给页面提示，不回退演示数据。
 * 页面本地筛选 / 分页；按后台 total 逐页取齐，不能只显示前 200 条。
 */
import type { Datasource, DsTable, DsTest, DsType } from '@/mock/datasources';
import { apiClient, currentApiContext, type PageResult } from './http';

const api = apiClient('data');
const ALL = 200; // 后台 MAX_PAGE_SIZE

/** 后台 DatasourceOut：mock 里缺省的可选字段输出 null；不含密码 */
export type DatasourceDto = Omit<Datasource, 'compatMode' | 'databaseMode' | 'erpSystem' | 'appId' | 'note' | 'refOnly'> & {
  compatMode: string | null; databaseMode: string | null; erpSystem: string | null; appId: string | null;
  note: string | null; refOnly: Datasource['refOnly'] | null;
};
export type DsTableDto = Omit<DsTable, 'ref'> & { ref: string | null };

/** 写入体（POST / PUT）；PUT 时 password 留空 = 不修改 */
export interface DatasourceWrite {
  name: string; type: DsType; host: string; port: number; database: string; user: string; password: string;
  compatMode?: string; databaseMode?: string; erpSystem?: string; appId?: string;
  ssl: boolean; owner: string; mode: Datasource['sync']['mode']; cron: string;
}

const opt = <T>(v: T | null): T | undefined => v ?? undefined;
/** 薄适配：null → 不带这个字段（和 mock 的可选字段一样） */
export const toDatasource = (d: DatasourceDto): Datasource => ({
  ...d,
  compatMode: opt(d.compatMode), databaseMode: opt(d.databaseMode), erpSystem: opt(d.erpSystem), appId: opt(d.appId),
  note: opt(d.note), refOnly: opt(d.refOnly),
});
export const toDsTable = (t: DsTableDto): DsTable => ({ ...t, ref: opt(t.ref) });

const path = (id: string) => `/datasources/${encodeURIComponent(id)}`;

export const isDataReal = () => api.isReal();

async function allPages<T>(path: string): Promise<T[]> {
  const ctx = { ...currentApiContext() };
  const items: T[] = [];
  for (let page = 1; ; page++) {
    const r = await api.get<PageResult<T>>(path, {
      query: { page, size: ALL, ver: ctx.ver, previewAs: ctx.previewAs },
      headers: { 'X-Demo-As': ctx.demoAs ?? '', 'X-Ontology-Id': ctx.ontologyId ?? '' }, readonlyParams: false,
    });
    if (r.page !== page) throw new Error('数据列表已变化，请刷新后重试');
    items.push(...r.items);
    if (!r.items.length || r.page * r.size >= r.total) return items;
  }
}

export async function queryDatasources(q: { page: number; size: number; type?: string; kw?: string }) {
  const r = await api.get<PageResult<DatasourceDto>>('/datasources', { query: { ...q, type: q.type === '全部' ? undefined : q.type } });
  return { ...r, items: r.items.map(toDatasource) };
}
export async function queryDsTables(id: string, q: { page: number; size: number; kw?: string }) {
  const r = await api.get<PageResult<DsTableDto>>(`${path(id)}/tables`, { query: q });
  return { ...r, items: r.items.map(toDsTable) };
}
export const queryDsRuns = (id: string, page: number, size: number) => api.get<PageResult<Datasource['runs'][number]>>(`${path(id)}/runs`, { query: { page, size } });

export async function listDatasources(): Promise<Datasource[]> {
  return (await allPages<DatasourceDto>('/datasources')).map(toDatasource);
}
export async function listDsTables(id: string): Promise<DsTable[]> {
  return (await allPages<DsTableDto>(`${path(id)}/tables`)).map(toDsTable);
}
export const getDatasource = async (id: string) => toDatasource(await api.get<DatasourceDto>(path(id)));
export const createDatasource = async (body: DatasourceWrite) => toDatasource(await api.post<DatasourceDto>('/datasources', body));
export const updateDatasource = async (id: string, body: DatasourceWrite) => toDatasource(await api.put<DatasourceDto>(path(id), body));
export const deleteDatasource = (id: string) => api.delete<void>(path(id));
export const testDatasource = (id: string) => api.post<DsTest>(`${path(id)}:test`);
export const testDatasourceParams = (body: Partial<DatasourceWrite> & { host: string }) => api.post<DsTest>('/datasources:test', body);
