<script setup lang="ts">
import { onScopeDispose, ref, watch } from 'vue';
import { actionContextKey, listDeliveryExceptions, type EventDelivery } from '@/api/action';
import AppPagination from './AppPagination.vue';
import DeliveryReview from './DeliveryReview.vue';
import ObjRef from './ObjRef.vue';
import { deliveryLabels } from './deliveryPresentation';

const props = defineProps<{ typeId: string }>();
const rows = ref<EventDelivery[]>([]), total = ref(0), page = ref(1), state = ref(''), keyword = ref('');
const loading = ref(false), error = ref(''), selected = ref<string | null>(null);
let seq = 0;
onScopeDispose(() => { ++seq; });
watch([() => props.typeId, actionContextKey], () => {
  ++seq; selected.value = null; rows.value = []; total.value = 0; page.value = 1;
  state.value = ''; keyword.value = ''; error.value = '';
}, { flush: 'sync' });
watch([state, keyword], () => { page.value = 1; }, { flush: 'sync' });
async function load() {
  const n = ++seq, key = actionContextKey(), id = props.typeId;
  rows.value = []; total.value = 0; error.value = ''; loading.value = true;
  if (!id) { loading.value = false; return; }
  try {
    const result = await listDeliveryExceptions(id, state.value, keyword.value, page.value);
    if (n === seq && key === actionContextKey() && id === props.typeId) {
      rows.value = result.items; total.value = result.total; page.value = result.page;
    }
  } catch (e) { if (n === seq) error.value = e instanceof Error ? e.message : '读取异常列表失败'; }
  finally { if (n === seq) loading.value = false; }
}
watch([() => props.typeId, actionContextKey, state, keyword, page], load, { immediate: true });
const states = [{ value: '', label: '全部异常' }, ...['unknown', 'failed', 'pending', 'quarantined'].map(value => ({ value, label: deliveryLabels[value] }))];
const columns = [
  { title: '事件', key: 'event', width: 220 }, { title: '订阅方', dataIndex: 'name', key: 'name' },
  { title: '状态', key: 'state', width: 160 }, { title: '尝试', dataIndex: 'attempts', key: 'attempts', width: 64 },
  { title: '核查', key: 'review', width: 110 },
];
</script>

<template>
  <div class="exception-page">
    <div class="exception-tools">
      <a-select v-model:value="state" :options="states" class="exception-state" aria-label="异常状态" />
      <a-input-search v-model:value="keyword" placeholder="搜索事件编号或订阅方" allow-clear :maxlength="128" class="exception-search" @search="load" />
      <span class="exception-spacer" /><a-button :loading="loading" @click="load">刷新</a-button>
    </div>
    <a-alert v-if="error" type="error" :message="error" show-icon />
    <div class="paged-scroll">
      <a-table :columns="columns" :data-source="rows" :pagination="false" :loading="loading" row-key="id" size="middle" :locale="{ emptyText: '暂无投递异常' }">
        <template #bodyCell="{ column, record }">
          <template v-if="column.key === 'event'"><ObjRef kind="event" :id="record.eventId" /><div class="t-12 c-3">{{ record.isTest ? '本人测试' : record.objectId }}</div></template>
          <template v-else-if="column.key === 'name'"><div>{{ record.name }}</div><div v-if="record.error" class="t-12 c-3 exception-error" :title="record.error">{{ record.error === 'ORDERING_PREDECESSOR_UNRESOLVED' ? '待前序完成' : record.error }}</div></template>
          <template v-else-if="column.key === 'state'"><a-tag :color="record.state === 'failed' || record.state === 'quarantined' ? 'error' : 'warning'">{{ deliveryLabels[record.state] ?? record.state }}</a-tag></template>
          <template v-else-if="column.key === 'review'"><a-button size="small" @click="selected = record.id">核查与处置</a-button></template>
        </template>
      </a-table>
    </div>
    <AppPagination v-model:page="page" variant="simple" :total="total" :size="10" :empty="false" />
    <DeliveryReview :delivery-id="selected" @close="selected = null" @changed="load" />
  </div>
</template>

<style scoped>
.exception-page { display: flex; flex-direction: column; gap: 12px; flex: 1; min-height: 0; }
.exception-tools { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; }
.exception-state { width: 168px; }
.exception-search { width: 280px; max-width: 100%; }
.exception-spacer { flex: 1; }
.exception-error { max-width: 360px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
</style>
