<script setup lang="ts">
/**
 * 高频问题推荐：按命中次数排序的高频问题（近 7 天 / 近 30 天），未认证的可“设为认证问答”（确认卡）。
 * 回答一致率低（同一问题多次回答的语义计划不一致）的优先处理。URL：?hp=7&hc=未认证&hkw=&hpage=
 */
import { computed, ref, watch, onBeforeUnmount } from 'vue';
import { useAppStore } from '@/stores/app';
import { useRoute, useRouter } from 'vue-router';
import { message } from 'ant-design-vue';
import AppPagination from '@/components/AppPagination.vue';
import { usePagedQuery } from '@/composables/usePagedQuery';
import { useConfirmStore } from '@/stores/confirm';
import { paginate } from '@/mock';
import { crById, type FreqQ } from '@/mock/qa-tuning';
import { metricOf, metricLabel, planText } from './catalog';
import { tuning, tuningTick, addCert, ME } from './state';
import { qaReal, listFreq, certifyFreq, qaRequestOptions, type QaFreq } from '@/api/qa';
import { qaLive as live, qaFallBackIfDown, qaWriteFail } from '@/pages/qa/qaFallback';
import { currentApiContext, type PageResult } from '@/api/http';
import MetricRef from './MetricRef.vue';

defineProps<{ readonly: boolean; reason?: string }>();
const route = useRoute();
const router = useRouter();
const confirm = useConfirmStore();
const q = (k: string) => (typeof route.query[k] === 'string' ? (route.query[k] as string) : '');
const setQuery = (patch: Record<string, string | undefined>) => router.replace({ query: { ...route.query, ...patch } });

const period = computed({ get: () => (q('hp') === '7' ? '7' : '30'), set: (v: string) => setQuery({ hp: v === '7' ? '7' : undefined }) });
const cF = computed({ get: () => q('hc') || undefined, set: (v?: string) => setQuery({ hc: v || undefined }) });
const kw = computed({ get: () => q('hkw'), set: (v: string) => setQuery({ hkw: v || undefined }) });
const hits = (f: FreqQ) => (period.value === '7' ? f.hits7 : f.hits30);
const ranked = computed(() => tuning.freq.slice().sort((a, b) => hits(b) - hits(a)));
const rankOf = (f: FreqQ) => (live.value ? (f as QaFreq).rank : ranked.value.indexOf(f) + 1);
const rows = computed(() => ranked.value.filter((f) => (!kw.value || f.question.includes(kw.value))
  && (!cF.value || (cF.value === '已认证' ? !!f.certId : !f.certId))));
const pq = usePagedQuery({ defaultSize: 15, filters: [period, cF, kw], prefix: 'h' });
/* 真接口（开关 qa）：T18 列表（排名、认证状态由后端给）/ T1 未认证计数；设为认证问答走 T19 */
const real = qaReal();
const contextKey = () => JSON.stringify(currentApiContext());
let listSeq = 0;
const app = useAppStore();
const remote = ref<PageResult<QaFreq>>({ items: [], total: 0, page: 1, size: 15 });
const listLoading = ref(false);
if (real) {
  watch(contextKey, () => { ++listSeq; remote.value = { items: [], total: 0, page: 1, size: pq.size.value }; }, { flush: 'sync' });
  watch([period, cF, kw, pq.page, pq.size, tuningTick, contextKey], async () => {
    if (!live.value) return;
    const n = ++listSeq, key = contextKey(); listLoading.value = true; remote.value = { ...remote.value, items: [], page: pq.page.value, size: pq.size.value };
    try {
      const rows = await listFreq({ period: period.value, cert: cF.value, kw: kw.value || undefined, page: pq.page.value, size: pq.size.value }, qaRequestOptions());
      if (n !== listSeq || key !== contextKey() || !live.value) return;
      remote.value = rows;
      if (rows.page !== pq.page.value) pq.page.value = rows.page;
    } catch (e) { if (n === listSeq && key === contextKey() && !qaFallBackIfDown(e)) message.error(`加载高频问题失败：${(e as Error).message}`); }
    finally { if (n === listSeq) listLoading.value = false; }
  }, { immediate: true });
}
onBeforeUnmount(() => { ++listSeq; });
const paged = computed(() => (live.value ? remote.value : paginate(rows.value, { page: pq.page.value, size: pq.size.value })));

const cols = [
  { title: '排名', key: 'rank', width: 60, align: 'right' },
  { title: '问题', key: 'question', width: 260, ellipsis: true },
  { title: '命中次数', key: 'hits', width: 90, align: 'right' },
  { title: '提问人数', key: 'users', width: 90, align: 'right' },
  { title: '当前理解 · 指标', key: 'metric', width: 230 },
  { title: '语义计划摘要', key: 'plan', ellipsis: true },
  { title: '回答一致率', key: 'cons', width: 100, align: 'right' },
  { title: '最近提问', key: 'last', width: 110 },
  { title: '认证', key: 'cert', width: 160 },
];
const certOf = (f: FreqQ) => (live.value ? (f.certId ? { status: (f as QaFreq).certStatus ?? '' } : undefined) : tuning.certs.find((c) => c.id === f.certId));
const openCert = (id: string) => setQuery({ tab: 'cert', cq: id });

const busyId = ref<string>();
async function certify(f: FreqQ) {
  if (busyId.value) return;
  busyId.value = f.id;
  try {
  const context = contextKey();
  const m = metricOf(f.plan.metric);
  const cr = !real && m.cr ? crById(m.cr) : undefined;
  const pendingCr = cr && cr.status !== '已发布' ? cr : undefined;
  const low = f.consistency < 0.7;
  const ok = await confirm.ask({
    title: '设为认证问答', target: f.question,
    fields: [
      { label: '命中', value: `近 7 天 ${f.hits7} 次 · 近 30 天 ${f.hits30} 次 · ${f.users} 人` },
      { label: '绑定指标', value: metricLabel(f.plan.metric) },
      { label: '语义计划', value: planText(f.plan) },
      { label: '认证人', value: real ? app.user.name : ME },
    ],
    impact: [
      real ? '将问题和语义计划保存为认证记录，可在认证问答页复核；问数执行链路尚未直接消费认证记录。' : '认证后相同或相近的问题直接按此语义计划回答，并标注“已认证”。',
      low ? `当前回答一致率只有 ${(f.consistency * 100).toFixed(0)}%，请确认上面的语义计划就是想要的理解。` : '',
      real && m.cr ? `指标受 ${m.cr} 影响，发布后需重新认证。` : pendingCr ? `${pendingCr.id}「${pendingCr.title}」${pendingCr.status}，发布后需重新认证。` : '',
    ].filter(Boolean).join(''),
    okText: '设为认证问答',
  });
  if (!ok || context !== contextKey()) return;
  if (real) {
    try { const c = await certifyFreq(f.id); if (context !== contextKey()) return; tuningTick.value++; message.success(`已保存认证问答 ${c.id}（${c.status}）`); } catch (e) { if (context === contextKey()) qaWriteFail('保存认证问答', e); }
    return;
  }
  const c = addCert(f.question, f.plan, '高频问题推荐', { hits: f.hits30, ...(pendingCr ? { status: '待重新认证' as const, cr: pendingCr.id, note: `已按 ${m.version} 认证；${pendingCr.id} 发布后需再次认证` } : {}) });
  message.success(`已设为认证问答 ${c.id}`);
  } finally { busyId.value = undefined; }
}
</script>

<template>
  <div class="qt-filters">
    <a-radio-group v-model:value="period" button-style="solid">
      <a-radio-button value="7">近 7 天</a-radio-button>
      <a-radio-button value="30">近 30 天</a-radio-button>
    </a-radio-group>
    <a-input-search v-model:value="kw" allow-clear placeholder="搜索问题" class="qt-kw" />
    <a-select v-model:value="cF" allow-clear placeholder="认证" class="qt-sel" :options="['未认证', '已认证'].map((s) => ({ value: s, label: s }))" />
    <span class="t-12 c-3 qt-hint">一致率 = 同一问题各次回答语义计划一致的比例</span>
  </div>
  <div class="qt-table" :class="{ 'paged-scroll': pq.overflow.value }">
    <a-table :locale="{ emptyText: '没有符合条件的问题' }" size="small" row-key="id" :columns="cols" :data-source="paged.items" :loading="listLoading" :pagination="false">
      <template #bodyCell="{ column, record }">
        <span v-if="column.key === 'rank'" class="num c-3">{{ rankOf(record) }}</span>
        <span v-else-if="column.key === 'question'" class="c-1" :title="record.question">{{ record.question }}</span>
        <span v-else-if="column.key === 'hits'" class="num c-1">{{ hits(record) }}</span>
        <span v-else-if="column.key === 'users'" class="num c-2">{{ record.users }}</span>
        <MetricRef v-else-if="column.key === 'metric'" :k="record.plan.metric" />
        <span v-else-if="column.key === 'plan'" class="c-2" :title="planText(record.plan)">{{ planText(record.plan).replace(metricLabel(record.plan.metric) + ' · ', '') }}</span>
        <span v-else-if="column.key === 'cons'" class="num" :class="record.consistency < 0.7 ? 'c-1 fw-600' : 'c-2'">{{ (record.consistency * 100).toFixed(0) }}%</span>
        <span v-else-if="column.key === 'last'" class="num c-2">{{ record.lastAsked.slice(5) }}</span>
        <span v-else-if="column.key === 'cert'">
          <a v-if="certOf(record)" class="c-2" @click="openCert(record.certId)">{{ certOf(record)!.status }} <span class="num">{{ record.certId }}</span></a>
          <a-tooltip v-else :title="reason"><a-button type="link" size="small" class="fq-btn" :disabled="readonly || !!busyId" :loading="busyId === record.id" @click="certify(record)">设为认证问答</a-button></a-tooltip>
        </span>
      </template>
    </a-table>
  </div>
  <AppPagination v-model:page="pq.page.value" v-model:size="pq.size.value" variant="full" :total="paged.total" :default-size="pq.defaultSize" unit="个问题" :empty="false" class="qt-pager" />
</template>

<style scoped src="./qt.css"></style>
<style scoped>
.fq-btn { padding: 0; }
</style>
