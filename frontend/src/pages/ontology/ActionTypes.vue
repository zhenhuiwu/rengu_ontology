<script setup lang="ts">
/**
 * 动作类型：左侧动作列表，右侧详情。页签：概览 / 参数 / 提交条件 / 写回与副作用 / 执行记录 / 版本历史。
 * URL：?action= 选中项，?tab= 页签，?ppage/psize 参数分页，?rpage/rsize 执行记录分页，?view=draft 草稿，?ver= 历史版本只读。
 * 规则、执行记录在当前页用抽屉打开（useDrawer），不跳转。
 */
import { computed, reactive, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { message } from 'ant-design-vue';
import { QuestionCircleOutlined } from '@ant-design/icons-vue';
import AppPagination from '@/components/AppPagination.vue';
import ObjRef from '@/components/ObjRef.vue';
import EChart from '@/components/EChart.vue';
import StatusTimeline from '@/components/StatusTimeline.vue';
import { useDrawer } from '@/composables/useDrawer';
import { usePagedQuery } from '@/composables/usePagedQuery';
import { useReadonly } from '@/composables/useReadonly';
import { useConfirmStore } from '@/stores/confirm';
import { GRAY, STATUS } from '@/theme';
import { paginate } from '@/mock/paginate';
import { fmtPct, fmtInt } from '@/mock/format';
import { RULES, ALERTS } from '@/mock/dynamic';
import type { DraftCR } from '@/mock/releases';
import {
  ACTION_TYPES, ACTION_GROUPS, ACTION_DRAFTS, ACTION_VERSION_NOTES, paramsOf, condsOf, writebackOf,
  runsOf, runStats, runDaily, versionsOf, type ActionType, type RunStatus,
} from '@/mock/actionEvent';
import DraftBanner from './parts/DraftBanner.vue';
import StartChangeModal from './parts/StartChangeModal.vue';
import { allDrafts } from './parts/useDraftView';

const route = useRoute();
const router = useRouter();
const drawer = useDrawer();
const confirm = useConfirmStore();
const { readonly, reason } = useReadonly();

/* 左侧列表 */
const group = ref('全部');
const kw = ref('');
const filtered = computed(() => ACTION_TYPES.filter((a) => (group.value === '全部' || a.group === group.value)
  && (!kw.value || a.name.includes(kw.value) || a.api.toLowerCase().includes(kw.value.toLowerCase()))));
const LPAGE = 12;
const lpage = ref(1);
watch([group, kw], () => { lpage.value = 1; });
const list = computed(() => paginate(filtered.value, { page: lpage.value, size: LPAGE }).items);
const cur = computed<ActionType>(() => ACTION_TYPES.find((a) => a.id === route.query.action) ?? ACTION_TYPES[0]);
const pick = (id: string) => router.replace({ query: { ...route.query, action: id, view: undefined, ppage: undefined, psize: undefined, rpage: undefined, rsize: undefined } });
const statusColor = (s: ActionType['status']) => (s === '已发布' ? 'success' : s === '变更中' ? 'warning' : 'default');

const TABS = ['overview', 'params', 'conds', 'writeback', 'runs', 'versions'];
const tab = computed({
  get: () => (TABS.includes(route.query.tab as string) ? (route.query.tab as string) : 'overview'),
  set: (v) => router.replace({ query: { ...route.query, tab: v === 'overview' ? undefined : v, ppage: undefined, psize: undefined, rpage: undefined, rsize: undefined } }),
});

/* 草稿：本页本地维护（共用 DraftBanner / StartChangeModal） */
const created = reactive<Record<string, DraftCR>>({});
const draft = computed<DraftCR | undefined>(() => created[cur.value.id] ?? ACTION_DRAFTS[cur.value.id]);
const isDraft = computed(() => !readonly.value && !!draft.value && route.query.view === 'draft');
const editTip = computed(() => (readonly.value ? reason.value : isDraft.value ? '' : '已发布版本只读，请先发起变更'));
const setView = (v: 'published' | 'draft') => router.replace({ query: { ...route.query, view: v === 'draft' ? 'draft' : undefined } });
const changeOpen = ref(false);
const changeTarget = ref('');
const openChange = (name: string) => { changeTarget.value = name; changeOpen.value = true; };
function start(opts: { join?: string; title?: string }) {
  const found = opts.join ? allDrafts().find((d) => d.id === opts.join) : undefined;
  created[cur.value.id] = found ?? { id: `CR-${String(20 + Object.keys(created).length).padStart(4, '0')}`, title: opts.title || '未命名变更', owner: '王敏', updatedAt: '2026-10-25 16:40', changes: 0 };
  setView('draft');
}

async function dryRun() {
  const a = cur.value;
  const ok = await confirm.ask({
    title: `试运行：${a.name}`, target: `${a.target}（使用示例对象）`, tag: '试运行',
    fields: paramsOf(a).filter((p) => p.required).slice(0, 5).map((p) => ({ label: p.name, value: p.def })),
    impact: `只校验提交条件并模拟调用 ${a.system}，不会写回外部系统，也不会修改本体对象。`,
    okText: '开始试运行',
  });
  if (ok) message.success(`试运行通过：${condsOf(a).length} 项提交条件全部满足`);
}

/* 概览 */
const stats = computed(() => runStats(cur.value.id));
const usedBy = computed(() => cur.value.usedBy.map((id) => {
  const r = RULES[id];
  const alerts = ALERTS.filter((al) => al.ruleId === id);
  return { id, name: r.name, trigger: r.trigger, mode: r.action ? '命中后自动执行' : '处理告警时手动执行', alerts: alerts.length, open: alerts.filter((al) => al.status === '待处理').length };
}));
const usedCols = [
  { title: '规则', key: 'rule', width: 220 },
  { title: '触发', dataIndex: 'trigger', width: 96 },
  { title: '使用方式', dataIndex: 'mode' },
  { title: '近期告警', key: 'alerts', width: 120, align: 'right' as const },
];
const daily = computed(() => runDaily(cur.value.id));
const chartOption = computed(() => ({
  grid: { left: 32, right: 8, top: 28, bottom: 24 },
  legend: { top: 0, right: 0, itemWidth: 10, itemHeight: 10, textStyle: { color: GRAY.g8, fontSize: 12 } },
  tooltip: { trigger: 'axis' },
  xAxis: { type: 'category', data: daily.value.map((d) => d.date), axisLine: { lineStyle: { color: GRAY.g6 } }, axisTick: { show: false }, axisLabel: { color: GRAY.g8, fontSize: 12, interval: 3 } },
  yAxis: { type: 'value', minInterval: 1, axisLabel: { color: GRAY.g8, fontSize: 12 }, splitLine: { lineStyle: { color: GRAY.g4 } } },
  series: [
    { name: '成功', type: 'bar', stack: 'r', barWidth: 10, data: daily.value.map((d) => d.ok), itemStyle: { color: GRAY.g7 } },
    { name: '结果未知', type: 'bar', stack: 'r', data: daily.value.map((d) => d.unknown), itemStyle: { color: STATUS.warning } },
    { name: '失败', type: 'bar', stack: 'r', data: daily.value.map((d) => d.failed), itemStyle: { color: STATUS.error } },
  ],
}));
const recentAbnormal = computed(() => runsOf(cur.value.id).filter((r) => r.status === '失败' || r.status === '结果未知').slice(0, 4));

/* 参数 */
const pq = usePagedQuery({ defaultSize: 12, prefix: 'p' });
const params = computed(() => paginate(paramsOf(cur.value), { page: pq.page.value, size: pq.size.value }));
const paramCols = [
  { title: '参数', dataIndex: 'name', width: 150 },
  { title: 'API 名', dataIndex: 'api', width: 150 },
  { title: '类型', dataIndex: 'type', width: 180 },
  { title: '必填', key: 'required', width: 72 },
  { title: '默认值', dataIndex: 'def', width: 200, ellipsis: { showTitle: true } },
  { title: '校验', dataIndex: 'rule', ellipsis: { showTitle: true } },
];

/* 提交条件 */
const conds = computed(() => condsOf(cur.value));
const condCols = [
  { title: '模板', dataIndex: 'kind', width: 110 },
  { title: '条件', dataIndex: 'name', width: 140 },
  { title: '表达式', key: 'expr' },
  { title: '不满足时提示', dataIndex: 'fail', width: 300 },
];

/* 写回与副作用 */
const wb = computed(() => writebackOf(cur.value));
const internal = computed(() => cur.value.endpoint === '—');
const flow = computed(() => {
  const a = cur.value;
  const items: { time: string; text: string; desc?: string; status?: 'done' | 'warn' | 'error' | 'unknown' }[] = [
    { time: '1', text: '校验提交条件', desc: `${conds.value.length} 项，任一不满足即拒绝提交` },
  ];
  if (a.approval !== '免审') items.push({ time: '2', text: '审批', desc: a.approval });
  if (!internal.value) {
    items.push({ time: String(items.length + 1), text: `写回 ${a.system}（同步）`, desc: `超时 ${a.timeout}；${a.idempotent ? '带幂等键，可安全重试' : '非幂等，不自动重试'}` });
    items.push({ time: String(items.length + 1), text: '超时或无响应 ⇒ 结果未知 · 待对账', desc: '不判成功也不判失败；按幂等键对账确认后再提交本体编辑', status: 'unknown' });
  }
  items.push({ time: String(items.length + 1), text: '提交本体编辑', desc: '写回成功后才提交，保证本体与外部系统一致' });
  items.push({ time: String(items.length + 1), text: '执行副作用（异步）', desc: '通知、事件；失败不影响动作结果' });
  return items;
});
const editCols = [
  { title: '操作', dataIndex: 'op', width: 72 },
  { title: '对象', dataIndex: 'obj', width: 180 },
  { title: '字段', dataIndex: 'field' },
];
const effectCols = [
  { title: '类型', dataIndex: 'kind', width: 88 },
  { title: '目标', dataIndex: 'target', width: 280 },
  { title: '时机', dataIndex: 'when', width: 140 },
  { title: '说明', dataIndex: 'note' },
];

/* 执行记录 */
const RUN_FILTERS = ['全部', '成功', '失败', '结果未知', '待审批', '已驳回'];
const runFilter = computed({
  get: () => (RUN_FILTERS.includes(route.query.rs as string) ? (route.query.rs as string) : '全部'),
  set: (v) => router.replace({ query: { ...route.query, rs: v === '全部' ? undefined : v, rpage: undefined } }),
});
const rq = usePagedQuery({ defaultSize: 12, prefix: 'r' });
const runRows = computed(() => runsOf(cur.value.id).filter((r) => runFilter.value === '全部' || r.status === runFilter.value));
const runs = computed(() => paginate(runRows.value, { page: rq.page.value, size: rq.size.value }));
const runCols = [
  { title: '执行时间', dataIndex: 'at', width: 172 },
  { title: '执行记录', key: 'id', width: 170 },
  { title: '目标对象', key: 'obj', width: 220, ellipsis: true },
  { title: '参数摘要', dataIndex: 'summary', ellipsis: { showTitle: true } },
  { title: '执行人', dataIndex: 'by', width: 110 },
  { title: '来源', dataIndex: 'source', width: 110 },
  { title: '结果', key: 'status', width: 160 },
  { title: '耗时', key: 'ms', width: 88, align: 'right' as const },
];
const runBadge = (s: RunStatus) => (s === '失败' ? 'error' : s === '待审批' ? 'warning' : 'default');

/* 版本历史 */
const versions = computed(() => versionsOf(cur.value.version, cur.value.owner, ACTION_VERSION_NOTES));
const verCols = [
  { title: '版本', dataIndex: 'ver', width: 80 },
  { title: '本体发布', dataIndex: 'release', width: 120 },
  { title: '状态', key: 'status', width: 96 },
  { title: '变更说明', dataIndex: 'change' },
  { title: '发布人', dataIndex: 'by', width: 96 },
  { title: '发布时间', dataIndex: 'at', width: 160 },
  { title: '操作', key: 'op', width: 110 },
];
</script>

<template>
  <Teleport defer to="#page-extra">
    <a-tooltip :title="reason"><a-button type="primary" :disabled="readonly" @click="openChange('新建动作类型')">新建动作类型</a-button></a-tooltip>
  </Teleport>

  <div class="at">
    <div class="panel at-list">
      <div class="at-filter">
        <a-input-search v-model:value="kw" placeholder="搜索名称或 API 名" />
        <a-segmented v-model:value="group" :options="ACTION_GROUPS" block size="small" />
      </div>
      <ul class="at-items">
        <li v-for="a in list" :key="a.id" :class="{ active: a.id === cur.id }" @click="pick(a.id)">
          <div class="at-row">
            <span class="t-14 at-name">{{ a.name }}</span>
            <span class="t-12 num" :class="a.status === '变更中' ? 'c-warning' : 'c-3'">{{ a.status === '已发布' ? '' : a.status + ' · ' }}{{ a.version }}</span>
          </div>
          <div class="at-row">
            <span class="t-12 c-3 num">{{ a.api }} → {{ a.target }}</span>
            <span class="t-12 c-3">{{ a.group }}</span>
          </div>
        </li>
      </ul>
      <div class="at-foot">
        <AppPagination v-model:page="lpage" variant="simple" :total="filtered.length" :size="LPAGE" unit="个动作" class="at-pager" empty-text="没有匹配的动作类型" />
      </div>
    </div>

    <div class="panel at-detail">
      <DraftBanner v-if="isDraft && draft" :draft="draft"
        @submit="message.success(`${draft.id} 已提交审核，可在“变更与发布”查看`)" @discard="setView('published')" />
      <div class="at-head">
        <span class="t-16 fw-600 c-1">{{ cur.name }}</span>
        <span class="t-14 c-3 num">{{ cur.api }}</span>
        <span class="t-14 c-2">目标对象 {{ cur.target }}</span>
        <span class="t-14 c-3">{{ cur.version }}</span>
        <a-tag :color="statusColor(cur.status)">{{ cur.status }}</a-tag>
        <a-segmented v-if="draft && !readonly" size="small" :value="isDraft ? 'draft' : 'published'"
          :options="[{ label: '已发布', value: 'published' }, { label: `草稿 ${draft.id}`, value: 'draft' }]"
          @change="(v: any) => setView(v)" />
        <span class="at-sp" />
        <a-button :disabled="cur.status === '草稿'" @click="dryRun">试运行</a-button>
        <a-tooltip :title="reason">
          <a-button v-if="!draft || readonly" type="primary" :disabled="readonly" @click="openChange(`${cur.name}（${cur.api}）`)">发起变更</a-button>
          <a-button v-else-if="!isDraft" type="primary" @click="setView('draft')">继续编辑草稿</a-button>
        </a-tooltip>
      </div>
      <a-tabs v-model:activeKey="tab" class="at-tabs">
        <a-tab-pane key="overview" tab="概览" />
        <a-tab-pane key="params" tab="参数" />
        <a-tab-pane key="conds" tab="提交条件" />
        <a-tab-pane key="writeback" tab="写回与副作用" />
        <a-tab-pane key="runs" tab="执行记录" />
        <a-tab-pane key="versions" tab="版本历史" />
      </a-tabs>

      <!-- 概览 -->
      <div v-if="tab === 'overview'" class="at-body at-ov">
        <div class="at-ov-left">
          <div class="at-sec t-14 fw-600">基本信息</div>
          <a-descriptions :column="2" bordered size="small" class="at-desc">
            <a-descriptions-item label="目标对象">{{ cur.target }}（{{ cur.targetApi }}）</a-descriptions-item>
            <a-descriptions-item label="负责人">{{ cur.owner }}</a-descriptions-item>
            <a-descriptions-item label="说明" :span="2">{{ cur.desc }}</a-descriptions-item>
            <a-descriptions-item label="谁可以执行">{{ cur.runners }}</a-descriptions-item>
            <a-descriptions-item label="审批">{{ cur.approval }}</a-descriptions-item>
            <a-descriptions-item label="写回系统">{{ cur.system }}</a-descriptions-item>
            <a-descriptions-item label="写回接口"><span class="num">{{ cur.endpoint }}</span></a-descriptions-item>
            <a-descriptions-item label="幂等键规则" :span="2"><span class="num">{{ cur.idemKey }}</span></a-descriptions-item>
            <a-descriptions-item label="超时">{{ cur.timeout }}</a-descriptions-item>
            <a-descriptions-item label="重试">{{ cur.retry }}</a-descriptions-item>
          </a-descriptions>
          <div class="at-sec t-14 fw-600 at-sec-row">
            <span>使用该动作的规则与告警</span><span class="t-12 c-3 fw-400">点规则编号在当前页查看规则</span>
          </div>
          <a-table :columns="usedCols" :data-source="usedBy" :pagination="false" size="small" row-key="id"
            :locale="{ emptyText: '暂无规则使用，只能在对象详情里手动执行' }">
            <template #bodyCell="{ column, record }">
              <template v-if="column.key === 'rule'"><ObjRef kind="rule" :id="record.id">{{ record.id }}</ObjRef><span class="c-1"> {{ record.name }}</span></template>
              <template v-else-if="column.key === 'alerts'">
                <span class="num">{{ record.alerts }}</span><span v-if="record.open" class="t-12 c-warning num">（待处理 {{ record.open }}）</span>
              </template>
            </template>
          </a-table>
        </div>
        <div class="at-ov-right">
          <div class="at-sec t-14 fw-600 at-sec-row"><span>近 30 天执行</span><span class="t-12 c-3 fw-400">截至 2026-10-25 15:42</span></div>
          <div class="at-stats">
            <div><div class="t-12 c-3">执行次数</div><div class="t-20 fw-600 c-1 num">{{ fmtInt(stats.total) }}</div></div>
            <div><div class="t-12 c-3">成功率</div><div class="t-20 fw-600 c-1 num">{{ stats.total ? fmtPct(stats.rate, 1) : '--' }}</div></div>
            <div><div class="t-12 c-3">失败</div><div class="t-20 fw-600 num" :class="stats.failed ? 'c-error' : 'c-1'">{{ stats.failed }}</div></div>
            <div><div class="t-12 c-3">结果未知 · 待对账</div><div class="t-20 fw-600 num" :class="stats.unknown ? 'c-warning' : 'c-1'">{{ stats.unknown }}</div></div>
          </div>
          <template v-if="stats.total">
            <div class="at-chart"><EChart :option="chartOption" /></div>
            <div class="at-sec t-14 fw-600">最近异常</div>
            <div v-for="r in recentAbnormal" :key="r.id" class="at-abn">
              <a class="t-14 num" @click="drawer.open('action_run', r.id)">{{ r.id }}</a>
              <span class="t-12 c-3 num">{{ r.at }}</span>
              <span class="t-12 at-abn-st" :class="r.status === '失败' ? 'c-error' : 'c-warning'">
                <QuestionCircleOutlined v-if="r.status === '结果未知'" />{{ r.status === '结果未知' ? '结果未知 · 待对账' : r.code }}
              </span>
            </div>
          </template>
          <a-empty v-else class="at-empty" description="草稿未发布，暂无执行记录" />
        </div>
      </div>

      <!-- 参数 -->
      <div v-else-if="tab === 'params'" class="at-body">
        <div class="at-tool">
          <span class="t-12 c-3">提交时按顺序校验；对象引用类参数在执行面板里用对象选择器填写</span>
          <span class="at-sp" />
          <a-tooltip :title="editTip"><a-button size="small" :disabled="!isDraft">新增参数</a-button></a-tooltip>
        </div>
        <div class="paged-scroll" :class="{ 'is-overflow': pq.overflow.value }">
          <a-table :columns="paramCols" :data-source="params.items" :pagination="false" size="middle" row-key="api">
            <template #bodyCell="{ column, record }">
              <template v-if="column.key === 'required'">{{ record.required ? '是' : '否' }}</template>
            </template>
          </a-table>
        </div>
        <AppPagination v-model:page="pq.page.value" v-model:size="pq.size.value" variant="full" :total="params.total" :default-size="pq.defaultSize" :empty="false" />
      </div>

      <!-- 提交条件 -->
      <div v-else-if="tab === 'conds'" class="at-body">
        <div class="at-tool">
          <span class="t-12 c-3">全部满足才允许提交；条件模板分三类：当前用户、参数、上下文对象</span>
          <span class="at-sp" />
          <a-tooltip :title="editTip"><a-button size="small" :disabled="!isDraft">新增条件</a-button></a-tooltip>
        </div>
        <a-table :columns="condCols" :data-source="conds" :pagination="false" size="middle" row-key="name">
          <template #bodyCell="{ column, record }">
            <template v-if="column.key === 'expr'"><span class="at-code t-12">{{ record.expr }}</span></template>
          </template>
        </a-table>
      </div>

      <!-- 写回与副作用 -->
      <div v-else-if="tab === 'writeback'" class="at-body at-ov">
        <div class="at-ov-left">
          <div class="at-sec t-14 fw-600 at-sec-row">
            <span>写回（同步，决定动作结果）</span>
            <a-tooltip :title="editTip"><a-button size="small" :disabled="!isDraft">编辑写回</a-button></a-tooltip>
          </div>
          <a-descriptions :column="2" bordered size="small">
            <a-descriptions-item label="外部系统">{{ cur.system }}</a-descriptions-item>
            <a-descriptions-item label="接口"><span class="num">{{ cur.endpoint }}</span></a-descriptions-item>
            <a-descriptions-item label="幂等键" :span="2"><span class="num">{{ cur.idemKey }}</span></a-descriptions-item>
            <a-descriptions-item label="超时">{{ cur.timeout }}</a-descriptions-item>
            <a-descriptions-item label="重试">{{ cur.retry }}</a-descriptions-item>
            <a-descriptions-item label="超时处理" :span="2">
              <span v-if="internal" class="c-2">本体内部编辑，不存在结果未知</span>
              <span v-else class="c-warning"><QuestionCircleOutlined /> 结果未知 · 待对账：每 15 分钟按幂等键查询 {{ cur.system }}，确认前不重复写回</span>
            </a-descriptions-item>
          </a-descriptions>
          <div class="at-sec t-14 fw-600">本体编辑（写回成功后提交）</div>
          <a-table :columns="editCols" :data-source="wb.edits" :pagination="false" size="small" row-key="obj" />
          <div class="at-sec t-14 fw-600">副作用（异步，失败不影响动作结果）</div>
          <a-table :columns="effectCols" :data-source="wb.effects" :pagination="false" size="small" row-key="target" />
        </div>
        <div class="at-ov-right">
          <div class="at-sec t-14 fw-600">执行顺序</div>
          <StatusTimeline :items="flow" />
        </div>
      </div>

      <!-- 执行记录 -->
      <div v-else-if="tab === 'runs'" class="at-body">
        <div class="at-tool">
          <a-segmented v-model:value="runFilter" :options="RUN_FILTERS" size="small" />
          <span class="at-sp" />
          <span class="t-12 c-3">点执行记录编号查看过程和写回结果</span>
        </div>
        <div class="paged-scroll">
          <a-table :columns="runCols" :data-source="runs.items" :pagination="false" size="middle" row-key="id"
            :locale="{ emptyText: cur.status === '草稿' ? '草稿未发布，暂无执行记录' : '没有符合条件的执行记录' }">
            <template #bodyCell="{ column, record }">
              <template v-if="column.key === 'id'"><a class="num" @click="drawer.open('action_run', record.id)">{{ record.id }}</a></template>
              <template v-else-if="column.key === 'obj'">
                <ObjRef v-if="record.objKind" :kind="record.objKind" :id="record.objId">{{ record.objLabel }}</ObjRef>
                <span v-else class="num">{{ record.objLabel }}</span>
              </template>
              <template v-else-if="column.key === 'status'">
                <span v-if="record.status === '结果未知'" class="c-warning"><QuestionCircleOutlined /> 结果未知 · 待对账</span>
                <a-badge v-else :status="runBadge(record.status)" :text="record.status" />
              </template>
              <template v-else-if="column.key === 'ms'"><span class="num">{{ (record.ms / 1000).toFixed(2) }} 秒</span></template>
            </template>
          </a-table>
        </div>
        <AppPagination v-model:page="rq.page.value" v-model:size="rq.size.value" variant="full" :total="runs.total" :default-size="rq.defaultSize" :empty="false" />
      </div>

      <!-- 版本历史 -->
      <div v-else class="at-body">
        <a-table :columns="verCols" :data-source="versions" :pagination="false" size="middle" row-key="ver">
          <template #bodyCell="{ column, record }">
            <template v-if="column.key === 'status'"><a-badge :status="'default'" :text="record.status" /></template>
            <template v-else-if="column.key === 'op'">
              <a-space><a>对比</a><a-tooltip v-if="record.status !== '生效中'" :title="reason"><a :class="{ 'at-dis': readonly }">回滚</a></a-tooltip></a-space>
            </template>
          </template>
        </a-table>
        <div class="t-12 c-3">动作类型版本只随本体整版发布生效；进行中的执行按提交时的版本完成，不受新版本影响。</div>
      </div>
    </div>
  </div>
  <StartChangeModal v-model:open="changeOpen" :target-name="changeTarget" @ok="start" />
</template>

<style scoped>
.at { flex: 1; min-height: 0; display: flex; gap: var(--gap); }
.at-list { width: 300px; flex: none; }
.at-filter { padding: 12px 12px 8px; display: flex; flex-direction: column; gap: 8px; flex: none; }
.at-items { list-style: none; margin: 0; padding: 0 8px; flex: 1; min-height: 0; }
.at-items li { padding: 6px 8px; border-radius: var(--radius); cursor: pointer; }
.at-items li:hover { background: var(--g3); }
.at-items li.active { background: var(--brand-bg); }
.at-items li.active .at-name { color: var(--brand); }
.at-row { display: flex; justify-content: space-between; align-items: center; gap: 8px; }
.at-pager { flex: 1; min-height: 0; }
.at-foot { padding: 8px 12px 8px 16px; border-top: var(--border); flex: none; display: flex; align-items: center; }
.at-detail { flex: 1; min-width: 0; }
.at-head { display: flex; align-items: center; gap: 12px; height: 52px; padding: 0 16px; flex: none; }
.at-head :deep(.ant-tag) { margin-inline-end: 0; }
.at-sp { flex: 1; }
.at-tabs { padding: 0 16px; flex: none; }
.at-tabs :deep(.ant-tabs-nav) { margin-bottom: 0; }
.at-body { flex: 1; min-height: 0; padding: 12px 16px; display: flex; flex-direction: column; gap: 8px; }
.at-ov { flex-direction: row; gap: 16px; }
.at-ov-left { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 8px; }
.at-ov-right { width: 400px; flex: none; display: flex; flex-direction: column; gap: 8px; padding-left: 16px; border-left: var(--border); }
.at-sec { margin-top: 4px; }
.at-sec-row { display: flex; justify-content: space-between; align-items: center; }
.at-stats { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
.at-stats > div { padding: 8px 12px; border: var(--border); border-radius: var(--radius); }
.at-chart { height: 180px; }
.at-abn { display: grid; grid-template-columns: 170px 1fr; column-gap: 8px; padding: 6px 0; border-bottom: var(--border); }
.at-abn-st { grid-column: 1 / 3; display: inline-flex; gap: 4px; align-items: center; }
.at-empty { margin-top: 32px; }
.at-tool { display: flex; align-items: center; gap: 8px; flex: none; }
.at-code { font-family: Menlo, Consolas, "Noto Sans Mono CJK SC", monospace; color: var(--g9); }
.at-dis { color: var(--g7); cursor: not-allowed; }
</style>
