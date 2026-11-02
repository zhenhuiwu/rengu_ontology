<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from 'vue';
import { currentApiContext } from '@/api/http';
import { loadNotificationSettings } from '@/api/settings';

const loading = ref(false), ready = ref(false), error = ref('');
const types = ref<string[]>([]);
const enabled = ref(false), externalConfigured = ref(false);
const typeLabel = (source: string) => source === 'cr-review' ? '历史变更通知' : source === 'page-subscription' ? '页面订阅' : source;
const typeDescription = (source: string) => source === 'cr-review' ? '历史变更记录相关消息' : source === 'page-subscription' ? '已订阅页面的更新消息' : '';
let sequence = 0;
const identity = () => currentApiContext().demoAs;
async function load() {
  const seq = ++sequence, key = identity(); loading.value = true; ready.value = false; error.value = '';
  try {
    const result = await loadNotificationSettings();
    if (seq === sequence && key === identity()) { enabled.value = result.inbox.enabled; types.value = result.inbox.sources; externalConfigured.value = result.externalChannelsConfigured; ready.value = true; }
  } catch (e) { if (seq === sequence && key === identity()) error.value = e instanceof Error ? e.message : '读取失败'; }
  finally { if (seq === sequence) loading.value = false; }
}
watch(identity, load, { immediate: true });
onBeforeUnmount(() => { sequence++; });
</script>

<template>
  <div class="notification-settings">
    <div class="notification-tools"><span class="t-12 c-2">通知服务状态</span><a-button size="small" :disabled="loading" @click="load">重新加载</a-button></div>
    <a-spin :spinning="loading">
      <a-alert v-if="error" type="error" :message="error" show-icon><template #action><a-button size="small" :disabled="loading" @click="load">重试</a-button></template></a-alert>
      <template v-else-if="ready">
        <section class="notification-panel" aria-label="站内通知">
          <header class="notification-heading"><h2 class="t-14 fw-600 c-1">站内通知</h2><a-badge :status="enabled ? 'success' : 'default'" :text="enabled ? '已启用' : '已停用'" /></header>
          <div class="notification-types">
            <div class="t-12 c-3 notification-label">通知类型</div>
            <ul v-if="types.length"><li v-for="source in types" :key="source"><span class="t-14 c-1">{{ typeLabel(source) }}</span><span v-if="typeDescription(source)" class="t-12 c-2">{{ typeDescription(source) }}</span></li></ul>
            <span v-else class="t-14 c-3">暂无通知类型</span>
          </div>
        </section>
        <section class="notification-external" aria-label="外部渠道"><span class="t-14 fw-600 c-1">外部渠道</span><span class="t-14 c-3">{{ externalConfigured ? '已配置' : '未配置' }}</span></section>
      </template>
    </a-spin>
  </div>
</template>

<style scoped>
.notification-settings { flex: 1; min-height: 0; overflow: auto; padding: 20px 24px 24px; display: flex; flex-direction: column; gap: 16px; }
.notification-tools { display: flex; align-items: center; justify-content: space-between; }
.notification-panel, .notification-external { max-width: 1040px; border: var(--border); border-radius: var(--radius); }
.notification-heading, .notification-external { display: flex; justify-content: space-between; align-items: center; gap: 16px; padding: 20px 24px; }
.notification-heading { border-bottom: var(--border); }
.notification-heading h2 { margin: 0; }
.notification-types { padding: 20px 24px; }
.notification-label { margin-bottom: 8px; }
.notification-types ul { list-style: none; margin: 0; padding: 0; }
.notification-types li { display: flex; flex-direction: column; gap: 6px; padding: 12px 0; }
.notification-types li + li { border-top: var(--border); }
.notification-external { margin-top: 16px; }
</style>
