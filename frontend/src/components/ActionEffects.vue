<script setup lang="ts">
import { ref, watch } from 'vue';
import dayjs from 'dayjs';
import utc from 'dayjs/plugin/utc';
import { currentApiContext, isApiError } from '@/api/http';
import { effectsEnabled, listActionEffects, retryActionEffect, type ActionEffect } from '@/api/effects';
import { useReadonly } from '@/composables/useReadonly';
import { useConfirmStore } from '@/stores/confirm';
import AppPagination from './AppPagination.vue';

const props = defineProps<{ runId: string }>();
const items = ref<ActionEffect[]>([]), total = ref(0), page = ref(1), error = ref('');
const operationError = ref('');
const loading = ref(false), busy = ref(false), confirm = useConfirmStore();
const { readonly } = useReadonly();
const key = () => JSON.stringify([props.runId, currentApiContext()]);
let sequence = 0;
const labels: Record<string, string> = { queued: '待投递', retrying: '等待重试', unknown: '结果未知', rejected: '未发送', failed: '明确失败', succeeded: '成功' };
dayjs.extend(utc);
const timeText = (value: string) => dayjs(value).isValid() ? dayjs(value).utcOffset(8).format('YYYY-MM-DD HH:mm:ss') : '—';
const attemptRows = (item: ActionEffect) => item.history.map(row => ({ ...row, at: timeText(row.at) }));
function operationMessage(e: unknown) {
  if (isApiError(e) && e.code === 'VERSION_CONFLICT') return '投递状态已变化，请刷新后重试';
  if (isApiError(e) && e.code === 'EFFECT_RETRY_UNSAFE') return '当前结果不允许重新投递，请先核对执行状态';
  if (isApiError(e) && e.status === 403) return '当前身份无权重新排队';
  return '当前执行通道不可用，未重新排队，请联系管理员';
}
async function load() {
  const seq = ++sequence, snapshot = key();
  items.value = []; total.value = 0; error.value = ''; operationError.value = '';
  if (!effectsEnabled() || !props.runId) return;
  loading.value = true;
  try { const data = await listActionEffects(props.runId, page.value); if (seq === sequence && snapshot === key()) { items.value = data.items; total.value = data.total; page.value = data.page; } }
  catch (e) { if (seq === sequence && snapshot === key()) error.value = e instanceof Error ? e.message : '读取失败'; }
  finally { if (seq === sequence) loading.value = false; }
}
watch(key, () => { page.value = 1; void load(); }, { immediate: true });
watch(page, load);
async function retry(item: ActionEffect) {
  if (readonly.value || busy.value || !item.canRetry) return;
  const snapshot = key();
  const ok = await confirm.ask({ title: '重新排队副作用', target: props.runId, okText: '重新排队', fields: [{ label: '当前状态', value: labels[item.state] || item.state }, { label: '已尝试次数', value: String(item.attempts) }], impact: '使用原幂等键重新排队，执行前再次核验当前授权与绑定。结果未知的外部写入不允许在此重发。' });
  if (!ok || snapshot !== key()) return;
  busy.value = true; operationError.value = '';
  try { await retryActionEffect(props.runId, item); if (snapshot === key()) await load(); }
  catch (e) { if (snapshot === key()) operationError.value = operationMessage(e); }
  finally { busy.value = false; }
}
</script>

<template>
  <section v-if="effectsEnabled()" class="effects">
    <div class="effects-head"><span class="fw-600">副作用投递</span><a-button size="small" :loading="loading" :disabled="busy" @click="load">刷新</a-button></div>
    <a-alert v-if="error" type="error" show-icon :message="error" />
    <a-alert v-if="operationError" type="error" show-icon :message="operationError" />
    <a-empty v-if="!loading && !error && !items.length" description="暂无副作用投递" />
    <a-collapse v-if="items.length" ghost>
      <a-collapse-panel v-for="item in items" :key="item.id" :header="`${labels[item.state] || item.state} · 已尝试 ${item.attempts} 次`">
        <div class="effects-head"><span>{{ item.lastError || '—' }}</span><a-button size="small" :disabled="readonly || busy || !item.canRetry" :title="item.retryReason || ''" @click="retry(item)">重新排队</a-button></div>
        <p v-if="item.retryReason" class="t-12 c-3">{{ item.retryReason }}</p>
        <a-table :data-source="attemptRows(item)" :pagination="false" size="small" :row-key="(row: { attempt: number }) => row.attempt" :columns="[{ title: '次数', dataIndex: 'attempt' }, { title: '时间', dataIndex: 'at' }, { title: 'HTTP', dataIndex: 'httpStatus' }, { title: '结果', dataIndex: 'error' }, { title: '耗时毫秒', dataIndex: 'elapsedMs' }]" :scroll="{ x: 560 }" />
        <p v-for="(entry, index) in item.manualRetries" :key="index" class="t-12 c-3">{{ timeText(entry.at) }} · {{ entry.actor }} 重新排队</p>
      </a-collapse-panel>
    </a-collapse>
    <AppPagination v-if="total" v-model:page="page" :total="total" :size="10" variant="simple" :empty="false" />
  </section>
</template>

<style scoped>
.effects { min-width: 0; padding-top: 16px; }
.effects-head { display: flex; justify-content: space-between; align-items: center; gap: 12px; margin-bottom: 12px; overflow-wrap: anywhere; }
</style>
