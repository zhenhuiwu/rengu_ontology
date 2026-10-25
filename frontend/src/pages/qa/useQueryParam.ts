import { computed } from 'vue';
import { useRoute, useRouter } from 'vue-router';

/**
 * 把问数页的局部状态（翻页、搜索、筛选）写进 URL 查询参数，刷新或复制链接可复现。
 * 例：?sessPage=2、?tblPage=3。值等于默认值时从 URL 去掉，保持链接干净。
 */
export function useQueryParam(key: string, def = '') {
  const route = useRoute();
  const router = useRouter();
  return computed<string>({
    get: () => { const v = route.query[key]; return (Array.isArray(v) ? v[0] : v) ?? def; },
    set: (val) => {
      const q = { ...route.query };
      if (val === def || val === '') delete q[key]; else q[key] = val;
      router.replace({ query: q });
    },
  });
}

/** 页码版：非法值回落到 1 */
export function useQueryPage(key: string) {
  const raw = useQueryParam(key, '1');
  return computed<number>({
    get: () => { const n = parseInt(raw.value, 10); return Number.isFinite(n) && n > 0 ? n : 1; },
    set: (n) => { raw.value = String(n); },
  });
}
