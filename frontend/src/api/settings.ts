/** 仅租户基础信息扩展；其余系统设置尚未接入。 */
import { apiClient } from './http';
import { setTenantOptions } from './tenants';

export const settingsApi = apiClient('admin');
export interface TenantSettingsData {
  tenantId: string;
  name: string;
  shortName: string;
  industry: string;
  defaultOrg: string | null; // SysOrg.code，不用组织展示名作键。
  orgOptions: { value: string; label: string }[];
  lockVersion: number;
  updatedAt: string;
}
export type TenantSettingsSave = Pick<TenantSettingsData, 'name' | 'shortName' | 'lockVersion'> & { defaultOrg: string };

function refreshTenants(data: TenantSettingsData) {
  setTenantOptions(data.orgOptions.map((o) => ({
    value: o.value, label: `${data.shortName} / ${o.label}`,
    tenantId: data.tenantId, tenantName: data.name, orgName: o.label, default: o.value === data.defaultOrg,
  })));
  return data;
}
export const loadTenantSettings = async () => refreshTenants(await settingsApi.get<TenantSettingsData>('/settings/tenant'));
export const saveTenantSettings = async (body: TenantSettingsSave) => refreshTenants(await settingsApi.put<TenantSettingsData>('/settings/tenant', body));
