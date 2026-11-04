<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue';
import type { Graph } from '@antv/x6';
import GraphCanvas from '@/components/GraphCanvas.vue';
import { relationshipRoutes } from '@/components/graphEdges';
import { layoutGraph, relationshipColor } from '@/components/graphLayout';
import { currentApiContext } from '@/api/http';
import { fetchLineage, type LineageGraph } from '@/api/lineage';
import { chrome, GRAY } from '@/theme';

const kinds: Record<string, string> = { source: '来源字段', object: '对象', property: '属性', metric: '指标', rule: '规则', action: '动作', parameter: '参数', endpoint: '外部接口', external: '外部系统' };
const edgeNames: Record<string, string> = { maps: '来源映射', contains: '定义归属', reads: '读取引用', aggregates: '对象聚合', writes: '写入属性', triggers: '触发动作', dispatches: '外部调用', belongs: '接口归属', request: '请求字段映射' };
const data = ref<LineageGraph>(), error = ref(''), loading = ref(false);
const focus = ref(''), selected = ref(''), selectedEdge = ref(''), depth = ref(3);
let graph: Graph | undefined, controller: AbortController | undefined, generation = 0, mounted = true;
const context = () => JSON.stringify(currentApiContext());
async function load() {
  const seq = ++generation, key = context();
  controller?.abort(); controller = new AbortController();
  data.value = undefined; focus.value = ''; selected.value = ''; selectedEdge.value = ''; error.value = ''; loading.value = true;
  try {
    const result = await fetchLineage(controller.signal);
    if (!mounted || seq !== generation || key !== context()) return;
    data.value = result;
    focus.value = result.nodes.find(n => n.kind === 'object')?.id ?? result.nodes[0]?.id ?? '';
  } catch (e) {
    if (mounted && seq === generation && key === context() && (e as Error).name !== 'AbortError') error.value = e instanceof Error ? e.message : '血缘加载失败';
  } finally { if (mounted && seq === generation && key === context()) loading.value = false; }
}
watch(context, load, { immediate: true, flush: 'sync' });
onBeforeUnmount(() => { mounted = false; generation++; controller?.abort(); graph = undefined; });
const options = computed(() => data.value?.nodes.map(n => ({ value: n.id, label: `${kinds[n.kind] ?? n.kind} · ${n.name}` })) ?? []);
const groups = computed(() => [...new Set(data.value?.nodes.map(n => kinds[n.kind] ?? n.kind) ?? [])].sort());
const visible = computed(() => {
  if (!data.value || !focus.value) return { nodes: [], edges: [] };
  const ids = new Set([focus.value]);
  for (let level = 0; level < depth.value; level++) {
    const next = new Set(ids);
    for (const edge of data.value.edges) if (ids.has(edge.source) || ids.has(edge.target)) { next.add(edge.source); next.add(edge.target); }
    if (next.size === ids.size) break;
    next.forEach(id => ids.add(id));
  }
  return { nodes: data.value.nodes.filter(n => ids.has(n.id)), edges: data.value.edges.filter(e => ids.has(e.source) && ids.has(e.target)) };
});
const current = computed(() => data.value?.nodes.find(n => n.id === selected.value));
const currentEdge = computed(() => data.value?.edges.find(e => e.id === selectedEdge.value));
const relationships = computed(() => visible.value.edges.map(e => ({ id: e.id, from: e.source, to: e.target, name: edgeNames[e.kind] ?? e.kind })));
const related = computed(() => data.value?.edges.filter(e => e.source === selected.value || e.target === selected.value) ?? []);
const unresolved = computed(() => data.value?.unresolved.filter(i => i.node === selected.value) ?? []);
const nameOf = (id: string) => data.value?.nodes.find(n => n.id === id)?.name ?? '';
function choose(id: string) { focus.value = id; selected.value = id; selectedEdge.value = ''; }
function fit() {
  // Current cell geometry avoids fitting the previous graph's asynchronously rendered DOM.
  if (graph?.getNodes().length) graph.zoomToFit({ useCellGeometry: true, padding: 32, minScale: 0.86, maxScale: 1 });
}
function highlight(hoverId?: string) {
  if (!graph) return;
  const id = hoverId ?? selected.value;
  const edges = visible.value.edges.filter(e => id ? e.source === id || e.target === id : e.id === selectedEdge.value);
  const ids = new Set([id, ...edges.flatMap(e => [e.source, e.target])]);
  const focused = !!id || !!selectedEdge.value;
  graph.batchUpdate('relationship-focus', () => {
    for (const node of graph!.getNodes()) {
      const active = !focused || ids.has(node.id);
      node.attr('body/opacity', active ? 1 : 0.2); node.attr('label/opacity', active ? 1 : 0.25);
      node.attr('body/strokeWidth', node.id === id ? 3 : 1.5);
    }
    for (const edge of graph!.getEdges()) {
      const active = !focused || edges.some(e => e.id === edge.id);
      edge.attr('line/opacity', active ? 1 : 0.12);
      edge.attr('line/strokeWidth', focused && active ? 1.8 : 1);
      edge.attr('line/stroke', focused && active ? GRAY.g10 : GRAY.g7);
      edge.attr('label/opacity', active ? 1 : 0.18); edge.attr('body/opacity', active ? 1 : 0.18);
    }
  });
}
function draw() {
  if (!graph) return;
  const g = graph, positions = layoutGraph(visible.value.nodes, relationships.value);
  const routes = relationshipRoutes(visible.value.nodes.map(n => ({ id: n.id, ...positions.get(n.id)! })), relationships.value);
  const nodes = visible.value.nodes.map(n => {
      const sourcePath = n.code.split('.');
      const label = n.kind === 'source' ? `${sourcePath[sourcePath.length - 1]}\n${sourcePath[sourcePath.length - 2] ?? ''}` : n.name;
      return { id: n.id, shape: 'circle', ...positions.get(n.id)!, zIndex: 1, label,
        attrs: {
          body: { fill: relationshipColor(kinds[n.kind] ?? n.kind, chrome.value === 'navy', groups.value), stroke: GRAY.g7, strokeWidth: 1.5, cursor: 'grab' },
          label: { fill: GRAY.g10, fontSize: 14, fontWeight: 600, title: n.name, textWrap: { width: 58, height: 54, ellipsis: true, breakWord: true }, cursor: 'grab' },
        } };
  });
  const edges = relationships.value.flatMap(e => {
      const route = routes.get(e.id);
      if (!route) return [];
      return [{ id: e.id, ...route, zIndex: 0, relationship: true,
        attrs: { line: { title: e.name, stroke: GRAY.g7, strokeWidth: 1, targetMarker: { name: 'classic', width: 7, height: 6 }, cursor: 'pointer' }, wrap: { strokeWidth: 14, cursor: 'pointer' } },
        labels: [{ position: route.labelPosition, attrs: { label: { text: e.name, title: e.name, fontSize: 14, fill: GRAY.g9, cursor: 'pointer' }, body: { fill: GRAY.g2, stroke: GRAY.g2, strokeWidth: 6, rx: 2, ry: 2, cursor: 'pointer' } } }],
      }];
  });
  // Reset cancels pending same-ID jobs and removes old views before rendering the replacement.
  g.fromJSON({ nodes, edges });
  highlight();
  fit();
}
function ready(value: Graph) {
  graph = value;
  graph.options.interacting = { nodeMovable: true, edgeMovable: false, edgeLabelMovable: false, magnetConnectable: false };
  graph.on('node:click', ({ node }) => { selected.value = node.id; selectedEdge.value = ''; highlight(); });
  graph.on('edge:click', ({ edge }) => { selected.value = ''; selectedEdge.value = edge.id; highlight(); });
  graph.on('blank:click', () => { selected.value = ''; selectedEdge.value = ''; highlight(); });
  graph.on('node:mouseenter', ({ node }) => { if (!selected.value && !selectedEdge.value) highlight(node.id); });
  graph.on('node:mouseleave', () => highlight());
  graph.on('node:change:position', () => {
    if (!graph) return;
    const routes = relationshipRoutes(graph.getNodes().map(node => ({ id: node.id, ...node.getBBox() })), relationships.value);
    graph.batchUpdate('relationship-drag', () => {
      for (const edge of graph!.getEdges()) {
        const vertices = routes.get(edge.id)?.vertices ?? [];
        if (JSON.stringify(vertices) !== JSON.stringify(edge.getVertices())) edge.setVertices(vertices);
      }
    });
  });
  graph.on('resize', fit);
  draw();
}
watch([visible, chrome], draw);
</script>

<template>
  <section class="lineage">
    <a-alert v-if="error" :message="error" type="error" show-icon />
    <div class="lineage-tools">
      <label for="lineage-focus" class="t-14">查看节点</label>
      <a-select id="lineage-focus" :value="focus" show-search option-filter-prop="label" :options="options" :loading="loading" class="lineage-select" @change="choose(String($event))" />
      <label for="lineage-depth" class="t-14">关联深度</label>
      <a-select id="lineage-depth" v-model:value="depth" :options="[1,2,3,6].map(value => ({ value, label: `${value} 层` }))" />
      <span class="t-12 c-3">{{ visible.nodes.length }} 个节点 · {{ visible.edges.length }} 条依赖 · 全本体未解析 {{ data?.unresolved.length ?? 0 }} 处</span>
      <a-button :loading="loading" @click="load">重新读取</a-button>
    </div>
    <div class="lineage-body" :aria-busy="loading">
      <div class="lineage-graph">
        <div class="lineage-canvas"><GraphCanvas relationship @ready="ready"><template #empty><span v-if="!visible.nodes.length">{{ loading ? '正在读取可见定义' : '当前没有可展示的血缘' }}</span></template></GraphCanvas></div>
        <div class="lineage-legend"><span v-for="group in groups" :key="group" class="t-12 c-2"><i :style="{ background: relationshipColor(group, chrome === 'navy', groups) }" />{{ group }}</span></div>
      </div>
      <aside class="lineage-detail">
        <template v-if="currentEdge">
          <div class="t-12 c-3">依赖关系</div><h2 class="t-16 fw-600">{{ edgeNames[currentEdge.kind] ?? currentEdge.kind }}</h2>
          <a-button type="link" class="lineage-link" @click="choose(currentEdge.source)">{{ nameOf(currentEdge.source) }}</a-button>
          <div class="t-12 c-3 lineage-direction">↓</div>
          <a-button type="link" class="lineage-link" @click="choose(currentEdge.target)">{{ nameOf(currentEdge.target) }}</a-button>
          <h3 class="t-14 fw-600">依据</h3><div class="t-12 c-3 num">{{ currentEdge.evidence }}</div>
        </template>
        <template v-else-if="current">
          <div class="t-12 c-3">{{ kinds[current.kind] ?? current.kind }}</div><h2 class="t-16 fw-600">{{ current.name }}</h2>
          <div class="t-12 c-3 num">{{ current.code }}<span v-if="current.version"> · {{ current.version }}</span></div>
          <h3 class="t-14 fw-600">上下游与依据</h3>
          <div v-for="edge in related" :key="edge.id" class="lineage-reference">
            <div class="t-12 c-3">{{ edgeNames[edge.kind] ?? edge.kind }} · {{ edge.target === selected ? '上游' : '下游' }}</div>
            <a-button type="link" class="lineage-link" @click="choose(edge.source === selected ? edge.target : edge.source)">{{ nameOf(edge.source === selected ? edge.target : edge.source) }}</a-button>
            <div class="t-12 c-3 num">{{ edge.evidence }}</div>
          </div>
          <p v-if="!related.length" class="t-12 c-3">没有可见的显式上下游引用。</p>
          <a-alert v-for="issue in unresolved" :key="issue.location" :message="issue.reason" :description="issue.location" type="warning" show-icon class="lineage-issue" />
        </template>
        <p v-else class="t-14 c-3">选择节点查看依赖依据。</p>
      </aside>
    </div>
  </section>
</template>

<style scoped>
.lineage { flex: 1; min-height: 0; min-width: 0; display: flex; flex-direction: column; gap: var(--gap); }
.lineage-tools { flex: none; min-width: 0; display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
.lineage-detail h2 { margin: 0 0 8px; }
.lineage-select { flex: 1; min-width: 220px; max-width: 540px; }
.lineage-body { flex: 1; min-height: 0; min-width: 0; display: grid; grid-template-columns: minmax(0, 1fr) clamp(280px, 24vw, 360px); grid-template-rows: minmax(0, 1fr); border: var(--border); border-radius: var(--radius); overflow: hidden; }
.lineage-graph { min-width: 0; min-height: 0; display: flex; flex-direction: column; }
.lineage-canvas { flex: 1; min-width: 0; min-height: 0; position: relative; }
.lineage-legend { flex: none; min-height: 48px; padding: 8px 16px; display: flex; align-items: center; flex-wrap: wrap; gap: 8px 16px; border-top: var(--border); background: var(--g1); }
.lineage-legend > span { display: inline-flex; align-items: center; gap: 6px; }
.lineage-legend i { width: 10px; height: 10px; border: 1px solid var(--g7); border-radius: 50%; }
.lineage-detail { min-height: 0; min-width: 0; padding: 16px; overflow: auto; border-left: var(--border); background: var(--g1); overflow-wrap: anywhere; }
.lineage-direction { margin: 12px 0; }
.lineage-detail h3 { margin: 24px 0 12px; }
.lineage-reference { margin-bottom: 16px; }
.lineage-link { padding: 0; height: auto; white-space: normal; text-align: left; }
.lineage-issue { margin-top: 12px; }
@media (max-width: 820px) { .lineage-body { grid-template-columns: minmax(0, 1fr); grid-template-rows: minmax(0, 1fr) minmax(160px, 35%); } .lineage-detail { border-left: 0; border-top: var(--border); } }
</style>
