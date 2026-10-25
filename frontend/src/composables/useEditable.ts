import { computed } from 'vue';
import { useAppStore } from '@/stores/app';

/**
 * 页面能否编辑（本体、指标、规则等建模页面统一用）。
 *   const { disabled, reason } = useEditable();
 *   <a-tooltip :title="reason"><a-button :disabled="disabled">保存</a-button></a-tooltip>
 * 历史版本只读；在已发布版本上没有加入草稿时也不能改，要先加入或新建草稿（JoinDraftModal）。
 */
export function useEditable() {
  const app = useAppStore();
  const disabled = computed(() => app.viewMode === 'history' || !app.activeDraft);
  const reason = computed(() =>
    app.viewMode === 'history'
      ? `正在查看历史版本 ${app.viewVersion}，只读`
      : !app.activeDraft
        ? '当前是已发布版本，加入或新建草稿后才能编辑'
        : '');
  return { disabled, reason };
}
