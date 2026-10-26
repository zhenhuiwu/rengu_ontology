/** Current authenticated profile and server-calculated release capabilities. No persona fallback. */
import { apiClient, currentApiContext } from './http';
export interface CurrentProfile { id: string; name: string; roles: string[]; roleNames: string[]; func: Record<string, string>; capabilities: { publish: boolean; rollback: boolean } }
const api = apiClient('admin');
export async function loadMyProfile(): Promise<CurrentProfile | null> {
  if (!api.isReal()) return null;
  const ctx = { ...currentApiContext() };
  return await api.get<CurrentProfile>('/me', { readonlyParams: false, query: { ver: ctx.ver, previewAs: ctx.previewAs }, headers: { 'X-Demo-As': ctx.demoAs ?? '' } });
}
