<script setup lang="ts">
/**
 * 静态本体页的取数闸门（src/api/static.ts）：
 * - mock 模式：直接渲染页面本体，和以前完全一样（不发请求）；
 * - real 模式：任何接口错误停在错误页，不显示演示数据。
 * - real 模式：paged页面自行取可见页；其余按sets加载所需完整目录；切换历史版本（?ver=）时重新拉取并重建页面本体。
 */
import { computed, ref, watch } from 'vue';
import { isApiError, isBackendDown } from '@/api/http';
import { isStaticReal, loadStatic, staticContextKey, type StaticDataset } from '@/api/static';

const props = defineProps<{ sets: StaticDataset[]; paged?: boolean }>();
const state = ref<'loading' | 'ready' | 'error'>(isStaticReal() ? 'loading' : 'ready');
const error = ref('');
const loadedKey = ref('');
const bodyKey = computed(() => loadedKey.value);
const ready = computed(() => state.value === 'ready' && (!isStaticReal() || loadedKey.value === staticContextKey()));
let seq = 0;

async function load() {
  const my = ++seq;
  const key = staticContextKey();
  state.value = isStaticReal() ? 'loading' : 'ready';
  error.value = '';
  try {
    await loadStatic(props.paged ? [] : props.sets);
    if (my !== seq || key !== staticContextKey()) return;
    loadedKey.value = key;
    state.value = 'ready';
  } catch (e) {
    if (my !== seq || key !== staticContextKey()) return;
    console.error('[api] 静态本体接口加载失败', e);
    error.value = isBackendDown(e) ? '后台未启动' : `接口加载失败（${isApiError(e) ? `${e.code}：${e.message}` : String(e)}）`;
    state.value = 'error';
  }
}
watch(staticContextKey, load, { immediate: true });
</script>

<template>
  <slot v-if="ready" :ver-key="bodyKey" />
  <div v-else-if="state === 'loading'" style="padding: 120px 0; text-align: center"><a-spin tip="加载中…" /></div>
  <a-result v-else status="warning" title="静态本体数据加载失败" :sub-title="error">
    <template #extra><a-button type="primary" @click="load">重试</a-button></template>
  </a-result>
</template>
