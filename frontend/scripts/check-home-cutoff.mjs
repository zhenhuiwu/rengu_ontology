// Run: node scripts/check-home-cutoff.mjs. No server or database writes.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import ts from 'typescript';
import * as vue from 'vue';
function load(path, imports, expose = '') {
  let source = readFileSync(new URL(`../src/${path}`, import.meta.url), 'utf8');
  if (path.endsWith('.vue')) source = source.match(/<script setup lang="ts">([\s\S]*?)<\/script>/)[1];
  const { outputText } = ts.transpileModule(`${source}\n${expose}`, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } });
  const exports = {};
  runInNewContext(outputText, { exports, console: { warn() {} }, require: (key) => {
    if (key in imports) return imports[key];
    if (key.endsWith('.vue')) return {};
    throw new Error(`Missing boundary: ${key}`);
  } });
  return exports;
}
const adapter = load('api/dashboard.ts', { './http': { apiClient: () => ({ get: async () => ({ dataAsOf: '2020-01-01 10:00', items: [{ id: 'gmv', value: null, prev: 3 }] }) }) } });
const dto = await adapter.fetchKpis(['gmv']);
assert.equal(dto.dataAsOf, '2020-01-01 10:00');
assert.equal(dto.items[0].value, 0, 'normalization must preserve the existing KPI contract');
const context = vue.reactive({ demoAs: 'admin', ver: 'v1' });
const real = vue.ref(true);
const pending = { kpi: [], rank: [], orders: [], trend: [] };
let trendFailure;
const request = (kind) => { let resolve; const p = new Promise((r) => { resolve = r; }); pending[kind].push(resolve); return p; };
const page = load('pages/Home.vue', {
  vue: { ...vue, watch() {} }, 'ant-design-vue': {},
  'vue-router': { useRoute: () => ({ query: {} }), useRouter: () => ({}) }, '@/theme': { GRAY: {} },
  '@/composables/usePagedQuery': { usePagedQuery: () => ({ page: vue.ref(1), size: vue.ref(15) }) },
  '@/mock': { exTax: (v) => v / 1.13, ORDERS: [], STORES: [{ id: 'DEMO-STORE', name: 'Demo' }], METRICS: ['gmv', 'order_cnt', 'gross_margin', 'ship_ontime'].map((id) => ({ id })), DAILY_GMV_EX: [{ date: 'demo', value: 999 }], paginate: (items) => ({ items, total: items.length }) },
  '@/api/http': { currentApiContext: () => context, isBackendDown: () => false },
  '@/api/dashboard': { isDashboardReal: () => real.value, fetchSalesDaily: () => trendFailure ? Promise.reject(trendFailure) : request('trend'), fetchKpis: () => request('kpi'), fetchStoreRanking: () => request('rank') },
  '@/api/biz': { listOrderDtos: () => request('orders') },
}, 'export { loadSummary, dataAsOf, kpiMetrics, ranking, realOrders, loadTrend, trend, trendPoints };');
assert.equal(page.kpiMetrics.value.length, 0);
assert.equal(page.ranking.value.length, 0, 'real mode must not initialize with demonstration stores');
const old = page.loadSummary();
context.demoAs = 'viewer'; context.ver = 'v2';
assert.equal(page.dataAsOf.value, '—');
const current = page.loadSummary();
const result = (date, id) => ({ dataAsOf: date, items: [{ id }], total: 1 });
pending.kpi[1](result('2026-10-24 22:00', 'new-kpi'));
pending.rank[2](result('2026-10-24 21:00', 'new-page'));
pending.rank[3](result('2026-10-24 21:00', 'new-rank'));
await current;
assert.equal(page.dataAsOf.value, '—', 'do not declare a shared cutoff until every source is available');
pending.orders[1](result('2026-10-24 20:00', 'new-order'));
await new Promise(setImmediate);
assert.equal(page.dataAsOf.value, '2026-10-24 20:00', 'use the shared actual cutoff, not KPI update time');
pending.kpi[0](result('2020-01-01 00:00', 'old-kpi'));
pending.rank[0](result('2020-01-01 00:00', 'old-page'));
pending.rank[1](result('2020-01-01 00:00', 'old-rank'));
pending.orders[0](result('2020-01-01 00:00', 'old-order'));
await old;
assert.equal(page.dataAsOf.value, '2026-10-24 20:00');
assert.equal(page.kpiMetrics.value[0].id, 'new-kpi');
assert.equal(page.ranking.value[0].id, 'new-rank');
assert.equal(page.realOrders.value.items[0].id, 'new-order');
context.previewAs = 'other';
assert.equal(page.dataAsOf.value, '—', 'hide the previous identity cutoff before watcher flush');
const oldTrend = page.loadTrend();
context.ver = 'v3';
const newTrend = page.loadTrend();
assert.equal(page.trendPoints.value.length, 0);
pending.trend[1]({ items: [{ date: '2026-10-25', value: 12 }], dataAsOf: '2026-10-25 15:42' }); await newTrend;
pending.trend[0]({ items: [{ date: '2020-01-01', value: 888 }], dataAsOf: '2020-01-01 00:00' }); await oldTrend;
assert.equal(page.trendPoints.value[0].value, 12);
trendFailure = new Error('403 FORBIDDEN'); await page.loadTrend();
assert.equal(page.trendPoints.value.length, 0);
assert.match(page.trend.error, /403/);
trendFailure = new Error('Network unavailable'); await page.loadTrend();
assert.equal(page.trendPoints.value.length, 0, 'even connection failure must not return demo sales');
real.value = false;
assert.equal(page.trendPoints.value[0].value, 999);
assert.equal(page.dataAsOf.value, '2026-10-24 23:00');
console.log('Home cutoff checks passed (DTO metadata, common cutoff, late identities/versions, real sales trend/errors, explicit mock).');
