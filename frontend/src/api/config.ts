/**
 * mock / 真接口开关（前后端联调，docs/backend/integration.md）。
 *
 * 开关键 ≠ 接口前缀：开关按「负责的后台模块」切，接口地址按 api-index §1.1 的前缀拼。
 * static / dynamic / action / versioning 都挂在 /api/ontology/v1 下，但各自独立切换。
 *
 * - VITE_API_MODE=mock（默认）：全部走 src/mock，界面和现在完全一样，不发任何 /api 请求。
 * - VITE_API_MODE=real：已接线的模块全部走真接口。
 * - VITE_API_REAL_MODULES=versioning,biz（可选，逗号分隔，取值见 SWITCH_KEYS）：
 *     mock 模式下只把列出的模块切到真接口；real 模式下只有列出的模块走真接口（不写 = 全部）。
 * 每个接口文件用 apiClient(开关键, 前缀) 声明自己归哪个开关（src/api/http.ts）。
 */

/** 接口前缀：/api/<prefix>/v1（api-index §1.1） */
export type ApiPrefix = 'ontology' | 'qa' | 'data' | 'admin' | 'biz';

/** 开关键 = 后台负责模块 → 它的接口前缀 */
export const SWITCH_PREFIX = {
  static: 'ontology',      // 静态本体：对象类型、关系、指标、维度、业务知识…
  dynamic: 'ontology',     // 动态本体：规则、告警、action-runs…
  action: 'ontology',      // 动作 / 事件类型（Palantir）
  versioning: 'ontology',  // 发布记录、快照
  qa: 'qa',
  biz: 'biz',
  admin: 'admin',
  data: 'data',            // 数据接入
  integration: 'data',     // 外部系统 / 写回（代码在 integration，接口挂 /api/data/v1，api-index §1.1）
} as const satisfies Record<string, ApiPrefix>;

export type SwitchKey = keyof typeof SWITCH_PREFIX;
export const SWITCH_KEYS = Object.keys(SWITCH_PREFIX) as SwitchKey[];
const isSwitchKey = (s: string): s is SwitchKey => s in SWITCH_PREFIX;

const mode: 'mock' | 'real' = import.meta.env.VITE_API_MODE === 'real' ? 'real' : 'mock';
const raw = String(import.meta.env.VITE_API_REAL_MODULES ?? '').split(',').map((s) => s.trim()).filter(Boolean);
const unknown = raw.filter((s) => !isSwitchKey(s));
if (unknown.length) {
  // 例如旧写法 ontology：前缀不是开关键，提示一下（只在配置了这个变量时才可能出现）
  console.warn(`[api] VITE_API_REAL_MODULES 里有未知开关键：${unknown.join(', ')}；可用：${SWITCH_KEYS.join(', ')}`);
}
const listed = new Set(raw.filter(isSwitchKey));

const realSet: ReadonlySet<SwitchKey> = mode === 'real' && listed.size === 0 ? new Set(SWITCH_KEYS) : listed;

export const API_MODE = mode;
/** 某个开关键是否走真接口 */
export const isReal = (k: SwitchKey): boolean => realSet.has(k);
/** 是否有任何模块走真接口（全 mock 时启动流程和现在完全一样） */
export const anyReal = (): boolean => realSet.size > 0;
export const realModules = (): SwitchKey[] => SWITCH_KEYS.filter((k) => realSet.has(k));
