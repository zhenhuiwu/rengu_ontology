/**
 * 外部系统（integration 模块，开关键 integration，前缀 /api/data/v1；docs/backend/action-event-api.md §3.1「外部系统」，
 * 后台 backend/app/modules/integration/router.py）。
 *
 * 页面（pages/data/ExternalSystems.vue）在 integrationApi.isReal() 时走这里，否则保持原来的 mock 逻辑不变。
 * 字段名已和 mock/extsys.ts 的 ExtSystem / ExtCall 对齐，这里只把后台的 null 收成前端的可选字段。
 */
import type { ExtCall, ExtSystem } from '@/mock/extsys';
import { apiClient } from './http';

export const integrationApi = apiClient('integration');
const api = integrationApi;
const enc = encodeURIComponent;

/** 后台 ExtSystemOut：ExtSystem + 关联动作数（列表悬停文字用） */
export type ExtSystemDto = Omit<ExtSystem, 'usage'> & { usage?: ExtSystem['usage'] | null; actionCount?: number };
export interface LinkedActionTypeDto { id: string; name: string; group: string; status: string; endpoint: string }
export interface ExtSystemDetailDto extends ExtSystemDto { actionTypes: LinkedActionTypeDto[] }
/** 新建 / 编辑请求体（表单字段，id 由后台生成） */
export type ExtSystemInput = Omit<ExtSystem, 'id'>;

const clean = <T extends ExtSystemDto>(s: T): T & ExtSystem => ({ ...s, usage: s.usage ?? undefined }) as T & ExtSystem;

export const listExtSystems = async (q: { writeback?: boolean; kw?: string } = {}) =>
  (await api.get<{ items: ExtSystemDto[] }>('/ext-systems', { query: { writeback: q.writeback ? 1 : undefined, kw: q.kw?.trim() || undefined } })).items.map(clean);
export const getExtSystem = async (id: string) => clean(await api.get<ExtSystemDetailDto>(`/ext-systems/${enc(id)}`));
export const createExtSystem = async (body: ExtSystemInput) => clean(await api.post<ExtSystemDetailDto>('/ext-systems', body));
/** 文档是 PUT；apiClient 只有 get / post，后台同一路由也收 POST（【推断】，见文档 §3.1） */
export const updateExtSystem = async (id: string, body: ExtSystemInput) => clean(await api.post<ExtSystemDetailDto>(`/ext-systems/${enc(id)}`, body));
/** 测试调用：返回一条 ExtCall（后台同时记入 writeback_call，页面重新拉「最近调用」）；停用 → 409 EXT_SYSTEM_DISABLED */
export const testExtSystem = (id: string) => api.post<ExtCall>(`/ext-systems/${enc(id)}:test`, {});

/** 「最近调用」一页：counts = 单选按钮上的数字（全部 / 成功 / 失败 / 结果未知，不受 result 筛选影响） */
export interface ExtCallPage { items: ExtCall[]; total: number; page: number; size: number; counts: Record<string, number> }
const cleanCall = (c: ExtCall & { runId?: string | null }): ExtCall => {
  const { runId, ...rest } = c;
  return runId ? { ...rest, runId } : rest;
};
const cleanPage = (p: ExtCallPage): ExtCallPage => ({ ...p, items: p.items.map(cleanCall) });
/** 某系统的最近调用（后台 writeback_call 表；测试调用排最前，其余按时间倒序） */
export const listExtCalls = async (sysId: string, q: { result?: string; page?: number; size?: number } = {}) =>
  cleanPage(await api.get<ExtCallPage>(`/ext-systems/${enc(sysId)}/calls`, { query: { result: q.result && q.result !== '全部' ? q.result : undefined, page: q.page, size: q.size } }));
/** 调用详情（抽屉 ?call=） */
export const getWritebackCall = async (id: string) => cleanCall(await api.get<ExtCall>(`/writeback-calls/${enc(id)}`));
/** 人工核对：确认对方已处理 → 结果改成功（只限结果未知、无执行记录的调用，否则 409 CALL_NOT_CHECKABLE） */
export const markWritebackChecked = async (id: string) => cleanCall(await api.post<ExtCall>(`/writeback-calls/${enc(id)}:mark-checked`, {}));
