import { createRouter, createWebHistory, type RouteRecordRaw, type RouteComponent } from 'vue-router';
import AppLayout from '@/layouts/AppLayout.vue';
import { PAGES } from '@/nav';
import { PDE_PAGES } from './pdePages';
import { useAppStore, PERSONAS, type AreaKey, type PersonaKey } from '@/stores/app';

const Placeholder = () => import('@/pages/PlaceholderPage.vue');

/** 二级页面路由全部由 src/nav.ts 生成：name = 页面 key，meta.navKey 用于菜单高亮和面包屑；页面组件取 nav.ts 的 component，其次 ./pdePages */
const pageRoutes: RouteRecordRaw[] = PAGES.map((p) => ({
  path: p.path,
  name: p.key,
  component: (p.component ?? PDE_PAGES[p.key] ?? Placeholder) as () => Promise<RouteComponent>,
  meta: { navKey: p.key },
}));

const routes: RouteRecordRaw[] = [
  {
    path: '/',
    component: AppLayout,
    children: [
      { path: '', name: 'home', component: () => import('@/pages/Home.vue'), meta: { title: '首页', crumbs: [], area: 'use' } },
      ...pageRoutes,
      { path: ':pathMatch(.*)*', name: 'not-found', component: () => import('@/pages/NotFound.vue'), meta: { title: '页面不存在' } },
    ],
  },
];

export const router = createRouter({ history: createWebHistory(), routes });

const areaOf = (navKey: unknown, metaArea: unknown): AreaKey | undefined =>
  PAGES.find((p) => p.key === navKey)?.area.key ?? (metaArea as AreaKey | undefined);

/** 演示身份（?as=viewer / ?as=admin）+ 区权限：无权限的区重定向回首页 */
router.beforeEach((to) => {
  const app = useAppStore();
  const as = to.query.as;
  if (typeof as === 'string' && as in PERSONAS && as !== app.persona) app.setPersona(as as PersonaKey);
  const area = areaOf(to.meta.navKey, to.meta.area);
  if (area && !app.can(area) && to.name !== 'home') return { name: 'home', query: as ? { as } : {} };
  // 历史版本：?ver= 决定 viewMode；在历史版本里切换页面时自动带上 ?ver=，点“返回当前版本”才退出
  const ver = typeof to.query.ver === 'string' ? to.query.ver : null;
  if (ver) app.setViewVersion(ver);
  else if (app.viewVersion) return { ...to, query: { ...to.query, ver: app.viewVersion } };
});

/** 记住每个区上次访问的路由（去掉抽屉、确认卡、演示身份参数），切换区时回到这里 */
const TRANSIENT_QUERY = ['drawer', 'drawer2', 'confirm', 'as', 'ver'];
router.afterEach((to) => {
  const area = areaOf(to.meta.navKey, to.meta.area);
  if (area && to.name !== 'not-found') {
    const query = { ...to.query };
    TRANSIENT_QUERY.forEach((k) => delete query[k]);
    useAppStore().rememberRoute(area, router.resolve({ path: to.path, query }).fullPath);
  }
  const t = (to.meta.title as string) ?? PAGES.find((p) => p.key === to.meta.navKey)?.title;
  document.title = t ? `${t} - 本体智能平台` : '本体智能平台';
});
