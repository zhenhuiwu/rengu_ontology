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
  canManage: boolean;
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

export interface ModelSettingsData {
  usage: 'qa' | 'ontology'; label: string; model: string; baseUrl: string; timeoutSec: number;
  enabled: boolean; hasKey: boolean; source: 'environment' | 'database';
  keySource: 'environment' | 'database' | 'none'; lockVersion: number; updatedAt: string | null;
}
export interface ModelsSettingsData { items: ModelSettingsData[]; canManage: boolean; trustedOrigins: string[] }
export type ModelSettingsSave = Pick<ModelSettingsData, 'model' | 'baseUrl' | 'timeoutSec' | 'enabled' | 'lockVersion'> & { apiKey?: string };
export const loadModelSettings = () => settingsApi.get<ModelsSettingsData>('/settings/models', { ontologyScope: false });
export const saveModelSettings = (usage: ModelSettingsData['usage'], body: ModelSettingsSave) => settingsApi.put<ModelSettingsData>(`/settings/models/${usage}`, body, { ontologyScope: false });
export const loadNotificationSettings = () => settingsApi.get<{ inbox: { enabled: boolean; sources: string[] }; externalChannelsConfigured: boolean }>('/settings/notifications', { ontologyScope: false });
