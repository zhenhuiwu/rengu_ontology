import { apiClient, currentApiContext } from './http';
import type { PageResult } from './http';
import type { CheckKind } from '@/mock/quality';

const api = apiClient('data');
export const isQualityReal = () => api.isReal();
export interface QualityRun { id?: string; time: string; result: string; actual: string; ms: number; dataCutoff?: string; sourceTime?: string | null; definition?: Record<string, unknown>; trigger?: 'manual' | 'scheduled'; scheduledAt?: string | null }
export interface QualityDefinition { name: string; typeKey: string; kind: CheckKind; threshold: string; schedule: string }
export interface QualityDraft { crId: string; lockVersion: number; status: string; isNew: boolean; definition: QualityDefinition; retire: boolean }
export interface QualityChange { id: string; crId: string; lockVersion: number; status: string }
export interface QualityChangeOptions { crId?: string; lockVersion?: number; title?: string; reason?: string }
export interface QualityCheck { retired?: boolean; hasPublished?: boolean; pending?: QualityDraft | null; schedule?: string; scheduleStatus?: string; nextRun?: string | null; scheduleError?: string | null; id: string; name?: string; typeKey: string; typeName: string; kind: CheckKind; rule: string; threshold: string; actual: string; result: string; lastRun: string; failSince: string | null; history: QualityRun[]; note?: string }
export interface QualityObject { key: string; name: string; kinds: CheckKind[]; rules?: Record<string, string> }
export interface QualitySummary { total: number; passed: number; failed: number; unrun: number; lastRun: string }
export const fetchQualityMeta = () => api.get<{ objects: QualityObject[]; automaticScheduling: boolean; schedulingTimezone: string }>('/quality-checks/meta');
export const fetchQualityChecks = (query: Record<string, string | number | undefined>) =>
  api.get<PageResult<QualityCheck> & { dataCutoff: string; summary: QualitySummary }>('/quality-checks', { query });
export const fetchQualityRuns = (id: string, page: number, size: number) =>
  api.get<PageResult<QualityRun>>(`/quality-checks/${encodeURIComponent(id)}/runs`, { query: { page, size } });
const pinned = () => { const ctx = currentApiContext(); return { readonlyParams: false, headers: { 'X-Demo-As': ctx.demoAs ?? '', 'X-Ontology-Id': ctx.ontologyId ?? '' }, query: { ver: ctx.ver, previewAs: ctx.previewAs } }; };
export const createQualityCheck = (body: QualityDefinition & QualityChangeOptions) => api.post<QualityChange>('/quality-checks', body, pinned());
export const updateQualityCheck = (id: string, body: QualityDefinition & QualityChangeOptions) => api.put<QualityChange>(`/quality-checks/${encodeURIComponent(id)}`, body, pinned());
export const retireQualityCheck = (id: string, options: QualityChangeOptions) => api.post<QualityChange>(`/quality-checks/${encodeURIComponent(id)}:retire`, options, pinned());
export const runQualityCheck = (id: string) => api.post<{ completed: boolean }>(`/quality-checks/${encodeURIComponent(id)}:run`, {});
export const runAllQualityChecks = () => api.post<{ completed: boolean; count: number }>('/quality-checks:run-all', {});
