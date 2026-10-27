<script setup lang="ts">
import { ref, watch } from 'vue';
import { currentApiContext } from '@/api/http';
import { loadNotificationSettings } from '@/api/settings';

const loading = ref(false), ready = ref(false), error = ref('');
let sequence = 0;
const identity = () => currentApiContext().demoAs;
async function load() {
  const seq = ++sequence, key = identity(); loading.value = true; ready.value = false; error.value = '';
  try {
    const result = await loadNotificationSettings();
    if (seq === sequence && key === identity()) ready.value = result.inbox.enabled && result.inbox.sources.includes('cr-review');
  } catch (e) { if (seq === sequence && key === identity()) error.value = e instanceof Error ? e.message : '读取失败'; }
  finally { if (seq === sequence) loading.value = false; }
}
watch(identity, load, { immediate: true });
</script>

<template>
  <div class="st-body">
    <a-spin :spinning="loading">
      <a-alert v-if="error" type="error" :message="error" show-icon><template #action><a-button size="small" :disabled="loading" @click="load">重试</a-button></template></a-alert>
      <a-alert v-else-if="ready" type="success" message="站内通知：已启用" description="通知类型：变更单待审核" show-icon />
      <a-empty v-else-if="!loading" description="站内消息尚未就绪" />
    </a-spin>
    <p class="t-14 c-3" style="margin-top: 20px">外部渠道：未配置</p>
  </div>
</template>
