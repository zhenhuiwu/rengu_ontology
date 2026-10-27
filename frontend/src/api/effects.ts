import { apiClient, currentApiContext, type PageResult } from './http';

const api = apiClient('integration');
export interface EffectAttempt { attempt: number; at: string; httpStatus: number | null; error: string | null; retryable: boolean; signed: boolean; elapsedMs: number }
export interface ActionEffect { id: string; runId: string; state: string; attempts: number; lastError: string | null; createdAt: string; availableAt: string; dispatchedAt: string | null; canRetry: boolean; retryReason: string | null; history: EffectAttempt[]; manualRetries: { at: string; actor: string; fromState: string }[] }
const context = () => { const ctx = currentApiContext(); return { readonlyParams: false, headers: { 'X-Demo-As': ctx.demoAs ?? '', 'X-Ontology-Id': ctx.ontologyId ?? '' } }; };
export const effectsEnabled = () => api.isReal();
export const listActionEffects = (run: string, page: number) => api.get<PageResult<ActionEffect>>(`/action-runs/${encodeURIComponent(run)}/effects`, { ...context(), query: { page, size: 10 } });
export const retryActionEffect = (run: string, item: ActionEffect) => api.post<ActionEffect>(`/action-runs/${encodeURIComponent(run)}/effects/${encodeURIComponent(item.id)}:retry`, { attempts: item.attempts, state: item.state }, context());
