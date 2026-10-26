// Run: node scripts/check-static-loading.mjs (no server or database writes).
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import ts from 'typescript';
import * as vue from 'vue';
const defer = () => { let resolve, reject; const promise = new Promise((r, j) => { resolve = r; reject = j; }); return { promise, resolve, reject }; };
const tick = () => new Promise(setImmediate);
function load(path, imports, expose = '', globals = {}) {
  let source = readFileSync(new URL(`../src/${path}`, import.meta.url), 'utf8');
  if (path.endsWith('.vue')) source = source.match(/<script setup lang="ts">([\s\S]*?)<\/script>/)[1];
  const exports = {};
  const { outputText } = ts.transpileModule(`${source}\n${expose}`, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } });
  runInNewContext(outputText, { exports, ...globals, require: (name) => {
    if (name in imports) return imports[name];
    throw new Error(`Missing dependency: ${name}`);
  } });
  return exports;
}
let context = { demoAs: 'admin', ver: 'v1' };
let real = true;
let postFails = false;
const requests = [], posts = [];
const links = { LINKS: [{ id: 'demo' }], LINK_OBJECTS: [], PATH_GROUPS: [] };
const api = load('api/static.ts', {
  './http': { currentApiContext: () => context, apiClient: () => ({ isReal: () => real, post: async (path, body, options) => { posts.push({path,body,options}); if (postFails) throw Error('publish failed'); return { ver: 'new', crId:'CR-ROLLBACK', lockVersion:8 }; }, get: (path, options) => {
    const pending = defer(); requests.push({ path, options, ...pending }); return pending.promise;
  } }) },
  '@/mock/links': links, '@/mock/objectProps': { OBJECT_PROPS: {} },
  '@/mock/metricCatalog': { METRIC_LIST: [], METRIC_DEF: [], METRIC_DIMS: [], METRIC_VERSIONS: [], METRIC_REFS: [] },
  '@/mock/dimensions': { DIM_ITEMS: [] }, '@/mock/knowledge': { KNOWLEDGE: [] }, '@/mock/releases': {},
});
const settle = (ver) => requests.filter((r) => r.options.query.ver === ver).forEach((r) => {
  r.resolve(r.path.endsWith('/paths') ? [{ id: ver }] : { items: [{ id: ver }], total: 1, page: 1 });
});
const oldLoad = api.loadStatic(['links']); await tick();
context = { demoAs: 'viewer', previewAs: 'user', ver: 'v2' };
const newLoad = api.loadStatic(['links']); await tick();
settle('v2'); await newLoad;
assert.equal(links.LINKS[0].id, 'v2');
settle('v1'); await oldLoad;
assert.equal(links.LINKS[0].id, 'v2', 'a late old version must not replace current arrays');
assert.equal(links.PATH_GROUPS[0].id, 'v2');
assert.ok(requests.filter((r) => r.options.query.ver === 'v1').every((r) => r.options.headers['X-Demo-As'] === 'admin'));
const count = requests.length;
await api.loadStatic(['links']);
assert.equal(requests.length, count, 'current datasets must be cached');
context = { demoAs: 'viewer', ver: 'forbidden' };
const forbidden = api.loadStatic(['links']); await tick();
requests.filter((r) => r.options.query.ver === 'forbidden').forEach((r) => r.reject(new Error('FORBIDDEN')));
await assert.rejects(forbidden, /FORBIDDEN/);
assert.equal(links.LINKS.length, 0, 'failed real loads must expose neither old identities nor demo arrays');
real = false;
await api.loadStatic(['links']);
assert.equal(links.LINKS[0].id, 'demo');
real = true; context = { demoAs: 'admin' };
const firstIndex = requests.length;
const beforePublish = api.loadStatic(['links']); await tick();
const pendingBefore = requests.slice(firstIndex);
await api.publishRelease();
const nextIndex = requests.length;
const afterPublish = api.loadStatic(['links']); await tick();
const resolveRows = (rows, id) => rows.forEach((r) => r.resolve(r.path.endsWith('/paths') ? [] : { items: [{ id }], total: 1, page: 1 }));
resolveRows(requests.slice(nextIndex), 'published'); await afterPublish;
resolveRows(pendingBefore, 'stale pre-publish'); await beforePublish;
assert.equal(links.LINKS[0].id, 'published', 'publish invalidates same-current-context arrays and stale requests');
const afterCount = requests.length; postFails = true;
await assert.rejects(api.publishRelease(), /publish failed/); await api.loadStatic(['links']);
assert.equal(requests.length, afterCount, 'failed publish retains successful dataset cache');
postFails = false; await api.rollbackRelease('new');
const rolledBack = api.loadStatic(['links']); await tick();
resolveRows(requests.slice(afterCount), 'rolled back'); await rolledBack;
assert.equal(links.LINKS[0].id, 'rolled back');


context = { demoAs: 'knowledge-user', previewAs: 'preview-user' };
const choiceStart = requests.length;
const choiceLoad = api.fetchKnowledgeChoices(); await tick();
requests.slice(choiceStart).filter((r) => r.path === '/metrics').forEach((r) => r.resolve({ items: [{ id: 'M-DB', name: 'DB metric', status: '已发布' }, { id: 'M-OFF', status: '已下线' }], total: 2 }));
requests.slice(choiceStart).filter((r) => r.path === '/object-types').forEach((r) => r.resolve({ items: Array.from({length:200},(_,i) => ({id:`O-${i}`, name:`Object ${i}`, status:'已发布'})), total:201 }));
await tick();
requests.slice(choiceStart).filter((r) => r.path === '/object-types' && r.options.query.page === 2).forEach((r) => r.resolve({ items: [{ id:'O-200', name:'Last DB object',status:'已发布' }],total:201 }));
const realChoices = await choiceLoad;
assert.equal(realChoices.length, 202); assert.ok(!realChoices.some((r) => r.id === 'M-OFF'));
assert.ok(requests.slice(choiceStart).every((r) => r.options.headers['X-Demo-As'] === 'knowledge-user' && r.options.query.previewAs === 'preview-user'));
const ruleRead = api.fetchKnowledgeConflictRules(); await tick(); requests.at(-1).resolve({ rule: {} }); assert.equal(Object.keys(await ruleRead).length, 0, 'missing DB conflict configuration stays empty');
const usageStart = requests.length, usageRead = api.fetchDimensionUsage('D-DB'); await tick();
requests.at(-1).resolve({ items:Array.from({length:200},(_,i)=>({name:`DB usage ${i}`})),total:201 }); await tick(); requests.at(-1).resolve({items:[{name:'Last DB usage'}],total:201});
assert.equal((await usageRead).length,201); assert.equal(requests.length-usageStart,2);

const gates = [];
let key = 'old';
const errors = [];
const gate = load('pages/ontology/parts/StaticGate.vue', {
  vue: { ...vue, watch() {} }, 'ant-design-vue': { message: { warning: (e) => errors.push(e) } },
  '@/api/http': { isBackendDown: (e) => e.message === 'NETWORK_ERROR', isApiError: () => false },
  '@/api/static': { isStaticReal: () => true, staticContextKey: () => key, loadStatic: () => { const p = defer(); gates.push(p); return p.promise; } },
}, 'export { load, state, error, bodyKey };', { defineProps: () => ({ sets: ['links'] }), console: { error() {} } });
const oldGate = gate.load(); key = 'new'; const newGate = gate.load();
gates[1].resolve(); await newGate;
gates[0].reject(new Error('old failure')); await oldGate;
assert.equal(gate.state.value, 'ready');
assert.equal(gate.bodyKey.value, 'new');
const staleSuccess = gate.load(); key = 'newer'; const activeFailure = gate.load();
gates[3].reject(new Error('FORBIDDEN')); await activeFailure;
gates[2].resolve(); await staleSuccess;
assert.equal(gate.state.value, 'error', 'old successes must not clear current business errors');
assert.match(gate.error.value, /FORBIDDEN/);
assert.equal(errors.length, 0, '403 must not trigger the down/mock fallback');
const networkFailure = gate.load();
gates.at(-1).reject(new Error('NETWORK_ERROR')); await networkFailure;
assert.equal(gate.state.value, 'error', 'network failure must not mount demo data');
assert.match(gate.error.value, /后台未启动/);
const fallback = load('api/fallback.ts', { vue });
assert.equal(fallback.fallBackIfDown(new Error('NETWORK_ERROR')), false);
assert.equal(fallback.backend.down, false, 'shared errors must not switch real pages to mock');
console.log('Static loading checks passed (out-of-order arrays, pinned context, cache, 403, gate success/error races).');

// Dedicated object history adapter fetches every page with one pinned context.
real=true;context={demoAs:'history-user',previewAs:'preview-user',ver:'history-version'};
const historyRead=api.fetchObjectCrHistory('DB-OBJECT');await tick();
const historyFirst=requests.at(-1);assert.equal(historyFirst.path,'/object-types/DB-OBJECT/usage');assert.equal(historyFirst.options.query.kind,'cr');
historyFirst.resolve({items:Array.from({length:200},(_,i)=>({cr:{id:`CR-${i}`},parts:['definition']})),total:201,page:1});await tick();
const historyLast=requests.at(-1);assert.equal(historyLast.options.query.page,2);assert.equal(historyLast.options.headers['X-Demo-As'],'history-user');assert.equal(historyLast.options.query.previewAs,'preview-user');assert.equal(historyLast.options.query.ver,'history-version');
historyLast.resolve({items:[{cr:{id:'CR-200'},parts:['properties']}],total:201,page:2});assert.equal((await historyRead).length,201);

// Rollback joins use the head lock read before the command; context changes abort writes.
context={demoAs:'rollback-owner',previewAs:undefined,ver:undefined};postFails=false;
const rollbackRead=api.rollbackMetric('metric-db','v1',{join:'CR-JOIN',desc:'actual reason'});await tick();
requests.at(-1).resolve({id:'CR-JOIN',lockVersion:7});const rollbackResult=await rollbackRead;assert.equal(rollbackResult.crId,'CR-ROLLBACK');
const rollbackPost=posts.at(-1);assert.equal(rollbackPost.path,'/metrics/metric-db:rollback');assert.equal(rollbackPost.body.lockVersion,7);assert.equal(rollbackPost.body.crId,'CR-JOIN');assert.equal(rollbackPost.body.toVersion,'v1');assert.equal(rollbackPost.options.headers['X-Demo-As'],'rollback-owner');
const writesBefore=posts.length;const staleRollback=api.rollbackMetric('metric-db','v1',{join:'CR-JOIN'});await tick();const oldHead=requests.at(-1);context={demoAs:'different-user'};oldHead.resolve({id:'CR-JOIN',lockVersion:9});assert.equal(await staleRollback,undefined);assert.equal(posts.length,writesBefore);

// Visible lists issue exactly one requested page, with the captured identity and version.
context = { demoAs: 'page-user', ver: 'db-version', previewAs: 'db-preview' };
const onePageAt = requests.length;
const onePage = api.fetchStaticPage('/metrics', { page: 3, size: 10, kw: 'actual filter' });
assert.equal(requests.length, onePageAt + 1);
const pageCall = requests.at(-1);
assert.equal(pageCall.options.query.page, 3); assert.equal(pageCall.options.query.size, 10); assert.equal(pageCall.options.query.kw, 'actual filter');
assert.equal(pageCall.options.headers['X-Demo-As'], 'page-user'); assert.equal(pageCall.options.query.ver, 'db-version'); assert.equal(pageCall.options.query.previewAs, 'db-preview');
pageCall.resolve({ items: [{ id: 'ONLY-PAGE-3' }], total: 500, page: 3, size: 10 });
assert.equal((await onePage).items[0].id, 'ONLY-PAGE-3'); assert.equal(requests.length, onePageAt + 1, 'a visible page must never fetch all following pages');
const headers = api.fetchChangeRequests(undefined, false); assert.equal(requests.at(-1).options.query.full, false); requests.at(-1).resolve([]); await headers;

const workflow = api.fetchWorkflowRecords({ readonlyParams: false, query: { ver: '', previewAs: 'workflow-preview' }, headers: { 'X-Demo-As': 'workflow-user' } });
assert.equal(requests.at(-1).options.query.workflow, true); assert.equal(requests.at(-1).options.query.page, undefined); assert.equal(requests.at(-1).options.query.size, undefined);
assert.equal(requests.at(-1).options.headers['X-Demo-As'], 'workflow-user');
requests.at(-1).resolve([{ id: 'CR-ACTUAL', status: '审核中', detail: { touches: ['object:O1', 'metric:M1'] }, targets: [] }]);
assert.equal((await workflow)[0].detail.touches[1], 'metric:M1');

const badWorkflow = api.fetchWorkflowRecords(); requests.at(-1).resolve([{ id: 'BAD-HEADER' }]);
await assert.rejects(badWorkflow, /工作流目录响应无效/);
