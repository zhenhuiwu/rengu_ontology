/** Visible static pages stay independent of selected definition/editor buffers. */
import { computed, onBeforeUnmount, ref, shallowRef, watch, unref, type Ref } from 'vue';
import { fetchStaticPage, isStaticReal, staticContextKey } from '@/api/static';
import type { PageResult } from '@/api/http';
type Query = Record<string, string | number | boolean | undefined>;
export function useStaticPage<T>(path: () => string, query: () => Query, page: Ref<number>, size: number | Ref<number>, enabled: () => boolean = () => true) {
  const take = () => Math.min(200, Math.max(1, Math.floor(unref(size)) || 10));
  const result = shallowRef<PageResult<T> & Record<string, unknown>>({ items: [], total: 0, page: 1, size: take() });
  const loading = ref(false), error = ref('');
  let seq = 0, disposed = false;
  let request: AbortController | undefined;
  const key = () => JSON.stringify([staticContextKey(), path(), query(), page.value, take(), enabled()]);
  async function reload() {
    const n = ++seq, captured = key();
    request?.abort(); error.value = '';
    if (!isStaticReal() || !enabled() || !path()) { loading.value = false; return; }
    request = new AbortController();
    loading.value = true; error.value = '';
    result.value = { ...result.value, items: [] };
    const valid = () => !disposed && n === seq && captured === key();
    try {
      const data = await fetchStaticPage<T>(path(), { ...query(), page: page.value, size: take() }, request.signal);
      if (!valid()) return;
      result.value = data;
      if (data.page !== page.value) page.value = data.page;
    } catch (e) {
      if (valid() && (e as Error)?.name !== 'AbortError') error.value = e instanceof Error ? e.message : String(e);
    } finally { if (n === seq) loading.value = false; }
  }
  watch(() => JSON.stringify([staticContextKey(), path()]), () => { ++seq; request?.abort(); result.value = { items: [], total: 0, page: 1, size: take() }; error.value = ''; loading.value = false; }, { flush: 'sync' });
  watch(key, reload, { immediate: true });
  onBeforeUnmount(() => { disposed = true; ++seq; request?.abort(); });
  return { items: computed(() => result.value.items), total: computed(() => result.value.total), extra: computed(() => result.value), loading, error, reload };
}
