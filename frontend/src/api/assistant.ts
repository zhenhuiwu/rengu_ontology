import { apiClient, currentApiContext } from './http';
import { useAppStore } from '@/stores/app';

export type AssistantTarget = 'function' | 'object' | 'relation' | 'action' | 'metric' | 'dimension' | 'dictionary' | 'knowledge' | 'event' | 'rule';
export interface AssistantEdit { targetId: string; lockVersion: number; version: string }
export interface AssistantDraft { target: AssistantTarget | 'auto'; message: string; draft: Record<string, unknown>; warnings: string[]; edit?: AssistantEdit }
export interface AssistantPrefill { target: AssistantTarget; draft: Record<string, unknown>; ontologyId: string; version: string; identity: string; edit?: AssistantEdit }

const api = apiClient('static');
export const generateAssistantDraft = (target: AssistantTarget | 'auto', prompt: string, history: { role: 'user' | 'assistant'; content: string }[], edit?: AssistantEdit) =>
  api.post<AssistantDraft>('/assistant:generate', { target, prompt, history, ...(edit ? { edit } : {}) });

const PREFILL_KEY = 'ontology:assistant-prefill';
export function clearAssistantPrefill() { sessionStorage.removeItem(PREFILL_KEY); }
export function putAssistantPrefill(value: AssistantPrefill) { sessionStorage.setItem(PREFILL_KEY, JSON.stringify(value)); }
export function consumeAssistantPrefill(target: AssistantTarget, ontologyId: string): AssistantPrefill | null {
  const raw = sessionStorage.getItem(PREFILL_KEY);
  if (!raw) return null;
  try {
    const value = JSON.parse(raw) as AssistantPrefill;
    const app = useAppStore(), context = currentApiContext();
    if (value.ontologyId !== ontologyId || value.version !== app.ontologyVersion || value.version === '—' || value.identity !== (context.demoAs || app.persona) || context.ver || context.previewAs) { clearAssistantPrefill(); return null; }
    if (value.target !== target) return null;
    clearAssistantPrefill();
    return value;
  } catch { clearAssistantPrefill(); return null; }
}
