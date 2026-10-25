<script setup lang="ts">
/**
 * 指标口径：左侧指标列表，右侧详情。页签：定义 / 允许的维度 / 版本历史 / 引用与影响。
 * 选中指标写进 ?metric=，页签写进 ?tab=。
 */
import { computed, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import HoverCard from '@/components/HoverCard.vue';
import EChart from '@/components/EChart.vue';
import AppPagination from '@/components/AppPagination.vue';
import { GRAY, BRAND } from '@/theme';
import { DAILY_GMV, fmtMoney } from '@/mock';
import { METRIC_LIST as LIST, METRIC_DEF as DEF, METRIC_DIMS as DIMS, METRIC_VERSIONS as VERSIONS, METRIC_REFS as REFS, type MetricListItem as M } from '@/mock/metricCatalog';
import DraftBanner from './parts/DraftBanner.vue';
import StartChangeModal from './parts/StartChangeModal.vue';
import { useDraftView, draftOf } from './parts/useDraftView';
import { CHANGE_REQUESTS } from '@/mock/changes';
import { message } from 'ant-design-vue';
import { useConfirmStore } from '@/stores/confirm';

const GROUPS = ['全部', '销售', '利润', '履约', '库存', '会员'];
/** 审核中的变更单号：按变更单状态判断（只有“审核中”才算，草稿、已驳回不算） */
const reviewCr = (m: M) => (m.review && CHANGE_REQUESTS.find((c) => c.id === m.review!.cr)?.status === '审核中' ? m.review.cr : undefined);
/** 显示状态：从变更单推出来。审核中 → 橙色“审核中”；只有草稿 → 灰色“草稿中”（问数仍用已发布版）；其余按指标自身状态，一律灰色。 */
const shownStatus = (m: M) => (m.status !== '已发布' ? m.status : reviewCr(m) ? '审核中' : draftOf('metric', m.id) ? '草稿中' : '已发布');
const statusColor = (m: M) => (shownStatus(m) === '审核中' ? 'warning' : 'default');

const route = useRoute();
const router = useRouter();
const selected = computed(() => (route.query.metric as string) || 'gmv');
const tab = computed({
  get: () => (route.query.tab as string) || 'def',
  set: (v) => router.replace({ query: { ...route.query, tab: v } }),
});
const pick = (id: string) => router.replace({ query: { ...route.query, metric: id, view: undefined } });
const { draft, isDraft, canEdit, editTip, readonly, reason, setView, start } = useDraftView('metric', () => cur.value.id);
const changeOpen = ref(false);
const changeTarget = ref('');
const openChange = (name: string) => { changeTarget.value = name; changeOpen.value = true; };
const group = ref('全部');
const kw = ref('');
const filtered = computed(() => LIST.filter((m) => (group.value === '全部' || m.group === group.value) && (!kw.value || m.name.includes(kw.value) || m.api.includes(kw.value))));
const LPAGE = 18;
const lpage = ref(1);
watch([group, kw], () => { lpage.value = 1; });
const lpages = computed(() => Math.max(1, Math.ceil(filtered.value.length / LPAGE)));
const list = computed(() => filtered.value.slice((lpage.value - 1) * LPAGE, lpage.value * LPAGE));
const cur = computed(() => LIST.find((m) => m.id === selected.value) ?? LIST[0]);

const FORMULA = 'SUM(订单行.不含税金额) − SUM(退款行.不含税退款金额)';

const DENY = [
  { dim: '供应商', reason: '一个订单行对应多个供应商批次，按供应商拆分会重复计算，请用“采购金额”。' },
  { dim: '促销活动', reason: '一单可参加多个活动，金额不可加，请用“活动让利额”。' },
];


const trend = DAILY_GMV.map((d) => ({ date: d.date, value: Math.round((d.value / 1.13) * 100) / 100 }));
const trendOption = {
  grid: { left: 52, right: 12, top: 12, bottom: 24 },
  tooltip: { trigger: 'axis', valueFormatter: (v: number) => fmtMoney(v) },
  xAxis: { type: 'category', data: trend.map((d) => d.date), axisLine: { lineStyle: { color: GRAY.g6 } }, axisTick: { show: false }, axisLabel: { color: GRAY.g8, fontSize: 12, interval: 6 } },
  yAxis: { type: 'value', axisLabel: { color: GRAY.g8, fontSize: 12, formatter: (v: number) => `${(v / 10000).toFixed(0)}万` }, splitLine: { lineStyle: { color: GRAY.g4 } } },
  series: [{ type: 'line', data: trend.map((d) => d.value), symbol: 'none', lineStyle: { width: 1.5, color: BRAND } }],
};

const verPage = ref(1);
const refPage = ref(1);
const confirm = useConfirmStore();
async function rollback(v: { ver: string; release: string }) {
  const ok = await confirm.ask({
    title: '回滚口径版本', target: `${cur.value.name} · 回滚到 ${v.ver}（${v.release}）`,
    fields: [{ label: '当前生效', value: VERSIONS.find((x) => x.status === '生效中')?.ver ?? '--' }, { label: '回滚到', value: v.ver }],
    impact: '回滚会生成一个新的变更单，经评审后随下一次本体整版发布生效；发布前当前口径继续生效。',
    okText: '生成变更单',
  });
  if (ok) openChange(`${cur.value.name}（${cur.value.api}）回滚到 ${v.ver}`);
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
  { title: '发布时间', dataIndex: 'at', width: 168 },
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
    <a-tooltip :title="readonly ? reason : ''"><a-button :disabled="readonly" @click="!readonly && openChange('导入指标')">导入</a-button></a-tooltip>
    <a-tooltip :title="readonly ? reason : ''"><a-button :disabled="readonly" @click="!readonly && openChange('新建指标')">新建指标</a-button></a-tooltip>
  </Teleport>

  <div class="mt">
    <div class="panel mt-list">
      <div class="mt-filter">
        <a-input-search v-model:value="kw" placeholder="搜索名称或 API 名" />
        <a-segmented v-model:value="group" :options="GROUPS" block size="small" />
      </div>
      <ul class="mt-items">
        <li v-for="m in list" :key="m.id" :class="{ active: m.id === cur.id }" :title="`${m.name} · ${m.api} · ${m.group}`" @click="pick(m.id)">
          <div class="mt-row">
            <span class="t-14 mt-name mt-ell">{{ m.name }}</span>
            <span class="t-12 num" :class="shownStatus(m) === '审核中' ? 'c-warning' : 'c-3'">{{ shownStatus(m) === '已发布' ? '' : shownStatus(m) + ' · ' }}{{ m.version }}</span>
          </div>
        </li>
      </ul>
      <div class="mt-foot">
        <AppPagination v-model:page="lpage" variant="simple" :total="filtered.length" :size="LPAGE" class="mt-pager" empty-text="没有匹配的指标" />
      </div>
    </div>

    <div class="panel mt-detail">
      <DraftBanner v-if="isDraft && draft" :draft="draft"
        @submit="message.success(`${draft.id} 已提交审核，可在“变更与发布”查看`)" @discard="setView('published')" />
      <div class="mt-head">
        <span class="t-16 fw-600 c-1">{{ cur.name }}</span>
        <span class="t-14 c-3 num">{{ cur.api }} · {{ cur.unit }}</span>
        <a-tag :color="statusColor(cur)">{{ shownStatus(cur) }}</a-tag>
        <span class="t-14 c-3">口径 {{ cur.version }}</span>
        <span v-if="cur.review" class="t-12 c-3">变更单 <a @click="router.push({ path: '/build/ontology/releases', query: { cr: cur.review.cr } })">{{ cur.review.cr }}</a> {{ cur.review.note }}</span>
        <a-segmented v-if="draft && !readonly" size="small" :value="isDraft ? 'draft' : 'published'"
          :options="[{ label: '已发布', value: 'published' }, { label: `草稿 ${draft.id}`, value: 'draft' }]"
          @change="(v: any) => setView(v)" />
        <span class="mt-sp" />
        <a-button @click="router.replace({ query: { ...route.query, tab: 'def' } }); message.success(`已按${isDraft ? `草稿 ${draft?.id}` : '已发布口径'}试算近 30 日，结果见“近 30 日试算”`)">试算</a-button>
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
            <a-tooltip :title="editTip"><a-button size="small" :disabled="!canEdit">编辑定义</a-button></a-tooltip>
          </div>
          <div class="mt-formula t-14 num">{{ FORMULA }}</div>
          <a-descriptions :column="2" bordered size="small" class="mt-desc" :label-style="{ width: '96px', whiteSpace: 'nowrap' }">
            <a-descriptions-item v-for="d in DEF" :key="d.k" :label="d.k"><span class="mt-ell" :title="d.v">{{ d.v }}</span></a-descriptions-item>
          </a-descriptions>
          <div class="mt-sec t-14 fw-600">允许的维度</div>
          <a-table :columns="dimCols" :data-source="DIMS" :pagination="false" size="small" row-key="dim" />
        </div>
        <div class="mt-def-right">
          <div class="mt-sec t-14 fw-600 mt-sec-row">
            <span>近 30 日试算</span><span class="t-12 c-3 fw-400">按口径 v3 计算 · 数据截至 2026-10-24 23:00</span>
          </div>
          <div class="mt-chart"><EChart :option="trendOption" /></div>
          <div class="mt-sec t-14 fw-600">不允许的维度</div>
          <div v-for="d in DENY" :key="d.dim" class="mt-deny">
            <span class="t-14 c-1">{{ d.dim }}</span>
            <span class="t-12 c-3 mt-ell" :title="d.reason">{{ d.reason }}</span>
          </div>
          <div class="mt-sec t-14 fw-600">被引用</div>
          <div class="mt-refs t-14">
            <span>指标 <HoverCard kind="metric" id="atv">客单价</HoverCard>、<HoverCard kind="metric" id="gross_margin">毛利率</HoverCard> 等 3 个</span>
            <span>规则 1 条，看板 2 个，认证问答 1 条</span>
          </div>
        </div>
      </div>

      <div v-else-if="tab === 'dims'" class="mt-body">
        <a-table :columns="dimCols" :data-source="DIMS" :pagination="false" size="middle" row-key="dim" />
        <div class="mt-sec t-14 fw-600">不允许的维度</div>
        <a-table :columns="[{ title: '维度', dataIndex: 'dim', width: 120 }, { title: '原因', dataIndex: 'reason' }]" :data-source="DENY" :pagination="false" size="middle" row-key="dim" />
      </div>

      <div v-else-if="tab === 'versions'" class="mt-body">
        <a-table :columns="verCols" :data-source="VERSIONS.slice((verPage - 1) * 10, verPage * 10)" :pagination="false" size="middle" row-key="ver">
          <template #bodyCell="{ column, record }">
            <template v-if="column.key === 'status'">
              <a-badge status="default" :text="record.status" />
            </template>
            <template v-else-if="column.key === 'op'">
              <a-space><a-tooltip title="暂未开放"><span class="c-3">对比</span></a-tooltip><a v-if="record.status !== '生效中' && !readonly" @click="rollback(record)">回滚</a><a-tooltip v-else-if="record.status !== '生效中'" :title="reason"><span class="c-3">回滚</span></a-tooltip></a-space>
            </template>
          </template>
        </a-table>
        <AppPagination v-model:page="verPage" variant="full" :total="VERSIONS.length" :size="10" unit="条" :empty="false" />
        <div class="t-12 c-3 mt-note">口径版本只随本体整版发布生效；回滚会生成一个新的变更单，经评审后随下一次发布生效。</div>
      </div>

      <div v-else class="mt-body">
        <a-table :columns="refCols" :data-source="REFS.slice((refPage - 1) * 10, refPage * 10)" :pagination="false" size="middle" row-key="name">
          <template #bodyCell="{ column, record }">
            <template v-if="column.key === 'name'"><a v-if="record.to" @click="router.push(record.to)">{{ record.name }}</a><span v-else class="c-1">{{ record.name }}</span></template>
          </template>
        </a-table>
        <AppPagination v-model:page="refPage" variant="full" :total="REFS.length" :size="10" unit="条" :empty="false" />
        <div class="t-12 c-3 mt-note">修改本指标时，以上对象会出现在变更单的影响分析里。</div>
      </div>
    </div>
  </div>
  <StartChangeModal v-model:open="changeOpen" :target-name="changeTarget" @ok="start" />
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
.mt-body { flex: 1; min-height: 0; padding: 12px 16px; display: flex; flex-direction: column; gap: 8px; }
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
