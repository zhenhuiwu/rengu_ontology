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
import { EVAL_QS, EVAL_GROUPS, T_METRICS, TUNING_CUTOFF, TUNING_DIMS, TUNING_TIMES, planText, type SemPlan } from '@/mock/qa-tuning';
import { tuning, tuningTick, addCert, startRun } from './qa-tuning/state';
import { qaReal, getTuningSummary, createCert as apiCreateCert, type QaTuningSummary } from '@/api/qa';
import { ApiError } from '@/api/http';
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
const viewVer = computed(() => (route.query.ver as string) || app.ontologyVersion);
const TABS = ['cert', 'eval', 'feedback', 'freq'];
const tab = computed({
  get: () => (TABS.includes(route.query.tab as string) ? (route.query.tab as string) : 'cert'),
  set: (v: string) => router.replace({ query: { ...(v === 'cert' ? {} : { tab: v }), ...(route.query.ver ? { ver: route.query.ver } : {}) } }),
});
const pendingFb = computed(() => tuning.feedbacks.filter((f) => f.status === '待处理').length);
// 真接口（开关 qa）：认证问答、高频问题两个页签的计数取 T1；评测 / 反馈页签仍是 mock，计数照旧
const real = qaReal();
const summary = ref<QaTuningSummary>();
if (real) watch(tuningTick, async () => { try { summary.value = await getTuningSummary(); } catch { /* 计数失败不挡页面 */ } }, { immediate: true });
const recert = computed(() => (real ? summary.value?.recertCount ?? 0 : tuning.certs.filter((c) => c.status === '待重新认证').length));
const uncert = computed(() => (real ? summary.value?.uncertFreq ?? 0 : tuning.freq.filter((f) => !f.certId).length));

/* 新建认证问答 */
const newOpen = ref(false);
const nq = ref('');
const nm = ref('gmv');
const nd = ref<string[]>([]);
const nt = ref('上个月');
const nf = ref('');
const newPlan = computed<SemPlan>(() => ({ metric: nm.value, dims: nd.value, time: nt.value, filter: nf.value.trim() || undefined }));
async function createCert() {
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
const runOptions = [{ value: '全部', label: `全部题目（${EVAL_QS.length}）` }, ...EVAL_GROUPS.map((g) => ({ value: g, label: `${g}相关` }))];
async function runEval() {
  const qs = runGroup.value === '全部' ? EVAL_QS : EVAL_QS.filter((x) => x.group === runGroup.value);
  const scope = runGroup.value === '全部' ? `全部 ${qs.length} 题` : `${runGroup.value}相关 ${qs.length} 题`;
  const ok = await confirm.ask({
    title: '运行评测', target: scope,
    fields: [{ label: '本体版本', value: app.ontologyVersion }, { label: '题数', value: `${qs.length} 题` }],
    impact: '按当前已发布版本逐题生成语义计划并与期望比对，不改动任何口径；预计 3 分钟完成。',
    okText: '运行',
  });
  if (!ok) return;
  const r = startRun(scope, qs.length, app.ontologyVersion);
  router.replace({ query: { tab: 'eval', run: r.id } });
  message.success(`已开始运行 ${r.id}（${scope}）`);
}
</script>

<template>
  <Teleport defer to="#page-extra">
    <a-space>
      <span class="t-12 c-3">本体版本 <span class="num">{{ viewVer }}</span> · 数据截至 <span class="num">{{ TUNING_CUTOFF }}</span></span>
      <a-popover v-model:open="newOpen" trigger="click" placement="bottomRight">
        <template #content>
          <div class="qt-pop">
            <div class="t-14 fw-600 c-1">新建认证问答</div>
            <div class="qt-f"><span class="t-12 c-3 qt-l">问题</span><a-input v-model:value="nq" class="qt-c" :maxlength="40" placeholder="例如：上周各大区有效订单数" /></div>
            <div class="qt-f"><span class="t-12 c-3 qt-l">指标</span><a-select v-model:value="nm" class="qt-c" :options="Object.values(T_METRICS).map((m) => ({ value: m.key, label: `${m.name} ${m.version}` }))" /></div>
            <div class="qt-f"><span class="t-12 c-3 qt-l">维度</span><a-select v-model:value="nd" mode="multiple" class="qt-c" placeholder="不选为汇总" :options="TUNING_DIMS.map((d) => ({ value: d, label: d }))" /></div>
            <div class="qt-f"><span class="t-12 c-3 qt-l">时间</span><a-select v-model:value="nt" class="qt-c" :options="TUNING_TIMES.map((d) => ({ value: d, label: d }))" /></div>
            <div class="qt-f"><span class="t-12 c-3 qt-l">过滤</span><a-input v-model:value="nf" class="qt-c" placeholder="可选，例如：大区 = 华东一区" /></div>
            <div class="t-12 c-3">语义计划：{{ planText(newPlan) }}</div>
            <div class="qt-btns"><a-button size="small" @click="newOpen = false">取消</a-button><a-button size="small" type="primary" :disabled="!nq.trim()" @click="createCert">认证</a-button></div>
          </div>
        </template>
        <a-tooltip :title="reason"><a-button :disabled="readonly">新建认证问答</a-button></a-tooltip>
      </a-popover>
      <a-select v-model:value="runGroup" class="qt-rg" :disabled="readonly" :options="runOptions" />
      <a-tooltip :title="reason"><a-button type="primary" :disabled="readonly" @click="runEval">运行评测</a-button></a-tooltip>
    </a-space>
  </Teleport>

  <div class="panel qt">
    <a-tabs v-model:active-key="tab" class="qt-tabs">
      <a-tab-pane key="cert" :tab="`认证问答（待重新认证 ${recert}）`" />
      <a-tab-pane key="eval" :tab="`评测题库（${EVAL_QS.length}）`" />
      <a-tab-pane key="feedback" :tab="`反馈与纠错（待处理 ${pendingFb}）`" />
      <a-tab-pane key="freq" :tab="`高频问题推荐（未认证 ${uncert}）`" />
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
