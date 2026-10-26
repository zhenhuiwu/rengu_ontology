// Run: node scripts/check-data-real.mjs (real data API and Connections page scripts).
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
  runInNewContext(outputText, { exports, console: { error() {} }, require: (name) => {
    assert.ok(name in imports, `Missing dependency ${name}`); return imports[name];
  } });
  return exports;
}
const deferred = () => { let resolve; const promise = new Promise((r) => { resolve = r; }); return { promise, resolve }; };
const tick = () => new Promise(setImmediate);
let context = { demoAs: 'first', ver: 'v1', previewAs: 'preview' };
const records = Array.from({ length: 401 }, (_, n) => ({ id: `DS-${n}`, name: `DB ${n}` }));
const calls = [];
let first = deferred();
const api = load('api/datasources.ts', { './http': {
  currentApiContext: () => context, apiClient: () => ({ isReal: () => true, get: async (path, opts) => {
    calls.push({ path, opts });
    const page = opts.query.page;
    if (page === 1) await first.promise;
    return { page, size: 200, total: records.length, items: records.slice((page - 1) * 200, page * 200) };
  } }),
} });
const pending = api.listDatasources();
context = { demoAs: 'other', ver: 'v2' };
first.resolve();
assert.equal((await pending).length, 401);
assert.deepEqual(calls.map((r) => r.opts.query.page), [1, 2, 3]);
assert.ok(calls.every((r) => r.opts.headers['X-Demo-As'] === 'first' && r.opts.query.ver === 'v1' && r.opts.query.previewAs === 'preview' && r.opts.readonlyParams === false));
calls.length = 0;
assert.equal((await api.listDsTables('DS-1')).length, 401);
assert.ok(calls.every((r) => r.path === '/datasources/DS-1/tables' && r.opts.headers['X-Demo-As'] === 'other'));

context = vue.reactive({ demoAs: 'first', ver: null, previewAs: null });
const readonly = vue.ref(false);
const errors = [];
const success = [];
const record = (id) => ({ id, name: id, type: 'PostgreSQL', host: 'localhost', port: 5432, database: 'db', user: 'user', status: '未测试', lastTest: null, owner: 'owner', objectTypes: [], sync: { mode: '手动', cron: '-', cronText: '-', window: '全天' }, runs: [], tableCount: 0 });
const network = Object.assign(new Error('后台不可用'), { code: 'NETWORK_ERROR' });
let listResult = Promise.reject(network);
let tableResult = [];
let confirmation;
let testResult;
let writes = 0;
let deletions = 0; const sourceQueries=[], tableQueries=[]; const connectionsRoute={query:vue.reactive({})};
const page = load('pages/data/Connections.vue', {
  vue, 'vue-router': { useRoute: () => connectionsRoute, useRouter: () => ({ replace() {} }) },
  'ant-design-vue': { message: { error: (m) => errors.push(m), warning: (m) => errors.push(m), success: (m) => success.push(m) } },
  '@/components/AppPagination.vue': {}, '@/components/InfoTip.vue': {}, '@/pages/ontology/parts/ObjectMappingModal.vue': {}, '@/api/objectImport': { fetchDatasourceMappings:async()=>[] },
  '@/stores/confirm': { useConfirmStore: () => ({ ask: () => confirmation.promise }) },
  '@/composables/useReadonly': { useReadonly: () => ({ readonly, reason: vue.ref('') }) },
  '@/composables/useUnsavedGuard': { useUnsavedGuard: () => (fn) => fn(), confirmDiscard: async () => true },
  '@/mock': { paginate: (items) => ({ items, total: items.length }), fmtInt: String, EMPTY: '—' },
  '@/mock/datasources': { DATASOURCES: [record('MOCK')], DS_TYPES: ['PostgreSQL'], DS_DEFAULT_PORT: { PostgreSQL: 5432 }, DM_COMPAT: [], KB_MODES: [], ERP_SYSTEMS: [], dsTables: () => { throw new Error('real mode read mock tables'); } },
  '@/api/http': { currentApiContext: () => context, isApiError: (e) => !!e?.code },
  '@/api/datasources': {
    isDataReal: () => true, queryDatasources: async(q) => { sourceQueries.push({...q}); const items=await listResult; const page=Math.max(1,Math.min(q.page,Math.ceil(items.length/q.size)));return {items:items.slice((page-1)*q.size,page*q.size),total:items.length,page,size:q.size}; }, queryDsTables: async(_id,q) => {tableQueries.push({...q});return {items:tableResult.slice((q.page-1)*q.size,q.page*q.size),total:tableResult.length,page:q.page,size:q.size};}, queryDsRuns:async()=>({items:[],total:0,page:1,size:10}),
    createDatasource: async () => { writes++; throw network; }, updateDatasource: async () => { writes++; throw network; },
    getDatasource: async (id) => record(id), deleteDatasource: async () => { deletions++; },
    testDatasource: async () => { if (testResult) return testResult; throw network; }, testDatasourceParams: async () => { throw network; },
  },
}, 'export { real, list, realTables, save, form, remove, testConn, testing, listPage, listTotal, tablePage, tableTotal };');
assert.equal(page.list.value.length, 0, 'real initial load must not render mock records');
await tick();
assert.equal(page.real.value, true, 'network error must not change to local write mode');
assert.equal(page.list.value.length, 0);
assert.ok(errors.length);

const old = deferred();
listResult = old.promise;
context.ver = 'v1';
listResult = [record('NEW')];
context.demoAs = 'other';
await tick();
old.resolve([record('PRIVATE-OLD')]);
await tick();
assert.equal(page.list.value[0].id, 'NEW');
await page.testConn(page.list.value[0]);
assert.equal(page.list.value[0].lastTest, null);
assert.equal(page.list.value[0].status, '未测试');
assert.equal(page.testing.value, false);
assert.equal(success.length, 0, 'failed tests cannot become mock success');
testResult = { ok: false, ms: 0, msg: '未保存密码，未执行测试', time: 'now' };
await page.testConn(page.list.value[0]);
assert.equal(page.list.value[0].lastTest, null, 'displayed history must be reloaded from the database');
Object.assign(page.form, { name: 'new', host: 'db', database: 'db', user: 'user', password: 'secret' });
page.save();
await tick();
assert.equal(writes, 1);
assert.equal(page.list.value.length, 1, 'failed real save must not add local records');

confirmation = deferred();
const removing = page.remove(page.list.value[0]);
context.demoAs = 'third';
confirmation.resolve(true);
await removing;
assert.equal(deletions, 0, 'confirmation after identity switch must not delete');
readonly.value = true;
page.save();
assert.equal(writes, 1);
readonly.value = false;
// Actual page controls request only the selected server page; totals stay visible while waiting.
listResult=Array.from({length:23},(_,i)=>record(`PAGE-${i}`));context.demoAs='paging';await tick();
assert.equal(page.list.value.length,10);assert.equal(page.listTotal.value,23);page.listPage.value=2;await tick();assert.equal(sourceQueries.at(-1).page,2);assert.equal(sourceQueries.at(-1).size,10);assert.equal(page.list.value[0].id,'PAGE-10');
const waiting=deferred();listResult=waiting.promise;page.listPage.value=3;await tick();assert.equal(page.listTotal.value,23,'same-context paging never flashes total zero');waiting.resolve(Array.from({length:23},(_,i)=>record(`PAGE-${i}`)));await tick();assert.equal(page.list.value.length,3);
tableResult=Array.from({length:23},(_,i)=>({schema:'public',name:`table${i}`,comment:'',rows:0,cols:1}));connectionsRoute.query.tab='tables';await tick();assert.equal(tableQueries.at(-1).size,10);assert.equal(page.tableTotal.value,23);page.tablePage.value=2;await tick();assert.equal(tableQueries.at(-1).page,2);assert.equal(page.realTables.value.items.length,10);
let externalRequests = 0; let externalRows=[{id:'EXT-TEST',name:'真实测试系统'}], externalDeferred;const externalPages=[];
let probeConfirm = async () => true;
const external = load('pages/data/ExternalSystems.vue', {
  vue, 'vue-router': { useRoute: () => ({ query: vue.reactive({}) }), useRouter: () => ({ replace() {} }) },
  'ant-design-vue': { message: { error: (m) => errors.push(m), warning: (m) => errors.push(m), success: (m) => success.push(m) } },
  '@ant-design/icons-vue': {}, '@/components/AppPagination.vue': {},
  '@/stores/confirm': { useConfirmStore: () => ({ ask: () => probeConfirm() }) },
  '@/composables/useReadonly': { useReadonly: () => ({ readonly, reason: vue.ref('') }) },
  '@/composables/useUnsavedGuard': { useUnsavedGuard: () => () => {}, confirmDiscard: async () => true },
  '@/mock': { paginate: (items) => ({ items, total: items.length }), EMPTY: '—' },
  '@/mock/extsys': { EXT_SYSTEMS: [], extCalls: () => [] },
  '@/mock/actionEvent': { ACTION_TYPES: [], runsOf: () => [] },
  '@/mock/actions': { ACTIONS: [], RULE_CONFIRMER: {}, OBJ_KIND_LABEL: {} },
  '@/api/http': { currentApiContext:()=>context, isApiError: (e) => !!e?.code },
  '@/api/fallback': { backend: vue.reactive({ down: false }), fallBackIfDown: () => false },
  '@/api/integration': { integrationApi: { isReal: () => true }, queryExtSystems: async (page,size) => {externalPages.push({page,size}); const rows=externalDeferred ? await externalDeferred.promise : externalRows;return {items:rows.slice((page-1)*size,page*size),total:rows.length,page,size};}, getExtSystem:async(id)=>({id,name:id,actionTypes:[]}), listExtCalls:async()=>({items:[],total:0,page:1,size:10,counts:{}}),
    testExtSystem: async () => { externalRequests++; throw Object.assign(new Error('未配置真实探测'),{code:'EXT_SYSTEM_TEST_UNAVAILABLE'}); } },
}, 'export { testCall, reqBody, respBody, loadSystems, systems, systemTotal, systemPage };');
await external.loadSystems(); await tick();
await external.testCall();
assert.equal(externalRequests, 1);
assert.ok(errors.at(-1).includes('EXT_SYSTEM_TEST_UNAVAILABLE'));
assert.equal(external.systems.length, 1, 'unconfigured probe does not fabricate a system or call');
readonly.value=true; await external.testCall(); readonly.value=false;
assert.equal(externalRequests, 1, 'read-only context does not probe');
const probeApproval=deferred();probeConfirm=()=>probeApproval.promise;
const staleProbe=external.testCall();context.demoAs='probe-other-user';probeApproval.resolve(true);await staleProbe;
assert.equal(externalRequests, 1, 'stale probe confirmation never sends under another identity');
assert.equal(external.reqBody({ http: 200 }), '未保存原始请求体');
assert.ok(external.respBody({ http: 200 }).includes('未保存原始响应体'));
console.log('Data full pagination/context, real empty/error state, failed writes and tests checks passed.');

externalRows=Array.from({length:23},(_,i)=>({id:`EXT-${i}`,name:`External ${i}`}));await external.loadSystems();assert.equal(external.systems.length,10);assert.equal(external.systemTotal.value,23);external.systemPage.value=2;await tick();assert.equal(externalPages.at(-1).page,2);assert.equal(external.systems[0].id,'EXT-10');
externalDeferred=deferred();const pendingExternal=external.loadSystems();assert.equal(external.systemTotal.value,23);const delayed=externalDeferred;context.demoAs='external-new-user';externalDeferred=undefined;externalRows=Array.from({length:21},(_,i)=>({id:`SAFE-${i}`,name:'Current user'}));await external.loadSystems();delayed.resolve([{id:'PRIVATE-OLD',name:'Private'}]);await pendingExternal;await tick();assert.ok(external.systems.every(s=>s.id.startsWith('SAFE-')));
