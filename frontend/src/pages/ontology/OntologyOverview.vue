<script setup lang="ts">
/**
 * 本体总览：六类资源的数量与健康度、待处理（变更单 + 数据质量失败）、对象关系缩略图、最近发布。
 * 数据全部取自已有 mock（links / actionEvent / metrics / objectProps / changes / quality / releases），本页不维护数据。
 * 颜色：灰=正常（含草稿、已发布、已通过待发布），橙=审核中 / 结果未知，红=失败；数量为 0 一律灰。
 * 写操作只有“发起变更”，预览 / 历史版本期间只读（useReadonly）。
 */
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import { useRouter } from 'vue-router';
import { Empty, message } from 'ant-design-vue';
import { RightOutlined } from '@ant-design/icons-vue';
import type { Graph } from '@antv/x6';
import GraphCanvas from '@/components/GraphCanvas.vue';
import AppPagination from '@/components/AppPagination.vue';
import InfoTip from '@/components/InfoTip.vue';
import StartChangeModal from './parts/StartChangeModal.vue';
import { useReadonly } from '@/composables/useReadonly';
import { GRAY, STATUS } from '@/theme';
import { LINK_OBJECTS, LINKS } from '@/mock/links';
import { ACTION_TYPES, EVENT_TYPES, runsOf, eventsOf } from '@/mock/actionEvent';
import { METRICS } from '@/mock/metrics';
import { DIM_ITEMS } from '@/mock/dimensions';
import { draftOf, useDraftView } from './parts/useDraftView';
import { useAppStore } from '@/stores/app';
import { CHANGE_REQUESTS, CURRENT_USER } from '@/mock/changes';
import { QUALITY_CHECKS, DATA_CUTOFF } from '@/mock/quality';
import { RELEASES } from '@/api/releases';
import { fallBackIfDown } from '@/api/fallback';
import { fetchGraph, isOverviewReal, mockGraph, type OverviewGraph } from '@/api/overview';

const router = useRouter();
const { readonly, reason } = useReadonly();
const app = useAppStore();
// 当前 / 下一版本号取 app store（mock 模式是 mock 值，versioning 走真接口时是 /releases/current 的 current / next）
const CURRENT_VERSION = computed(() => app.ontologyVersion);
const NEXT_VERSION = computed(() => app.ontologyNextVersion);
/** 查看历史版本（?ver=）：不显示当前待处理，最近发布只列到所选版本；预览模式不算 */
const histVer = computed(() => (app.viewMode === 'history' ? app.viewVersion : null));

/* ── 资源卡片 ── */
// [推断] 卡片计数 / 健康度仍读 mock：/ontology/graph 只给已发布对象和两端都已发布的关系，没有草稿数、孤儿记录、动作 / 事件 / 指标 / 维度统计
type Tone = 'normal' | 'attention' | 'failed';
interface Health { n: number; text: string; tone: Tone }
interface Card { key: string; title: string; count: number; unit: string; path: string; health: Health[] }

const failedChecks = QUALITY_CHECKS.filter((c) => c.result === '失败');
const failedByType = new Map<string, number>();
failedChecks.forEach((c) => failedByType.set(c.typeKey, (failedByType.get(c.typeKey) ?? 0) + 1));
const runs = ACTION_TYPES.flatMap((a) => runsOf(a.id));
const events = EVENT_TYPES.flatMap((e) => eventsOf(e.id));
// 维度与字典：与“维度与字典”页同一份数据（dimensions.ts），历史版本只算当时已有的
const dimItems = computed(() => DIM_ITEMS.filter((d) => d.status !== '已驳回' && (!histVer.value || d.since <= histVer.value)));
const openCrs = CHANGE_REQUESTS.filter((c) => c.status === '草稿' || c.status === '审核中' || c.status === '已通过');

const cards = computed<Card[]>(() => [
  { key: 'obj', title: '对象类型', unit: '个', path: '/build/ontology/object-types', count: LINK_OBJECTS.filter((o) => !o.draft).length, health: [
    { n: LINK_OBJECTS.filter((o) => o.draft).length, text: '个草稿', tone: 'normal' },
    { n: failedByType.size, text: '个有质量检查失败', tone: 'failed' },
  ] },
  { key: 'link', title: '关系类型', unit: '条', path: '/build/ontology/link-types', count: LINKS.filter((l) => l.status === '已发布').length, health: [
    { n: LINKS.filter((l) => l.status === '草稿').length, text: '条草稿', tone: 'normal' },
    { n: LINKS.filter((l) => (l.quality?.orphans ?? 0) > 0).length, text: '条有孤儿记录', tone: 'normal' },
  ] },
  { key: 'action', title: '动作类型', unit: '个', path: '/build/ontology/action-types', count: ACTION_TYPES.length, health: [
    { n: ACTION_TYPES.filter((a) => a.status !== '已发布').length, text: '个变更中 / 草稿', tone: 'normal' },
    { n: runs.filter((r) => r.status === '结果未知').length, text: '次结果未知', tone: 'attention' },
    { n: runs.filter((r) => r.status === '失败').length, text: '次执行失败', tone: 'failed' },
  ] },
  { key: 'event', title: '事件类型', unit: '个', path: '/build/ontology/event-types', count: EVENT_TYPES.length, health: [
    { n: EVENT_TYPES.filter((e) => e.status !== '已发布').length, text: '个变更中 / 草稿', tone: 'normal' },
    { n: events.filter((e) => e.outcome === '投递失败').length, text: '次投递失败', tone: 'failed' },
  ] },
  { key: 'metric', title: '指标', unit: '个', path: '/build/ontology/metrics', count: METRICS.filter((m) => m.status === '已发布').length, health: [
    { n: METRICS.filter((m) => m.status === '草稿').length, text: '个草稿', tone: 'normal' },
    { n: METRICS.filter((m) => m.status === '已下线').length, text: '个已下线', tone: 'normal' },
  ] },
  { key: 'dim', title: '维度与字典', unit: '个', path: '/build/ontology/dimensions', count: dimItems.value.length, health: [
    { n: dimItems.value.filter((d) => d.kind === '维度').length, text: '个维度', tone: 'normal' },
    { n: dimItems.value.filter((d) => d.kind === '码表').length, text: '个码表', tone: 'normal' },
    { n: dimItems.value.filter((d) => d.status === '草稿' || draftOf('dimension', d.id)).length, text: '个变更中', tone: 'normal' },
  ] },
]);
const healthTip = (hs: Health[]) => hs.map((h) => `${h.n} ${h.text}`).join(' · ');
const alerts = (hs: Health[]) => hs.filter((h) => h.n > 0 && h.tone !== 'normal');
const toneCls = (h: Health) => (h.n === 0 || h.tone === 'normal' ? 'c-3' : h.tone === 'failed' ? 'tl-failed' : 'tl-attention');

/* ── 待处理：未发布的变更单 + 失败的数据质量检查 ── */
interface Todo { id: string; kind: '变更' | '数据质量'; title: string; who: string; status: string; tone: Tone; time: string; go: () => void }
const CR_STATUS: Record<string, string> = { 草稿: '草稿', 审核中: '审核中', 已通过: '已通过 · 待发布' };
const todos = computed<Todo[]>(() => [
  ...openCrs.map((c): Todo => ({
    id: c.id, kind: '变更', title: c.title, who: c.status === '审核中' ? `审核人 ${c.reviewer}` : c.owner,
    status: CR_STATUS[c.status], tone: c.status === '审核中' ? 'attention' : 'normal', time: c.updatedAt,
    go: () => router.push({ path: '/build/ontology/releases', query: { cr: c.id } }),
  })),
  ...failedChecks.map((c): Todo => ({
    id: c.id, kind: '数据质量', title: `${c.typeName} · ${c.kind}：${c.rule}`, who: `实际 ${c.actual}`,
    status: '失败', tone: 'failed', time: c.failSince ?? c.lastRun,
    go: () => router.push({ path: '/build/data/quality', query: { result: '失败', check: c.id } }),
  })),
]);
const filterKind = ref<'全部' | '变更' | '数据质量'>('全部');
const shown = computed(() => todos.value.filter((t) => filterKind.value === '全部' || t.kind === filterKind.value));
const page = ref(1);
const ROW_H = 44;
const listEl = ref<HTMLElement>();
const size = ref(8);
let ro: ResizeObserver | null = null;
onMounted(() => {
  ro = new ResizeObserver(([e]) => { size.value = Math.max(3, Math.floor(e.contentRect.height / ROW_H)); });
  if (listEl.value) ro.observe(listEl.value);
});
onBeforeUnmount(() => ro?.disconnect());
watch(filterKind, () => { page.value = 1; });
const pageRows = computed(() => shown.value.slice((page.value - 1) * size.value, page.value * size.value));
const stCls = (t: Tone) => (t === 'failed' ? 'tl-failed' : t === 'attention' ? 'tl-attention' : 'c-3');

/* ── 对象关系缩略图（只读；点对象进入对象类型） ── */
const POS: Record<string, [number, number]> = {
  promotion: [0.25, 0.1], product: [0.5, 0.12], category: [0.72, 0.04], supplier: [0.92, 0.2],
  refund_line: [0.05, 0.33], order_line: [0.27, 0.33], inventory: [0.73, 0.34],
  refund: [0.25, 0.52], order: [0.5, 0.5], warehouse: [0.94, 0.55], shipment: [0.72, 0.64],
  member_level: [0.05, 0.6], member: [0.25, 0.72], stored_card: [0.05, 0.86],
  store: [0.5, 0.8], employee: [0.86, 0.84], province: [0.3, 0.96], region: [0.7, 0.96],
};
const VW = 960; const VH = 540; const NW = 96; const NH = 32;
// 节点 / 边：mock 模式读 LINK_OBJECTS / LINKS；真接口（开关 static）读 GET /ontology/graph（src/api/overview.ts），
// 后台没起来时回退 mock 并提示一次（fallback.ts）；其他接口错误保留 mock 并 console.warn。坐标 POS 仍由前端写死。
const graphData = ref<OverviewGraph>(mockGraph());
const nodeCount = computed(() => graphData.value.nodes.length);
const edgeCount = computed(() => graphData.value.edges.filter((l) => l.to && POS[l.from] && POS[l.to]).length);
let graphRef: Graph | null = null;
async function loadGraph() {
  if (!isOverviewReal()) return;
  try {
    graphData.value = await fetchGraph();
  } catch (e) {
    if (!fallBackIfDown(e)) console.warn('[api] /ontology/graph 加载失败，保留演示数据', e);
    graphData.value = mockGraph();
  }
  if (graphRef) { graphRef.clearCells(); addCells(graphRef); }
}
loadGraph();
watch(() => app.viewVersion, () => { loadGraph(); });
onBeforeUnmount(() => { graphRef = null; });
function draw(g: Graph) {
  graphRef = g;
  (g.options as unknown as { interacting: boolean }).interacting = false;
  addCells(g);
  g.on('node:click', ({ node }) => router.push({ path: '/build/ontology/object-types', query: { type: node.id as string } }));
  g.on('resize', () => { g.zoomToFit({ padding: 16, maxScale: 1.4 }); g.centerContent(); });
}
/** 画节点和边（首次 @ready 时画；真接口数据到了 / 切换版本后清空重画），画完适配视图 */
function addCells(g: Graph) {
  const { nodes, edges } = graphData.value;
  nodes.forEach((o) => {
    const [fx, fy] = POS[o.id] ?? [0.5, 0.5];
    const failed = failedByType.has(o.id);
    g.addNode({
      id: o.id, shape: 'rect', x: fx * VW - NW / 2, y: fy * VH - NH / 2, width: NW, height: NH, label: o.name,
      attrs: {
        body: { fill: GRAY.g1, stroke: failed ? STATUS.error : o.draft ? GRAY.g7 : GRAY.g6, strokeWidth: 1, strokeDasharray: o.draft ? '4 3' : '', rx: 4, ry: 4, cursor: 'pointer' },
        label: { fill: o.draft ? GRAY.g8 : GRAY.g10, fontSize: 12, cursor: 'pointer' },
      },
    });
  });
  const seen = new Set<string>();
  edges.forEach((l) => {
    if (!l.to || !POS[l.from] || !POS[l.to] || l.from === l.to) return;
    const k = [l.from, l.to].sort().join('|');
    if (seen.has(k)) return;
    seen.add(k);
    g.addEdge({
      source: l.from, target: l.to,
      attrs: { line: { stroke: GRAY.g7, strokeWidth: 1, strokeDasharray: l.status === '已发布' ? '' : '4 3', targetMarker: null } },
    });
  });
  if (g.getNodes().length) requestAnimationFrame(() => { g.zoomToFit({ padding: 16, maxScale: 1.4 }); g.centerContent(); });
}

/* ── 最近发布 ── */
const releases = computed(() => {
  const i = histVer.value ? RELEASES.findIndex((r) => r.ver === histVer.value) : 0;
  return RELEASES.slice(Math.max(0, i));
});
const crOf = (ver: string) => CHANGE_REQUESTS.find((c) => c.release === ver)?.id;
const openRelease = (ver: string) => {
  const cr = crOf(ver);
  router.push({ path: '/build/ontology/releases', query: cr ? { st: '已发布', cr } : { st: '已发布' } });
};

/* ── 发起变更 ── */
const changeOpen = ref(false);
const dv = useDraftView('object', () => '');
function start(opts: { join?: string; title?: string }) {
  if (opts.join) { router.push({ path: '/build/ontology/releases', query: { cr: opts.join } }); return; }
  // 总览没有具体对象：只建单（不切草稿视图），登记到变更与发布的列表里，再跳过去选中新单
  const id = dv.start({ title: opts.title, target: 'ontology' });
  const at = new Date();
  const pad = (n: number) => String(n).padStart(2, '0');
  CHANGE_REQUESTS.unshift({
    id, title: opts.title || '未命名变更', owner: app.user?.name || CURRENT_USER, reviewer: '待指定', status: '草稿', level: '修订', changes: 0,
    updatedAt: `${at.getFullYear()}-${pad(at.getMonth() + 1)}-${pad(at.getDate())} ${pad(at.getHours())}:${pad(at.getMinutes())}`, reason: '从本体总览发起，待补充',
  });
  message.success(`已创建变更单 ${id}，将随 ${NEXT_VERSION.value} 发布`);
  router.push({ path: '/build/ontology/releases', query: { cr: id } });
}
</script>

<template>
  <Teleport defer to="#page-extra">
    <a-space>
      <span class="t-12 c-3">当前版本 <span class="num">{{ CURRENT_VERSION }}</span> · 数据截至 <span class="num">{{ DATA_CUTOFF }}</span></span>
      <a-tooltip :title="reason"><a-button type="primary" :disabled="readonly" @click="changeOpen = true">发起变更</a-button></a-tooltip>
    </a-space>
  </Teleport>

  <div class="oo">
    <div class="oo-cards">
      <div v-for="c in cards" :key="c.key" class="panel oo-card" role="link" @click="router.push(c.path)">
        <div class="oo-card-top"><span class="t-14 c-2">{{ c.title }}<InfoTip :text="healthTip(c.health)" /></span><RightOutlined class="t-12 c-3" /></div>
        <div class="oo-count">
          <span class="t-kpi fw-600 num" :class="c.count ? 'c-1' : 'c-3'">{{ c.count }}</span><span class="t-14 c-2"> {{ c.unit }}</span>
          <span v-for="h in alerts(c.health)" :key="h.text" class="t-14 oo-alert" :class="toneCls(h)"><span class="num">{{ h.n }}</span> {{ h.text }}</span>
        </div>
      </div>
    </div>

    <div class="oo-main">
      <div class="panel oo-graph">
        <div class="panel-head">
          <span class="t-16 fw-600 c-1">对象关系</span><span class="oo-grow" />
          <span class="oo-legend t-12 c-3">
            <span>{{ nodeCount }} 个对象 · {{ edgeCount }} 条关系</span>
            <span><i class="lg lg-solid" />已发布</span>
            <span><i class="lg lg-dash" />草稿</span>
            <span><i class="lg lg-fail" />数据质量失败</span>
          </span>
          <InfoTip text="点击对象查看对象类型；虚线为草稿，红框为数据质量检查失败" />
        </div>
        <div class="oo-canvas"><GraphCanvas @ready="draw" /></div>
      </div>

      <div class="oo-side">
        <div class="panel oo-todo">
          <div class="panel-head">
            <span class="t-16 fw-600 c-1">待处理</span>
            <a-segmented v-if="!histVer" v-model:value="filterKind" size="small" :options="['全部', '变更', '数据质量']" />
          </div>
          <div v-if="histVer" class="oo-hist"><a-empty :image="Empty.PRESENTED_IMAGE_SIMPLE" description="查看历史版本时不显示待处理" /></div>
          <div v-show="!histVer" ref="listEl" class="oo-list">
            <div v-for="t in pageRows" :key="t.id" class="oo-row" role="link" @click="t.go()">
              <span class="t-12 c-2 oo-kind">{{ t.kind }}</span>
              <span class="t-14 c-1 oo-ell oo-row-main" :title="`${t.id}　${t.title}`"><span class="num c-2">{{ t.id }}</span>　{{ t.title }}</span>
              <span class="t-14 c-2 oo-ell oo-who" :title="t.who">{{ t.who }}</span>
              <span class="t-14 c-2 num oo-time" :title="t.time">{{ t.time.slice(5) }}</span>
              <span class="t-12 oo-st" :class="stCls(t.tone)"><i class="oo-dot" />{{ t.status }}</span>
            </div>
          </div>
          <AppPagination v-if="!histVer" v-model:page="page" variant="simple" :total="shown.length" :size="size" unit="项" class="oo-pager" />
        </div>

        <div class="panel oo-rel">
          <div class="panel-head">
            <span class="t-16 fw-600 c-1">最近发布</span>
            <a class="t-12 c-2 oo-more" @click="router.push({ path: '/build/ontology/releases', query: { st: '已发布' } })">全部发布记录</a>
          </div>
          <div v-for="r in releases" :key="r.ver" class="oo-rrow" role="link" @click="openRelease(r.ver)">
            <span class="t-14 num c-1 oo-ver">{{ r.ver }}</span>
            <span class="t-14 c-2 oo-ell oo-note" :title="r.note">{{ r.note }}</span>
            <span class="t-14 c-2 oo-ell oo-by" :title="r.by">{{ r.by }}</span>
            <span class="t-14 c-2 num oo-time" :title="r.publishedAt">{{ r.publishedAt.slice(5) }}</span>
          </div>
        </div>
      </div>
    </div>
  </div>

  <StartChangeModal v-model:open="changeOpen" target-name="本体（可在草稿中添加任意对象、关系、指标）" title-placeholder="例如：门店新增“业态”枚举值" @ok="start" />
</template>

<style scoped>
.oo { flex: 1; min-height: 0; display: flex; flex-direction: column; gap: var(--gap); }
.oo-cards { flex: none; display: grid; grid-template-columns: repeat(6, minmax(0, 1fr)); gap: var(--gap); }
.oo-card { padding: 12px 16px; gap: 2px; cursor: pointer; }
.oo-card:hover { border-color: var(--g7); }
.oo-card-top { display: flex; align-items: center; justify-content: space-between; }
.oo-count { display: flex; align-items: baseline; gap: 0; white-space: nowrap; overflow: hidden; }
.oo-alert { margin-left: 12px; }
.oo-grow { flex: 1; }
.oo-main { flex: 1; min-height: 0; display: flex; gap: var(--gap); }
.oo-graph { flex: 1; min-width: 0; }
.oo-hist { flex: 1; min-height: 0; display: flex; align-items: center; justify-content: center; }
.oo-canvas { flex: 1; min-height: 0; }
.oo-legend { margin-left: 16px; display: flex; gap: 16px; align-items: center; }
.oo-legend > span { display: inline-flex; align-items: center; gap: 6px; }
.lg { display: inline-block; width: 16px; height: 10px; border-radius: 2px; background: var(--g1); }
.lg-solid { border: 1px solid var(--g6); }
.lg-dash { border: 1px dashed var(--g7); }
.lg-fail { border: 1px solid var(--error); }
.oo-side { width: 560px; flex: none; display: flex; flex-direction: column; gap: var(--gap); min-height: 0; }
.oo-todo { flex: 1; }
.oo-list { flex: 1; min-height: 0; overflow: hidden; }
.oo-row { height: 44px; display: flex; align-items: center; gap: 12px; padding: 0 16px; border-bottom: 1px solid var(--g4); cursor: pointer; }
.oo-row:hover, .oo-rrow:hover { background: var(--g2); }
.oo-kind { width: 48px; flex: none; }
.oo-row-main { flex: 1; }
.oo-who { width: 88px; flex: none; }
.oo-by { width: 64px; flex: none; }
.oo-time { width: 84px; flex: none; white-space: nowrap; }
.oo-ell { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; min-width: 0; }
.oo-st { flex: none; display: inline-flex; align-items: center; gap: 6px; width: 104px; white-space: nowrap; }
.oo-dot { width: 6px; height: 6px; border-radius: 50%; background: currentColor; flex: none; }
.c-3 > .oo-dot { background: var(--g7); }
.oo-pager { padding: 8px 16px; border-top: var(--border); flex: none; }
.oo-rel { flex: none; }
.oo-more:hover { color: var(--g10); }
.oo-rrow { height: 40px; display: flex; align-items: center; gap: 12px; padding: 0 16px; border-bottom: 1px solid var(--g4); cursor: pointer; }
.oo-rrow:last-child { border-bottom: none; }
.oo-ver { width: 96px; flex: none; }
.oo-note { flex: 1; }
</style>
