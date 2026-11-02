import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { parse, compileScript } from '@vue/compiler-sfc';
import * as vue from 'vue';
import * as vueRouter from 'vue-router';
import ts from 'typescript';

// Execute the actual page and pagination composable with a memory router; no network or DOM.
const require = createRequire(import.meta.url);
const calls = [];
const exports = [];
const context = vue.reactive({ ontologyId: '1' });
let holdNext = false, finish;
const empty = () => ({ items: [], nextCursor: null, hasMore: false });
const mocks = {
  vue, 'vue-router': vueRouter,
  'ant-design-vue': { message: { info() {}, error() {} } },
  '@/api/http': { currentApiContext: () => context, isApiError: () => false },
  '@/api/admin': {
    adminApiOn: () => true, queryAuditLogsMock: empty, mockAuditActions: () => [], fetchAuditActions: async () => [],
    queryAuditLogs: (q) => { calls.push(q); return holdNext ? (holdNext = false, new Promise(resolve => { finish = resolve; })) : Promise.resolve(empty()); },
    exportAuditLogs: async (q) => { exports.push(q); return null; },
  },
  '@/components/AppPagination.vue': {}, '@/tableColumns': { fitColumns: v => v },
  '@/composables/useReadonly': { useReadonly: () => ({ readonly: vue.ref(false), reason: vue.ref('') }) },
};
function load(path, sfc = false) {
  const source = readFileSync(new URL(`../src/${path}`, import.meta.url), 'utf8');
  const code = sfc ? compileScript(parse(source).descriptor, { id: 'audit-scheduling' }).content : source;
  const js = ts.transpileModule(code, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, esModuleInterop: true } }).outputText;
  const module = { exports: {} };
  new Function('require', 'exports', 'module', js)(id => mocks[id] ?? require(id), module.exports, module);
  return module.exports;
}
mocks['./useAdaptivePageSize'] = load('composables/useAdaptivePageSize.ts');
mocks['@/composables/usePagedQuery'] = load('composables/usePagedQuery.ts');
mocks['@/composables/useDebouncedRef'] = load('composables/useDebouncedRef.ts');
const page = load('pages/admin/AuditLog.vue', true).default;
page.render = () => null;
const router = vueRouter.createRouter({ history: vueRouter.createMemoryHistory(), routes: [{ path: '/audit', component: page }] });
await router.push('/audit?cursor=old');
const renderer = vue.createRenderer({
  createComment: () => ({}), createElement: () => ({}), createText: () => ({}),
  insert() {}, remove() {}, setText() {}, setElementText() {}, patchProp() {}, parentNode: () => null, nextSibling: () => null,
});
const app = renderer.createApp(page);
app.use(router); app.mount({});
const state = app._instance.setupState;
const flush = async () => { await vue.nextTick(); await new Promise(resolve => setTimeout(resolve, 0)); await vue.nextTick(); };
await flush();
assert.equal(calls.length, 1);
assert.equal(calls[0].cursor, 'old');
state.kw = 'a'; await vue.nextTick(); state.kw = 'alice'; await vue.nextTick();
assert.equal(calls.length, 1, 'Typing must not query before debounce');
await new Promise(resolve => setTimeout(resolve, 300)); await flush();
assert.equal(calls.length, 2, 'Settled search must query once after resetting the cursor');
assert.equal(calls[1].kw, 'alice'); assert.equal(calls[1].cursor, null);
await router.push('/audit?cursor=next'); await flush();
assert.equal(calls.length, 3, 'Cursor pagination stays immediate');
state.action = '更新'; await flush();
assert.equal(calls.length, 4, 'Dropdown filter must only fetch its first page');
assert.equal(calls[3].cursor, null); assert.equal(calls[3].action, '更新');
holdNext = true; state.result = '失败'; await flush();
context.ontologyId = '2'; await flush();
finish({ items: [{ id: 'stale' }], nextCursor: 'stale', hasMore: true }); await flush();
assert.deepEqual(state.res.items, [], 'Old workspace response must not replace the current result');
state.kw = 'bob'; await state.onExport();
assert.equal(state.searchKw, 'alice', 'Export check must occur before the list debounce settles');
assert.equal(exports[0].kw, 'bob', 'Export must use the latest typed search immediately');
state.kw = ''; await state.onExport();
assert.equal(exports[1].kw, '', 'Export must immediately use a cleared search');
app.unmount();
console.log('PASS: debounced search, one filter reset request, immediate paging, stale workspace response ignored, current search exported.');
