import { onBeforeUnmount, ref, watch, type Ref } from 'vue';

/** Commit text filters after typing settles; page and size changes remain immediate. */
export function useDebouncedRef(source: Ref<string>, delay = 250) {
  const value = ref(source.value);
  let timer: ReturnType<typeof setTimeout> | undefined;
  watch(source, (next) => {
    if (timer) clearTimeout(timer);
    timer = setTimeout(() => { value.value = next; }, delay);
  });
  onBeforeUnmount(() => { if (timer) clearTimeout(timer); });
  return value;
}
