/**
 * mock / 真接口开关（前后端联调，docs/backend/integration.md）。
 *
 * - VITE_API_MODE=mock（默认）：全部走 src/mock，界面和现在完全一样，不发任何 /api 请求。
 * - VITE_API_MODE=real：已接线的模块全部走真接口。
 * - VITE_API_REAL_MODULES=ontology,biz（可选，逗号分隔）：
 *     mock 模式下只把列出的模块切到真接口；real 模式下只有列出的模块走真接口（不写 = 全部）。
 * 模块名就是接口前缀 /api/<模块>/v1 里的模块（api-index §1.1）。
 */
export type ApiModule = 'ontology' | 'qa' | 'data' | 'admin' | 'biz';
export const API_MODULES: readonly ApiModule[] = ['ontology', 'qa', 'data', 'admin', 'biz'];

const mode: 'mock' | 'real' = import.meta.env.VITE_API_MODE === 'real' ? 'real' : 'mock';
const listed = new Set(
  String(import.meta.env.VITE_API_REAL_MODULES ?? '')
    .split(',')
    .map((s) => s.trim())
    .filter((s): s is ApiModule => (API_MODULES as readonly string[]).includes(s)),
);

const realSet: ReadonlySet<ApiModule> = mode === 'real' && listed.size === 0 ? new Set(API_MODULES) : listed;

export const API_MODE = mode;
/** 某个模块是否走真接口 */
export const isReal = (m: ApiModule): boolean => realSet.has(m);
/** 是否有任何模块走真接口（全 mock 时启动流程和现在完全一样） */
export const anyReal = (): boolean => realSet.size > 0;
export const realModules = (): ApiModule[] => API_MODULES.filter((m) => realSet.has(m));
