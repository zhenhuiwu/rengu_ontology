// Actual page watchers/loaders + paging/debounce modules with delayed route commits and reads.
// No browser, HTTP, database, model, or mutation calls.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import ts from 'typescript';
import * as vue from 'vue';

function evaluate(source, deps, globals = {}) {
  const js = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } }).outputText;
  const exports = {};
  runInNewContext(js, { exports, Error, AbortController, ...globals, require: key => { assert.ok(key in deps, key); return deps[key]; } });
  return exports;
}
const readSource = path => readFileSync(new URL(`../src/${path}`, import.meta.url), 'utf8');
async function flush() { for (let i = 0; i < 10; i++) await Promise.resolve(); await vue.nextTick(); }
function deferred() { let resolve, reject; const promise = new Promise((a, b) => { resolve = a; reject = b; }); return { promise, resolve, reject }; }

for (const name of ['Actions', 'Alerts']) {
  const cleanups = [], timers = new Map(), calls = [], errors = [], navigations = [];
  let timer = 0;
  const framework = { ...vue, onBeforeUnmount: callback => cleanups.push(callback) };
  const clock = { setTimeout: callback => { timers.set(++timer, callback); return timer; }, clearTimeout: id => timers.delete(id) };
  const route = vue.reactive({ query: { page: '2' } });
  const routing = { useRoute: () => route, useRouter: () => ({ replace: ({ query }) => { navigations.push(query); return Promise.resolve(); } }) };
  const paging = evaluate(readSource('composables/usePagedQuery.ts'), { vue: framework, 'vue-router': routing, './useAdaptivePageSize': {} });
  const debounce = evaluate(readSource('composables/useDebouncedRef.ts'), { vue: framework }, clock);
  const source = readSource(`pages/ops/${name}.vue`).match(/<script setup lang="ts">([\s\S]*?)<\/script>/)[1];
  const tree = ts.createSourceFile(name + '.ts', source, ts.ScriptTarget.Latest, true);
  const variables = new Set(['kw', 'searchKw', 'pq', 'page', 'pageSize', 'actionName', 'system', 'status', 'level', 'ruleId', 'objKind', 'dimSel',
    'listLoading', 'listSeq', 'detailSeq', 'facetSeq', 'mounted', 'listRequest', 'detailRequest', 'metaRequest', 'facetRequest', 'decisionRequest', 'peopleRequest',
    'alertQuery', 'decisionSeq', 'peopleSeq', 'transferTo', 'transferOpen', 'peopleLoading', 'peopleError']);
  const functions = new Set(['loadList', 'loadDetail', 'loadFacets', 'loadPeople', 'doTransfer']);
  const pieces = tree.statements.filter(node => {
    if (ts.isVariableStatement(node)) return node.declarationList.declarations.some(declaration => variables.has(declaration.name.getText(tree)));
    if (ts.isFunctionDeclaration(node)) return functions.has(node.name?.text);
    if (ts.isIfStatement(node)) return node.expression.getText(tree) === 'live.value';
    const text = node.getText(tree);
    return ts.isExpressionStatement(node) && (text.startsWith('onBeforeUnmount(') || text.startsWith('watch(transferOpen,') || text.includes('transferOpen.value = false; transferTo.value = undefined; closeOpen'));
  }).map(node => node.getText(tree)).join('\n');
  const context = vue.ref('scope17'), remote = vue.reactive({ items: [], total: 0, detail: null, meta: null, people: [], counts: {} });
  const selectedId = vue.computed(() => remote.items[0]?.id);
  const request = (kind, query, signal) => { const task = { kind, query: { ...query }, signal, ...deferred() }; calls.push(task); return task.promise; };
  const read = kind => calls.filter(call => call.kind === kind);
  const globals = {
    ...framework, ...clock, usePagedQuery: paging.usePagedQuery, useDebouncedRef: debounce.useDebouncedRef,
    live: vue.ref(true), REAL_API: true, dynamicContextKey: () => context.value, remote, selectedId,
    tab: vue.ref(name === 'Actions' ? 'records' : 'open'), TABS: [{ key: 'open' }, { key: 'all' }],
    toRec: value => value, toRow: value => value, toDetail: value => value,
    readFail: error => errors.push(error), isApiError: () => false,
    pinned: vue.ref(), selectedKeys: vue.ref([]), ruleOptions: vue.ref([]), facets: vue.ref(null), seen: new Map(),
    closeOpen: vue.ref(false), batchCloseOpen: vue.ref(false), actionOpen: vue.ref(false), actionParamsText: vue.ref('{}'), closeReason: vue.ref(''),
    current: vue.computed(() => remote.detail),
    listActionRuns: (query, signal) => request('list', query, signal), listAlerts: (query, signal) => request('list', query, signal),
    getActionRun: (id, signal) => request('detail', { id }, signal), getAlert: (id, signal) => request('detail', { id }, signal),
    getActionMeta: signal => request('meta', {}, signal), getAlertMeta: signal => request('meta', {}, signal),
    getAlertFacets: (query, signal) => request('facet', query, signal), listPeople: (id, signal) => request('people', { id }, signal),
    transferAlert() { throw Error('mutation must not be reached'); },
  };
  const scope = vue.effectScope(); let page;
  scope.run(() => { page = evaluate(`${pieces}\nexport { kw, searchKw, pq, loadList, loadDetail${name === 'Alerts' ? ', loadFacets, transferOpen, transferTo, peopleError, loadPeople, doTransfer' : ''} };`, {}, globals); });
  const reply = (call, id) => ({ items: [{ id }], total: 40, counts: {}, page: call.query.page });
  assert.equal(read('list').length, 1); assert.equal(read('list')[0].query.page, 2);
  read('list')[0].resolve(reply(read('list')[0], 'A')); await flush();
  for (const text of ['物', '物流']) { page.kw.value = text; await flush(); }
  assert.equal(read('list').length, 1, `${name}: keystrokes do not immediately fetch`);
  assert.equal(timers.size, 1); for (const callback of timers.values()) callback(); timers.clear(); await flush();
  assert.equal(read('list').length, 1, `${name}: new filter never reads the old page before router commits page=1`);
  route.query = navigations.splice(0).at(-1); await flush();
  assert.equal(read('list').length, 2); assert.equal(read('list').at(-1).query.page, 1); assert.equal(read('list').at(-1).query.kw, '物流');
  const oldDetail = read('detail').at(-1);
  read('list').at(-1).resolve(reply(read('list').at(-1), 'B')); await flush();
  assert.equal(oldDetail.signal.aborted, true);
  oldDetail.resolve({ id: 'A', stale: true }); read('detail').at(-1).resolve({ id: 'B' }); await flush();
  assert.equal(remote.detail.id, 'B');
  const facetCount = read('facet').length;
  for (const number of [2, 3]) { page.pq.page.value = number; route.query = navigations.splice(0).at(-1); await flush(); }
  const oldPage = read('list').at(-2), currentPage = read('list').at(-1);
  assert.equal(oldPage.signal.aborted, true); assert.equal(read('facet').length, facetCount, 'plain pagination does not reload facets');
  currentPage.resolve(reply(currentPage, 'D')); oldPage.resolve(reply(oldPage, 'C')); await flush(); assert.equal(remote.items[0].id, 'D');
  if (name === 'Alerts') {
    assert.equal(read('people').length, 0, 'selecting/paging never preloads transfer candidates');
    page.transferOpen.value = true; await flush(); assert.equal(read('people').length, 1);
    read('people').at(-1).reject(new Error('synthetic permission refusal')); await flush();
    assert.match(page.peopleError.value, /permission refusal/);
    page.transferTo.value = 'not-loaded'; await page.doTransfer();
    const retry = page.loadPeople(); const peopleRequest = read('people').at(-1);
    page.transferOpen.value = false; await flush(); assert.equal(peopleRequest.signal.aborted, true);
    peopleRequest.resolve([{ id: 'late' }]); await retry; assert.equal(remote.people.length, 0);
  }
  const count = errors.length;
  const cancelled = page.loadList(); read('list').at(-1).reject(Object.assign(new Error('cancel'), { name: 'AbortError' })); await cancelled;
  assert.equal(errors.length, count, 'abort is silent'); assert.equal(remote.items[0].id, 'D');
  const oldScope = page.loadList(), oldScopeCall = read('list').at(-1); context.value = 'scope18'; await flush();
  assert.equal(oldScopeCall.signal.aborted, true); oldScopeCall.resolve(reply(oldScopeCall, 'OLD-SCOPE')); await oldScope;
  assert.equal(remote.items.length, 0);
  for (const cleanup of cleanups) cleanup(); scope.stop();
  for (const call of calls) if (call.signal) assert.equal(call.signal.aborted, true, `${name} teardown cancels every read`);
  read('list').at(-1).resolve(reply(read('list').at(-1), 'UNMOUNTED')); await flush(); assert.equal(remote.items.length, 0);
}
console.log('Ops reads passed: debounced page reset, one committed list read, independent aborts, scope/unmount safety, lazy transfer candidates with visible failure.');
