import { defineStore } from 'pinia';
import type { NavArea } from '@/nav';
import { NOTICES } from '@/mock';
import { releaseByVer } from '@/api/releases';
import { NEXT_VERSION as MOCK_NEXT_VERSION } from '@/mock/changes';

export const TENANTS = [
  { value: 'hd', label: '鲜邻零售集团 / 华东事业部' },
  { value: 'hn', label: '鲜邻零售集团 / 华南事业部' },
  { value: 'hq', label: '鲜邻零售集团 / 集团总部' },
];

export type AreaKey = NavArea['key'];

/**
 * 演示用户（mock）。permissions 决定能进入哪些区（使用 / 构建 / 管理）：
 * 没有权限的区，左侧切换按钮隐藏，直接访问该区路由会被重定向回首页。
 * 演示切换：URL 带 ?as=viewer（只有“使用”）或 ?as=admin（默认，三个区都有），在当前标签页内保持。
 */
export const PERSONAS = {
  admin: { name: '王敏', role: '数据产品经理', permissions: ['use', 'build', 'admin'] as AreaKey[] },
  viewer: { name: '李娜', role: '门店运营（只读）', permissions: ['use'] as AreaKey[] },
} as const;
export type PersonaKey = keyof typeof PERSONAS;

const PERSONA_KEY = 'ui-vue:persona';     // sessionStorage：只在当前标签页生效，不影响其他标签页和截图
const LAST_ROUTE_KEY = 'ui-vue:lastRoute'; // localStorage：每个区上次访问的路由

const loadPersona = (): PersonaKey => {
  try { const v = sessionStorage.getItem(PERSONA_KEY); return v && v in PERSONAS ? (v as PersonaKey) : 'admin'; } catch { return 'admin'; }
};
const loadLastRoute = (): Partial<Record<AreaKey, string>> => {
  try { return JSON.parse(localStorage.getItem(LAST_ROUTE_KEY) || '{}'); } catch { return {}; }
};

export const useAppStore = defineStore('app', {
  state: () => {
    const persona = loadPersona();
    return {
      tenant: 'hd',
      ontologyVersion: 'v2026.10.3',
      ontologyPublishedAt: '2026-10-24 21:17',
      /** 下一个待发布版本号：mock 模式取 mock 的 NEXT_VERSION；versioning 走真接口时由 GET /releases/current 的 next 覆盖 */
      ontologyNextVersion: MOCK_NEXT_VERSION,
      /** 正在查看的历史版本（URL ?ver=）；null = 当前版本 */
      viewVersion: null as string | null,
      notices: NOTICES.map((n) => ({ ...n })),
      persona,
      user: { name: PERSONAS[persona].name as string, role: PERSONAS[persona].role as string },
      permissions: [...PERSONAS[persona].permissions] as AreaKey[],
      /** 每个区上次访问的完整路由（不含抽屉参数），切换区时回到这里 */
      lastRoute: loadLastRoute(),
    };
  },
  getters: {
    can: (s) => (area: AreaKey) => s.permissions.includes(area),
    viewMode: (s): 'current' | 'history' => (s.viewVersion ? 'history' : 'current'),
    unread: (s) => s.notices.filter((n) => !n.read).length,
  },
  actions: {
    setPersona(p: PersonaKey) {
      this.persona = p;
      this.user = { name: PERSONAS[p].name, role: PERSONAS[p].role };
      this.permissions = [...PERSONAS[p].permissions];
      try { sessionStorage.setItem(PERSONA_KEY, p); } catch { /* 隐私模式 */ }
    },
    /** 由路由守卫根据 ?ver= 调用；等于当前版本或不存在的版本都视为当前版本 */
    setViewVersion(v: string | null) {
      this.viewVersion = v && v !== this.ontologyVersion && releaseByVer(v) ? v : null;
    },
    rememberRoute(area: AreaKey, fullPath: string) {
      this.lastRoute[area] = fullPath;
      try { localStorage.setItem(LAST_ROUTE_KEY, JSON.stringify(this.lastRoute)); } catch { /* 隐私模式 */ }
    },
  },
});
