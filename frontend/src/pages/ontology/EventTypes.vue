<script setup lang="ts">
/**
 * 事件类型：左侧事件列表，右侧详情。页签：概览 / 载荷字段 / 订阅方 / 近期事件 / 版本历史。
 * URL：?event= 选中项，?tab= 页签，?fpage/fsize 载荷字段分页，?range= 时间范围 + ?cursor= 近期事件游标，?view=draft 草稿，?ver= 历史版本只读。
 */
import { computed, reactive, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { message } from 'ant-design-vue';
import AppPagination from '@/components/AppPagination.vue';
import ObjRef from '@/components/ObjRef.vue';
import EChart from '@/components/EChart.vue';
import { useDrawer } from '@/composables/useDrawer';
import { usePagedQuery, useCursorQuery } from '@/composables/usePagedQuery';
import { useReadonly } from '@/composables/useReadonly';
import { useConfirmStore } from '@/stores/confirm';
import { GRAY } from '@/theme';
import { paginate, paginateCursor } from '@/mock/paginate';
import { fmtInt } from '@/mock/format';
import { RULES } from '@/mock/dynamic';
import type { DraftCR } from '@/mock/releases';
import {
  EVENT_TYPES, EVENT_SOURCES, EVENT_DRAFTS, EVENT_VERSION_NOTES, payloadOf, subscribersOf, eventsOf, eventDaily, versionsOf,
  type EventType, type EventOutcome,
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
const source = ref('全部');
const kw = ref('');
const filtered = computed(() => EVENT_TYPES.filter((e) => (source.value === '全部' || e.source === source.value)
  && (!kw.value || e.name.includes(kw.value) || e.api.toLowerCase().includes(kw.value.toLowerCase()))));
const LPAGE = 12;
const lpage = ref(1);
watch([source, kw], () => { lpage.value = 1; });
const list = computed(() => paginate(filtered.value, { page: lpage.value, size: LPAGE }).items);
const cur = computed<EventType>(() => EVENT_TYPES.find((e) => e.id === route.query.event) ?? EVENT_TYPES[0]);
const RESET = { view: undefined, fpage: undefined, fsize: undefined, cursor: undefined };
const pick = (id: string) => router.replace({ query: { ...route.query, ...RESET, event: id } });
const statusColor = (s: EventType['status']) => (s === '已发布' ? 'success' : s === '变更中' ? 'warning' : 'default');

const TABS = ['overview', 'payload', 'subs', 'recent', 'versions'];
const tab = computed({
  get: () => (TABS.includes(route.query.tab as string) ? (route.query.tab as string) : 'overview'),
  set: (v) => router.replace({ query: { ...route.query, ...RESET, view: route.query.view, tab: v === 'overview' ? undefined : v } }),
});

/* 草稿（本页本地维护，共用 DraftBanner / StartChangeModal） */
const created = reactive<Record<string, DraftCR>>({});
const draft = computed<DraftCR | undefined>(() => created[cur.value.id] ?? EVENT_DRAFTS[cur.value.id]);
const isDraft = computed(() => !readonly.value && !!draft.value && route.query.view === 'draft');
const editTip = computed(() => (readonly.value ? reason.value : isDraft.value ? '' : '已发布版本只读，请先发起变更'));
const setView = (v: 'published' | 'draft') => router.replace({ query: { ...route.query, view: v === 'draft' ? 'draft' : undefined } });
const changeOpen = ref(false);
const changeTarget = ref('');
const openChange = (name: string) => { changeTarget.value = name; changeOpen.value = true; };
function start(opts: { join?: string; title?: string }) {
  const found = opts.join ? allDrafts().find((d) => d.id === opts.join) : undefined;
  created[cur.value.id] = found ?? { id: `CR-${String(30 + Object.keys(created).length).padStart(4, '0')}`, title: opts.title || '未命名变更', owner: '王敏', updatedAt: '2026-10-25 16:40', changes: 0 };
  setView('draft');
}

async function simulate() {
  const e = cur.value;
  const ok = await confirm.ask({
    title: `模拟触发：${e.name}`, target: `${e.object}（使用示例载荷）`, tag: '测试',
    fields: e.specific.slice(0, 4).map((f) => ({ label: f.name, value: f.sample })),
    impact: '只投递到订阅方的测试通道，用来验证过滤条件，不会产生告警、执行动作或写入下游。',
    okText: '发送测试事件',
  });
  if (ok) message.success(`测试事件已投递 ${subscribersOf(e).filter((s) => s.status !== '停用').length} 个订阅方的测试通道`);
}

/* 概览 */
const subs = computed(() => subscribersOf(cur.value));
const daily = computed(() => eventDaily(cur.value.id, cur.value.weekly));
const chartOption = computed(() => ({
  grid: { left: 48, right: 8, top: 12, bottom: 24 },
  tooltip: { trigger: 'axis' },
  xAxis: { type: 'category', data: daily.value.map((d) => d.date), axisLine: { lineStyle: { color: GRAY.g6 } }, axisTick: { show: false }, axisLabel: { color: GRAY.g8, fontSize: 12, interval: 3 } },
  yAxis: { type: 'value', minInterval: 1, axisLabel: { color: GRAY.g8, fontSize: 12 }, splitLine: { lineStyle: { color: GRAY.g4 } } },
  series: [{ name: '事件数', type: 'bar', barWidth: 10, data: daily.value.map((d) => d.value), itemStyle: { color: GRAY.g7 } }],
}));
const recentAll = computed(() => eventsOf(cur.value.id));
const health = computed(() => {
  const rows = recentAll.value;
  const c = (o: EventOutcome) => rows.filter((r) => r.outcome === o).length;
  return { total: rows.length, dedup: c('已去重'), failed: c('投递失败'), partial: c('部分投递'),
    p95: rows.length ? [...rows].sort((a, b) => a.latencyMs - b.latencyMs)[Math.floor(rows.length * 0.95)]?.latencyMs ?? 0 : 0 };
});

/* 载荷字段 */
const fq = usePagedQuery({ defaultSize: 12, prefix: 'f' });
const fields = computed(() => paginate(payloadOf(cur.value), { page: fq.page.value, size: fq.size.value }));
const fieldCols = [
  { title: '字段', dataIndex: 'name', width: 140 },
  { title: 'API 名', dataIndex: 'api', width: 150 },
  { title: '类型', dataIndex: 'type', width: 100 },
  { title: '必填', key: 'required', width: 72 },
  { title: '说明', dataIndex: 'desc', ellipsis: { showTitle: true } },
  { title: '示例', dataIndex: 'sample', width: 260, ellipsis: { showTitle: true } },
];
const isEnvelope = (api: string) => !cur.value.specific.some((f) => f.api === api);

/* 订阅方 */
const subCols = [
  { title: '类型', dataIndex: 'kind', width: 96 },
  { title: '订阅方', key: 'name', width: 280 },
  { title: '过滤条件', dataIndex: 'filter', width: 220, ellipsis: { showTitle: true } },
  { title: '收到后', dataIndex: 'then', ellipsis: { showTitle: true } },
  { title: '近 7 天投递', key: 'weekly', width: 120, align: 'right' as const },
  { title: '状态', key: 'status', width: 88 },
];

/* 近期事件（事件流：先选时间范围，游标分页） */
const RANGES = [{ label: '近 24 小时', value: '24h' }, { label: '近 7 天', value: '7d' }];
const range = computed({
  get: () => (route.query.range === '24h' ? '24h' : '7d'),
  set: (v) => router.replace({ query: { ...route.query, range: v === '7d' ? undefined : v, cursor: undefined } }),
});
const OUTCOMES = ['全部', '已投递', '已生成告警', '已去重', '部分投递', '投递失败'];
const outcome = computed({
  get: () => (OUTCOMES.includes(route.query.oc as string) ? (route.query.oc as string) : '全部'),
  set: (v) => router.replace({ query: { ...route.query, oc: v === '全部' ? undefined : v, cursor: undefined } }),
});
const cq = useCursorQuery({ size: 12 });
const CUTOFF_24H = '2026-10-24 15:42:18';
const recentRows = computed(() => recentAll.value.filter((r) => (range.value === '7d' || r.at >= CUTOFF_24H) && (outcome.value === '全部' || r.outcome === outcome.value)));
const recent = computed(() => paginateCursor(recentRows.value, { cursor: cq.cursor.value, size: cq.size }));
const recentCols = [
  { title: '时间', dataIndex: 'at', width: 172 },
  { title: '事件', key: 'id', width: 190 },
  { title: '对象', key: 'obj', width: 220, ellipsis: true },
  { title: '载荷摘要', dataIndex: 'summary', ellipsis: { showTitle: true } },
  { title: '订阅方', key: 'subs', width: 88, align: 'right' as const },
  { title: '延迟', key: 'lat', width: 96, align: 'right' as const },
  { title: '结果', key: 'outcome', width: 120 },
];
const outcomeBadge = (o: EventOutcome) => (o === '投递失败' ? 'error' : o === '部分投递' ? 'warning' : 'default');

/* 版本历史 */
const versions = computed(() => versionsOf(cur.value.version, cur.value.owner, EVENT_VERSION_NOTES));
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
    <a-tooltip :title="reason"><a-button type="primary" :disabled="readonly" @click="openChange('新建事件类型')">新建事件类型</a-button></a-tooltip>
  </Teleport>

  <div class="et">
    <div class="panel et-list">
      <div class="et-filter">
        <a-input-search v-model:value="kw" placeholder="搜索名称或 API 名" />
        <a-segmented v-model:value="source" :options="EVENT_SOURCES" block size="small" />
      </div>
      <ul class="et-items">
        <li v-for="e in list" :key="e.id" :class="{ active: e.id === cur.id }" @click="pick(e.id)">
          <div class="et-row">
            <span class="t-14 et-name">{{ e.name }}</span>
            <span class="t-12 num" :class="e.status === '变更中' ? 'c-warning' : 'c-3'">{{ e.status === '已发布' ? '' : e.status + ' · ' }}{{ e.version }}</span>
          </div>
          <div class="et-row">
            <span class="t-12 c-3 num">{{ e.api }}</span>
            <span class="t-12 c-3">{{ e.source }} · {{ e.object }}</span>
          </div>
        </li>
      </ul>
      <div class="et-foot">
        <AppPagination v-model:page="lpage" variant="simple" :total="filtered.length" :size="LPAGE" unit="个事件" class="et-pager" empty-text="没有匹配的事件类型" />
      </div>
    </div>

    <div class="panel et-detail">
      <DraftBanner v-if="isDraft && draft" :draft="draft"
        @submit="message.success(`${draft.id} 已提交审核，可在“变更与发布”查看`)" @discard="setView('published')" />
      <div class="et-head">
        <span class="t-16 fw-600 c-1">{{ cur.name }}</span>
        <span class="t-14 c-3 num">{{ cur.api }}</span>
        <span class="t-14 c-2">{{ cur.source }} · {{ cur.object }}</span>
        <span class="t-14 c-3">{{ cur.version }}</span>
        <a-tag :color="statusColor(cur.status)">{{ cur.status }}</a-tag>
        <a-segmented v-if="draft && !readonly" size="small" :value="isDraft ? 'draft' : 'published'"
          :options="[{ label: '已发布', value: 'published' }, { label: `草稿 ${draft.id}`, value: 'draft' }]"
          @change="(v: any) => setView(v)" />
        <span class="et-sp" />
        <a-button :disabled="cur.status === '草稿'" @click="simulate">模拟触发</a-button>
        <a-tooltip :title="reason">
          <a-button v-if="!draft || readonly" type="primary" :disabled="readonly" @click="openChange(`${cur.name}（${cur.api}）`)">发起变更</a-button>
          <a-button v-else-if="!isDraft" type="primary" @click="setView('draft')">继续编辑草稿</a-button>
        </a-tooltip>
      </div>
      <a-tabs v-model:activeKey="tab" class="et-tabs">
        <a-tab-pane key="overview" tab="概览" />
        <a-tab-pane key="payload" tab="载荷字段" />
        <a-tab-pane key="subs" tab="订阅方" />
        <a-tab-pane key="recent" tab="近期事件" />
        <a-tab-pane key="versions" tab="版本历史" />
      </a-tabs>

      <!-- 概览 -->
      <div v-if="tab === 'overview'" class="et-body et-ov">
        <div class="et-ov-left">
          <div class="et-sec t-14 fw-600">基本信息</div>
          <a-descriptions :column="2" bordered size="small">
            <a-descriptions-item label="来源">{{ cur.source }}</a-descriptions-item>
            <a-descriptions-item label="来源说明">
              <template v-if="cur.ruleId"><ObjRef kind="rule" :id="cur.ruleId">{{ cur.ruleId }}</ObjRef> {{ RULES[cur.ruleId].name }}</template>
              <template v-else>{{ cur.sourceRef }}</template>
            </a-descriptions-item>
            <a-descriptions-item label="触发对象">{{ cur.object }}（{{ cur.objectApi }}）</a-descriptions-item>
            <a-descriptions-item label="负责人">{{ cur.owner }}</a-descriptions-item>
            <a-descriptions-item label="说明" :span="2">{{ cur.desc }}</a-descriptions-item>
            <a-descriptions-item label="载荷摘要" :span="2">{{ cur.specific.map((f) => f.name).join('、') }}，另含 10 个通用信封字段</a-descriptions-item>
            <a-descriptions-item label="去重键"><span class="num">{{ cur.dedupKey }}</span></a-descriptions-item>
            <a-descriptions-item label="去重窗口">{{ cur.dedupWindow }}</a-descriptions-item>
            <a-descriptions-item label="投递保证">{{ cur.delivery }}</a-descriptions-item>
            <a-descriptions-item label="顺序">{{ cur.ordering }}</a-descriptions-item>
            <a-descriptions-item label="保留期">{{ cur.retention }}</a-descriptions-item>
            <a-descriptions-item label="近 7 天">{{ fmtInt(cur.weekly) }} 条</a-descriptions-item>
          </a-descriptions>
          <div class="et-sec t-14 fw-600 et-sec-row">
            <span>订阅方 {{ subs.length }} 个</span><span class="t-12 c-3 fw-400">完整列表见“订阅方”页签</span>
          </div>
          <div v-for="s in subs.slice(0, 4)" :key="s.name" class="et-sub">
            <span class="t-12 c-3 et-sub-k">{{ s.kind }}</span>
            <span class="t-14 c-1">
              <template v-if="s.ruleId"><ObjRef kind="rule" :id="s.ruleId">{{ s.ruleId }}</ObjRef> </template>{{ s.name }}
            </span>
            <span class="t-12" :class="s.status === '异常' ? 'c-warning' : 'c-3'">{{ s.status }}</span>
          </div>
        </div>
        <div class="et-ov-right">
          <div class="et-sec t-14 fw-600 et-sec-row"><span>近 14 天事件数</span><span class="t-12 c-3 fw-400">截至 2026-10-25 15:42</span></div>
          <template v-if="cur.status !== '草稿'">
            <div class="et-chart"><EChart :option="chartOption" /></div>
            <div class="et-stats">
              <div><div class="t-12 c-3">近 7 天</div><div class="t-20 fw-600 c-1 num">{{ fmtInt(cur.weekly) }}</div></div>
              <div><div class="t-12 c-3">处理延迟 P95</div><div class="t-20 fw-600 c-1 num">{{ health.p95 }} 毫秒</div></div>
              <div><div class="t-12 c-3">去重丢弃（近期样本）</div><div class="t-20 fw-600 c-1 num">{{ health.dedup }}</div></div>
              <div><div class="t-12 c-3">投递失败 / 部分投递</div>
                <div class="t-20 fw-600 num"><span :class="health.failed ? 'c-error' : 'c-1'">{{ health.failed }}</span><span class="c-3"> / </span><span :class="health.partial ? 'c-warning' : 'c-1'">{{ health.partial }}</span></div></div>
            </div>
          </template>
          <a-empty v-else class="et-empty" description="草稿未发布，暂无事件" />
        </div>
      </div>

      <!-- 载荷字段 -->
      <div v-else-if="tab === 'payload'" class="et-body">
        <div class="et-tool">
          <span class="t-12 c-3">事件自身字段在前，通用信封字段在后（所有事件类型共有，不可修改）</span>
          <span class="et-sp" />
          <a-tooltip :title="editTip"><a-button size="small" :disabled="!isDraft">新增字段</a-button></a-tooltip>
        </div>
        <div class="paged-scroll">
          <a-table :columns="fieldCols" :data-source="fields.items" :pagination="false" size="middle" row-key="api">
            <template #bodyCell="{ column, record }">
              <template v-if="column.key === 'required'">{{ record.required ? '是' : '否' }}</template>
              <template v-else-if="column.dataIndex === 'name'">{{ record.name }}<span v-if="isEnvelope(record.api)" class="t-12 c-3"> · 信封</span></template>
            </template>
          </a-table>
        </div>
        <AppPagination v-model:page="fq.page.value" v-model:size="fq.size.value" variant="full" :total="fields.total" :default-size="fq.defaultSize" :empty="false" />
      </div>

      <!-- 订阅方 -->
      <div v-else-if="tab === 'subs'" class="et-body">
        <div class="et-tool">
          <span class="t-12 c-3">规则、自动化、通知和外部订阅都按同一份载荷接收；点规则编号在当前页查看规则</span>
          <span class="et-sp" />
          <a-tooltip :title="editTip"><a-button size="small" :disabled="!isDraft">新增订阅</a-button></a-tooltip>
        </div>
        <a-table :columns="subCols" :data-source="subs" :pagination="false" size="middle" row-key="name">
          <template #bodyCell="{ column, record }">
            <template v-if="column.key === 'name'">
              <template v-if="record.ruleId"><ObjRef kind="rule" :id="record.ruleId">{{ record.ruleId }}</ObjRef> </template>{{ record.name }}
            </template>
            <template v-else-if="column.key === 'weekly'"><span class="num">{{ fmtInt(record.weekly) }}</span></template>
            <template v-else-if="column.key === 'status'">
              <a-badge :status="record.status === '异常' ? 'warning' : 'default'" :text="record.status" />
            </template>
          </template>
        </a-table>
      </div>

      <!-- 近期事件 -->
      <div v-else-if="tab === 'recent'" class="et-body">
        <div class="et-tool">
          <a-segmented v-model:value="range" :options="RANGES" size="small" />
          <a-segmented v-model:value="outcome" :options="OUTCOMES" size="small" />
          <span class="et-sp" />
          <span class="t-12 c-3">事件流按时间倒序，点事件编号查看投递过程</span>
        </div>
        <div class="paged-scroll">
          <a-table :columns="recentCols" :data-source="recent.items" :pagination="false" size="middle" row-key="id"
            :locale="{ emptyText: cur.status === '草稿' ? '草稿未发布，暂无事件' : '该时间范围内没有事件' }">
            <template #bodyCell="{ column, record }">
              <template v-if="column.key === 'id'"><a class="num" @click="drawer.open('event', record.id)">{{ record.id }}</a></template>
              <template v-else-if="column.key === 'obj'">
                <ObjRef v-if="record.objKind" :kind="record.objKind" :id="record.objId">{{ record.objLabel }}</ObjRef>
                <span v-else class="num">{{ record.objLabel }}</span>
              </template>
              <template v-else-if="column.key === 'subs'"><span class="num">{{ record.subscribers }}</span></template>
              <template v-else-if="column.key === 'lat'"><span class="num">{{ record.latencyMs }} 毫秒</span></template>
              <template v-else-if="column.key === 'outcome'"><a-badge :status="outcomeBadge(record.outcome)" :text="record.outcome" /></template>
            </template>
          </a-table>
        </div>
        <AppPagination variant="cursor" :has-prev="cq.hasPrev.value" :has-next="!!recent.nextCursor" @prev="cq.prev()" @next="cq.next(recent.nextCursor)">
          <template #extra><span class="t-12 c-3">事件流不显示总数；保留期 {{ cur.retention }}</span></template>
        </AppPagination>
      </div>

      <!-- 版本历史 -->
      <div v-else class="et-body">
        <a-table :columns="verCols" :data-source="versions" :pagination="false" size="middle" row-key="ver">
          <template #bodyCell="{ column, record }">
            <template v-if="column.key === 'status'"><a-badge status="default" :text="record.status" /></template>
            <template v-else-if="column.key === 'op'">
              <a-space><a>对比</a><a-tooltip v-if="record.status !== '生效中'" :title="reason"><a :class="{ 'et-dis': readonly }">回滚</a></a-tooltip></a-space>
            </template>
          </template>
        </a-table>
        <div class="t-12 c-3">载荷只能新增可选字段；删除或改类型属于不兼容变更，需要所有订阅方确认后随整版发布。</div>
      </div>
    </div>
  </div>
  <StartChangeModal v-model:open="changeOpen" :target-name="changeTarget" @ok="start" />
</template>

<style scoped>
.et { flex: 1; min-height: 0; display: flex; gap: var(--gap); }
.et-list { width: 300px; flex: none; }
.et-filter { padding: 12px 12px 8px; display: flex; flex-direction: column; gap: 8px; flex: none; }
.et-items { list-style: none; margin: 0; padding: 0 8px; flex: 1; min-height: 0; }
.et-items li { padding: 6px 8px; border-radius: var(--radius); cursor: pointer; }
.et-items li:hover { background: var(--g3); }
.et-items li.active { background: var(--brand-bg); }
.et-items li.active .et-name { color: var(--brand); }
.et-row { display: flex; justify-content: space-between; align-items: center; gap: 8px; }
.et-pager { flex: 1; min-height: 0; }
.et-foot { padding: 8px 12px 8px 16px; border-top: var(--border); flex: none; display: flex; align-items: center; }
.et-detail { flex: 1; min-width: 0; }
.et-head { display: flex; align-items: center; gap: 12px; height: 52px; padding: 0 16px; flex: none; }
.et-head :deep(.ant-tag) { margin-inline-end: 0; }
.et-sp { flex: 1; }
.et-tabs { padding: 0 16px; flex: none; }
.et-tabs :deep(.ant-tabs-nav) { margin-bottom: 0; }
.et-body { flex: 1; min-height: 0; padding: 12px 16px; display: flex; flex-direction: column; gap: 8px; }
.et-ov { flex-direction: row; gap: 16px; }
.et-ov-left { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 8px; }
.et-ov-right { width: 400px; flex: none; display: flex; flex-direction: column; gap: 8px; padding-left: 16px; border-left: var(--border); }
.et-sec { margin-top: 4px; }
.et-sec-row { display: flex; justify-content: space-between; align-items: center; }
.et-chart { height: 200px; }
.et-stats { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
.et-stats > div { padding: 8px 12px; border: var(--border); border-radius: var(--radius); }
.et-sub { display: grid; grid-template-columns: 72px 1fr auto; gap: 8px; align-items: center; padding: 6px 0; border-bottom: var(--border); }
.et-empty { margin-top: 32px; }
.et-tool { display: flex; align-items: center; gap: 8px; flex: none; }
.et-dis { color: var(--g7); cursor: not-allowed; }
</style>
