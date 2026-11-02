/** Public chat has its own API and never sends platform cookies or authorization headers. */
export interface PublicWorkspace { id: string | number; label: string; version?: string }
export interface PublicConfig { workspaces: PublicWorkspace[]; selected: PublicWorkspace | null }
export interface PublicTable {
  id: string; title: string; columns: { key: string; title: string; align?: string }[];
  items: Record<string, unknown>[]; total: number; summary?: string; paged?: boolean;
}
export interface PublicTurn {
  id: string; kind: string; question?: string; text?: string; lead?: string; bold?: string; tail?: string;
  answer?: {
    card: string; text?: string; table?: PublicTable;
    clarify?: { options: { value: string; label: string }[] };
  };
}
export interface PublicSession { id: string; title: string; ontologyId: string | number; turns: PublicTurn[] }
export class PublicApiError extends Error {
  constructor(public status: number, message: string) { super(message); }
}
export function publicApiUrl(path: string, pageUrl = window.location.href) {
  return new URL(`api/agent/v1${path}`, new URL('./', pageUrl)).href;
}
async function request<T>(path: string, signal: AbortSignal, body?: object): Promise<T> {
  const response = await fetch(publicApiUrl(path), {
    method: body ? 'POST' : 'GET', credentials: 'omit', cache: 'no-store', signal,
    headers: body ? { 'Content-Type': 'application/json' } : {},
    ...(body ? { body: JSON.stringify(body) } : {}),
  });
  const data = await response.json().catch(() => null);
  if (!response.ok) {
    const notReady = response.status === 404 && path.startsWith('/config') && data?.detail === 'Not Found';
    throw new PublicApiError(response.status, notReady ? '公开问数服务尚未就绪，请重试' : typeof data?.message === 'string' ? data.message : `请求失败（${response.status}）`);
  }
  if (!data) throw new Error('服务返回了无法识别的响应，请稍后重试');
  return data as T;
}
export const publicApi = {
  config: (ontologyId: string, signal: AbortSignal) => request<PublicConfig>(`/config${ontologyId ? `?ontologyId=${encodeURIComponent(ontologyId)}` : ''}`, signal),
  create: (ontologyId: string, signal: AbortSignal) => request<PublicSession>('/sessions', signal, { ontologyId: Number(ontologyId) }),
  session: (ontologyId: string, id: string, signal: AbortSignal) => request<PublicSession>(`/sessions/${encodeURIComponent(id)}?ontologyId=${encodeURIComponent(ontologyId)}`, signal),
  ask: (ontologyId: string, id: string, question: string, signal: AbortSignal) => request<{ turnId: string; turn: PublicTurn }>(`/sessions/${encodeURIComponent(id)}/ask`, signal, { ontologyId: Number(ontologyId), question, refs: [] }),
};
