import { onBeforeUnmount, onMounted, onUpdated, ref, watch, type Ref } from 'vue';

export type PageSizeMode = 'auto' | number;
export interface AdaptivePageSizeOptions {
  /** Observe the allocated viewport, never a list whose height depends on its row count. */
  container: Readonly<Ref<HTMLElement | null | undefined>>;
  rowHeight: number;
  rowSelector?: string;
  reserve?: number | (() => number);
  min?: number;
  max?: number;
  initialSize?: number;
  currentSize?: () => number;
  onSizeChange?: (size: number) => void;
  observe?: () => Element[];
}

export function pageSizeForHeight(height: number, rowHeight: number, reserve = 0, min = 5, max = 50, fallback = 10) {
  const count = Number.isFinite(height) && height > 0 && Number.isFinite(rowHeight) && rowHeight > 0
    ? Math.floor((height - (Number.isFinite(reserve) ? reserve : 0)) / rowHeight) : fallback;
  return Math.max(min, Math.min(max, count));
}

/** A bounded, debounced measurement; pages still fetch exactly the selected server-side size. */
export function useAdaptivePageSize(options: AdaptivePageSizeOptions) {
  const size = ref(options.initialSize ?? 10);
  const mode = ref<PageSizeMode>('auto');
  let observer: ResizeObserver | undefined;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let measuredRow = options.rowHeight;
  let observed: Element[] = [];
  let disposed = false;
  const apply = (next: number) => {
    const changed = (options.currentSize?.() ?? size.value) !== next;
    size.value = next;
    if (changed) options.onSizeChange?.(next);
  };
  const measure = () => {
    if (disposed) return;
    if (mode.value !== 'auto') { apply(mode.value); return; }
    const container = options.container.value;
    if (!container || container.clientHeight <= 0) return;
    const row = options.rowSelector ? container.querySelector<HTMLElement>(options.rowSelector) : null;
    const height = row?.getBoundingClientRect().height;
    if (height && height > 0) measuredRow = height;
    const reserve = typeof options.reserve === 'function' ? options.reserve() : options.reserve ?? 0;
    apply(pageSizeForHeight(container.clientHeight, measuredRow, reserve, options.min ?? 5, options.max ?? 50));
  };
  const schedule = () => {
    if (timer) clearTimeout(timer);
    if (!disposed && mode.value === 'auto') timer = setTimeout(measure, 120);
  };
  const connect = () => {
    if (!observer) { schedule(); return; }
    const container = options.container.value;
    const row = options.rowSelector && container?.querySelector(options.rowSelector);
    const elements = [container, row, ...(options.observe?.() ?? [])].filter((element): element is Element => !!element);
    if (elements.length !== observed.length || elements.some((element, i) => element !== observed[i])) {
      observer?.disconnect();
      observed = elements;
      for (const element of observed) observer?.observe(element);
    }
    schedule();
  };
  watch(mode, () => { if (timer) clearTimeout(timer); measure(); }, { flush: 'sync' });
  watch(options.container, connect, { flush: 'post' });
  onMounted(() => { observer = new ResizeObserver(schedule); connect(); });
  onUpdated(connect);
  onBeforeUnmount(() => { disposed = true; observer?.disconnect(); if (timer) clearTimeout(timer); });
  return { size, mode, measure };
}
