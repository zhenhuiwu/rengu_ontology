import { onBeforeUnmount, onMounted, unref, type Ref } from 'vue';
import { onBeforeRouteLeave, onBeforeRouteUpdate } from 'vue-router';
import { Modal } from 'ant-design-vue';

/** 弹出“有未保存的修改”确认，放弃修改返回 true */
export function confirmDiscard(): Promise<boolean> {
  return new Promise((resolve) => {
    Modal.confirm({
      title: '有未保存的修改',
      content: '离开后，本次修改不会保存。',
      okText: '放弃修改', cancelText: '继续编辑', okButtonProps: { danger: true },
      onOk: () => resolve(true), onCancel: () => resolve(false),
    });
  });
}

/**
 * 未保存修改保护。
 *   const dirty = ref(false);
 *   const guard = useUnsavedGuard(dirty);
 *   // 切换页签 / 切换列表选中项：
 *   const onSelect = (id: string) => guard(() => { selected.value = id; dirty.value = false; });
 * - 离开当前路由（侧栏、面包屑、浏览器后退）时自动拦截；只改 query（如打开抽屉、翻页）不拦截。
 * - 关闭或刷新浏览器标签页时，用浏览器自带的提示。
 * - guard(fn) 返回 Promise<boolean>，true 表示已执行 fn。
 */
export function useUnsavedGuard(dirty: Ref<boolean> | (() => boolean)) {
  const isDirty = () => (typeof dirty === 'function' ? dirty() : unref(dirty));
  onBeforeRouteLeave(async () => (isDirty() ? await confirmDiscard() : true));
  onBeforeRouteUpdate(async (to, from) => to.query.ontology !== from.query.ontology && isDirty() ? await confirmDiscard() : true);
  const onUnload = (e: BeforeUnloadEvent) => { if (isDirty()) { e.preventDefault(); e.returnValue = ''; } };
  onMounted(() => window.addEventListener('beforeunload', onUnload));
  onBeforeUnmount(() => window.removeEventListener('beforeunload', onUnload));
  return async function guard(fn: () => void): Promise<boolean> {
    if (isDirty() && !(await confirmDiscard())) return false;
    fn();
    return true;
  };
}
