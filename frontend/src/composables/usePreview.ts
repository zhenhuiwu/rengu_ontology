import { computed } from 'vue';
import { storeToRefs } from 'pinia';
import { usePreviewStore } from '@/stores/preview';

/**
 * “以此用户预览”状态，给各页面读取（不需要预览的页面不用管：写按钮已由 useReadonly 统一禁用）。
 *   const { active, userName, scopeOf, scopeLabel } = usePreview();
 *   // 页头或筛选区显示行级范围：<span v-if="active" class="t-12 c-2">{{ scopeLabel('订单') }}</span>  → “数据范围：大区 = 华东”
 *   // 问数：按 scopeOf('订单') 给结果加说明“仅含 大区 = 华东 的数据”
 */
export function usePreview() {
  const store = usePreviewStore();
  const { session } = storeToRefs(store);
  const active = computed(() => store.active);
  const userName = computed(() => session.value?.user.name ?? '');
  const reason = computed(() => (active.value ? `正在以 ${userName.value} 的权限预览，只读` : ''));
  /** null = 不在预览；'全部' / '大区 = 华东' / '无权限' */
  const scopeOf = (objType: string) => store.scopeOf(objType);
  const scopeLabel = (objType: string) => {
    const s = store.scopeOf(objType);
    return s === null ? '' : s === '全部' ? `数据范围：全部${objType}` : s === '无权限' ? `无权查看${objType}` : `数据范围：${objType} ${s}`;
  };
  return { active, session, userName, reason, canPage: store.canPage, level: store.level, scopeOf, scopeLabel, limited: computed(() => store.limited) };
}
