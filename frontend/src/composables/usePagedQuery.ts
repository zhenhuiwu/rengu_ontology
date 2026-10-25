import { computed, ref, watch, type WatchSource } from 'vue';
import { useRoute, useRouter, type LocationQueryRaw } from 'vue-router';

/**
 * 分页状态同步到 URL：?page=&size=（第 1 页、默认条数时不写进 URL）。
 * - defaultSize：该容器在 1920×1080 下一屏正好放下的行数（每个页面按自己的容器高度给）。
 * - filters：筛选条件变化时自动回到第 1 页。
 * - prefix：同一页面有多个分页列表时区分参数，例如 prefix: 'u' → ?upage=&usize=。
 * - overflow：用户选了比默认更大的条数时为 true，此时把表格容器加上 .paged-scroll，
 *   让表格主体成为页面唯一的滚动区（表头吸顶），页面本身仍不出现滚动条。
 *
 *   const pq = usePagedQuery({ defaultSize: 15, filters: [status, kw] });
 *   const res = computed(() => paginate(rows.value, { page: pq.page.value, size: pq.size.value }));
 *   <AppPagination variant="full" v-model:page="pq.page.value" v-model:size="pq.size.value" :total="res.total" :default-size="pq.defaultSize" />
 */
export function usePagedQuery(opts: { defaultSize: number; filters?: WatchSource | WatchSource[]; prefix?: string }) {
  const route = useRoute();
  const router = useRouter();
  const pk = `${opts.prefix ?? ''}page`;
  const sk = `${opts.prefix ?? ''}size`;
  const num = (v: unknown, d: number) => {
    const n = parseInt(String(Array.isArray(v) ? v[0] : v ?? ''), 10);
    return Number.isFinite(n) && n > 0 ? n : d;
  };
  const setQuery = (patch: Record<string, number | null>) => {
    const q: LocationQueryRaw = { ...route.query };
    Object.entries(patch).forEach(([k, v]) => { if (v == null) delete q[k]; else q[k] = String(v); });
    router.replace({ query: q });
  };

  const page = computed<number>({
    get: () => num(route.query[pk], 1),
    set: (v) => setQuery({ [pk]: v > 1 ? v : null }),
  });
  const size = computed<number>({
    get: () => num(route.query[sk], opts.defaultSize),
    // 改条数时一并回到第 1 页（同一次 replace，避免两次导航互相覆盖）
    set: (v) => setQuery({ [sk]: v === opts.defaultSize ? null : v, [pk]: null }),
  });
  const overflow = computed(() => size.value > opts.defaultSize);
  const reset = () => { if (page.value !== 1) page.value = 1; };
  if (opts.filters) watch(opts.filters as WatchSource[], reset, { deep: true });

  return { page, size, overflow, reset, defaultSize: opts.defaultSize };
}

/**
 * 游标分页（cursor 变体：审计日志、事件流等海量数据，不显示总数）。
 * 当前游标写在 URL ?cursor=；“下一页”用 push（浏览器后退 = 上一页），“上一页”优先后退。
 */
export function useCursorQuery(opts: { size: number; filters?: WatchSource | WatchSource[] }) {
  const route = useRoute();
  const router = useRouter();
  const stack = ref<(string | null)[]>([]);
  const cursor = computed(() => (typeof route.query.cursor === 'string' ? route.query.cursor : null));
  const hasPrev = computed(() => !!cursor.value);
  const next = (c: string | null) => {
    if (!c) return;
    stack.value.push(cursor.value);
    router.push({ query: { ...route.query, cursor: c } });
  };
  const prev = () => {
    if (stack.value.length) { stack.value.pop(); router.back(); return; }
    const q = { ...route.query }; delete q.cursor; router.replace({ query: q });
  };
  const reset = () => {
    stack.value = [];
    if (cursor.value) { const q = { ...route.query }; delete q.cursor; router.replace({ query: q }); }
  };
  if (opts.filters) watch(opts.filters as WatchSource[], reset, { deep: true });
  return { cursor, size: opts.size, hasPrev, next, prev, reset };
}
