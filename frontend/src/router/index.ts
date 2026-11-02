import { createRouter, createWebHistory, type RouteRecordRaw, type RouteComponent } from 'vue-router';
import AppLayout from '@/layouts/AppLayout.vue';
import { PAGES } from '@/nav';
import { PDE_PAGES } from './pdePages';
import { previewGuard, usePreviewStore } from '@/stores/preview';
import { invalidateWorkspaceRequests, isApiError } from '@/api/http';
import { currentWorkspace } from '@/api/workspaces';
import { anyReal } from '@/api/config';
import { loadReleases, RELEASES } from '@/api/releases';
import { resetDraftEdits } from '@/pages/ontology/useDraftEdits';
import { resetDraftState } from '@/pages/ontology/parts/useDraftView';
import { useConfirmStore } from '@/stores/confirm';
import { useAppStore, type AreaKey } from '@/stores/app';
import { useAuthStore } from '@/stores/auth';
import { safeReturnUrl } from '@/api/auth';

const Placeholder = () => import('@/pages/PlaceholderPage.vue');

/** 二级页面路由全部由 src/nav.ts 生成：name = 页面 key，meta.navKey 用于菜单高亮和面包屑；页面组件取 nav.ts 的 component，其次 ./pdePages */
const pageRoutes: RouteRecordRaw[] = PAGES.map((p) => ({
  path: p.path,
  name: p.key,
  component: (p.component ?? PDE_PAGES[p.key] ?? Placeholder) as () => Promise<RouteComponent>,
  meta: { navKey: p.key },
}));

const routes: RouteRecordRaw[] = [
  { path: '/login', name: 'login', component: () => import('@/pages/Login.vue'), meta: { title: '登录' } },
  {
    path: '/',
    component: AppLayout,
    children: [
      { path: '', name: 'home', component: () => import('@/pages/Home.vue'), meta: { title: anyReal() ? '零售经营看板' : '首页', crumbs: [], area: 'use' } },
      { path: 'workspaces', name: 'workspaces', component: () => import('@/pages/Workspaces.vue'), meta: { title: '本体工作空间', crumbs: [{ title: '平台' }, { title: '本体工作空间' }] } },
      ...pageRoutes,
      { path: ':pathMatch(.*)*', name: 'not-found', component: () => import('@/pages/NotFound.vue'), meta: { title: '页面不存在' } },
    ],
  },
];

export const router = createRouter({ history: createWebHistory(), routes });

const areaOf = (navKey: unknown, metaArea: unknown): AreaKey | undefined =>
  PAGES.find((p) => p.key === navKey)?.area.key ?? (metaArea as AreaKey | undefined);

router.beforeEach(async (to) => {
  const auth = useAuthStore();
  await auth.restore();
  if (!auth.authenticated) return to.name === 'login' ? true : { name: 'login', query: { returnUrl: safeReturnUrl(to.fullPath) } };
  if (to.name === 'login') return safeReturnUrl(to.query.returnUrl);
  // Retired impersonation links cannot change the authenticated identity.
  if ('as' in to.query || 'previewAs' in to.query) {
    const query = { ...to.query }; delete query.as; delete query.previewAs;
    return { path: to.path, hash: to.hash, query, replace: true };
  }
});

/** 本体与区权限：身份只来自已恢复的真实会话。 */
router.beforeEach((to) => {
  if (to.name === 'login') return;
  const app = useAppStore();
  if (anyReal() && to.name === 'home' && !Object.prototype.hasOwnProperty.call(to.query, 'ontology') && (app.workspaceError || !app.workspace && !app.workspaceSelectionSaved)) return { name: 'workspaces', query: to.query };
  if (to.name !== 'workspaces' && !Object.prototype.hasOwnProperty.call(to.query, 'ontology')) return { path: to.path, hash: to.hash, query: { ...to.query, ontology: app.ontologyId } };
});

let workspaceGuardSeq = 0;
let validatedIdentity = '';
router.beforeResolve(async (to, from) => {
  if (to.name === 'login' || !useAuthStore().authenticated) { validatedIdentity = ''; return; }
  const app = useAppStore();
  const seq = ++workspaceGuardSeq;
  if (to.name === 'workspaces') { app.workspaceLoading = false; return; }
  const id = typeof to.query.ontology === 'string' ? to.query.ontology : '';
  const identity = `${useAuthStore().user?.code}:${id}`;
  if (anyReal() && (identity !== validatedIdentity || app.workspaceError)) {
    app.workspaceLoading = true;
    let workspace;
    let error = '';
    try {
      if (!/^[1-9]\d*$/.test(id)) throw new Error('本体工作空间编号无效');
      workspace = await currentWorkspace(id);
    } catch (e) { error = isApiError(e) ? e.message : e instanceof Error ? e.message : '无法加载本体工作空间'; }
    if (seq !== workspaceGuardSeq) return false;
    invalidateWorkspaceRequests();
    useConfirmStore().settle(false);
    const preview = usePreviewStore();
    if (validatedIdentity || preview.session?.ontologyId !== id) preview.exit();
    resetDraftEdits(); resetDraftState();
    RELEASES.splice(0);
    app.enterWorkspace(id, workspace ?? null, error);
    validatedIdentity = workspace ? identity : '';
    if (workspace) {
      try {
        const release = await loadReleases();
        if (seq !== workspaceGuardSeq) return false;
        if (release.kind === 'empty') app.ontologyNextVersion = release.next ?? '';
        if (release.kind === 'ok') {
          app.ontologyVersion = release.current; app.ontologyPublishedAt = release.publishedAt; app.ontologyNextVersion = release.next;
        }
      } catch (e) { if ((e as Error)?.name !== 'AbortError') console.error('[workspace] 发布版本加载失败', e); }
    }
    if (seq !== workspaceGuardSeq) return false;
    app.workspaceLoading = false;
  }
  const switched = from.query.ontology !== to.query.ontology && !!from.name;
  if (switched && Object.keys(to.query).some(key => !['ontology', 'as'].includes(key))) {
    return { path: to.path, query: { ontology: id, ...(to.query.as ? { as: to.query.as } : {}) } };
  }
  if (anyReal() && to.name === 'home' && id !== '1' && !app.workspaceError) return { name: app.can('build') ? 'ontology-overview' : 'workspaces', query: to.query };
  if (!app.workspaceError) {
    const pvTo = previewGuard(to);
    if (pvTo) return pvTo;
    const area = areaOf(to.meta.navKey, to.meta.area);
    if (area && !usePreviewStore().active && !app.can(area) && to.name !== 'home') return { name: 'home', query: { ontology: id, ...(to.query.as ? { as: to.query.as } : {}) } };
  }
  const ver = typeof to.query.ver === 'string' ? to.query.ver : null;
  if (ver) app.setViewVersion(ver);
  else if (app.viewVersion && !switched) return { path: to.path, hash: to.hash, query: { ...to.query, ver: app.viewVersion } };
});

/** 记住每个区上次访问的路由（去掉抽屉、确认卡、演示身份参数），切换区时回到这里 */
const TRANSIENT_QUERY = ['drawer', 'drawer2', 'confirm', 'as', 'ver', 'previewAs', 'ontology'];
router.afterEach((to, _from, failure) => {
  if (failure) return;
  const area = areaOf(to.meta.navKey, to.meta.area);
  if (area && to.name !== 'not-found') {
    const query = { ...to.query };
    TRANSIENT_QUERY.forEach((k) => delete query[k]);
    useAppStore().rememberRoute(area, router.resolve({ path: to.path, query }).fullPath);
  }
  const t = (to.meta.title as string) ?? PAGES.find((p) => p.key === to.meta.navKey)?.title;
  document.title = t ? `${t} - 本体智能平台` : '本体智能平台';
});
