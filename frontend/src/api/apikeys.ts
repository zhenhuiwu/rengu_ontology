import type { ApiKey, KeyBinding, KeyCall, KeyScope, KeyStatus } from '@/mock/apikeys';
import { apiClient, type PageResult } from './http';

const api = apiClient('admin');
export interface KeysPage extends PageResult<ApiKey> { counts: Record<KeyStatus, number> }
export interface KeysMeta { scopes: KeyScope[]; binding: KeyBinding; allowedPages: string[]; canManage: boolean }
export interface KeyCreate { name: string; env: '生产' | '测试'; scopes: KeyScope[]; binding: { kind: '用户'; userId: string }; lifeDays: number; note: string; ipAllow: string[] }
export const queryApiKeys = (page: number, size: number, status: string, scope: string, kw: string) =>
  api.get<KeysPage>('/api-keys', { query: { page, size, status, scope, kw } });
export const apiKeysMeta = () => api.get<KeysMeta>('/api-keys/meta');
export const apiKeyDetail = (id: string) => api.get<ApiKey>(`/api-keys/${encodeURIComponent(id)}`);
export const apiKeyCalls = (id: string, page: number, size: number) => api.get<PageResult<KeyCall>>(`/api-keys/${encodeURIComponent(id)}/calls`, { query: { page, size } });
export const createApiKey = (body: KeyCreate) => api.post<ApiKey & { secret: string }>('/api-keys', body);
export const revokeApiKey = (id: string) => api.post<ApiKey>(`/api-keys/${encodeURIComponent(id)}:revoke`);
