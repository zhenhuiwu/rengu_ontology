import { computed } from 'vue';
import { useAppStore } from '@/stores/app';

/**
 * 历史版本只读状态（顶栏选了旧版本，URL ?ver=）。
 *   const { readonly, reason } = useReadonly();
 *   <a-tooltip :title="reason"><a-button :disabled="readonly || !isDraft">保存</a-button></a-tooltip>
 * 只管历史版本；已发布 / 草稿的编辑规则在 pages/ontology/parts/useDraftView。
 */
export function useReadonly() {
  const app = useAppStore();
  const readonly = computed(() => app.viewMode === 'history');
  const reason = computed(() => (readonly.value ? `正在查看历史版本 ${app.viewVersion}，只读` : ''));
  return { readonly, reason };
}
