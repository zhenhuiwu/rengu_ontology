/**
 * 统一接口调用（fetch）。所有真接口请求都从这里走：
 * - 地址：`/api/<前缀>/v1<path>`；开关键（谁负责）和前缀分开，见 config.ts，接口文件用 apiClient(开关键) 声明，前缀取 SWITCH_PREFIX；开关没切到真接口时请求直接拒绝（MOCK_ONLY）（开发时由 Vite 代理到后台骨架，见 vite.config.ts）；
 * - 身份：请求头 `X-Demo-As: <sys_user.code>`，由当前演示身份换算（?as=admin → U010001，?as=viewer → U010006，api-index §1.4）；
 * - 只读模式：页面处于历史版本（?ver=）或以此用户预览（?previewAs=）时，每个请求自动带同名 query 参数（api-index §1.7、§1.8）；
 * - 错误：后台统一错误体 `{code, message, details, requestId}` 转成 ApiError（api-index §1.2）。
 *
 * 不依赖 store：当前身份 / 版本 / 预览由 setApiContext 注册的函数提供（src/api/index.ts 里注册），避免和 store 循环引用。
 */
import { isReal, SWITCH_PREFIX, type ApiPrefix, type SwitchKey } from './config';

export const API_BASE = '/api';

export interface ApiContext {
  /** sys_user.code，例如 U010001 */
  demoAs?: string | null;
  /** 正在查看的历史版本（?ver=），null = 当前版本 */
  ver?: string | null;
  /** 以此用户预览的用户编号（?previewAs=），null = 不在预览 */
  previewAs?: string | null;
}

let ctxProvider: () => ApiContext = () => ({});
export function setApiContext(fn: () => ApiContext) { ctxProvider = fn; }
export const currentApiContext = (): ApiContext => ctxProvider();

/** 后台错误体（app/core/errors.py error_body） */
export interface ApiErrorBody { code: string; message: string; details?: unknown; requestId?: string | null }

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly details: unknown;
  readonly requestId: string | null;
  constructor(status: number, body: ApiErrorBody) {
    super(body.message);
    this.name = 'ApiError';
    this.status = status;
    this.code = body.code;
    this.details = body.details ?? null;
    this.requestId = body.requestId ?? null;
  }
  /** 历史版本 / 预览下写入被拒（403 READONLY，details.mode = history | preview） */
  get readonlyMode(): 'history' | 'preview' | null {
    const m = (this.details as { mode?: unknown } | null)?.mode;
    return this.code === 'READONLY' && (m === 'history' || m === 'preview') ? m : null;
  }
}

export const isApiError = (e: unknown): e is ApiError => e instanceof ApiError;

export type QueryValue = string | number | boolean | null | undefined;

export interface RequestOptions {
  method?: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE';
  query?: Record<string, QueryValue | QueryValue[]>;
  body?: unknown;
  headers?: Record<string, string>;
  signal?: AbortSignal;
  /** 是否带上当前的 ?ver= / ?previewAs=（默认带，api-index：只读模式下所有请求都带） */
  readonlyParams?: boolean;
}

function buildUrl(prefix: ApiPrefix, path: string, opts: Pick<RequestOptions, 'query' | 'readonlyParams'> = {}): string {
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(opts.query ?? {})) {
    for (const one of Array.isArray(v) ? v : [v]) if (one !== undefined && one !== null && one !== '') qs.append(k, String(one));
  }
  if (opts.readonlyParams !== false) {
    const ctx = currentApiContext();
    if (ctx.ver && !qs.has('ver')) qs.set('ver', ctx.ver);
    if (ctx.previewAs && !qs.has('previewAs')) qs.set('previewAs', ctx.previewAs);
  }
  const s = qs.toString();
  return `${API_BASE}/${prefix}/v1${path.startsWith('/') ? path : `/${path}`}${s ? `?${s}` : ''}`;
}

async function parseError(res: Response): Promise<ApiError> {
  let body: Partial<ApiErrorBody> | null = null;
  try { body = (await res.json()) as Partial<ApiErrorBody>; } catch { /* 非 JSON（例如代理连不上后台时的 502/504） */ }
  if (body && typeof body.code === 'string') {
    return new ApiError(res.status, { code: body.code, message: String(body.message ?? ''), details: body.details, requestId: body.requestId ?? res.headers.get('X-Request-ID') });
  }
  return new ApiError(res.status, { code: `HTTP_${res.status}`, message: res.statusText || `请求失败（${res.status}）`, requestId: res.headers.get('X-Request-ID') });
}

async function request<T>(prefix: ApiPrefix, path: string, opts: RequestOptions = {}, blob = false): Promise<T> {
  const ctx = currentApiContext();
  const headers: Record<string, string> = { Accept: 'application/json', ...(opts.headers ?? {}) };
  if (ctx.demoAs && !Object.keys(headers).some((name) => name.toLowerCase() === 'x-demo-as')) headers['X-Demo-As'] = ctx.demoAs;
  let body: BodyInit | undefined;
  if (opts.body !== undefined) { headers['Content-Type'] = 'application/json'; body = JSON.stringify(opts.body); }
  let res: Response;
  try {
    res = await fetch(buildUrl(prefix, path, opts), { method: opts.method ?? 'GET', headers, body, signal: opts.signal });
  } catch (e) {
    if ((e as { name?: string })?.name === 'AbortError') throw e;
    throw new ApiError(0, { code: 'NETWORK_ERROR', message: `无法连接后台：${(e as Error)?.message ?? e}` });
  }
  if (!res.ok) throw await parseError(res);
  if (res.status === 204) return undefined as T;
  if (blob) return (await res.blob()) as T;
  return (await res.json()) as T;
}

/**
 * 接口文件的唯一入口：声明「归哪个开关」，前缀取 SWITCH_PREFIX[key]。
 * 例：apiClient('versioning') → 地址 /api/ontology/v1/...
 * 开关没切到真接口时，请求不发出，直接抛 ApiError(0, 'MOCK_ONLY')，调用方绕不过开关。
 */
export function apiClient(key: SwitchKey) {
  const prefix: ApiPrefix = SWITCH_PREFIX[key];
  const guard = () => {
    if (!isReal(key)) throw new ApiError(0, { code: 'MOCK_ONLY', message: `开关 ${key} 未切到真接口（VITE_API_MODE / VITE_API_REAL_MODULES）`, details: { key } });
  };
  return {
    key,
    prefix,
    /** 这个开关当前是否走真接口 */
    isReal: () => isReal(key),
    get: async <T>(path: string, opts: Omit<RequestOptions, 'method' | 'body'> = {}) => { guard(); return request<T>(prefix, path, { ...opts, method: 'GET' }); },
    getBlob: async (path: string, opts: Omit<RequestOptions, 'method' | 'body'> = {}) => { guard(); return request<Blob>(prefix, path, { ...opts, method: 'GET' }, true); },
    post: async <T>(path: string, body?: unknown, opts: Omit<RequestOptions, 'method' | 'body'> = {}) => { guard(); return request<T>(prefix, path, { ...opts, method: 'POST', body }); },
    put: async <T>(path: string, body?: unknown, opts: Omit<RequestOptions, 'method' | 'body'> = {}) => { guard(); return request<T>(prefix, path, { ...opts, method: 'PUT', body }); },
    patch: async <T>(path: string, body?: unknown, opts: Omit<RequestOptions, 'method' | 'body'> = {}) => { guard(); return request<T>(prefix, path, { ...opts, method: 'PATCH', body }); },
    delete: async <T>(path: string, opts: Omit<RequestOptions, 'method' | 'body'> = {}) => { guard(); return request<T>(prefix, path, { ...opts, method: 'DELETE' }); },
  };
}

/**
 * 后台没起来：fetch 直接失败（NETWORK_ERROR），或 Vite 代理连不上后台返回的非 JSON 5xx（HTTP_500/502/503/504）。
 * 后台自己出的 5xx 带统一错误体（code 不是 HTTP_xxx），不算。
 */
export const isBackendDown = (e: unknown): boolean =>
  isApiError(e) && (e.code === 'NETWORK_ERROR' || /^HTTP_5\d\d$/.test(e.code));

/** 页码分页统一返回（api-index §1.3） */
export interface PageResult<T> { items: T[]; total: number; page: number; size: number }
