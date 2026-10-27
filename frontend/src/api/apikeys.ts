/**
 * API 密钥与服务账号（ApiKeys.vue，接口见 backend app/modules/admin/api_keys.py，admin_0025）。
 * 读：真模式失败直接抛出，页面显示错误；写：只走后台，绝不回落 mock。
 * 权限范围对外是编码（meta:read / objects:read / …），scopeOptions 给中文显示名。
 */
import type { ApiKey, KeyBinding, KeyCall, KeyStatus } from '@/mock/apikeys';
import { apiClient, type PageResult } from './http';

const api = apiClient('admin');
const enc = encodeURIComponent;

export type ScopeCode = 'meta:read' | 'objects:read' | 'functions:invoke' | 'actions:dry-run' | 'actions:execute' | 'events:ingest' | 'dynamic:read';
export interface ScopeOption { value: ScopeCode; label: string; pages: string[] }
export interface KeyUsage { minute: number; day: number }
/** 后台密钥行：在 mock 结构上多了主体类型、白名单、限流配额和当前用量 */
export interface RemoteKey extends Omit<ApiKey, 'scopes' | 'binding'> {
  scopes: string[];
  binding: KeyBinding & { serviceAccountId?: string; serviceAccountName?: string; roleId?: string; roleName?: string };
  principalKind?: 'user' | 'service';
  ontologyIds?: number[];
  actionTypes?: string[] | null;
  eventTypes?: string[] | null;
  functionTypes?: string[] | null;
  ratePerMinute?: number | null;
  quotaPerDay?: number | null;
  usage?: KeyUsage;
  note?: string;
}
export interface KeysPage extends PageResult<RemoteKey> { counts: Record<KeyStatus, number> }
export interface ServiceAccountBrief { id: string; name: string; roleId: string; roleName: string }
export interface KeysMeta {
  scopes: ScopeCode[];
  scopeOptions: ScopeOption[];
  binding: KeyBinding;
  allowedPages: string[];
  canManage: boolean;
  canManageServiceAccounts?: boolean;
  serviceAccounts?: ServiceAccountBrief[];
}
export interface KeyPolicy {
  actionTypes?: string[] | null;
  eventTypes?: string[] | null;
  functionTypes?: string[] | null;
  ratePerMinute?: number | null;
  quotaPerDay?: number | null;
}
export type KeyBindingIn = { kind: '用户'; userId: string } | { kind: '服务账号'; serviceAccountId: string };
export interface KeyCreate extends KeyPolicy {
  name: string; env: '生产' | '测试'; scopes: string[]; binding: KeyBindingIn; lifeDays: number; note: string; ipAllow: string[];
}
export interface KeyPatch extends KeyPolicy { scopes?: string[] }
export interface ServiceAccount {
  id: string; name: string; description: string; roleId: string; roleName: string; status: '启用' | '已吊销';
  activeKeys: number; creator: string; createdAt: string; revokedAt: string | null; revokedBy: string | null;
}
export interface ServiceAccountIn { name: string; description: string; roleId: string }

export const queryApiKeys = (page: number, size: number, status: string, scope: string, kw: string, principal?: string) =>
  api.get<KeysPage>('/api-keys', { query: { page, size, status, scope, kw, principal } });
export const apiKeysMeta = () => api.get<KeysMeta>('/api-keys/meta');
export const apiKeyDetail = (id: string) => api.get<RemoteKey>(`/api-keys/${enc(id)}`);
export const apiKeyCalls = (id: string, page: number, size: number) => api.get<PageResult<KeyCall>>(`/api-keys/${enc(id)}/calls`, { query: { page, size } });
export const createApiKey = (body: KeyCreate) => api.post<RemoteKey & { secret: string }>('/api-keys', body);
export const updateApiKey = (id: string, body: KeyPatch) => api.patch<RemoteKey>(`/api-keys/${enc(id)}`, body);
export const revokeApiKey = (id: string) => api.post<RemoteKey>(`/api-keys/${enc(id)}:revoke`);

export const listServiceAccounts = () => api.get<{ items: ServiceAccount[] }>('/service-accounts');
export const createServiceAccount = (body: ServiceAccountIn) => api.post<ServiceAccount>('/service-accounts', body);
export const updateServiceAccount = (id: string, body: Partial<Omit<ServiceAccountIn, 'name'>>) => api.patch<ServiceAccount>(`/service-accounts/${enc(id)}`, body);
export const revokeServiceAccount = (id: string) => api.post<ServiceAccount>(`/service-accounts/${enc(id)}:revoke`);
