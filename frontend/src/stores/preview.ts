import { defineStore } from 'pinia';
import type { RouteLocationNormalized, RouteLocationRaw } from 'vue-router';
import { PAGES, type NavArea } from '@/nav';
import { ADMIN_USERS, AUDIT_LOGS, type AdminUser } from '@/mock/admin';
import { effectiveAccess, type EffectiveAccess, type EffectiveObj, type FuncLevel } from '@/mock/roles';
import { useAppStore } from './app';

/**
 * “以此用户预览”（角色与权限 → 成员 → 以此用户预览 → 进入预览）。
 * 预览期间：顶部横幅“正在以 XXX 的权限预览 · 退出预览”；左侧导航只显示该用户有权限的区和页面；
 * 没权限的页面直接打开会被路由守卫转到该区第一个有权限的页面；useReadonly() 返回只读（写按钮全部禁用并提示原因）；
 * 所有确认卡（useConfirmStore().ask）在预览期间自动带阻断原因，不会执行写操作。
 * 页面里读取：usePreview()（src/composables/usePreview.ts）。
 * URL：?previewAs=U010231 直接进入（截图和分享用），?previewAs=off 退出。状态存 sessionStorage，只影响当前标签页。
 */
export interface PreviewSession {
  user: Pick<AdminUser, 'id' | 'name' | 'account' | 'dept'>;
  roles: string[];
  func: Record<string, FuncLevel>;   // 页面 key → 级别（没有的就是无权限）
  objs: EffectiveObj[];             // 能查看的对象类型及行级范围（scope = '全部' 表示不限）
  startedAt: string;
  returnTo: string;                 // 退出预览后回到哪里
}

const KEY = 'ui-vue:preview';
const load = (): PreviewSession | null => {
  try { return JSON.parse(sessionStorage.getItem(KEY) || 'null'); } catch { return null; }
};
const pad = (n: number) => String(n).padStart(2, '0');
const now = () => { const d = new Date(); return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`; };

export const usePreviewStore = defineStore('preview', {
  state: () => ({ session: load() }),
  getters: {
    active: (s) => !!s.session,
    /** 页面是否可见：不在预览时都可见（区权限仍由 app.can 管） */
    canPage: (s) => (key: string) => !s.session || (s.session.func[key] ?? 'none') !== 'none',
    canArea: (s) => (area: NavArea['key']) => !s.session || PAGES.some((p) => p.area.key === area && (s.session!.func[p.key] ?? 'none') !== 'none'),
    level: (s) => (key: string): FuncLevel => (s.session ? s.session.func[key] ?? 'none' : 'manage'),
    /** 某对象类型的行级范围文字：'全部' / '大区 = 华东' / '无权限'；不在预览时返回 null */
    scopeOf: (s) => (objType: string): string | null => {
      if (!s.session) return null;
      return s.session.objs.find((o) => o.name === objType)?.scope ?? '无权限';
    },
    /** 有行级限制的对象，例如 [{ name: '订单', scope: '大区 = 华东' }] */
    limited: (s): EffectiveObj[] => (s.session ? s.session.objs.filter((o) => o.scope !== '全部') : []),
  },
  actions: {
    /** 进入预览；access 传入时用它（角色页正在编辑的角色），否则按 mock 里的角色计算 */
    start(u: AdminUser, returnTo: string, access?: EffectiveAccess) {
      const a = access ?? effectiveAccess(u);
      this.session = {
        user: { id: u.id, name: u.name, account: u.account, dept: u.dept },
        roles: a.rs.map((r) => r.name),
        func: Object.fromEntries(a.func.map((p) => [p.key, p.lv])),
        objs: a.objs,
        startedAt: now(),
        returnTo,
      };
      this.persist();
      this.audit('开始预览');
    },
    exit(): string {
      const back = this.session?.returnTo ?? '/';
      if (this.session) this.audit('结束预览');
      this.session = null;
      this.persist();
      return back;
    },
    persist() {
      try { if (this.session) sessionStorage.setItem(KEY, JSON.stringify(this.session)); else sessionStorage.removeItem(KEY); } catch { /* 无痕模式 */ }
    },
    audit(action: string) {
      const app = useAppStore();
      const u = this.session!.user;
      AUDIT_LOGS.unshift({ id: `ALPV${Date.now()}`, time: now(), actor: app.user.name, action, target: `以 ${u.name}（${u.id}）的权限预览`, ip: '10.12.40.8', result: '成功' });
    },
  },
});

/** 某区第一个有权限的页面 */
export function firstAllowedPage(area?: NavArea['key']): string | null {
  const pv = usePreviewStore();
  const p = PAGES.find((x) => (!area || x.area.key === area) && pv.canPage(x.key));
  return p ? p.key : null;
}

/** 路由守卫（router/index.ts 调用）：处理 ?previewAs=，以及预览期间打开没权限的页面 */
export function previewGuard(to: RouteLocationNormalized): RouteLocationRaw | undefined {
  const pv = usePreviewStore();
  const as = to.query.previewAs;
  if (typeof as === 'string') {
    const query = { ...to.query };
    delete query.previewAs;
    if (as === 'off') pv.exit();
    else {
      const u = ADMIN_USERS.find((x) => x.id === as);
      if (u) pv.start(u, '/admin/system/roles');
    }
    return { path: to.path, query, hash: to.hash };
  }
  const key = to.meta.navKey as string | undefined;
  if (pv.active && key && !pv.canPage(key)) {
    const area = PAGES.find((p) => p.key === key)?.area.key;
    const target = firstAllowedPage(area) ?? firstAllowedPage();
    return target ? { name: target } : { name: 'home' };
  }
  return undefined;
}

