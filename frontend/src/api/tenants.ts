/**
 * 顶栏租户 / 组织下拉（TopBar.vue，api-index GET /tenants；接口见 docs/backend/admin-tenants-row-scope-api.md）。
 * 开关键 admin。mock 使用 TENANTS；真模式失败清空候选并抛错。
 */
import { TENANTS } from '@/stores/app';
import { ref } from 'vue';
import { apiClient } from './http';

const api = apiClient('admin');

export interface TenantOption { value: string; label: string; tenantId?: string; tenantName?: string; orgName?: string; default?: boolean }

export const tenantOptions = ref<TenantOption[]>(api.isReal() ? [] : TENANTS);
let loadSeq = 0;
export function setTenantOptions(options: TenantOption[]) {
  loadSeq += 1; // 保存后的新标签不能被先发出的旧 GET 覆盖。
  tenantOptions.value = options;
}

export async function loadTenants(): Promise<TenantOption[]> {
  if (!api.isReal()) return TENANTS;
  const seq = ++loadSeq;
  try {
    const list = await api.get<TenantOption[]>('/tenants');
    if (seq === loadSeq) tenantOptions.value = list;
    return tenantOptions.value;
  } catch (e) {
    if (seq === loadSeq) tenantOptions.value = [];
    throw e;
  }
}
