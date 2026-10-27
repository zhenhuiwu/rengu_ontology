<script setup lang="ts">
/**
 * 指标口径：左侧指标列表，右侧详情。页签：定义 / 允许的维度 / 版本历史 / 引用与影响。
 * 选中指标写进 ?metric=，页签写进 ?tab=。
 */
import { computed, reactive, ref, shallowRef, watch, onBeforeUnmount } from 'vue';
import { currentApiContext } from '@/api/http';
import { useRoute, useRouter } from 'vue-router';
import HoverCard from '@/components/HoverCard.vue';
import EChart from '@/components/EChart.vue';
import AppPagination from '@/components/AppPagination.vue';
import { GRAY, BRAND } from '@/theme';
import { DAILY_GMV, fmtMoney } from '@/mock';
import { METRIC_LIST as MOCK_LIST, METRIC_DEF, METRIC_DIMS, METRIC_VERSIONS, METRIC_REFS, type MetricListItem as M } from '@/mock/metricCatalog';
import { isStaticReal, fetchMetricGroups, staticContextKey, fetchStaticDetail, rollbackMetric, trialMetric, crErr, type MetricTrialResult } from '@/api/static';
import DraftBanner from './parts/DraftBanner.vue';
import StartChangeModal from './parts/StartChangeModal.vue';
import DefinitionEditor from './parts/DefinitionEditor.vue';
import { loadDraftState } from './parts/useDraftView';
import { useDraftView, draftOf } from './parts/useDraftView';
import { useStaticCr, useRealCrRecords, activeTargetCr, draftFields } from './parts/useStaticCr';
import { useStaticPage } from './parts/useStaticPage';
import { CHANGE_REQUESTS } from '@/mock/changes';
import { message } from 'ant-design-vue';
import { useConfirmStore } from '@/stores/confirm';

const LIST: M[] = isStaticReal() ? reactive<M[]>([]) : MOCK_LIST;
const firstId = ref('');
const MOCK_GROUPS = ['全部', '销售', '利润', '履约', '库存', '会员'];
const metricGroups = ref<string[]>([]), groupsError = ref('');
const GROUPS = computed(() => isStaticReal() ? ['全部', ...metricGroups.value.filter(value => value !== '全部')] : MOCK_GROUPS);
/** 审核中的变更单号：按变更单状态判断（只有“审核中”才算，草稿、已驳回不算） */
const workflow = useRealCrRecords();
const reviewCr = (m: M) => isStaticReal() ? workflow.rows.value.find((c) => c.id === m.review?.cr && c.status === '审核中')?.id : (m.review && CHANGE_REQUESTS.find((c) => c.id === m.review!.cr)?.status === '审核中' ? m.review.cr : undefined);
/** 显示状态：从变更单推出来。审核中 → 橙色“审核中”；只有草稿 → 灰色“草稿中”（问数仍用已发布版）；其余按指标自身状态，一律灰色。 */
const shownStatus = (m: M) => {
  if (isStaticReal()) {
    if (currentApiContext().ver || m.status !== '已发布') return m.status;
    const record = activeTargetCr(workflow.rows.value, 'metric', m.id);
    return record ? record.status === '草稿' ? '草稿中' : record.status === '已通过' ? '已通过待发布' : record.status : draftOf('metric', m.id) ? '草稿中' : m.status;
  }
  return (m.status !== '已发布' ? m.status : reviewCr(m) ? '审核中' : draftOf('metric', m.id) ? '草稿中' : '已发布');
};
const statusColor = (m: M) => (shownStatus(m) === '审核中' ? 'warning' : 'default');

const route = useRoute();
const router = useRouter();
const selected = computed(() => (route.query.metric as string) || (isStaticReal() ? firstId.value : 'gmv'));
const tab = computed({
  get: () => (route.query.tab as string) || 'def',
  set: (v) => router.replace({ query: { ...route.query, tab: v } }),
});
const pick = (id: string) => router.replace({ query: { ...route.query, metric: id, view: undefined } });
const publishedCurrent = computed<M>(() => LIST.find((m) => m.id === selected.value) ?? (isStaticReal() ? { id: selected.value, name: '', api: '', group: '', unit: '', version: '', owner: '', status: '草稿' } : LIST[0]));
const dv = useDraftView('metric', () => isStaticReal() ? selected.value : publishedCurrent.value.id);
const { draft, isDraft: draftIsDraft, editTip, readonly, reason, setView } = dv;
// real 模式先写库（发起 / 加入、提交、放弃），见 parts/useStaticCr.ts
const cr = useStaticCr('metric', () => ({ id: isStaticReal() ? selected.value : publishedCurrent.value.id, name: publishedCurrent.value.name }), dv, workflow.reload);
const newTarget = computed(() => !!cr.head.value?.targets.find((t) => t.kind === 'metric' && t.id === selected.value)?.isNew);
const isDraft = computed(() => draftIsDraft.value || (isStaticReal() && !readonly.value && !!draft.value && newTarget.value));
const canEdit = computed(() => isDraft.value && (!isStaticReal() || cr.head.value?.status === '草稿' && !cr.loading.value && !cr.error.value));
const cur = computed<M>(() => isStaticReal() && isDraft.value ? { ...publishedCurrent.value, ...draftFields(cr.items.value, 'def') } as M : publishedCurrent.value);
const definitionOpen = ref(false), newDefinition = ref(false);
function openDefinition(isNew = false) { if (readonly.value) return; newDefinition.value = isNew; definitionOpen.value = true; }
async function definitionSaved(result: { id: string; crId: string }) { const key = staticContextKey(); await loadDraftState(true); if (key !== staticContextKey()) return; await router.replace({ query: { ...route.query, metric: result.id, view: 'draft' } }); if (key === staticContextKey()) { await cr.reload(); if (key === staticContextKey()) await workflow.reload(); } }
const rollbackPending = ref<{ id: string; ver: string; key: string }>();
const rollbackBusy = ref(false);
async function start(opts: { join?: string; title?: string; desc?: string }) {
  if (!isStaticReal() || !rollbackPending.value) return cr.start(opts);
  const pending = rollbackPending.value;
  if (readonly.value || rollbackBusy.value || pending.key !== staticContextKey() || pending.id !== cur.value.id) return;
  rollbackBusy.value = true;
  try {
    const result = await rollbackMetric(pending.id, pending.ver, opts);
    if (!result || pending.key !== staticContextKey() || pending.id !== cur.value.id) return;
    changeOpen.value = false; rollbackPending.value = undefined;
    message.success(`已记入 ${result.crId}，发布前当前口径继续生效`);
    await definitionSaved({ id: pending.id, crId: result.crId });
  } catch (e) { if (pending.key === staticContextKey() && pending.id === cur.value.id) message.error(e instanceof Error ? e.message : String(e)); }
  finally { if (pending.key === staticContextKey()) rollbackBusy.value = false; }
}
const changeOpen = ref(false);
const changeTarget = ref('');
const openChange = (name: string) => { rollbackPending.value = undefined; changeTarget.value = name; changeOpen.value = true; };
watch(() => JSON.stringify([staticContextKey(), selected.value]), () => { changeOpen.value = false; rollbackPending.value = undefined; rollbackBusy.value = false; });
const group = ref('全部');
let groupsSeq = 0;
async function loadGroups() {
  if (!isStaticReal()) return;
  const my = ++groupsSeq, key = staticContextKey();
  metricGroups.value = []; groupsError.value = '';
  try {
    const groups = await fetchMetricGroups();
    if (my !== groupsSeq || key !== staticContextKey()) return;
    metricGroups.value = groups;
    if (!GROUPS.value.includes(group.value)) group.value = '全部';
  } catch (e) { if (my === groupsSeq && key === staticContextKey()) groupsError.value = e instanceof Error ? e.message : '指标分类加载失败'; }
}
watch(staticContextKey, () => { group.value = '全部'; void loadGroups(); }, { immediate: true });
onBeforeUnmount(() => { groupsSeq++; });
const kw = ref('');
const visibleMetrics = computed(() => {
  const target = cr.head.value?.targets.find((t) => t.kind === 'metric' && t.id === selected.value);
  return isStaticReal() && target?.isNew && !LIST.some((m) => m.id === target.id) ? [...LIST, cur.value] : LIST;
});
const filtered = computed(() => visibleMetrics.value.filter((m) => (group.value === '全部' || m.group === group.value) && (!kw.value || m.name.includes(kw.value) || m.api.includes(kw.value))));
const LPAGE = 18;
const lpage = ref(1);
watch([group, kw], () => { lpage.value = 1; });
const metricPage = useStaticPage<M>(() => '/metrics', () => ({ kw: kw.value, group: group.value === '全部' ? '' : group.value }), lpage, LPAGE);
watch(metricPage.items, (rows) => {
  if (!isStaticReal()) return;
  for (const row of rows) { const i = LIST.findIndex((x) => x.id === row.id); if (i >= 0) LIST[i] = row; else LIST.push(row); }
  if (!firstId.value && rows.length) firstId.value = rows[0].id;
});
const list = computed(() => isStaticReal() ? metricPage.items.value : filtered.value.slice((lpage.value - 1) * LPAGE, lpage.value * LPAGE));
const listTotal = computed(() => isStaticReal() ? metricPage.total.value : filtered.value.length);
// 定义 / 允许的维度 / 口径版本 / 引用：mock 只有一份（所有指标共用）；真接口按选中指标取（GET /metrics/{id}、/versions、/refs）
const metricDetailError = ref('');
const rawDefinition = shallowRef<{ formula?: string; variants?: { code: string; label: string; default: boolean; api?: string; version?: string }[] }>({});
const DEF = shallowRef([...METRIC_DEF]);
const DIMS = shallowRef([...METRIC_DIMS]);
type MetricVersion = (typeof METRIC_VERSIONS)[number] & { canRollback?: boolean; rollbackUnavailable?: string; baseline?: boolean };
const VERSIONS = shallowRef<MetricVersion[]>([...METRIC_VERSIONS]);
const REFS = shallowRef([...METRIC_REFS]);
if (isStaticReal()) {
  let detailSeq = 0;
  onBeforeUnmount(() => { ++detailSeq; });
  watch(() => JSON.stringify([publishedCurrent.value?.id, staticContextKey()]), async () => {
    const seq = ++detailSeq, key = staticContextKey(), id = publishedCurrent.value?.id;
    metricDetailError.value = '';
    DEF.value = []; DIMS.value = []; VERSIONS.value = []; REFS.value = []; rawDefinition.value = {};
    if (!id || newTarget.value) return;
    try {
      const detail = await fetchStaticDetail<M & { formula?: string; variants?: typeof rawDefinition.value.variants; def: typeof METRIC_DEF; dims: typeof METRIC_DIMS }>(`/metrics/${encodeURIComponent(id)}`);
      if (seq !== detailSeq || key !== staticContextKey() || cur.value?.id !== id || !detail) return;
      const at = LIST.findIndex((m) => m.id === id); if (at >= 0) LIST[at] = detail; else LIST.push(detail);
      rawDefinition.value = detail;
      DEF.value = detail.def ?? []; DIMS.value = detail.dims ?? [];
    } catch (e) {
      if (seq === detailSeq && key === staticContextKey() && !newTarget.value) metricDetailError.value = e instanceof Error ? e.message : String(e);
    }
  }, { immediate: true });
}

const FORMULA = computed(() => isStaticReal() ? (isDraft.value ? String(draftFields(cr.items.value, 'def').formula ?? rawDefinition.value.formula ?? '尚未配置公式') : rawDefinition.value.formula ?? '尚未配置公式') : 'SUM(订单行.不含税金额) − SUM(退款行.不含税退款金额)');

const VARIANTS = computed(() => isStaticReal() && isDraft.value ? (draftFields(cr.items.value, 'def').variants ?? rawDefinition.value.variants ?? []) as NonNullable<typeof rawDefinition.value.variants> : rawDefinition.value.variants ?? []);
const DISPLAY_DIMS = computed(() => isStaticReal() && isDraft.value && Array.isArray(draftFields(cr.items.value, 'def').dims) ? (draftFields(cr.items.value, 'def').dims as string[]).map((name) => DIMS.value.find((d) => d.dim === name) ?? { dim: name, hier: '', obj: '', def: '', note: '' }) : DIMS.value);
const DENY = [
  { dim: '供应商', reason: '一个订单行对应多个供应商批次，按供应商拆分会重复计算，请用“采购金额”。' },
  { dim: '促销活动', reason: '一单可参加多个活动，金额不可加，请用“活动让利额”。' },
];


const trend = (isStaticReal() ? [] : DAILY_GMV).map((d) => ({ date: d.date, value: Math.round((d.value / 1.13) * 100) / 100 }));
const trialResult = shallowRef<MetricTrialResult>();
const trialBusy = ref(false), trialError = ref('');
let trialSeq = 0;
const trialKey = () => JSON.stringify([staticContextKey(), selected.value, isDraft.value, draft.value?.id, cr.head.value?.lockVersion]);
watch(trialKey, () => { ++trialSeq; trialResult.value = undefined; trialError.value = ''; trialBusy.value = false; });
onBeforeUnmount(() => { ++trialSeq; });
async function runTrial() {
  if (!isStaticReal()) {
    void router.replace({ query: { ...route.query, tab: 'def' } });
    message.success(`已按${isDraft.value ? `草稿 ${draft.value?.id}` : '已发布口径'}试算近 30 日，结果见“近 30 日试算”`);
    return;
  }
  if (trialBusy.value || !selected.value || (isDraft.value && (!cr.head.value || cr.loading.value || cr.error.value))) return;
  const seq = ++trialSeq, key = trialKey(), ctx = currentApiContext(), id = selected.value;
  trialBusy.value = true; trialError.value = '';
  try {
    const result = await trialMetric(id, isDraft.value ? draft.value?.id : undefined, { readonlyParams: false, query: { ver: ctx.ver, previewAs: ctx.previewAs }, headers: { 'X-Demo-As': ctx.demoAs ?? '', 'X-Ontology-Id': ctx.ontologyId ?? '' } });
    if (seq !== trialSeq || key !== trialKey()) return;
    trialResult.value = result;
    void router.replace({ query: { ...route.query, tab: 'def' } });
    if (result.rows.length) message.success('已按真实数据完成试算');
    else message.info('所选范围内没有可见数据');
  } catch (e) { if (seq === trialSeq && key === trialKey()) { trialError.value = crErr(e); message.error(trialError.value); } }
  finally { if (seq === trialSeq && key === trialKey()) trialBusy.value = false; }
}
const trialTrend = computed(() => isStaticReal() ? (trialResult.value?.rows ?? []).map((row) => ({ date: row.date, value: row.value === null ? null : Number(row.value) })) : trend);
const trendOption = computed(() => ({
  grid: { left: 52, right: 12, top: 12, bottom: 24 },
  tooltip: { trigger: 'axis', valueFormatter: (v: number) => fmtMoney(v) },
  xAxis: { type: 'category', data: trialTrend.value.map((d) => d.date), axisLine: { lineStyle: { color: GRAY.g6 } }, axisTick: { show: false }, axisLabel: { color: GRAY.g8, fontSize: 12, interval: 6 } },
  yAxis: { type: 'value', axisLabel: { color: GRAY.g8, fontSize: 12, formatter: (v: number) => `${(v / 10000).toFixed(0)}万` }, splitLine: { lineStyle: { color: GRAY.g4 } } },
  series: [{ type: 'line', data: trialTrend.value.map((d) => d.value), symbol: 'none', lineStyle: { width: 1.5, color: BRAND } }],
}));

const verPage = ref(1);
const refPage = ref(1);
watch(selected, () => { verPage.value = 1; refPage.value = 1; });
const verSize = ref(10);
watch(verSize, () => { verPage.value = 1; });
const versionRows = useStaticPage<MetricVersion>(() => selected.value ? `/metrics/${encodeURIComponent(selected.value)}/versions` : '', () => ({}), verPage, verSize, () => tab.value === 'versions' && !newTarget.value);
const refSize = ref(10);
watch(refSize, () => { refPage.value = 1; });
const referenceRows = useStaticPage<(typeof METRIC_REFS)[number]>(() => selected.value ? `/metrics/${encodeURIComponent(selected.value)}/refs` : '', () => ({}), refPage, refSize, () => tab.value === 'refs' && !newTarget.value);
const confirm = useConfirmStore();
async function rollback(v: MetricVersion) {
  if (readonly.value || rollbackBusy.value || (isStaticReal() && !v.canRollback)) return;
  const key = staticContextKey(), id = cur.value.id;
  const ok = await confirm.ask({
    title: '回滚口径版本', target: `${cur.value.name} · 回滚到 ${v.ver}（${v.release}）`,
    fields: [{ label: '当前生效', value: isStaticReal() ? cur.value.version : VERSIONS.value.find((x) => x.status === '生效中')?.ver ?? '--' }, { label: '回滚到', value: v.ver }],
    impact: '回滚会生成一个新的变更单，经评审后随下一次本体整版发布生效；发布前当前口径继续生效。',
    okText: '生成变更单',
  });
  if (ok && !readonly.value && key === staticContextKey() && id === cur.value.id) {
    openChange(`${cur.value.name}（${cur.value.api}）回滚到 ${v.ver}`);
    if (isStaticReal()) rollbackPending.value = { id, ver: v.ver, key };
  }
}
const dimCols = [
  { title: '维度', dataIndex: 'dim', width: 88 },
  { title: '层级', dataIndex: 'hier' },
  { title: '取自', dataIndex: 'obj', width: 160 },
  { title: '默认粒度', dataIndex: 'def', width: 88 },
  { title: '说明', dataIndex: 'note', ellipsis: { showTitle: true } },
];
const verCols = [
  { title: '口径版本', dataIndex: 'ver', width: 96 },
  { title: '本体发布', dataIndex: 'release', width: 120 },
  { title: '状态', key: 'status', width: 96 },
  { title: '变更说明', dataIndex: 'change' },
  { title: '发布人', dataIndex: 'by', width: 96 },
  { title: isStaticReal() ? '记录时间' : '发布时间', dataIndex: 'at', width: 168 },
  { title: '操作', key: 'op', width: 120 },
];
const refCols = [
  { title: '类型', dataIndex: 'type', width: 96 },
  { title: '名称', key: 'name' },
  { title: '引用方式', dataIndex: 'note' },
];
</script>

<template>
  <Teleport defer to="#page-extra">
    <a-tooltip :title="isStaticReal() ? '暂不可用' : readonly ? reason : ''"><a-button :disabled="readonly || isStaticReal()" @click="!readonly && openChange('导入指标')">导入</a-button></a-tooltip>
    <a-tooltip :title="readonly ? reason : ''"><a-button :disabled="readonly" @click="!readonly && (isStaticReal() ? openDefinition(true) : openChange('新建指标'))">新建指标</a-button></a-tooltip>
  </Teleport>

  <div class="mt">
    <div class="panel mt-list">
      <div class="mt-filter">
        <a-input-search v-model:value="kw" placeholder="搜索名称或 API 名" />
        <a-segmented v-model:value="group" :options="GROUPS" block size="small" />
      </div>
      <a-alert v-if="groupsError" type="error" :message="groupsError" show-icon><template #action><a-button size="small" @click="loadGroups">重试分类</a-button></template></a-alert>
      <a-alert v-if="metricPage.error.value || metricDetailError" type="error" :message="metricPage.error.value || metricDetailError" show-icon />
      <div v-if="metricPage.loading.value" style="text-align: center; padding: 8px"><a-spin size="small" /></div>
      <ul class="mt-items" :aria-busy="metricPage.loading.value">
        <li v-for="m in list" :key="m.id" :class="{ active: m.id === cur.id }" :title="`${m.name} · ${m.api} · ${m.group}`" @click="pick(m.id)">
          <div class="mt-row">
            <span class="t-14 mt-name mt-ell">{{ m.name }}</span>
            <span class="t-12 num" :class="shownStatus(m) === '审核中' ? 'c-warning' : 'c-3'">{{ shownStatus(m) === '已发布' ? '' : shownStatus(m) + ' · ' }}{{ m.version }}</span>
          </div>
        </li>
      </ul>
      <div class="mt-foot">
        <AppPagination v-model:page="lpage" variant="simple" :total="listTotal" :size="LPAGE" class="mt-pager" empty-text="没有匹配的指标" />
      </div>
    </div>

    <div v-if="selected" class="panel mt-detail">
      <DraftBanner v-if="isDraft && draft && (!isStaticReal() || cr.head.value?.status === '草稿')" :draft="draft"
        @submit="cr.submit" @discard="cr.discard" />
      <div class="mt-head">
        <span class="t-16 fw-600 c-1">{{ cur.name }}</span>
        <span class="t-14 c-3 num">{{ cur.api }} · {{ cur.unit }}</span>
        <a-tag :color="statusColor(cur)">{{ shownStatus(cur) }}</a-tag>
        <span class="t-14 c-3">口径 {{ cur.version }}</span>
        <span v-if="cur.review" class="t-12 c-3">变更单 <a @click="router.push({ path: '/build/ontology/releases', query: { cr: cur.review.cr } })">{{ cur.review.cr }}</a> {{ cur.review.note }}</span>
        <a-segmented v-if="draft && !readonly && !newTarget" size="small" :value="isDraft ? 'draft' : 'published'"
          :options="[{ label: '已发布', value: 'published' }, { label: `草稿 ${draft.id}`, value: 'draft' }]"
          @change="(v: any) => setView(v)" />
        <span class="mt-sp" />
        <a-button :loading="trialBusy" :disabled="isStaticReal() && isDraft && (!cr.head.value || cr.loading.value || !!cr.error.value)" @click="runTrial">试算</a-button>
        <a-tooltip v-if="readonly" :title="reason"><a-button disabled>发起变更</a-button></a-tooltip>
        <a-tooltip v-else-if="reviewCr(cur)" :title="`${reviewCr(cur)} 审核中`"><a-button disabled>发起变更</a-button></a-tooltip>
        <a-button v-else-if="!draft" type="primary" @click="openChange(`${cur.name}（${cur.api}）`)">发起变更</a-button>
        <a-button v-else-if="!isDraft" type="primary" @click="setView('draft')">继续编辑草稿</a-button>
      </div>
      <a-tabs v-model:activeKey="tab" class="mt-tabs">
        <a-tab-pane key="def" tab="定义" />
        <a-tab-pane key="dims" tab="允许的维度" />
        <a-tab-pane key="versions" tab="版本历史" />
        <a-tab-pane key="refs" tab="引用与影响" />
      </a-tabs>

      <div v-if="tab === 'def'" class="mt-body mt-def">
        <div class="mt-def-left">
          <div class="mt-sec t-14 fw-600 mt-sec-row">
            <span>计算公式<a-tag v-if="isDraft" class="mt-diff">修改</a-tag></span>
            <a-tooltip :title="editTip"><a-button size="small" :disabled="!canEdit" @click="isStaticReal() && openDefinition()">编辑定义</a-button></a-tooltip>
          </div>
          <div class="mt-formula t-14 num">{{ FORMULA }}</div>
          <a-descriptions v-if="isStaticReal()" :column="2" bordered size="small"><a-descriptions-item label="分组">{{ cur.group }}</a-descriptions-item><a-descriptions-item label="负责人">{{ cur.owner }}</a-descriptions-item></a-descriptions>
          <a-descriptions :column="2" bordered size="small" class="mt-desc" :label-style="{ width: '96px', whiteSpace: 'nowrap' }">
            <a-descriptions-item v-for="d in DEF" :key="d.k" :label="d.k"><span class="mt-ell" :title="d.v">{{ d.v }}</span></a-descriptions-item>
          </a-descriptions>
          <template v-if="isStaticReal()"><div class="mt-sec t-14 fw-600">口径变体</div><a-empty v-if="!VARIANTS.length" description="未配置口径变体" /><div v-for="v in VARIANTS" :key="v.code" class="t-14">{{ v.label }} · {{ v.code }} · {{ v.api }} · {{ v.version }} {{ v.default ? '· 同名默认' : '' }}</div></template>
          <div class="mt-sec t-14 fw-600">允许的维度</div>
          <a-table :columns="dimCols" :data-source="DISPLAY_DIMS" :pagination="false" size="small" row-key="dim" />
        </div>
        <div class="mt-def-right">
          <div class="mt-sec t-14 fw-600 mt-sec-row">
            <span>近 30 日试算</span><span v-if="!isStaticReal()" class="t-12 c-3 fw-400">按口径 v3 计算 · 数据截至 2026-10-24 23:00</span>
          </div>
          <a-alert v-if="trialError" type="error" :message="trialError" show-icon />
          <div v-if="isStaticReal() && trialResult" class="t-12 c-3">{{ trialResult.scope }} · {{ trialResult.crId ? `已保存草稿 ${trialResult.crId}` : `口径 ${trialResult.version}` }} · 数据截至 {{ trialResult.dataCutoff ?? '无可见数据' }}</div>
          <div class="mt-chart"><a-empty v-if="isStaticReal() && !trialResult?.rows.length" :description="trialResult ? '所选范围内没有可见数据' : '暂无试算结果'" /><EChart v-else :option="trendOption" /></div>
          <div v-if="!isStaticReal()" class="mt-sec t-14 fw-600">不允许的维度</div>
          <div v-for="d in (isStaticReal() ? [] : DENY)" :key="d.dim" class="mt-deny">
            <span class="t-14 c-1">{{ d.dim }}</span>
            <span class="t-12 c-3 mt-ell" :title="d.reason">{{ d.reason }}</span>
          </div>
          <div class="mt-sec t-14 fw-600">被引用</div>
          <span v-if="isStaticReal()" class="t-14 c-2">详见“引用与影响”</span>
          <div v-if="!isStaticReal()" class="mt-refs t-14">
            <span>指标 <HoverCard kind="metric" id="atv">客单价</HoverCard>、<HoverCard kind="metric" id="gross_margin">毛利率</HoverCard> 等 3 个</span>
            <span>规则 1 条，看板 2 个，认证问答 1 条</span>
          </div>
        </div>
      </div>

      <div v-else-if="tab === 'dims'" class="mt-body">
        <a-table :columns="dimCols" :data-source="DISPLAY_DIMS" :pagination="false" size="middle" row-key="dim" />
        <div v-if="!isStaticReal()" class="mt-sec t-14 fw-600">不允许的维度</div>
        <a-table v-if="!isStaticReal()" :columns="[{ title: '维度', dataIndex: 'dim', width: 120 }, { title: '原因', dataIndex: 'reason' }]" :data-source="DENY" :pagination="false" size="middle" row-key="dim" />
      </div>

      <div v-else-if="tab === 'versions'" class="mt-body">
        <a-alert v-if="versionRows.error.value" type="error" :message="versionRows.error.value" show-icon />
        <a-table :columns="verCols" :data-source="isStaticReal() ? versionRows.items.value : VERSIONS.slice((verPage - 1) * verSize, verPage * verSize)" :loading="versionRows.loading.value" :pagination="false" size="middle" row-key="ver">
          <template #bodyCell="{ column, record }">
            <template v-if="column.key === 'status'">
              <a-badge status="default" :text="record.status" />
            </template>
            <template v-else-if="column.key === 'op'">
              <a-space><a-tooltip title="暂未开放"><span class="c-3">对比</span></a-tooltip><a v-if="record.status !== '生效中' && !readonly && (!isStaticReal() || record.canRollback)" @click="rollback(record)">回滚</a><a-tooltip v-else-if="record.status !== '生效中'" :title="readonly ? reason : record.rollbackUnavailable || '未保存该版本定义'"><span class="c-3">回滚</span></a-tooltip></a-space>
            </template>
          </template>
        </a-table>
        <AppPagination v-model:page="verPage" variant="full" :total="isStaticReal() ? versionRows.total.value : VERSIONS.length" v-model:size="verSize" unit="条" :empty="false" />
        <div class="t-12 c-3 mt-note">口径版本只随本体整版发布生效；回滚会生成一个新的变更单，经评审后随下一次发布生效。<template v-if="isStaticReal()">标为基线的记录保存的是变更前定义，时间为实际登记时间。</template></div>
      </div>

      <div v-else class="mt-body">
        <a-alert v-if="referenceRows.error.value" type="error" :message="referenceRows.error.value" show-icon />
        <a-table :columns="refCols" :data-source="isStaticReal() ? referenceRows.items.value : REFS.slice((refPage - 1) * refSize, refPage * refSize)" :loading="referenceRows.loading.value" :pagination="false" size="middle" row-key="name">
          <template #bodyCell="{ column, record }">
            <template v-if="column.key === 'name'"><a v-if="record.to" @click="router.push(record.to)">{{ record.name }}</a><span v-else class="c-1">{{ record.name }}</span></template>
          </template>
        </a-table>
        <AppPagination v-model:page="refPage" variant="full" :total="isStaticReal() ? referenceRows.total.value : REFS.length" v-model:size="refSize" unit="条" :empty="false" />
        <div class="t-12 c-3 mt-note">修改本指标时，以上对象会出现在变更单的影响分析里。</div>
      </div>
    </div>
  </div>
  <DefinitionEditor v-model:open="definitionOpen" kind="metric" :target-id="newDefinition ? undefined : cur.id" :cr-id="newDefinition ? undefined : draft?.id" :new-target="!!cr.head.value?.targets.find((t) => t.kind === 'metric' && t.id === cur.id)?.isNew" @saved="definitionSaved" />
  <StartChangeModal v-model:open="changeOpen" :target-name="changeTarget" :close-on-ok="!rollbackPending" :busy="rollbackBusy" @ok="start" />
</template>

<style scoped>
.mt { flex: 1; min-height: 0; display: flex; gap: var(--gap); }
.mt-list { width: 300px; flex: none; }
.mt-filter { padding: 12px 12px 8px; display: flex; flex-direction: column; gap: 8px; flex: none; }
.mt-items { list-style: none; margin: 0; padding: 0 8px; flex: 1; min-height: 0; }
.mt-items li { padding: 6px 8px; border-radius: var(--radius); cursor: pointer; }
.mt-items li:hover { background: var(--g3); }
.mt-items li.active { background: var(--brand-bg); }
.mt-items li.active .mt-name { color: var(--brand); }
.mt-row { display: flex; justify-content: space-between; align-items: center; gap: 8px; }
.mt-pager { flex: 1; min-height: 0; }
.mt-foot { padding: 8px 12px 8px 16px; border-top: var(--border); flex: none; display: flex; align-items: center; justify-content: space-between; }
.mt-detail { flex: 1; }
.mt-diff { margin-inline: 8px 0; font-weight: var(--fw-regular); }
.mt-head { display: flex; align-items: center; gap: 12px; height: 52px; padding: 0 16px; flex: none; }
.mt-sp { flex: 1; }
.mt-tabs { padding: 0 16px; flex: none; }
.mt-tabs :deep(.ant-tabs-nav) { margin-bottom: 0; }
.mt-body { flex: 1; min-height: 0; min-width: 0; overflow: auto; padding: 12px 16px; display: flex; flex-direction: column; gap: 8px; }
.mt-def { flex-direction: row; gap: 16px; }
.mt-def-left { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 8px; }
.mt-def-right { width: 440px; flex: none; display: flex; flex-direction: column; gap: 8px; padding-left: 16px; border-left: var(--border); }
.mt-sec { margin-top: 4px; }
.mt-sec-row { display: flex; justify-content: space-between; align-items: baseline; }
.mt-formula { padding: 8px 12px; background: var(--g2); border: var(--border); border-radius: var(--radius); font-family: Menlo, Consolas, "Noto Sans Mono CJK SC", monospace; }
.mt-chart { height: 200px; }
.mt-deny { display: flex; align-items: baseline; gap: 12px; padding: 6px 0; border-bottom: var(--border); }
.mt-desc :deep(table) { table-layout: fixed; }
.mt-ell { display: block; overflow: hidden; white-space: nowrap; text-overflow: ellipsis; max-width: 100%; }
.mt-refs { display: flex; flex-direction: column; gap: 4px; }
.mt-note { margin-top: 4px; }

.mt-ell { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.mt-ft { flex-shrink: 0; }
</style>
