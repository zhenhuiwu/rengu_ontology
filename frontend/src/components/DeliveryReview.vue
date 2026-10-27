<script setup lang="ts">
import { computed, onScopeDispose, reactive, ref, watch } from 'vue';
import { actionContextKey, disposeDelivery, getDeliveryDetail, type DeliveryDecision, type DeliveryDetail } from '@/api/action';
import { currentApiContext } from '@/api/http';
import { useConfirmStore } from '@/stores/confirm';
import AppPagination from './AppPagination.vue';
import ObjRef from './ObjRef.vue';
import { decisionLabels, deliveryLabels, historyLabels } from './deliveryPresentation';

const props = defineProps<{ deliveryId: string | null }>();
const emit = defineEmits<{ close: []; changed: [] }>();
const confirm = useConfirmStore();
const detail = ref<DeliveryDetail | null>(null);
const loading = ref(false), saving = ref(false), error = ref(''), historyPage = ref(1);
const form = reactive({ decision: undefined as DeliveryDecision | undefined, reason: '', evidence: '', authorized: false });
const readonly = computed(() => { actionContextKey(); const ctx = currentApiContext(); return !!(ctx.ver || ctx.previewAs); });
let seq = 0;
onScopeDispose(() => { ++seq; });
watch([() => props.deliveryId, actionContextKey], () => {
  ++seq; detail.value = null; error.value = ''; historyPage.value = 1; saving.value = false;
  Object.assign(form, { decision: undefined, reason: '', evidence: '', authorized: false });
}, { flush: 'sync' });
async function load() {
  const id = props.deliveryId, key = actionContextKey(), n = ++seq;
  if (!id) return;
  loading.value = true; error.value = '';
  try {
    const result = await getDeliveryDetail(id, historyPage.value);
    if (n === seq && key === actionContextKey() && id === props.deliveryId) { detail.value = result; historyPage.value = result.history.page; }
  } catch (e) { if (n === seq) error.value = e instanceof Error ? e.message : '读取详情失败'; }
  finally { if (n === seq) loading.value = false; }
}
watch([() => props.deliveryId, actionContextKey, historyPage], load, { immediate: true });
const choices = computed(() => (detail.value?.decisions ?? []).map(value => ({ value, label: decisionLabels[value] })));
const canSubmit = computed(() => !!detail.value?.canManage && !readonly.value && !!form.decision
  && form.reason.trim().length >= 5 && form.evidence.trim().length >= 5 && form.authorized && !loading.value && !saving.value);
async function submit() {
  const row = detail.value, decision = form.decision, key = actionContextKey(), n = seq;
  if (!canSubmit.value || !row || !decision) return;
  const body = { version: row.version, decision, reason: form.reason.trim(), evidence: form.evidence.trim(), authorized: true as const };
  const ok = await confirm.ask({
    title: decisionLabels[decision], target: `${row.eventId} · ${row.name}`, tag: row.isTest ? '测试事件' : '生产事件',
    fields: [{ label: '原状态', value: deliveryLabels[row.state] ?? row.state }, { label: '处置理由', value: body.reason }, { label: '核查证据', value: body.evidence }],
    impact: decision === 'received' ? '保存人工确认结论并解除该订阅的前序阻塞。原始尝试和回执保留。'
      : decision === 'skip' ? '该订阅将停止投递，后序事件可以继续。原失败和核查证据保留。'
        : '按原事件及订阅快照重新排队，沿用原幂等键；通过前序和通道检查后才执行。',
    irreversible: true, danger: decision === 'skip', okText: '确认处置',
  });
  if (!ok || n !== seq || key !== actionContextKey() || row.id !== props.deliveryId) return;
  saving.value = true; error.value = '';
  try {
    await disposeDelivery(row.id, body);
    if (n === seq && key === actionContextKey()) {
      Object.assign(form, { decision: undefined, reason: '', evidence: '', authorized: false });
      await load(); emit('changed');
    }
  } catch (e) { if (n === seq) error.value = e instanceof Error ? e.message : '处置失败'; }
  finally { if (key === actionContextKey() && row.id === props.deliveryId) saving.value = false; }
}
</script>

<template>
  <a-modal :open="!!deliveryId" title="投递核查与处置" :width="760" :footer="null" @cancel="emit('close')">
    <a-alert v-if="error" type="error" :message="error" show-icon class="review-gap" />
    <a-spin :spinning="loading">
      <template v-if="detail">
        <a-descriptions :column="2" size="small" bordered>
          <a-descriptions-item label="事件"><ObjRef kind="event" :id="detail.eventId" /></a-descriptions-item>
          <a-descriptions-item label="订阅方">{{ detail.name }}</a-descriptions-item>
          <a-descriptions-item label="状态">{{ deliveryLabels[detail.state] ?? detail.state }}</a-descriptions-item>
          <a-descriptions-item label="实际尝试">{{ detail.attempts }} 次</a-descriptions-item>
          <a-descriptions-item label="原原因" :span="2">{{ detail.error ?? '—' }}</a-descriptions-item>
          <a-descriptions-item label="幂等键" :span="2"><span class="review-wrap num">{{ detail.idempotencyKey }}</span></a-descriptions-item>
          <a-descriptions-item v-if="detail.result?.httpStatus" label="原HTTP回执">{{ detail.result.httpStatus }}</a-descriptions-item>
          <a-descriptions-item v-if="detail.result?.blockedByEventId" label="未决前序"><ObjRef kind="event" :id="detail.result.blockedByEventId" /></a-descriptions-item>
          <a-descriptions-item v-if="detail.result?.alertId" label="已有告警"><ObjRef kind="alert" :id="detail.result.alertId" /></a-descriptions-item>
          <a-descriptions-item v-if="detail.result?.actionId" label="已有动作"><ObjRef kind="action" :id="detail.result.actionId" /></a-descriptions-item>
          <a-descriptions-item v-if="detail.result?.hitId" label="已有命中">{{ detail.result.hitId }}</a-descriptions-item>
        </a-descriptions>
        <section v-if="detail.canManage && !readonly && choices.length" class="review-section">
          <div class="fw-600 review-gap">记录核查结论</div>
          <a-form layout="vertical">
            <a-form-item label="处置方式" required><a-select v-model:value="form.decision" :options="choices" placeholder="选择明确核查结论" /></a-form-item>
            <a-form-item label="处置理由" required><a-textarea v-model:value="form.reason" :maxlength="1000" :rows="2" placeholder="说明此次处置的业务原因，至少5个字" /></a-form-item>
            <a-form-item label="核查证据" required><a-textarea v-model:value="form.evidence" :maxlength="2000" :rows="3" placeholder="填写接收端查询记录、工单或核查人员及时间。重试必须已明确确认未接收；至少5个字" /></a-form-item>
            <a-checkbox v-model:checked="form.authorized">已核查上述证据，并授权执行所选处置</a-checkbox>
            <div class="review-submit"><a-button type="primary" :disabled="!canSubmit" :loading="saving" @click="submit">核对并确认处置</a-button></div>
          </a-form>
        </section>
        <div v-else-if="readonly" class="t-12 c-3 review-section">历史版本与权限预览只读。</div>
        <div v-else-if="!detail.canManage" class="t-12 c-3 review-section">需要事件管理权限才能记录处置。</div>
        <section class="review-section">
          <div class="review-history-title"><span class="fw-600">尝试与处置记录</span><a-button size="small" :loading="loading" @click="load">刷新</a-button></div>
          <a-list v-if="detail.history.items.length" :data-source="detail.history.items" size="small">
            <template #renderItem="{ item }">
              <a-list-item><div class="review-wrap">
                <div>{{ historyLabels[item.kind] ?? item.kind }} · {{ deliveryLabels[item.state] ?? item.state }} · 第 {{ item.attempt }} 次</div>
                <div class="t-12 c-3">{{ item.at }} · {{ item.actor }}</div>
                <div v-if="item.error" class="t-12">{{ item.error }}</div>
                <div v-if="item.detail.previousError" class="t-12">前次原因：{{ item.detail.previousError }}</div>
                <div v-if="item.detail.decision">{{ decisionLabels[item.detail.decision as DeliveryDecision] ?? item.detail.decision }}</div>
                <div v-if="item.detail.reason">理由：{{ item.detail.reason }}</div>
                <div v-if="item.detail.evidence">证据：{{ item.detail.evidence }}</div>
                <div v-if="item.result?.httpStatus" class="t-12">HTTP回执：{{ item.result.httpStatus }}</div>
                <div v-if="item.result?.alertId" class="t-12">告警：<ObjRef kind="alert" :id="item.result.alertId" /></div>
                <div v-if="item.result?.actionId" class="t-12">动作：<ObjRef kind="action" :id="item.result.actionId" /></div>
              </div></a-list-item>
            </template>
          </a-list>
          <a-empty v-else description="暂无追加记录，原始尝试次数及最后回执见上方" :image-style="{ height: '44px' }" />
          <AppPagination v-if="detail.history.total" v-model:page="historyPage" variant="simple" :total="detail.history.total" :size="10" :empty="false" />
        </section>
      </template>
    </a-spin>
  </a-modal>
</template>

<style scoped>
.review-section { margin-top: 20px; padding-top: 16px; border-top: var(--border); }
.review-gap { margin-bottom: 12px; }
.review-wrap { overflow-wrap: anywhere; min-width: 0; white-space: pre-wrap; }
.review-submit { display: flex; justify-content: flex-end; margin-top: 16px; }
.review-history-title { display: flex; justify-content: space-between; align-items: center; }
</style>
