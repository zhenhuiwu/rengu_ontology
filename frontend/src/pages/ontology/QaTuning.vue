<script setup lang="ts">
/**
 * 问数调优（/build/ontology/qa-tuning）
 * 页签：认证问答（?tab=cert）、评测题库（eval）、反馈与纠错（feedback）、高频问题推荐（freq）。
 * 路线 B：问数不写 SQL；回答由本体查询模块按语义计划生成。与 changes.ts 的 CR-0010 / CR-0011 / CR-0008 对齐。
 */
import { computed, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { message } from 'ant-design-vue';
import { useReadonly } from '@/composables/useReadonly';
import { useConfirmStore } from '@/stores/confirm';
import { useAppStore } from '@/stores/app';
import { EVAL_QS, EVAL_GROUPS, T_METRICS, TUNING_CUTOFF, TUNING_DIMS, TUNING_TIMES, type SemPlan } from '@/mock/qa-tuning';
import { tuning, tuningTick, addCert, startRun } from './qa-tuning/state';
import { qaReal, getTuningSummary, getPlanOptions, listEvalQuestions, createCert as apiCreateCert, startEvalRun, qaRequestOptions, type QaTuningSummary } from '@/api/qa';
import { qaLive as live, qaFallBackIfDown } from '@/pages/qa/qaFallback';
import { ApiError, currentApiContext } from '@/api/http';
import { setPlanMetrics, planText } from './qa-tuning/catalog';
import { useQaSession } from '@/pages/qa/useQaSession';
import CertTab from './qa-tuning/CertTab.vue';
import EvalTab from './qa-tuning/EvalTab.vue';
import FeedbackTab from './qa-tuning/FeedbackTab.vue';
import FreqTab from './qa-tuning/FreqTab.vue';

const route = useRoute();
const router = useRouter();
// 当前本体版本取 app store（mock 模式是 mock 值，versioning 走真接口时是 /releases/current 的 current）
const app = useAppStore();
const { readonly, reason } = useReadonly();
const confirm = useConfirmStore();
const TABS = ['cert', 'eval', 'feedback', 'freq'];
const tab = computed({
  get: () => (TABS.includes(route.query.tab as string) ? (route.query.tab as string) : 'cert'),
  set: (v: string) => router.replace({ query: { ...(v === 'cert' ? {} : { tab: v }), ...(route.query.ver ? { ver: route.query.ver } : {}) } }),
});
// 运行评测所需版本/题库分组元数据；子列表独立加载，不等待这些统计。
const real = qaReal();
const qaSession = useQaSession();
const summary = ref<QaTuningSummary>();
const contextKey = () => JSON.stringify(currentApiContext());
const viewVer = computed(() => real ? summary.value?.version || '未知' : (route.query.ver as string) || app.ontologyVersion);
const dataCutoff = computed(() => real ? summary.value?.dataAsOf || '未知' : TUNING_CUTOFF);
const groupCounts = ref<{ group: string; n: number }[]>([]);
const metaLoading = ref(real);
const metaError = ref(false);
let metaSeq = 0;
if (real) watch([tuningTick, contextKey], async () => {
  const n = ++metaSeq;
  summary.value = undefined; groupCounts.value = [];
  metaLoading.value = true; metaError.value = false;
  try {
    const [s, qs] = await Promise.all([getTuningSummary(qaRequestOptions()), listEvalQuestions({ page: 1, size: 1 }, qaRequestOptions())]);
    if (n !== metaSeq) return;
    summary.value = s; groupCounts.value = qs.groupCounts;
    if (runGroup.value !== '全部' && !qs.groupCounts.some((g) => g.group === runGroup.value)) runGroup.value = '全部';
  } catch (e) {
    if (n === metaSeq) { metaError.value = true; message.error(`评测元数据加载失败：${(e as Error).message}`); }
  } finally { if (n === metaSeq) metaLoading.value = false; }
}, { immediate: true, flush: 'sync' });
const evalTotal = computed(() => (live.value ? summary.value?.evalTotal ?? 0 : EVAL_QS.length));

/* 新建认证问答 */
const newOpen = ref(false);
const nq = ref('');
const nm = ref(real ? '' : 'gmv');
const nd = ref<string[]>([]);
const nt = ref(real ? '' : '上个月');
const nf = ref('');
/* 真接口：选项取 T2；mock 用前端常量 */
const planOpts = ref({
  metrics: real ? [] : Object.values(T_METRICS).map((m) => ({ value: m.key, label: `${m.name} ${m.version}` })),
  dims: real ? [] : TUNING_DIMS.map((d) => ({ value: d, label: d })),
  times: real ? [] : TUNING_TIMES.map((d) => ({ value: d, label: d })),
});
let planSeq = 0;
if (real) watch(contextKey, async () => {
  const n = ++planSeq;
  planOpts.value = { metrics: [], dims: [], times: [] };
  nm.value = ''; nt.value = ''; setPlanMetrics([]);
  try {
    const o = await getPlanOptions(qaRequestOptions());
    if (n !== planSeq) return;
    setPlanMetrics(o.metrics);
    planOpts.value = {
      metrics: o.metrics.map((m) => ({ value: m.key, label: `${m.name} ${m.version}` })),
      dims: o.dims.map((d) => ({ value: d, label: d })),
      times: o.times.map((d) => ({ value: d, label: d })),
    };
    nm.value = o.metrics[0]?.key ?? '';
    nt.value = o.times[0] ?? '';
  } catch (e) { if (n === planSeq && !qaFallBackIfDown(e)) message.error(`语义计划选项加载失败：${(e as Error).message}`); }
}, { immediate: true, flush: 'sync' });
const newPlan = computed<SemPlan>(() => ({ metric: nm.value, dims: nd.value, time: nt.value, filter: nf.value.trim() || undefined }));
async function createCert() {
  if (readonly.value || (real && (!planOpts.value.metrics.length || !nm.value || !nt.value))) return;
  const qn = nq.value.trim();
  if (real) {
    try {
      const c = await apiCreateCert(qn, newPlan.value);
      newOpen.value = false; nq.value = ''; nd.value = []; nf.value = '';
      tuningTick.value++;
      router.replace({ query: { cq: c.id } });
      message.success(`已新建认证问答 ${c.id}（待认证）`);
    } catch (e) {
      if (e instanceof ApiError && e.code === 'DUPLICATE_KEY') message.warning(`已有认证问答「${qn}」`);
      else message.error((e as Error).message);
    }
    return;
  }
  if (tuning.certs.some((c) => c.question === qn)) { message.warning(`已有认证问答「${qn}」`); return; }
  const m = T_METRICS[nm.value];
  // 新建只是登记问题和语义计划，需在认证问答里点“认证”复核后才生效；不直接“已认证”
  const c = addCert(qn, newPlan.value, '手动新建', { status: '待认证', certifier: '--', certifiedAt: '--', cr: m.cr,
    note: m.cr ? `${m.name}受 ${m.cr} 影响（未发布），认证后发布还需按新口径重新认证` : '新建问答，复核语义计划后点“认证”' });
  newOpen.value = false; nq.value = ''; nd.value = []; nf.value = '';
  router.replace({ query: { cq: c.id } });
  message.success(`已新建认证问答 ${c.id}（待认证）`);
}

/* 运行评测 */
const runGroup = ref<string>('全部');
const runOptions = computed(() => [{ value: '全部', label: `全部题目（${evalTotal.value}）` }, ...(real ? groupCounts.value.map((g) => ({ value: g.group, label: `${g.group}相关（${g.n}）` })) : EVAL_GROUPS.map((g) => ({ value: g, label: `${g}相关` })))]);
const evalStarting = ref(false);
async function runEval() {
  if (readonly.value || evalStarting.value || (real && (metaLoading.value || metaError.value))) return;
  const group = runGroup.value;
  const sessionId = qaSession.activeId.value;
  if (real && !sessionId) { message.warning('请先在问数工作台选择一个会话，评测将使用该会话的范围和口径'); return; }
  const context = contextKey();
  const seq = metaSeq;
  const current = () => seq === metaSeq && context === contextKey() && group === runGroup.value && (!real || sessionId === qaSession.activeId.value) && !readonly.value;
  const total = real ? (group === '全部' ? evalTotal.value : groupCounts.value.find((g) => g.group === group)?.n ?? 0) : EVAL_QS.filter((x) => group === '全部' || x.group === group).length;
  if (!total) { message.warning('没有可运行的评测题目'); return; }
  const scope = group === '全部' ? `全部 ${total} 题` : `${group}相关 ${total} 题`;
  evalStarting.value = true;
  try {
    const ok = await confirm.ask({
      title: '运行评测', target: scope,
      fields: [{ label: '本体版本', value: viewVer.value }, { label: '题数', value: `${total} 题` }, ...(real ? [{ label: '来源会话', value: sessionId }] : [])],
      impact: '逐题调用已配置模型，与题库期望比对，不改动任何口径；完成后自动更新运行记录。',
      okText: '运行',
    });
    if (!ok || !current()) return;
    if (real) {
      // T11：后台逐题跑（202 立即返回运行中），评测页签每 3 秒重拉进度
      try {
        const r = await startEvalRun(runGroup.value === '全部' ? 'all' : runGroup.value, sessionId);
        if (!current()) return;
        tuningTick.value++;
        router.replace({ query: { tab: 'eval', run: r.id } });
        message.success(`已进入评测队列 ${r.id}（${r.scope}）`);
      } catch (e) {
        if (!current()) return;
        if (e instanceof ApiError && e.code === 'QA_LLM_UNAVAILABLE') message.error('问数模型未配置，暂时无法运行评测');
        else if (!qaFallBackIfDown(e)) message.error((e as Error).message);
      }
      return;
    }
    const r = startRun(scope, total, app.ontologyVersion);
    router.replace({ query: { tab: 'eval', run: r.id } });
    message.success(`已开始运行 ${r.id}（${scope}）`);
  } finally { evalStarting.value = false; }
}
</script>

<template>
  <Teleport defer to="#page-extra">
    <a-space>
      <span class="t-12 c-3">本体版本 <span class="num">{{ viewVer }}</span> · 数据截至 <span class="num">{{ dataCutoff }}</span></span>
      <a-popover v-model:open="newOpen" trigger="click" placement="bottomRight">
        <template #content>
          <div class="qt-pop">
            <div class="t-14 fw-600 c-1">新建认证问答</div>
            <div class="qt-f"><span class="t-12 c-3 qt-l">问题</span><a-input v-model:value="nq" class="qt-c" :maxlength="40" placeholder="例如：上周各大区有效订单数" /></div>
            <div class="qt-f"><span class="t-12 c-3 qt-l">指标</span><a-select v-model:value="nm" class="qt-c" :options="planOpts.metrics" /></div>
            <div class="qt-f"><span class="t-12 c-3 qt-l">维度</span><a-select v-model:value="nd" mode="multiple" class="qt-c" placeholder="不选为汇总" :options="planOpts.dims" /></div>
            <div class="qt-f"><span class="t-12 c-3 qt-l">时间</span><a-select v-model:value="nt" class="qt-c" :options="planOpts.times" /></div>
            <div class="qt-f"><span class="t-12 c-3 qt-l">过滤</span><a-input v-model:value="nf" class="qt-c" placeholder="可选，例如：大区 = 华东一区" /></div>
            <div class="t-12 c-3">语义计划：{{ planText(newPlan) }}</div>
            <div class="qt-btns"><a-button size="small" @click="newOpen = false">取消</a-button><a-button size="small" type="primary" :disabled="readonly || !nq.trim() || (real && (!planOpts.metrics.length || !nm || !nt))" @click="createCert">认证</a-button></div>
          </div>
        </template>
        <a-tooltip :title="reason"><a-button :disabled="readonly">新建认证问答</a-button></a-tooltip>
      </a-popover>
      <a-select v-model:value="runGroup" class="qt-rg" :disabled="readonly" :options="runOptions" />
      <a-tooltip :title="reason"><a-button type="primary" :loading="evalStarting" :disabled="readonly || (real && (metaLoading || metaError || !evalTotal))" @click="runEval">运行评测</a-button></a-tooltip>
    </a-space>
  </Teleport>

  <div class="panel qt">
    <a-tabs v-model:active-key="tab" class="qt-tabs">
      <a-tab-pane key="cert" tab="认证问答" />
      <a-tab-pane key="eval" tab="评测题库" />
      <a-tab-pane key="feedback" tab="反馈与纠错" />
      <a-tab-pane key="freq" tab="高频问题推荐" />
    </a-tabs>
    <CertTab v-if="tab === 'cert'" :readonly="readonly" :reason="reason" />
    <EvalTab v-else-if="tab === 'eval'" :readonly="readonly" :reason="reason" />
    <FeedbackTab v-else-if="tab === 'feedback'" :readonly="readonly" :reason="reason" />
    <FreqTab v-else :readonly="readonly" :reason="reason" />
  </div>
</template>

<style scoped>
.qt { flex: 1; min-height: 0; }
.qt-tabs { flex: none; padding: 0 16px; }
.qt-tabs :deep(.ant-tabs-nav) { margin-bottom: 0; }
.qt-rg { width: 150px; }
.qt-pop { width: 340px; display: flex; flex-direction: column; gap: 10px; }
.qt-f { display: flex; align-items: center; gap: 8px; }
.qt-l { width: 32px; flex: none; }
.qt-c { flex: 1; min-width: 0; }
.qt-btns { display: flex; justify-content: flex-end; gap: 8px; }
</style>
