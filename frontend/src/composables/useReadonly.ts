import { computed } from 'vue';
import { useAppStore } from '@/stores/app';
import { usePreviewStore } from '@/stores/preview';

/**
 * 历史版本只读状态（顶栏选了旧版本，URL ?ver=）。
 *   const { readonly, reason } = useReadonly();
 *   <a-tooltip :title="reason"><a-button :disabled="readonly || !isDraft">保存</a-button></a-tooltip>
 * 历史版本、以及“以此用户预览”期间（stores/preview）都是只读；已发布 / 草稿的编辑规则在 pages/ontology/parts/useDraftView。
 */
export function useReadonly() {
  const app = useAppStore();
  const pv = usePreviewStore();
  const readonly = computed(() => app.viewMode === 'history' || pv.active || app.workspace?.role === 'viewer' || !!app.workspaceError);
  const reason = computed(() =>
    app.workspaceError ? app.workspaceError : app.workspace?.role === 'viewer' ? '当前本体工作空间为只读权限' : pv.active ? `正在以 ${pv.session!.user.name} 的权限预览，只读` : app.viewMode === 'history' ? `正在查看历史版本 ${app.viewVersion}，只读` : '');
  return { readonly, reason };
}
