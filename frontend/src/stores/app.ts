import { defineStore } from 'pinia';
import type { NavArea } from '@/nav';
import { NOTICES } from '@/mock';
import { releaseByVer } from '@/api/releases';
import { anyReal, isReal } from '@/api/config';
import { notificationContextKey } from '@/api/notifications';
import type { OntologyWorkspace } from '@/api/workspaces';
import { NEXT_VERSION as MOCK_NEXT_VERSION } from '@/mock/changes';

export const TENANTS = [{ value: 'hd', label: '仁谷科技' }];

export type AreaKey = NavArea['key'];

/**
 * 演示用户（mock）。permissions 决定能进入哪些区（使用 / 构建 / 管理）：
 * 没有权限的区，左侧切换按钮隐藏，直接访问该区路由会被重定向回首页。
 * 旧演示身份仅保留兼容字段；真实身份由登录会话决定，URL 参数不能切换身份。
 */
export const PERSONAS = {
  admin: { name: 'admin', role: '数据产品经理', permissions: ['use', 'build', 'admin'] as AreaKey[] },
  viewer: { name: '李娜', role: '门店运营（只读）', permissions: ['use'] as AreaKey[] },
} as const;
export type PersonaKey = keyof typeof PERSONAS;

const WORKSPACE_KEY = 'ui-vue:ontology';
const workspaceId = () => { try { return new URLSearchParams(location.search).get('ontology') ?? sessionStorage.getItem(WORKSPACE_KEY) ?? '1'; } catch { return '1'; } };
const hasSavedWorkspace = () => { try { return !!sessionStorage.getItem(WORKSPACE_KEY); } catch { return false; } };
const lastRouteKey = (id: string) => `ui-vue:lastRoute:${id}`;

const loadPersona = (): PersonaKey => {
  return 'admin';
};
const loadLastRoute = (id: string): Partial<Record<AreaKey, string>> => {
  try { return JSON.parse(localStorage.getItem(lastRouteKey(id)) || '{}'); } catch { return {}; }
};

export const useAppStore = defineStore('app', {
  state: () => {
    const persona = loadPersona();
    return {
      ontologyId: workspaceId(),
      workspaceSelectionSaved: hasSavedWorkspace(),
      workspace: null as OntologyWorkspace | null,
      workspaceLoading: false,
      workspaceError: '',
      workspaceEpoch: 0,
      tenant: 'hd',
      ontologyVersion: isReal('versioning') ? '—' : 'v2026.10.3',
      ontologyPublishedAt: isReal('versioning') ? '—' : '2026-10-24 21:17',
      /** 下一个待发布版本号：mock 模式取 mock 的 NEXT_VERSION；versioning 走真接口时由 GET /releases/current 的 next 覆盖 */
      ontologyNextVersion: isReal('versioning') ? '' : MOCK_NEXT_VERSION,
      /** 正在查看的历史版本（URL ?ver=）；null = 当前版本 */
      viewVersion: null as string | null,
      notices: anyReal() ? [] : NOTICES.map((n) => ({ ...n })),
      notificationUnread: 0,
      persona,
      user: { name: '', role: '' },
      permissions: [] as AreaKey[],
      /** 每个区上次访问的完整路由（不含抽屉参数），切换区时回到这里 */
      lastRoute: loadLastRoute(workspaceId()),
    };
  },
  getters: {
    can: (s) => (area: AreaKey) => s.permissions.includes(area),
    viewMode: (s): 'current' | 'history' => (s.viewVersion ? 'history' : 'current'),
    unread: (s) => anyReal() ? s.notificationUnread : s.notices.filter((n) => !n.read).length,
  },
  actions: {
    enterWorkspace(id: string, workspace: OntologyWorkspace | null, error = '') {
      this.ontologyId = id; this.workspace = workspace; this.workspaceError = error;
      this.viewVersion = null; this.ontologyVersion = '—'; this.ontologyPublishedAt = '—'; this.ontologyNextVersion = '';
      this.lastRoute = loadLastRoute(id); this.workspaceEpoch++;
      this.notificationUnread = 0;
      if (workspace) { this.workspaceSelectionSaved = true; try { sessionStorage.setItem(WORKSPACE_KEY, id); } catch { /* private mode */ } }
    },
    clearWorkspace() {
      this.enterWorkspace('', null);
      this.workspaceSelectionSaved = false;
      try { sessionStorage.removeItem(WORKSPACE_KEY); } catch { /* private mode */ }
    },
    /** Retained for old callers; a client persona cannot change a real session. */
    setPersona(_p: PersonaKey) {},
    /** 由路由守卫根据 ?ver= 调用；等于当前版本或不存在的版本都视为当前版本 */
    setViewVersion(v: string | null) {
      this.viewVersion = v && v !== this.ontologyVersion && releaseByVer(v) ? v : null;
    },
    setNotificationUnread(context: string, count: number) {
      if (context === notificationContextKey()) this.notificationUnread = count;
    },
    rememberRoute(area: AreaKey, fullPath: string) {
      this.lastRoute[area] = fullPath;
      try { localStorage.setItem(lastRouteKey(this.ontologyId), JSON.stringify(this.lastRoute)); } catch { /* 隐私模式 */ }
    },
  },
});
