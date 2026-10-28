// Real Vue watchers: runtime pages request each page and discard responses from old contexts.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import ts from 'typescript';
import * as vue from 'vue';
const actionReview = {};
runInNewContext(ts.transpileModule(readFileSync(new URL('../src/pages/ops/actionReview.ts', import.meta.url), 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText, { exports: actionReview });
import { parse, compileTemplate } from '@vue/compiler-sfc';

const deferred = () => { let resolve, reject; const promise = new Promise((a, b) => { resolve = a; reject = b; }); return { promise, resolve, reject }; };
const ctx = vue.reactive({ user: 'DB-admin', ver: '', previewAs: '' });
const contextKey = () => JSON.stringify(ctx);
const route = vue.reactive({ query: {} });
const errors = [], requests = [];
const tpl = { code: 'db', engine: 'db', scope: [], drivers: [], groups: [], impact: { columns: [] }, metrics: [] };
const row = (id) => ({ id, name: id, action: id, status: '草稿', owner: '数据库用户', createdAt: '2026-10-26 12:00:00', source: { type: '手动' }, objKind: 'product', dims: [], staged: [], scope: {}, drivers: {} });
const list = (query) => { const task = deferred(); requests.push({ ...task, query: { ...query }, ctx: contextKey() }); return task.promise; };
const dynamic = {
  dynamicApi: { isReal: () => true }, dynamicContextKey: contextKey,
  listAlerts: list, listActionRuns: list, listScenarios: list,
  listPeople: async () => [], getAlertMeta: async () => ({ rules: [] }),
  getActionMeta: async () => ({ actionNames: [], actionSystems: [], actionMeta: {} }),
  getScenariosMeta: async () => ({ executorConfigured: false, templates: [tpl] }),
  getAlert: async (id) => ({ ...row(id), log: [], rule: null }),
  getActionRun: async (id) => ({ ...row(id), log: [] }), getScenario: async (id) => row(id),
  listScenarioImpacts: async () => ({ items: [], total: 0 }),
};
const imports = {
  './actionReview': actionReview,
  vue: { ...vue, onBeforeUnmount: () => {} },
  'vue-router': { useRoute: () => route, useRouter: () => ({ replace: () => {} }), onBeforeRouteUpdate: () => {} },
  'ant-design-vue': { message: { error: (e) => errors.push(e), warning: () => {}, success: () => {} } },
  '@ant-design/icons-vue': {}, '@/api/dynamic': dynamic, '@/api/http': { isApiError: () => false },
  '@/api/static': { getObjectTypeFacets: async () => { throw Error('unconfigured engine must not load facets'); } },
  '@/stores/app': { useAppStore: () => ({ user: { name: '数据库用户' } }) },
  '@/stores/confirm': { useConfirmStore: () => ({ ask: async () => false }) },
  '@/composables/useReadonly': { useReadonly: () => ({ readonly: vue.ref(false), reason: vue.ref('') }) },
  '@/composables/usePagedQuery': { usePagedQuery: (opts) => ({ page: vue.ref(1), size: vue.ref(opts.defaultSize), defaultSize: opts.defaultSize }) },
  '@/composables/useUnsavedGuard': { useUnsavedGuard: () => {}, confirmDiscard: async () => true },
  '@/theme': { BRAND: '#123', GRAY: {} },
  '@/mock': { ALERTS: [], RULES: {}, METRICS: [], alertTimeline: () => [] },
  '@/mock/dynamic': { PEOPLE: [], alertDims: () => [] },
  '@/mock/paginate': { paginate: () => { throw Error('real mode cannot paginate local mock data'); } },
  '@/mock/actions': { ACTIONS: [], ACTION_NAMES: [], ACTION_SYSTEMS: [], ACTION_META: {}, OBJ_KIND_LABEL: {} },
  '@/mock/scenarios': { SCENARIOS: [], scenarioById: () => undefined },
  '@/mock/scenarioTemplates': { SC_TEMPLATES: [tpl], SC_ENGINES: { db: {} }, templateOf: () => tpl, allFields: () => [], defaultsOf: () => ({}), noDriver: () => false },
};
function evaluate(source, mapping) {
  const { outputText } = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } });
  const exports = {};
  runInNewContext(outputText, { exports, AbortController, window: { setTimeout }, require: (name) => {
    if (name in mapping) return mapping[name];
    if (name.endsWith('.vue')) return {};
    throw Error(`Missing dependency ${name}`);
  } });
  return exports;
}
const settle = async () => { await vue.nextTick(); await new Promise(setImmediate); };
const complete = (task, id) => task.resolve({ items: [row(id)], total: 35, page: task.query.page, size: task.query.size, counts: {} });
for (const name of ['Alerts', 'Actions', 'Scenarios']) {
  requests.length = 0; route.query = {}; ctx.user = 'DB-admin';
  const source = readFileSync(new URL(`../src/pages/ops/${name}.vue`, import.meta.url), 'utf8');
  const { descriptor } = parse(source);
  assert.equal(compileTemplate({ source: descriptor.template.content, filename: name, id: name }).errors.length, 0);
  assert.ok(!descriptor.template.content.includes('count(t.key)'), `${name}: tabs have no trailing counts`);
  if (name === 'Scenarios') {
    assert.ok(!descriptor.template.content.includes('{{ lockReason }}'));
    assert.ok(!descriptor.template.content.includes('{{ executorUnavailable }}'));
  }
  const scope = vue.effectScope();
  const page = scope.run(() => evaluate(`${descriptor.scriptSetup.content}\nexport { remote, pq, kw };`, imports));
  const state = () => name === 'Scenarios' ? page.remote.list : page.remote;
  assert.equal(requests[0].query.size, 10);
  complete(requests[0], 'first'); await settle();
  page.pq.page.value = 2; await settle();
  assert.equal(requests.at(-1).query.page, 2);
  assert.equal(state().total, 35, 'loading another page must retain total to avoid page-1 clamp');
  complete(requests.at(-1), 'second'); await settle();
  assert.equal(state().items[0].id, 'second');
  page.pq.page.value = 3; await settle(); const oldPage = requests.at(-1);
  page.pq.page.value = 4; await settle(); complete(requests.at(-1), 'latest'); await settle();
  complete(oldPage, 'late-old-page'); await settle(); assert.equal(state().items[0].id, 'latest');
  page.kw.value = '数据库过滤值'; await settle();
  assert.equal(requests.at(-1).query.kw, '数据库过滤值'); const oldContext = requests.at(-1);
  ctx.user = 'DB-viewer'; await settle();
  assert.equal(state().items.length, 0);
  complete(oldContext, 'old-user-data'); await settle(); assert.equal(state().items.length, 0);
  complete(requests.at(-1), 'new-user-data'); await settle(); assert.equal(state().items[0].id, 'new-user-data');
  ctx.ver = 'history'; await settle(); assert.ok(requests.at(-1).ctx.includes('history'));
  complete(requests.at(-1), 'history-data'); await settle();
  ctx.previewAs = 'DB-other'; await settle(); assert.ok(requests.at(-1).ctx.includes('DB-other'));
  requests.at(-1).reject(Error('数据库不可达')); await settle();
  assert.equal(state().items.length, 0); assert.match(errors.at(-1), /数据库不可达/);
  scope.stop(); ctx.ver = ''; ctx.previewAs = '';
}

// Execute the actual API adapter: filters/page/size reach HTTP, stale writes cannot update a new context.
const calls = []; let write;
const api = evaluate(readFileSync(new URL('../src/api/dynamic.ts', import.meta.url), 'utf8').replace('import.meta.env.VITE_DYNAMIC_CR_WRITE', "'0'"), {
  './http': { currentApiContext: () => ({ ...ctx }), isApiError: () => false, apiClient: () => ({
    get: async (path, opts) => { calls.push({ path, ...opts }); return { items: [] }; },
    post: () => { write = deferred(); return write.promise; },
  }) }, '@/mock/releases': { DRAFTS: [] }, './static': { registerDraft: () => {} },
});
await api.listAlerts({ status: 'all', level: '高', ruleId: 'R-db', kw: ' x ', page: 2, size: 10 });
await api.listActionRuns({ tab: 'records', status: '成功', action: 'DB动作', sysId: 'DB系统', kw: ' y ', page: 3, size: 10 });
await api.listScenarios({ status: '已完成', kw: ' z ', page: 4, size: 10 });
assert.equal(calls[0].query.ruleId, 'R-db'); assert.equal(calls[0].query.page, 2);
assert.equal(calls[1].query.page, 3); assert.equal(calls[1].query.sysId, 'DB系统');
assert.equal(calls[2].query.page, 4); assert.equal(calls[2].query.kw, 'z');
assert.ok(calls.every((call) => call.query.size === 10));
const staleWrite = api.claimAlert('DB-alert'); ctx.user = 'other'; write.resolve({ id: 'DB-alert' });
await assert.rejects(staleWrite, { name: 'DynamicContextChanged' });
console.log('dynamic pagination, filters, context and display checks passed');
