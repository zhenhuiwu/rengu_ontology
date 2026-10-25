/**
 * 数据连接。开关键 data，前缀 data（docs/backend/datasources-api.md，api-index §3.1）。
 *
 * Connections.vue 从这里取数：开关没切到真接口时（isReal() = false）页面照旧用 src/mock/datasources.ts；
 * 切到真接口后列表、库表、增改删、测试连接都走接口。后台没起来（isBackendDown）时页面回退 mock 并提示。
 * 列表只有十几条，页面仍在前端筛选 / 分页（和 mock 一致），所以一次取全量（size = 200）。
 */
import type { Datasource, DsTable, DsTest, DsType } from '@/mock/datasources';
import { apiClient, type PageResult } from './http';

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

export async function listDatasources(): Promise<Datasource[]> {
  const r = await api.get<PageResult<DatasourceDto>>('/datasources', { query: { size: ALL } });
  return r.items.map(toDatasource);
}
export async function listDsTables(id: string): Promise<DsTable[]> {
  const r = await api.get<PageResult<DsTableDto>>(`${path(id)}/tables`, { query: { size: ALL } });
  return r.items.map(toDsTable);
}
export const createDatasource = async (body: DatasourceWrite) => toDatasource(await api.post<DatasourceDto>('/datasources', body));
export const updateDatasource = async (id: string, body: DatasourceWrite) => toDatasource(await api.put<DatasourceDto>(path(id), body));
export const deleteDatasource = (id: string) => api.delete<void>(path(id));
export const testDatasource = (id: string) => api.post<DsTest>(`${path(id)}:test`);
export const testDatasourceParams = (body: Partial<DatasourceWrite> & { host: string }) => api.post<DsTest>('/datasources:test', body);
