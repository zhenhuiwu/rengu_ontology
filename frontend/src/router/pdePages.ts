/**
 * PDE工程师负责的二级页面组件（数据接入、函数、系统管理）。
 * src/nav.ts 里没填 component 的页面，路由先在这里找，找不到才用占位页。
 * 这样新增页面不用改 nav.ts（nav.ts 有其他同事在改）。
 */
export const PDE_PAGES: Record<string, () => Promise<unknown>> = {
  connections: () => import('@/pages/data/Connections.vue'),
  'external-systems': () => import('@/pages/data/ExternalSystems.vue'),
  'data-quality': () => import('@/pages/data/DataQuality.vue'),
};
