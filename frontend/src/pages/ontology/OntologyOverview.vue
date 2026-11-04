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
import { fitGraphContent, relationshipRoutes } from '@/components/graphEdges';
import { layoutGraph, relationshipColor } from '@/components/graphLayout';
import AppPagination from '@/components/AppPagination.vue';
import InfoTip from '@/components/InfoTip.vue';
import StartChangeModal from './parts/StartChangeModal.vue';
import BuildGuideDrawer from './parts/BuildGuideDrawer.vue';
import { createCR, staticContextKey, isStaticReal } from '@/api/static';
import { useReadonly } from '@/composables/useReadonly';
import { GRAY, STATUS, chrome } from '@/theme';
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
// 演示和真接口共用关系探索视图；节点位置由有限力导向布局生成。
const graphData = ref<OverviewGraph>(isOverviewReal() ? { nodes: [], edges: [] } : mockGraph());
const selection = ref<{ kind: 'node' | 'edge'; id: string } | null>(null);
const graphGroups = computed(() => [...new Set(graphData.value.nodes.map((node) => node.group).filter(Boolean))].sort());
const selectedName = computed(() => selection.value?.kind === 'node'
  ? graphData.value.nodes.find((node) => node.id === selection.value?.id)?.name
  : graphData.value.edges.find((edge) => edge.id === selection.value?.id)?.name);
const selectedDefinition = computed(() => !selection.value ? null : selection.value.kind === 'node'
  ? { path: '/build/ontology/object-types', query: { type: selection.value.id } }
  : { path: '/build/ontology/link-types', query: { link: selection.value.id } });
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
  selection.value = null;
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
      if (graphRef) addCells(graphRef);
    }).catch(failed),
    fetchOverview().then((s) => { if (valid()) summary.value = s; }).catch(failed),
    fetchOverviewReleases().then((r) => { if (valid()) recent.value = r.items; }).catch(failed),
  ]);
}
watch([contextKey, live], loadGraph, { immediate: true });
watch(chrome, () => { if (graphRef) addCells(graphRef); });
onBeforeUnmount(() => { ++overviewSeq; graphRef = null; });
function draw(g: Graph) {
  graphRef = g;
  g.options.interacting = { nodeMovable: true, edgeMovable: false, edgeLabelMovable: false, magnetConnectable: false };
  addCells(g);
  g.on('node:click', ({ node }) => { selection.value = { kind: 'node', id: String(node.id) }; focusGraph(); });
  g.on('edge:click', ({ edge }) => { selection.value = { kind: 'edge', id: String(edge.id) }; focusGraph(); });
  g.on('blank:click', clearGraphSelection);
  g.on('node:mouseenter', ({ node }) => { if (!selection.value) focusGraph(String(node.id)); });
  g.on('node:mouseleave', () => focusGraph());
  g.on('node:change:position', () => {
    const routes = relationshipRoutes(g.getNodes().map((node) => ({ id: String(node.id), ...node.getBBox() })), graphData.value.edges);
    g.batchUpdate('relationship-drag', () => {
      for (const edge of g.getEdges()) {
        const vertices = routes.get(String(edge.id))?.vertices ?? [];
        if (JSON.stringify(vertices) !== JSON.stringify(edge.getVertices())) edge.setVertices(vertices);
      }
    });
  });
  g.on('resize', () => fitGraphContent(g, 32, 1.4));
}
function clearGraphSelection() { selection.value = null; focusGraph(); }
function focusGraph(hoverId?: string) {
  const g = graphRef;
  if (!g) return;
  const selected = selection.value;
  const id = hoverId ?? (selected?.kind === 'node' ? selected.id : '');
  const relatedEdges = graphData.value.edges.filter((edge) => id ? edge.from === id || edge.to === id : edge.id === selected?.id);
  const relatedIds = new Set([id, ...relatedEdges.flatMap((edge) => [edge.from, edge.to])]);
  const focused = !!id || !!selected;
  g.batchUpdate('relationship-focus', () => {
    for (const node of g.getNodes()) {
      const active = !focused || relatedIds.has(String(node.id));
      node.attr('body/opacity', active ? 1 : 0.2); node.attr('label/opacity', active ? 1 : 0.25);
      node.attr('body/strokeWidth', String(node.id) === id ? 3 : 1.5);
    }
    for (const edge of g.getEdges()) {
      const active = !focused || relatedEdges.some((relation) => relation.id === edge.id);
      edge.attr('line/opacity', active ? 1 : 0.12);
      edge.attr('line/strokeWidth', focused && active ? 1.8 : 1);
      edge.attr('line/stroke', focused && active ? GRAY.g10 : GRAY.g7);
      edge.attr('label/opacity', active ? 1 : 0.18); edge.attr('body/opacity', active ? 1 : 0.18);
    }
  });
}
/** 画节点和边（首次 @ready 时画；真接口数据到了 / 切换版本后清空重画），画完适配视图 */
function addCells(g: Graph) {
  const { nodes, edges } = graphData.value;
  const positions = layoutGraph(nodes, edges);
  const graphNodes = nodes.map((o) => {
      const p = positions.get(o.id)!;
      const failed = !live.value && failedByType.has(o.id);
      return {
        id: o.id, shape: 'circle', x: p.x, y: p.y, width: p.width, height: p.height, zIndex: 1, label: o.name,
        attrs: {
          body: { fill: relationshipColor(o.group || o.api || o.id, chrome.value === 'navy', graphGroups.value), stroke: failed ? STATUS.error : GRAY.g7, strokeWidth: 1.5, strokeDasharray: o.draft ? '4 3' : '', cursor: 'grab' },
          label: { fill: GRAY.g10, fontSize: 14, fontWeight: 600, title: o.name, textWrap: { width: 58, height: 54, ellipsis: true, breakWord: true }, cursor: 'grab' },
        },
      };
  });
    const routes = relationshipRoutes(nodes.map((n) => ({ id: n.id, ...positions.get(n.id)! })),
      edges.map((e) => ({ ...e, text: e.name || e.id })));
  const graphEdges = edges.flatMap((l) => {
      const route = routes.get(l.id);
      if (!route) return [];
      const label = l.name || l.id;
      return [{
        id: l.id,
        ...route, zIndex: 0, relationship: true,
        attrs: { line: { title: label, stroke: GRAY.g7, strokeWidth: 1, strokeDasharray: l.status === '已发布' ? '' : '4 3', targetMarker: { name: 'classic', width: 7, height: 6 }, cursor: 'pointer' }, wrap: { strokeWidth: 14, cursor: 'pointer' } },
        labels: [{ position: route.labelPosition, attrs: { label: { text: label, title: label, fontSize: 14, fill: GRAY.g9, cursor: 'pointer' }, body: { fill: GRAY.g2, stroke: GRAY.g2, strokeWidth: 6, rx: 2, ry: 2, cursor: 'pointer' } } }],
      }];
  });
  // Reset cancels pending same-ID jobs before rendering the replacement views.
  g.fromJSON({ nodes: graphNodes, edges: graphEdges });
  focusGraph();
  if (g.getNodes().length) requestAnimationFrame(() => {
    if (graphRef !== g) return;
    fitGraphContent(g, 32, 1.4);
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
          <InfoTip text="拖动对象调整位置；点击对象聚焦关联，点击空白恢复全部。选中后可查看定义；虚线为草稿。" />
        </div>
        <div class="oo-canvas"><GraphCanvas relationship @ready="draw" /><div v-if="live && graphLoaded && !nodeCount" class="oo-empty"><a-empty :image="Empty.PRESENTED_IMAGE_SIMPLE" description="尚未定义对象类型" /></div></div>
        <div class="oo-graph-footer">
          <template v-if="selection">
            <span class="t-14 fw-600 c-1 oo-ell">{{ selectedName || selection.id }}</span>
            <span class="t-12 c-3">{{ selection.kind === 'node' ? '关联对象' : '关系' }}</span>
            <span class="oo-grow" />
            <a-button size="small" @click="clearGraphSelection">全部</a-button>
            <a-button v-if="selectedDefinition" size="small" type="primary" @click="router.push(selectedDefinition)">查看定义</a-button>
          </template>
          <template v-else>
            <span v-for="group in graphGroups" :key="group" class="oo-group t-12 c-2"><i :style="{ background: relationshipColor(group, chrome === 'navy', graphGroups) }" />{{ group }}</span>
          </template>
        </div>
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
.oo { flex: 1; min-height: 0; min-width: 0; display: flex; flex-direction: column; gap: var(--gap); }
.oo-cards { flex: none; display: grid; grid-template-columns: repeat(6, minmax(0, 1fr)); gap: var(--gap); }
.oo-card { padding: 12px 16px; gap: 2px; cursor: pointer; min-width: 0; }
.oo-card:hover { border-color: var(--g7); }
.oo-card-top { display: flex; align-items: center; justify-content: space-between; gap: 8px; min-width: 0; }
.oo-card-top > span { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.oo-count { display: flex; align-items: baseline; flex-wrap: wrap; column-gap: 8px; min-width: 0; }
.oo-alert { margin-left: 4px; }
.oo-grow { flex: 1; min-width: 8px; }
.oo-main { flex: 1; min-height: 0; min-width: 0; display: flex; gap: var(--gap); }
.oo-graph { flex: 1.4 1 0; min-width: 0; overflow: hidden; }
.oo-graph .panel-head { height: auto; min-height: 44px; min-width: 0; flex-wrap: wrap; gap: 4px 12px; overflow: hidden; }
.oo-hist { flex: 1; min-height: 0; display: flex; align-items: center; justify-content: center; }
.oo-canvas { flex: 1; min-height: 0; position: relative; }
.oo-graph-footer { flex: none; min-height: 48px; padding: 8px 16px; display: flex; align-items: center; flex-wrap: wrap; gap: 8px 16px; border-top: var(--border); }
.oo-group { display: inline-flex; gap: 6px; align-items: center; }
.oo-group > i { width: 10px; height: 10px; border: 1px solid var(--g7); border-radius: 50%; }
.oo-empty { position: absolute; inset: 0; display: grid; place-items: center; pointer-events: none; }
.oo-legend { margin-left: 0; min-width: 0; display: flex; flex-wrap: wrap; gap: 8px 16px; align-items: center; }
.oo-legend > span { display: inline-flex; align-items: center; gap: 6px; min-width: 0; }
.lg { display: inline-block; width: 10px; height: 10px; border-radius: 50%; background: var(--g1); }
.lg-solid { border: 1px solid var(--g6); }
.lg-dash { border: 1px dashed var(--g7); }
.lg-fail { border: 1px solid var(--error); }
.oo-side { flex: 1 1 0; width: auto; max-width: 560px; min-width: 0; display: flex; flex-direction: column; gap: var(--gap); min-height: 0; }
.oo-todo, .oo-rel { min-width: 0; overflow: hidden; }
.oo-side .panel-head { min-width: 0; gap: 8px; height: auto; min-height: 44px; }
.oo-todo { flex: 1 1 0; }
.oo-list { flex: 1; min-height: 0; overflow: hidden; }
.oo-row, .oo-rrow { display: grid; align-items: center; column-gap: 8px; padding: 0 12px; border-bottom: 1px solid var(--g4); cursor: pointer; min-width: 0; }
.oo-row { height: 44px; grid-template-columns: 3.2em minmax(0, 1.6fr) minmax(0, 0.9fr) 5.4em minmax(4.2em, 6.4em); }
.oo-row:hover, .oo-rrow:hover { background: var(--g2); }
.oo-kind, .oo-who, .oo-by, .oo-time, .oo-ver, .oo-st, .oo-row-main, .oo-note { width: auto; min-width: 0; }
.oo-ell { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; min-width: 0; }
.oo-st { display: inline-flex; align-items: center; gap: 6px; white-space: nowrap; }
.oo-dot { width: 6px; height: 6px; border-radius: 50%; background: currentColor; flex: none; }
.c-3 > .oo-dot { background: var(--g7); }
.oo-pager { padding: 8px 16px; border-top: var(--border); flex: none; }
.oo-rel { flex: 1 1 0; }
.oo-release-list { flex: 1; min-height: 0; overflow: auto; }
.oo-more:hover { color: var(--g10); }
.oo-rrow { height: 40px; grid-template-columns: minmax(4.6em, 7.2em) minmax(0, 1fr) minmax(0, 4.2em) 5.4em; }
.oo-rrow:last-child { border-bottom: none; }
@media (max-width: 1400px) {
  .oo-cards { grid-template-columns: repeat(3, minmax(0, 1fr)); }
}
</style>
