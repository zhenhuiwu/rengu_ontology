/**
 * 顶栏租户 / 组织下拉（TopBar.vue，api-index GET /tenants；接口见 docs/backend/admin-tenants-row-scope-api.md）。
 * 开关键 admin。后台没切真接口或没起来时，回退 stores/app.ts 的 TENANTS（演示数据）。
 */
import { TENANTS } from '@/stores/app';
import { apiClient, isBackendDown } from './http';

const api = apiClient('admin');

export interface TenantOption { value: string; label: string; tenantId?: string; tenantName?: string; orgName?: string; default?: boolean }

export async function loadTenants(): Promise<TenantOption[]> {
  if (!api.isReal()) return TENANTS;
  try {
    const list = await api.get<TenantOption[]>('/tenants');
    return list.length ? list : TENANTS;
  } catch (e) {
    if (isBackendDown(e)) return TENANTS;
    throw e;
  }
}
