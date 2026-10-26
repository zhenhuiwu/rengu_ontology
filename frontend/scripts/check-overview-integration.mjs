// Run: node scripts/check-overview-integration.mjs (no server or database writes).
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import ts from 'typescript';
import * as vue from 'vue';
const defer = () => { let resolve; const promise = new Promise((r) => { resolve = r; }); return { promise, resolve }; };
const real = vue.ref(true);
const backend = vue.reactive({ down: false });
const pending = [];
let failure;
const context = vue.reactive({ ver: 'v1' });
const navigations = [];
const imports = {
  vue: { ...vue, watch() {}, onMounted() {}, onBeforeUnmount() {} },
  'vue-router': { useRouter: () => ({ push(to) { navigations.push(to); } }) }, 'ant-design-vue': {}, '@ant-design/icons-vue': {},
  '@/composables/useReadonly': { useReadonly: () => ({}) }, '@/theme': { GRAY: {}, STATUS: {} },
  '@/mock/links': { LINK_OBJECTS: [], LINKS: [] }, '@/mock/actionEvent': { ACTION_TYPES: [], EVENT_TYPES: [] },
  '@/mock/metrics': { METRICS: [] }, '@/mock/dimensions': { DIM_ITEMS: [] },
  './parts/useDraftView': { useDraftView: () => ({}) }, '@/stores/app': { useAppStore: () => ({}) },
  '@/mock/changes': { CHANGE_REQUESTS: [{ id: 'DEMO', status: '草稿' }] }, '@/mock/quality': { QUALITY_CHECKS: [] },
  '@/api/releases': { RELEASES: [{ ver: 'demo' }] }, '@/api/http': { currentApiContext: () => context },
  '@/api/static': {},
  '@/api/fallback': { backend, fallBackIfDown: () => false },
  '@/api/overview': {
    isOverviewReal: () => real.value, mockGraph: () => ({ nodes: [{ id: 'demo' }], edges: [] }),
    fetchOverview: () => { if (failure) return Promise.reject(failure); const p = defer(); pending.push(p); return p.promise; },
    fetchGraph: async () => ({ nodes: [{ id: context.ver }], edges: [] }),
    fetchOverviewReleases: async () => ({ items: [{ ver: context.ver }] }),
    fetchOverviewTodos: async () => { if (failure) throw failure; return { items: [{ id: 'REAL', kind: '变更' }], total: 1, page: 1 }; },
  },
};
const source = readFileSync(new URL('../src/pages/ontology/OntologyOverview.vue', import.meta.url), 'utf8').match(/<script setup lang="ts">([\s\S]*?)<\/script>/)[1];
const { outputText } = ts.transpileModule(`${source}\nexport { loadGraph, loadTodos, cards, pageRows, releases, graphData, loadError, todoError, draw, edgeCount };`, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } });
const page = {};
runInNewContext(outputText, { exports: page, requestAnimationFrame: (fn) => fn(), require: (name) => {
  if (name in imports) return imports[name];
  if (name.endsWith('.vue')) return {};
  throw new Error(`Missing dependency ${name}`);
} });
const summary = (n) => ({ cards: [{ key: 'obj', title: '对象类型', count: n, health: [], unit: '个' }], qualityConfigured: false, dataAsOf: 'now' });
const old = page.loadGraph();
await Promise.resolve(); await vue.nextTick();
assert.equal(page.graphData.value.nodes[0].id, 'v1', 'graph nodes must render while the slower summary is still pending');
context.ver = 'v2';
const current = page.loadGraph();
pending[1].resolve(summary(2)); await current;
pending[0].resolve(summary(1)); await old;
assert.equal(page.cards.value[0].count, 2, 'late overview data must not overwrite the current version');
assert.equal(page.graphData.value.nodes[0].id, 'v2');
assert.equal(page.releases.value[0].ver, 'v2');
await page.loadTodos();
assert.equal(page.pageRows.value[0].id, 'REAL');
failure = new Error('FORBIDDEN');
await page.loadGraph(); await page.loadTodos();
assert.equal(page.cards.value.length, 0, 'business errors must not reveal demo or previous real cards');
assert.equal(page.pageRows.value.length, 0);
assert.match(page.loadError.value, /FORBIDDEN/);
assert.match(page.todoError.value, /FORBIDDEN/);
const nodes = [], edges = [], handlers = {};
page.graphData.value = {
  nodes: [{ id: 'store', name: '门店' }, { id: 'order', name: '订单' }, { id: 'future-object', name: '新对象' }, { id: 'warehouse', name: '仓库' }, { id: 'shipment', name: '出库单' }],
  edges: [
    { id: 'parallel-a', name: '下单门店', from: 'order', to: 'store', status: '已发布' },
    { id: 'parallel-b', name: '取货门店', from: 'order', to: 'store', status: '已发布' },
    { id: 'self', name: '上级门店', from: 'store', to: 'store', status: '已发布' },
    { id: 'future', name: '关联门店', from: 'future-object', to: 'store', status: '已发布' },
    { id: 'overlap', name: '补货仓', from: 'store', to: 'warehouse', status: '已发布' },
  ],
};
page.draw({ options: {}, batchUpdate(_name, fn) { fn(); },
  addNode(node) { nodes.push(node); }, addEdge(edge) { edges.push(edge); }, getNodes: () => nodes,
  zoomToFit() {}, centerContent() {}, on(name, callback) { handlers[name] = callback; },
});
assert.equal(edges.length, 5, 'parallel, self and newly published object relations must all remain visible');
assert.equal(page.edgeCount.value, 5);
assert.equal(edges[0].labels[0].attrs.label.text, '下单门店');
assert.equal(edges[0].attrs.line.targetMarker.name, 'block');
assert.notEqual(JSON.stringify(edges[0].vertices), JSON.stringify(edges[1].vertices));
assert.notEqual(edges[0].labels[0].position, edges[1].labels[0].position);
assert.equal(edges[2].source.cell, 'store');
assert.notEqual(edges[4].labels[0].position, 0.5, 'a relation label must avoid an intermediate node');
handlers['edge:click']({ edge: { id: 'parallel-b' } });
assert.equal(navigations.at(-1).query.link, 'parallel-b');
real.value = false;
assert.equal(page.cards.value.length, 6, 'mock mode must retain all six original resource cards');
assert.equal(page.pageRows.value[0].id, 'DEMO');
console.log('Overview integration checks passed (real summary/todos/releases, late versions, forbidden errors, mock mode).');
