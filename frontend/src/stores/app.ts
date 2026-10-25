import { defineStore } from 'pinia';
import type { NavArea } from '@/nav';
import { NOTICES, DRAFTS, releaseByVer } from '@/mock';

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
      /** 正在查看的历史版本（URL ?ver=）；null = 当前版本 */
      viewVersion: null as string | null,
      /** 当前加入的草稿（变更请求），null = 在看已发布版本 */
      activeDraft: null as string | null,
      /** 加入草稿弹窗（JoinDraftModal，全站一个） */
      joinDraft: { open: false, resolve: null as null | ((id: string | null) => void) },
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
    draft: (s) => DRAFTS.find((d) => d.id === s.activeDraft) ?? null,
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
    setDraft(id: string | null) { this.activeDraft = id; },
    /** 打开“加入草稿”弹窗，返回选中/新建的草稿号，取消返回 null */
    askJoinDraft(): Promise<string | null> {
      return new Promise((resolve) => { this.joinDraft = { open: true, resolve }; });
    },
    rememberRoute(area: AreaKey, fullPath: string) {
      this.lastRoute[area] = fullPath;
      try { localStorage.setItem(LAST_ROUTE_KEY, JSON.stringify(this.lastRoute)); } catch { /* 隐私模式 */ }
    },
  },
});
