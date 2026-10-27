// Run: node scripts/check-context-integration.mjs (no server or database writes).
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import ts from 'typescript';
import * as vue from 'vue';
const defer = () => { let resolve; const promise = new Promise((r) => { resolve = r; }); return { promise, resolve }; };
const tick = () => new Promise(setImmediate);
function load(path, imports, expose = '') {
  let source = readFileSync(new URL(`../src/${path}`, import.meta.url), 'utf8');
  if (path.endsWith('.vue')) source = source.match(/<script setup lang="ts">([\s\S]*?)<\/script>/)[1];
  const exports = {};
  const { outputText } = ts.transpileModule(`${source}\n${expose}`, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } });
  runInNewContext(outputText, { exports, sessionStorage: imports.sessionStorage, require: (name) => {
    if (name in imports) return imports[name];
    if (name === 'vue') return vue;
    if (name.endsWith('.vue')) return {};
    throw new Error(`Missing dependency: ${name}`);
  } });
  return exports;
}
let context = { demoAs: 'admin', ver: 'v1' };
let real = true;
const oldTypes = defer();
const requests = [];
const type = { id: 'store', name: 'Store', group: 'Objects' };
const api = { isReal: () => real, get: async (path, opts) => {
  requests.push({ path, opts });
  if (path === '/object-types') return opts.query.ver === 'v1' ? oldTypes.promise : { items: [type] };
  if (path.endsWith('/properties')) return { items: [] };
  return { properties: [], sampleQuestions: ['Question'], titleProp: 'id', pkProp: 'id', kwPlaceholder: opts.query.ver };
} };
const metaApi = load('pages/objects/objMeta.ts', {
  '@/api/http': { apiClient: () => api, currentApiContext: () => context, isBackendDown: () => false },
  '@/api/static': { loadStatic: async () => {} }, '@/mock/links': { LINK_OBJECTS: [type] },
  '@/mock/objectProps': { OBJECT_PROPS: {}, objectMeta: () => ({ qs: [] }) },
  '@/mock/actionEvent': {}, '@/mock/metrics': { METRICS: [] }, '@/mock/format': {},
});
const oldMeta = metaApi.loadMeta('store');
context = { demoAs: 'viewer', previewAs: 'other', ver: 'v2' };
const newMeta = await metaApi.loadMeta('store');
oldTypes.resolve({ items: [type] });
assert.equal((await oldMeta).kwPlaceholder, 'v1', 'requests after an await must keep their original context');
assert.equal(newMeta.kwPlaceholder, 'v2');
const count = requests.length;
assert.equal(await metaApi.loadMeta('store'), newMeta);
assert.equal(requests.length, count, 'the same context must reuse its result');
assert.ok(requests.filter((r) => r.opts.query.ver === 'v1').every((r) => r.opts.headers['X-Demo-As'] === 'admin'));
real = false;
assert.notEqual(await metaApi.loadMeta('store'), newMeta, 'mock mode must not reuse a real result');
real = true;

let runRequests = 0;
let runFailure;
const runType = { id: 'type', name: 'Action', target: 'Store', usedBy: [], version: 'v1' };
const runData = { id: 'ACT-1', typeId: 'type', at: '2026-10-26 00:00:00', status: '待确认', objLabel: 'Store', summary: 'private', log: [{ time: 'database-time', text: 'Actual stored log' }] };
const resolver = load('components/actionEventResolvers.ts', {
  '@/api/http': { currentApiContext: () => context },
  '@/api/action': { dynamicApi: { isReal: () => real, get: async () => { runRequests++; if (runFailure) throw runFailure; return runData; } } },
  '@/api/fallback': { backend: { down: false }, fallBackIfDown: () => false }, '@/mock/dynamic': {},
  '@/mock/actionEvent': { actionTypeById: () => { assert.equal(real, false, 'real action details cannot resolve demo type metadata'); return runType; }, extSystemOf: () => null, runById: () => ({ ...runData, summary: 'mock' }) },
}).ACTION_EVENT_RESOLVERS;
const runSummary = () => resolver.action('ACT-1').fields.find((f) => f.label === '参数摘要')?.value;
assert.equal(runSummary(), undefined);
await tick();
assert.equal(runSummary(), 'private');
assert.equal(resolver.action('ACT-1').timeline.length, 1);
assert.equal(resolver.action('ACT-1').timeline[0].text, 'Actual stored log');
context = { demoAs: 'other', ver: 'v3' };
runFailure = new Error('FORBIDDEN');
assert.equal(runSummary(), undefined);
await tick();
assert.equal(runSummary(), undefined);
assert.equal(resolver.action('ACT-1').status.text, '加载失败');
assert.equal(runRequests, 2, 'cached business errors must not trigger repeated requests');
context = { demoAs: 'other', previewAs: 'missing', ver: 'v3' };
runFailure = new Error('NOT_FOUND');
assert.equal(runSummary(), undefined);
await tick();
assert.equal(runSummary(), undefined, '404 must not show mock or previously authorized run data');
assert.equal(resolver.action('ACT-1').status.text, '加载失败');
assert.equal(runRequests, 3);
real = false;
assert.equal(runSummary(), 'mock');
real = true;

const watchers = [];
const rememberedSearch = new Map();
const route = vue.reactive({ query: { type: 'store', id: '1' } });
const metaPending = [];
const pageImports = {
  vue: { ...vue, watch: (sources, callback) => watchers.push({ sources, callback }) },
  'vue-router': { useRoute: () => route, useRouter: () => ({}) },
  'ant-design-vue': { message: { error: (e) => assert.fail(e) } }, '@ant-design/icons-vue': {},
  '@/composables/usePreview': { usePreview: () => ({ userName: vue.ref('User') }) },
  '@/composables/usePagedQuery': { usePagedQuery: () => ({ page: vue.ref(1), size: vue.ref(15) }) },
  '@/mock/format': {}, '@/api/qaObjects': {},
  '@/api/http': { currentApiContext: () => context }, '@/api/static': { isStaticReal: () => true }, '@/api/qa': { qaReal: () => true },
  '../qa/qaFallback': { qaLive: vue.ref(true) }, '../qa/useQaSession': {},
  './objMeta': { metaContextKey: metaApi.metaContextKey, listTypes: async () => [type], loadMeta: () => { const pending = defer(); metaPending.push(pending); return pending.promise; } },
  './objRows': {},
  sessionStorage: { setItem: (key, value) => rememberedSearch.set(key, value) },
};
const search = load('pages/objects/ObjectSearch.vue', pageImports, 'export { meta, selKeys, types };');
const loadSearchMeta = watchers[1].callback;
const firstSearch = loadSearchMeta(['store']);
context = { demoAs: 'new', ver: 'v4' };
const secondSearch = loadSearchMeta(['store']);
metaPending[1].resolve({ id: 'store', name: 'new' }); await secondSearch;
metaPending[0].resolve({ id: 'store', name: 'old' }); await firstSearch;
assert.equal(search.meta.value.name, 'new', 'same-type context changes must discard late metadata');
assert.equal(watchers[1].sources[1].value, metaApi.metaContextKey(), 'search must watch the context as well as the type');
const selectionWatch = watchers.find(({ sources }) => Array.isArray(sources) && sources.length === 4);
assert.ok(selectionWatch, 'selection must watch type, context, keyword and filters');
search.selKeys.value = ['old-store'];
route.query.kw = 'Shanghai';
assert.equal(selectionWatch.sources[2].value, 'Shanghai');
selectionWatch.callback();
assert.equal(search.selKeys.value.length, 0, 'new search scopes cannot retain hidden selections');
search.selKeys.value = ['store-on-another-page'];
const originalScope = selectionWatch.sources.map((s) => typeof s === 'function' ? s() : s.value);
route.query.page = '2'; route.query.sort = '-code';
assert.deepEqual(selectionWatch.sources.map((s) => typeof s === 'function' ? s() : s.value), originalScope,
  'page and sort must not invalidate selections within the same search scope');
assert.equal(search.selKeys.value.length, 1);
route.query.f = '{"city":["Shanghai"]}';
assert.notEqual(selectionWatch.sources[3](), originalScope[3]);
selectionWatch.callback();
assert.equal(search.selKeys.value.length, 0, 'facet changes must clear old selections');
search.types.value = [type]; delete route.query.type;
watchers.at(-1).callback();
assert.equal(JSON.parse([...rememberedSearch.values()].at(-1)).type, 'store', 'remember the resolved default type so detail returns preserve filters');
assert.equal(JSON.parse([...rememberedSearch.values()].at(-1)).kw, 'Shanghai');
route.query.type = 'store';
watchers.length = 0;
metaPending.length = 0;
const detail = load('pages/objects/ObjectDetail.vue', {
  ...pageImports,
  '@/stores/confirm': { useConfirmStore: () => ({}) }, '@/composables/useReadonly': { useReadonly: () => ({}) },
  './objRows': { getRow: async () => ({ row: { id: '1' } }) },
}, 'export { meta };');
const loadDetail = watchers[0].callback;
assert.equal(watchers[0].sources[3], metaApi.metaContextKey, 'detail must watch API context changes');
const firstDetail = loadDetail();
context = { demoAs: 'latest', ver: 'v5' };
const secondDetail = loadDetail();
metaPending[1].resolve({ id: 'store', name: 'new' }); await secondDetail;
metaPending[0].resolve({ id: 'store', name: 'old' }); await firstDetail;
assert.equal(detail.meta.value.name, 'new', 'detail must discard metadata from an old context');

const scopePending = [];
const dim = { id: 'region', kind: '维度', group: '组织', status: '已发布', object: 'store', objectName: 'Store', levels: [{ name: 'Region' }, { name: 'City' }], values: ['East', 'West'].map((name) => ({ name, code: name, synonyms: [], level: 'Region' })) };
const rowScopeApi = load('api/rowScope.ts', {
  './http': { apiClient: (module) => {
    assert.equal(module, 'admin');
    return { isReal: () => true, get: (path) => {
      assert.equal(path, '/me/row-scope', 'scope identity must use the actual singular endpoint');
      const pending = { ...defer(), userId: context.demoAs }; scopePending.push(pending); return pending.promise;
    } };
  } },
});
const scopeApi = load('pages/qa/qaScope.ts', {
  '@/api/rowScope': rowScopeApi,
  '@/pages/objects/objMeta': { metaContextKey: metaApi.metaContextKey, loadMeta: async () => ({ hasData: true, metrics: [] }) },
  '@/api/static': { fetchStaticPage: async (path) => {
    const items = path === '/dimensions' ? [dim] : dim.values;
    return { items, total: items.length, page: 1, size: 200 };
  }, isStaticReal: () => true }, '@/api/qa': { qaReal: () => true, getSessionOptions: async () => ({ scopes: [], calibers: [] }) },
  '@/mock/dimensions': { DIM_ITEMS: [dim] }, '@/mock': { QA_SCOPE_OPTIONS: [], QA_CALIBER_OPTIONS: [] },
});
const scopeResponse = (pending, value) => ({ userId: pending.userId, userName: 'User', items: [{ objType: 'Store', visible: true, unrestricted: false, conditions: [{ prop: 'Region', op: '=', values: [value], text: value }], text: value }] });
const oldScope = scopeApi.ensureScopeOptions(true); await tick();
context = { demoAs: 'newer' };
const newScope = scopeApi.ensureScopeOptions(true); await tick();
scopePending[1].resolve(scopeResponse(scopePending[1], 'West')); await newScope;
scopePending[0].resolve(scopeResponse(scopePending[0], 'East')); await oldScope;
assert.equal(scopeApi.useScopeCaliber().defaultScope(() => null), 'West', 'old identity scopes must not overwrite the new identity');
assert.equal(scopePending.length, 2);
console.log('Context integration checks passed (metadata/mode caches, delayed page metadata, action privacy, scope identity).');
