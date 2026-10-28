<script setup lang="ts">
/**
 * 本体总览：六类资源的数量与健康度、待处理（变更单 + 数据质量失败）、对象关系缩略图、最近发布。
 * static 真接口模式读取总览聚合、待处理、关系图和发布记录；演示模式只经 api/overview.ts 的 demo* 入口读写演示数据。
 * 颜色：灰=正常（含草稿、已发布、已通过待发布），橙=审核中 / 结果未知，红=失败；数量为 0 一律灰。
 * 写操作只有“发起变更”，预览 / 历史版本期间只读（useReadonly）。
 */
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import { useRouter } from 'vue-router';
import { Empty, message } from 'ant-design-vue';
import { RightOutlined } from '@ant-design/icons-vue';
import type { Graph } from '@antv/x6';
import GraphCanvas from '@/components/GraphCanvas.vue';
import { graphEdgeRoutes } from '@/components/graphEdges';
import AppPagination from '@/components/AppPagination.vue';
import InfoTip from '@/components/InfoTip.vue';
import StartChangeModal from './parts/StartChangeModal.vue';
import BuildGuideDrawer from './parts/BuildGuideDrawer.vue';
import { createCR, staticContextKey, isStaticReal } from '@/api/static';
import { useReadonly } from '@/composables/useReadonly';
import { GRAY, STATUS } from '@/theme';
import { draftOf, useDraftView } from './parts/useDraftView';
import { useAppStore } from '@/stores/app';
import { RELEASES } from '@/api/releases';
import { backend, fallBackIfDown } from '@/api/fallback';
import { currentApiContext } from '@/api/http';
import {
  DEMO_DATA_AS_OF, demoAddChange, demoCards, demoCrOfRelease, demoFailedTypes, demoTodos,
  fetchGraph, fetchOverview, fetchOverviewTodos, fetchOverviewReleases, isOverviewReal, mockGraph,
  type OverviewGraph, type OverviewSummary, type OverviewTodo,
} from '@/api/overview';

const router = useRouter();
const { readonly, reason } = useReadonly();
const app = useAppStore();
// 当前 / 下一版本号取 app store（mock 模式是 mock 值，versioning 走真接口时是 /releases/current 的 current / next）
const CURRENT_VERSION = computed(() => app.ontologyVersion);
const NEXT_VERSION = computed(() => app.ontologyNextVersion);
/** 查看历史版本（?ver=）：不显示当前待处理，最近发布只列到所选版本；预览模式不算 */
const histVer = computed(() => (app.viewMode === 'history' ? app.viewVersion : null));
const live = computed(() => isOverviewReal() && !backend.down);
const summary = ref<OverviewSummary | null>(null);
const loadError = ref('');
const recent = ref<(typeof RELEASES)[number][]>([]);
const contextKey = () => { const { ontologyId, demoAs, previewAs, ver } = currentApiContext(); return JSON.stringify([ontologyId, demoAs, previewAs, ver]); };

/* ── 资源卡片 ── */
// 演示模式的卡片；真接口模式由 /ontology/overview 统一返回。
type Tone = 'normal' | 'attention' | 'failed';
interface Health { n: number; text: string; tone: Tone }
interface Card { key: string; title: string; count: number; unit: string; path: string; health: Health[] }

const failedByType = demoFailedTypes();
const paths: Record<string, string> = { obj: 'object-types', link: 'link-types', action: 'action-types', event: 'event-types', metric: 'metrics', dim: 'dimensions' };
// 演示卡片：维度与字典与“维度与字典”页同一份数据，历史版本只算当时已有的
const mockCards = computed<Card[]>(() => demoCards(histVer.value, (id) => draftOf('dimension', id)).map((c) => ({ ...c, path: `/build/ontology/${paths[c.key]}` })));
const cards = computed<Card[]>(() => live.value ? (summary.value?.cards ?? []).map((c) => ({ ...c, path: `/build/ontology/${paths[c.key]}` })) : mockCards.value);
const healthTip = (hs: Health[]) => hs.map((h) => `${h.n} ${h.text}`).join(' · ');
const alerts = (hs: Health[]) => hs.filter((h) => h.n > 0 && h.tone !== 'normal');
const toneCls = (h: Health) => (h.n === 0 || h.tone === 'normal' ? 'c-3' : h.tone === 'failed' ? 'tl-failed' : 'tl-attention');

/* ── 待处理：未发布的变更单 + 失败的数据质量检查 ── */
interface Todo { id: string; kind: '变更' | '数据质量'; title: string; who: string; status: string; tone: Tone; time: string; go: () => void }
const goTodo = (t: OverviewTodo) => () => router.push(t.kind === '数据质量' ? { path: '/build/data/quality', query: { result: '失败', check: t.id } } : { path: '/build/ontology/releases', query: { cr: t.id } });
const mockTodos = computed<Todo[]>(() => demoTodos().map((t) => ({ ...t, go: goTodo(t) })));
const filterKind = ref<'全部' | '变更' | '数据质量'>('全部');
const shown = computed(() => mockTodos.value.filter((t) => filterKind.value === '全部' || t.kind === filterKind.value));
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
const realTodos = ref<{ items: OverviewTodo[]; total: number }>({ items: [], total: 0 });
watch([contextKey, filterKind], () => {
  page.value = 1;
  realTodos.value = { items: [], total: 0 };
});
const todoError = ref('');
const pageRows = computed<Todo[]>(() => live.value ? realTodos.value.items.map((t) => ({ ...t, go: goTodo(t) })) : shown.value.slice((page.value - 1) * size.value, page.value * size.value));
const todoTotal = computed(() => live.value ? realTodos.value.total : shown.value.length);
let todoSeq = 0;
async function loadTodos() {
  const my = ++todoSeq;
  todoError.value = '';
  // Keep current rows during a size refresh so ResizeObserver cannot alternate the available height.
  if (!live.value || histVer.value) return;
  try {
    const r = await fetchOverviewTodos(filterKind.value, page.value, size.value);
    if (my !== todoSeq) return;
    if (r.page !== page.value) { page.value = r.page; return; }
    realTodos.value = r;
  } catch (e) {
    // 业务错误（如 403）不能继续显示上一次的待处理行
    if (my === todoSeq && !fallBackIfDown(e)) { todoError.value = `待处理加载失败：${String(e)}`; realTodos.value = { items: [], total: 0 }; }
  }
}
watch([contextKey, live, filterKind, page, size], loadTodos, { immediate: true });
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
// 节点 / 边：演示模式读 mockGraph()；真接口（开关 static）读 GET /ontology/graph（src/api/overview.ts），
// 图谱独立加载，不等待统计与发布列表；坐标 POS 仍由前端定义。
const graphData = ref<OverviewGraph>(isOverviewReal() ? { nodes: [], edges: [] } : mockGraph());
const graphLoaded = ref(false);
const nodeCount = computed(() => graphData.value.nodes.length);
const edgeCount = computed(() => {
  const ids = new Set(graphData.value.nodes.map((n) => n.id));
  return graphData.value.edges.filter((l) => ids.has(l.from) && ids.has(l.to)).length;
});
let graphRef: Graph | null = null;
let overviewSeq = 0;
async function loadGraph() {
  const my = ++overviewSeq;
  const key = contextKey();
  summary.value = null; recent.value = []; loadError.value = ''; graphLoaded.value = false;
  graphData.value = live.value ? { nodes: [], edges: [] } : mockGraph();
  if (graphRef) graphRef.clearCells();
  if (!live.value) { if (graphRef) addCells(graphRef); return; }
  const valid = () => my === overviewSeq && key === contextKey();
  const failed = (e: unknown) => {
    if (valid() && !fallBackIfDown(e)) loadError.value = `本体总览加载失败：${String(e)}`;
  };
  await Promise.allSettled([
    fetchGraph().then((g) => {
      if (!valid()) return;
      graphData.value = g; graphLoaded.value = true;
      if (graphRef) { graphRef.clearCells(); addCells(graphRef); }
    }).catch(failed),
    fetchOverview().then((s) => { if (valid()) summary.value = s; }).catch(failed),
    fetchOverviewReleases().then((r) => { if (valid()) recent.value = r.items; }).catch(failed),
  ]);
}
watch([contextKey, live], loadGraph, { immediate: true });
onBeforeUnmount(() => { ++overviewSeq; graphRef = null; });
function draw(g: Graph) {
  graphRef = g;
  (g.options as unknown as { interacting: boolean }).interacting = false;
  addCells(g);
  g.on('node:click', ({ node }) => router.push({ path: '/build/ontology/object-types', query: { type: node.id as string } }));
  g.on('edge:click', ({ edge }) => router.push({ path: '/build/ontology/link-types', query: { link: String(edge.id) } }));
  g.on('resize', () => { g.zoomToFit({ padding: 16, maxScale: 1.4 }); g.centerContent(); });
}
/** 画节点和边（首次 @ready 时画；真接口数据到了 / 切换版本后清空重画），画完适配视图 */
function addCells(g: Graph) {
  const { nodes, edges } = graphData.value;
  const positions = new Map<string, { x: number; y: number }>();
  let extra = 0;
  for (const node of nodes) {
    let position = POS[node.id];
    if (!position) {
      position = [1.12 + Math.floor(extra / 5) * 0.2, 0.1 + (extra % 5) * 0.18];
      extra++;
    }
    const [x, y] = position;
    positions.set(node.id, { x: x * VW, y: y * VH });
  }
  g.batchUpdate('overview', () => {
    nodes.forEach((o) => {
      const p = positions.get(o.id)!;
      const failed = !live.value && failedByType.has(o.id);
      g.addNode({
        id: o.id, shape: 'rect', x: p.x - NW / 2, y: p.y - NH / 2, width: NW, height: NH, label: o.name,
        attrs: {
          body: { fill: GRAY.g1, stroke: failed ? STATUS.error : o.draft ? GRAY.g7 : GRAY.g6, strokeWidth: 1, strokeDasharray: o.draft ? '4 3' : '', rx: 4, ry: 4, cursor: 'pointer' },
          label: { fill: o.draft ? GRAY.g8 : GRAY.g10, fontSize: 12, cursor: 'pointer' },
        },
      });
    });
    const routes = graphEdgeRoutes(nodes.map((n) => ({ id: n.id, x: positions.get(n.id)!.x - NW / 2, y: positions.get(n.id)!.y - NH / 2, width: NW, height: NH })),
      edges.map((e) => ({ ...e, text: e.name || e.id })));
    edges.forEach((l) => {
      const route = routes.get(l.id);
      if (!route) return;
      const label = l.name || l.id;
      g.addEdge({
        id: l.id,
        ...route, zIndex: 0,
        attrs: { line: { stroke: GRAY.g7, strokeWidth: 1, strokeDasharray: l.status === '已发布' ? '' : '4 3', targetMarker: { name: 'block', width: 7, height: 5 }, cursor: 'pointer' } },
        labels: [{ position: route.labelPosition, attrs: { label: { text: label, fontSize: 12, fill: GRAY.g9, cursor: 'pointer' }, body: { fill: GRAY.g2, stroke: 'none', rx: 2, ry: 2, cursor: 'pointer' } } }],
      });
    });
  });
  if (g.getNodes().length) requestAnimationFrame(() => {
    if (graphRef !== g) return;
    g.zoomToFit({ padding: 16, maxScale: 1.4 }); g.centerContent();
  });
}

/* ── 最近发布 ── */
const releases = computed(() => {
  const list = live.value ? recent.value : RELEASES;
  const i = histVer.value ? list.findIndex((r) => r.ver === histVer.value) : 0;
  return histVer.value && i < 0 ? [] : list.slice(Math.max(0, i));
});
const crOf = (ver: string) => (live.value ? undefined : demoCrOfRelease(ver));
const openRelease = (ver: string) => {
  const cr = crOf(ver);
  router.push({ path: '/build/ontology/releases', query: cr ? { st: '已发布', cr } : { st: '已发布' } });
};

/* ── 发起变更 ── */
const changeOpen = ref(false);
const guideOpen = ref(false);
const dv = useDraftView('object', () => '');
async function start(opts: { join?: string; title?: string; desc?: string }) {
  if (readonly.value) return;
  if (isStaticReal()) {
    const key = staticContextKey();
    try {
      const id = opts.join ?? (await createCR({ title: opts.title || '未命名变更', reason: opts.desc })).id;
      if (key !== staticContextKey()) return;
      await router.push({ path: '/build/ontology/releases', query: { cr: id } });
    } catch (e) { message.error(e instanceof Error ? e.message : String(e)); }
    return;
  }
  if (opts.join) { router.push({ path: '/build/ontology/releases', query: { cr: opts.join } }); return; }
  // 总览没有具体对象：只建单（不切草稿视图），登记到变更与发布的列表里，再跳过去选中新单
  const id = dv.start({ title: opts.title, target: 'ontology' });
  demoAddChange(id, opts.title || '未命名变更', app.user?.name);
  message.success(`已创建变更单 ${id}，将随 ${NEXT_VERSION.value} 发布`);
  router.push({ path: '/build/ontology/releases', query: { cr: id } });
}
</script>

<template>
  <Teleport defer to="#page-extra">
    <a-space>
      <span class="t-12 c-3">当前版本 <span class="num">{{ CURRENT_VERSION }}</span> · {{ live ? '统计时间' : '数据截至' }} <span class="num">{{ live ? summary?.dataAsOf ?? '—' : DEMO_DATA_AS_OF }}</span></span>
      <a-button @click="guideOpen = true">构建引导</a-button>
      <a-tooltip :title="reason"><a-button type="primary" :disabled="readonly" @click="changeOpen = true">发起变更</a-button></a-tooltip>
    </a-space>
  </Teleport>

  <div class="oo">
    <a-alert v-if="loadError || todoError" type="error" :message="loadError || todoError" show-icon>
      <template #action><a-button size="small" @click="loadGraph(); loadTodos()">重试</a-button></template>
    </a-alert>
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
            <span v-if="!live"><i class="lg lg-fail" />数据质量失败</span>
          </span>
          <InfoTip :text="live ? '点击对象或关系查看定义' : '点击对象查看对象类型；虚线为草稿，红框为数据质量检查失败'" />
        </div>
        <div class="oo-canvas"><GraphCanvas @ready="draw" /><div v-if="live && graphLoaded && !nodeCount" class="oo-empty"><a-empty :image="Empty.PRESENTED_IMAGE_SIMPLE" description="尚未定义对象类型" /></div></div>
      </div>

      <div class="oo-side">
        <div class="panel oo-todo">
          <div class="panel-head">
            <span class="t-16 fw-600 c-1">待处理</span>
            <a-segmented v-if="!histVer" v-model:value="filterKind" size="small" :options="['全部', '变更', '数据质量']" />
          </div>
          <div v-if="histVer" class="oo-hist"><a-empty :image="Empty.PRESENTED_IMAGE_SIMPLE" description="查看历史版本时不显示待处理" /></div>
          <div v-show="!histVer" ref="listEl" class="oo-list">
            <a-empty v-if="live && !pageRows.length" :image="Empty.PRESENTED_IMAGE_SIMPLE" :description="filterKind === '数据质量' ? '暂无待处理的质量检查' : '暂无待处理事项'" />
            <div v-for="t in pageRows" :key="t.id" class="oo-row" role="link" @click="t.go()">
              <span class="t-12 c-2 oo-kind">{{ t.kind }}</span>
              <span class="t-14 c-1 oo-ell oo-row-main" :title="`${t.id}　${t.title}`"><span class="num c-2">{{ t.id }}</span>　{{ t.title }}</span>
              <span class="t-14 c-2 oo-ell oo-who" :title="t.who">{{ t.who }}</span>
              <span class="t-14 c-2 num oo-time" :title="t.time">{{ t.time.slice(5) }}</span>
              <span class="t-12 oo-st" :class="stCls(t.tone)"><i class="oo-dot" />{{ t.status }}</span>
            </div>
          </div>
          <AppPagination v-if="!histVer" v-model:page="page" variant="simple" :total="todoTotal" :size="size" :empty="false" unit="项" class="oo-pager" />
        </div>

        <div class="panel oo-rel">
          <div class="panel-head">
            <span class="t-16 fw-600 c-1">最近发布</span>
            <a class="t-12 c-2 oo-more" @click="router.push({ path: '/build/ontology/releases', query: { st: '已发布' } })">全部发布记录</a>
          </div>
          <div class="oo-release-list">
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
  </div>

  <BuildGuideDrawer v-model:open="guideOpen" />
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
.oo-canvas { flex: 1; min-height: 0; position: relative; }
.oo-empty { position: absolute; inset: 0; display: grid; place-items: center; pointer-events: none; }
.oo-legend { margin-left: 16px; display: flex; gap: 16px; align-items: center; }
.oo-legend > span { display: inline-flex; align-items: center; gap: 6px; }
.lg { display: inline-block; width: 16px; height: 10px; border-radius: 2px; background: var(--g1); }
.lg-solid { border: 1px solid var(--g6); }
.lg-dash { border: 1px dashed var(--g7); }
.lg-fail { border: 1px solid var(--error); }
.oo-side { width: 560px; flex: none; display: flex; flex-direction: column; gap: var(--gap); min-height: 0; }
.oo-todo { flex: 1 1 0; }
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
.oo-rel { flex: 1 1 0; }
.oo-release-list { flex: 1; min-height: 0; overflow-y: auto; }
.oo-more:hover { color: var(--g10); }
.oo-rrow { height: 40px; display: flex; align-items: center; gap: 12px; padding: 0 16px; border-bottom: 1px solid var(--g4); cursor: pointer; }
.oo-rrow:last-child { border-bottom: none; }
.oo-ver { width: 96px; flex: none; }
.oo-note { flex: 1; }
</style>
