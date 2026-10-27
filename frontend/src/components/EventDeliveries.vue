<script setup lang="ts">
import { computed, onScopeDispose, ref, watch } from 'vue';
import { actionContextKey, getEventDeliveries, type EventDeliveryStatus } from '@/api/action';
import AppPagination from './AppPagination.vue';
import ObjRef from './ObjRef.vue';

const props = defineProps<{ eventId: string }>();
const rows = ref<EventDeliveryStatus | null>(null);
const loading = ref(false);
const error = ref('');
const page = ref(1);
let seq = 0;
onScopeDispose(() => { ++seq; });
watch([() => props.eventId, actionContextKey], () => { page.value = 1; }, { flush: 'sync' });
async function load() {
  const n = ++seq;
  rows.value = null; error.value = ''; loading.value = true;
  try {
    const result = await getEventDeliveries(props.eventId, page.value);
    if (n === seq) { rows.value = result; page.value = result.page; }
  } catch (e) { if (n === seq) error.value = e instanceof Error ? e.message : '读取投递状态失败'; }
  finally { if (n === seq) loading.value = false; }
}
watch([() => props.eventId, actionContextKey, page], load, { immediate: true });
type Delivery = EventDeliveryStatus['items'][number];
const labels: Record<string, string> = {
  pending: '待投递', processing: '投递中', delivered: '已投递', skipped: '已跳过',
  failed: '投递失败', unknown: '结果待确认', quarantined: '迟到事件已隔离',
};
const reasons: Record<string, string> = {
  ORDERING_PREDECESSOR_UNRESOLVED: '待前序完成',
  ORDERING_LATE_EVENT_QUARANTINED: '更晚事件已开始投递，当前迟到事件已隔离',
  ORDERING_CHANNEL_NOT_CONFIGURED: '待配置有序投递通道',
  ORDERING_OBJECT_IDENTITY_REQUIRED: '待提供稳定对象身份',
  DELIVERY_SNAPSHOT_INCOMPLETE: '入站定义不完整，暂不投递',
  DELIVERY_ROUTE_NOT_CONFIGURED: '待配置投递通道',
  HTTP_ROUTE_NOT_CONFIGURED: '待配置HTTP投递通道',
  HTTP_DELIVERY_DISABLED: 'HTTP投递未启用',
  DELIVERY_IDENTITY_NOT_CONFIGURED: '待配置订阅身份',
  HTTP_IDEMPOTENCY_NOT_CONFIRMED: '待确认接收端幂等能力',
  HTTP_RESULT_UNKNOWN_REQUIRES_REVIEW: '接收结果未知，需人工核查',
  WORKER_INTERRUPTED_REQUIRES_REVIEW: '投递中断，需人工核查',
  EXECUTION_INTERRUPTED_REQUIRES_REVIEW: '处理结果未知，需人工核查',
  FILTER_NOT_MATCHED: '未匹配订阅过滤条件', SUBSCRIPTION_DISABLED: '订阅已停用',
  FILTER_NOT_EXECUTABLE: '过滤条件无法执行', HTTP_SUBSCRIBER_REJECTED: '订阅方明确拒绝',
  TEST_CHANNEL_NOT_CONFIGURED: '未配置独立测试通道',
  BUSINESS_TEST_CHANNEL_NOT_CONFIGURED: '测试事件不生成告警或动作',
};
function status(row: Delivery) {
  if (row.error === 'ORDERING_PREDECESSOR_UNRESOLVED') return '待前序完成';
  if (row.state === 'delivered' && row.result?.status === 'alert_created' && row.result.alertId) return '已生成告警';
  if (row.state === 'delivered' && row.result?.status === 'not_matched') return '规则已处理，未命中';
  if (row.state === 'delivered' && row.result?.status === 'deduped') return '规则已处理，已去重';
  return labels[row.state] ?? row.state;
}
const total = computed(() => rows.value?.total ?? 0);
</script>

<template>
  <div class="delivery-tools"><a-button size="small" :loading="loading" @click="load">刷新投递状态</a-button></div>
  <a-alert v-if="error" type="error" :message="error" show-icon />
  <a-spin :spinning="loading">
    <a-list v-if="rows?.items.length" :data-source="rows.items" size="small">
      <template #renderItem="{ item }">
        <a-list-item>
          <div class="delivery-row">
            <div class="delivery-title"><span class="fw-500">{{ item.name }}</span><a-tag :color="['failed', 'quarantined'].includes(item.state) ? 'error' : ['pending', 'unknown'].includes(item.state) ? 'warning' : 'default'">{{ status(item) }}</a-tag></div>
            <div v-if="item.error" class="t-12 c-2">{{ reasons[item.error] ?? (item.state === 'pending' ? '暂未满足投递条件' : '请核查投递详情') }}</div>
            <a-collapse ghost>
              <a-collapse-panel key="receipt" header="投递详情">
                <a-descriptions :column="1" size="small">
                  <a-descriptions-item label="实际尝试">{{ item.attempts }} 次</a-descriptions-item>
                  <a-descriptions-item v-if="item.error" label="原因代码">{{ item.error }}</a-descriptions-item>
                  <a-descriptions-item v-if="item.result?.blockedByEventId" label="前序事件"><ObjRef kind="event" :id="item.result.blockedByEventId" /></a-descriptions-item>
                  <a-descriptions-item v-if="item.result?.httpStatus" label="HTTP回执">{{ item.result.httpStatus }}</a-descriptions-item>
                  <a-descriptions-item v-if="item.result?.ruleId" label="规则">{{ item.result.ruleId }} {{ item.result.ruleVersion }}</a-descriptions-item>
                  <a-descriptions-item v-if="item.result?.alertId" label="告警"><ObjRef kind="alert" :id="item.result.alertId" /></a-descriptions-item>
                  <a-descriptions-item v-if="item.result?.actionId" label="动作"><ObjRef kind="action" :id="item.result.actionId" /></a-descriptions-item>
                </a-descriptions>
              </a-collapse-panel>
            </a-collapse>
          </div>
        </a-list-item>
      </template>
    </a-list>
    <a-empty v-else-if="!loading && !error" description="暂无投递记录" />
    <AppPagination v-if="rows && total" v-model:page="page" variant="simple" :total="total" :size="10" :empty="false" />
  </a-spin>
</template>

<style scoped>
.delivery-tools { display: flex; justify-content: flex-end; margin-bottom: 12px; }
.delivery-row { width: 100%; min-width: 0; }
.delivery-title { display: flex; justify-content: space-between; gap: 12px; margin-bottom: 4px; }
.delivery-title > span { overflow-wrap: anywhere; }
</style>
