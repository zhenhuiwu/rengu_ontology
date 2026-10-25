<script setup lang="ts">
/**
 * 静态本体页的取数闸门（src/api/static.ts）：
 * - mock 模式：直接渲染页面本体，和以前完全一样（不发请求）；
 * - real 模式：先按 sets 把整表从静态接口拉下来，再挂载页面本体；切换历史版本（?ver=）时重新拉取并重建页面本体。
 */
import { computed, ref, watch } from 'vue';
import { useAppStore } from '@/stores/app';
import { isApiError, isBackendDown } from '@/api/http';
import { isStaticReal, loadStatic, type StaticDataset } from '@/api/static';

const props = defineProps<{ sets: StaticDataset[] }>();
const app = useAppStore();
const real = isStaticReal();
const state = ref<'loading' | 'ready' | 'error'>(real ? 'loading' : 'ready');
const error = ref('');
const loadedVer = ref<string | null>(null);
const bodyKey = computed(() => loadedVer.value ?? 'current');

async function load() {
  if (!real) return;
  const ver = app.viewVersion ?? null;
  state.value = 'loading';
  try {
    await loadStatic(props.sets);
    loadedVer.value = ver;
    state.value = 'ready';
  } catch (e) {
    console.error('[api] 静态本体接口加载失败', e);
    error.value = isBackendDown(e) ? '后台未启动' : `接口加载失败（${isApiError(e) ? `${e.code}：${e.message}` : String(e)}）`;
    state.value = 'error';
  }
}
if (real) {
  load();
  watch(() => app.viewVersion, (v) => { if ((v ?? null) !== loadedVer.value) load(); });
}
</script>

<template>
  <slot v-if="state === 'ready'" :ver-key="bodyKey" />
  <div v-else-if="state === 'loading'" style="padding: 120px 0; text-align: center"><a-spin tip="加载中…" /></div>
  <a-result v-else status="warning" title="静态本体数据加载失败" :sub-title="error">
    <template #extra><a-button type="primary" @click="load">重试</a-button></template>
  </a-result>
</template>
