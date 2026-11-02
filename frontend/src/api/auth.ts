import { API_BASE, ApiError, parseError } from './http';

export interface AuthUser { code: string; name: string; account: string }
export interface AuthSession { authenticated: true; user: AuthUser }

async function authRequest<T>(path: string, body?: unknown): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE}/auth/v1/${path}`, {
      method: body === undefined ? 'GET' : 'POST', credentials: 'include',
      headers: { Accept: 'application/json', ...(body === undefined ? {} : { 'Content-Type': 'application/json' }) },
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    });
  } catch {
    throw new ApiError(0, { code: 'NETWORK_ERROR', message: '暂时无法连接服务，请稍后重试' });
  }
  if (!response.ok) throw await parseError(response);
  return await response.json() as T;
}

export const getSession = () => authRequest<AuthSession>('session');
export const login = (username: string, password: string) => authRequest<AuthSession>('login', { username, password });
export const logout = () => authRequest<{ authenticated: false }>('logout', {});
export const changePassword = (currentPassword: string, newPassword: string) => authRequest<AuthSession>('password', { currentPassword, newPassword });

/** Only same-origin application paths may be used as a post-login destination. */
export function safeReturnUrl(value: unknown): string {
  if (typeof value !== 'string' || !value.startsWith('/') || value.startsWith('//') || /[\\\u0000-\u0020]/.test(value)) return '/';
  try {
    const url = new URL(value, window.location.origin);
    let decodedPath = url.pathname;
    for (let i = 0; i < 5 && /%[0-9a-f]{2}/i.test(decodedPath); i++) decodedPath = decodeURIComponent(decodedPath);
    if (/%[0-9a-f]{2}/i.test(decodedPath) || !decodedPath.startsWith('/') || decodedPath.startsWith('//') || /[\\\u0000-\u0020]/.test(decodedPath)) return '/';
    const normalizedPath = new URL(decodedPath, window.location.origin);
    if (url.origin !== window.location.origin || normalizedPath.origin !== url.origin || /^\/login(?:\/|$)/i.test(normalizedPath.pathname)) return '/';
    url.searchParams.delete('as'); url.searchParams.delete('previewAs');
    return `${url.pathname}${url.search}${url.hash}`;
  } catch { return '/'; }
}

/** Clear only this application's session data; retain the user's theme preference. */
export function clearSessionStorage() {
  for (const getStorage of [() => sessionStorage, () => localStorage]) {
    try {
      const storage = getStorage();
      for (const key of Object.keys(storage)) {
        if (key !== 'ui-vue:chrome' && /^(ui-vue:|ontology:assistant|obj-search:last:)/.test(key)) storage.removeItem(key);
      }
    } catch { /* Storage can be unavailable in privacy mode. */ }
  }
}
