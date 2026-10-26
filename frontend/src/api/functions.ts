/** Function data is always explicit real API data; mock mode never calls these methods. */
import { apiClient, type PageResult, type RequestOptions } from './http';

export interface FnParam { name: string; type: string; objType?: string; desc: string }
export interface FnVersion { ver: string; time: string; author: string; note: string; status: '当前' | '历史' | '变更中'; cr?: string }
export interface FunctionRow {
  id: string; name: string; title: string; runtime: string; version: string; status: '已发布' | '草稿' | '已停用';
  owner: string; updatedAt: string; timeoutS: number; desc: string; code: string;
  inputs: FnParam[]; output: FnParam; usedBy: { kind: string; name: string }[];
  sampleIn: Record<string, unknown>; sampleOut: unknown; avgMs: number; calls7d: number; lockVersion: number;
  pending?: { cr: string; crLockVersion: number; status: string; isNew: boolean; at: string; author: string; note: string; definition: Partial<FunctionRow> };
}
export interface FunctionSave {
  title: string; desc: string; timeoutS: number; code: string; lockVersion?: number;
  crId?: string; crLockVersion?: number; changeTitle?: string; reason?: string;
}
export interface FunctionSaved { id: string; crId: string; crLockVersion: number; status: '草稿' }
const api = apiClient('static');
const path = (id: string, suffix = '') => `/functions/${encodeURIComponent(id)}${suffix}`;
export const listFunctions = (query: { kw: string; status: string; page: number; size: number }, options: RequestOptions) => api.get<PageResult<FunctionRow>>('/functions', { ...options, query: { ...options.query, ...query } });
export const getFunction = (id: string, options: RequestOptions) => api.get<FunctionRow>(path(id), options);
export const functionVersions = (id: string, options: RequestOptions) => api.get<PageResult<FnVersion>>(path(id, '/versions'), options);
export const createFunction = (body: FunctionSave & { name: string }, options: RequestOptions) => api.post<FunctionSaved>('/functions', body, options);
export const saveFunction = (id: string, body: FunctionSave, options: RequestOptions) => api.put<FunctionSaved>(path(id), body, options);
export const rollbackFunction = (id: string, body: Pick<FunctionSave, 'lockVersion' | 'crId' | 'crLockVersion' | 'changeTitle' | 'reason'> & { toVersion: string }, options: RequestOptions) => api.post<FunctionSaved>(path(id, ':rollback'), body, options);
export const testFunction = (id: string, input: Record<string, unknown>, options: RequestOptions) => api.post<{ output: unknown; ms: number; at: string }>(path(id, ':test'), { input }, options);
