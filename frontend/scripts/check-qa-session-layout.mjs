// Run: node scripts/check-qa-session-layout.mjs. Actual SFC/API, no HTTP or DB writes.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import ts from 'typescript';
import * as vue from 'vue';
const source = readFileSync(new URL('../src/pages/qa/components/QaSessionPanel.vue', import.meta.url), 'utf8');
const script = source.match(/<script setup lang="ts">([\s\S]*?)<\/script>/)[1];
function deferred() { let resolve, reject; const promise = new Promise((r, j) => { resolve = r; reject = j; }); return { promise, resolve, reject }; }
async function settle() { for (let i = 0; i < 8; i++) { await Promise.resolve(); await vue.nextTick(); } }
function setup(real = true) {
  const context = vue.reactive({ demoAs: 'admin', ver: '', previewAs: '' });
  const active = vue.ref('S7'), state = { total: 104, height: 540, rowHeight: 30, next: null, shortcuts: null };
  const requests = [], errors = [], mounted = [], unmounted = [], observers = [];
  const data = Array.from({ length: 104 }, (_, i) => ({ id: `S${i + 1}`, title: `DB session ${i + 1}`, date: '2026-10-25' }));
  const refs = { sessions: vue.ref([...data]), activeId: active, live: vue.ref(real), real, detail: vue.ref(null), listTick: vue.ref(0), favTick: vue.ref(0) };
  const result = q => ({ items: data.slice((q.page - 1) * q.size, Math.min(q.page * q.size, state.total)), total: state.total });
  const api = {
    listSessions: (q, options) => { requests.push({ q: { ...q }, options }); if (state.next) { const d = state.next; state.next = null; return d.promise; } return Promise.resolve(result(q)); },
    listFavorites: () => state.shortcuts?.promise ?? Promise.resolve({ items: [{ question: 'DB favorite' }] }),
    listPagesBrief: () => state.shortcuts?.promise ?? Promise.resolve({ items: [{ name: 'DB page' }] }),
    createSession: () => { throw Error('Must not create sessions'); }, askTurn: () => { throw Error('Must not invoke a model'); },
  };
  class ResizeObserver { constructor(cb) { this.cb = cb; this.targets = []; observers.push(this); } observe(el) { this.targets.push(el); } disconnect() { this.targets = []; } }
  const params = { sessQ: vue.ref(''), sessRange: vue.ref('all'), sessPage: vue.ref(1) };
  const deps = {
    vue: { ...vue, onMounted: cb => mounted.push(cb), onBeforeUnmount: cb => unmounted.push(cb) },
    'ant-design-vue': { message: { error: m => errors.push(m), success() {} } }, '@ant-design/icons-vue': {},
    '@/mock': { QA_TODAY: '2026-10-25', QA_FAVORITES: ['Mock favorite'], QA_MY_PAGES: ['Mock page'] },
    '../useQaSession': { useQaSession: () => refs, errText: String }, '../qaFallback': { qaFallBackIfDown: () => false },
    '@/api/qa': api, '@/api/http': { currentApiContext: () => context }, '../useQueryParam': { useQueryParam: key => params[key], useQueryPage: key => params[key] },
    '@/composables/useReadonly': { useReadonly: () => ({ readonly: vue.ref(false) }) }, '@/composables/usePreview': { usePreview: () => ({}) }, '../qaScope': { useScopeCaliber: () => ({}) },
  };
  const exports = {}, scope = vue.effectScope();
  const { outputText } = ts.transpileModule(`${script}\nexport { pageSize, page, active, rows, total, listEl, fitRows, load, loadShortcuts, favTop, pageTop };`, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } });
  scope.run(() => runInNewContext(outputText, { exports, ResizeObserver, getComputedStyle: () => ({ lineHeight: '22px' }), defineEmits: () => () => {}, require: key => key in deps ? deps[key] : key.endsWith('.vue') ? {} : (() => { throw Error(key); })() }));
  const row = { getBoundingClientRect: () => ({ height: state.rowHeight }) };
  exports.listEl.value = { getBoundingClientRect: () => ({ height: state.height }), querySelector: () => exports.rows.value.length ? row : null };
  return { ...exports, state, requests, errors, observers, context, active, refs, result, mount: () => mounted.forEach(cb => cb()), close: () => { unmounted.forEach(cb => cb()); scope.stop(); } };
}
const p = setup(); await settle(); p.mount(); await settle();
assert.equal(p.pageSize.value, 18); assert.equal(p.requests.at(-1).q.size, 18); assert.equal(p.rows.value.length, 18); assert.equal(p.active.value, 'S7');
p.state.height = 90; p.observers[0].cb([]); await settle();
assert.equal(p.pageSize.value, 3); assert.equal(p.page.value, 3); assert.equal(p.rows.value[0].id, 'S7', 'resize keeps visible selection on its new page');
p.state.rowHeight = 45; p.observers[0].cb([]); await settle(); assert.equal(p.pageSize.value, 2, 'font/zoom remeasures actual row'); assert.equal(p.active.value, 'S7');
p.state.height = 99999; p.fitRows(); await settle(); assert.equal(p.pageSize.value, 200); assert.equal(p.requests.at(-1).q.size, 200);
const oldResize = deferred(); p.state.next = oldResize; p.state.height = 180; p.fitRows(); await settle();
const newResize = deferred(); p.state.next = newResize; p.state.height = 270; p.fitRows(); await settle();
newResize.resolve(p.result(p.requests.at(-1).q)); await settle(); const currentIds = p.rows.value.map(r => r.id).join();
oldResize.resolve({ items: [{ id: 'STALE-SIZE', title: 'stale' }], total: 999 }); await settle(); assert.equal(p.rows.value.map(r => r.id).join(), currentIds); assert.equal(p.total.value, 104);
const oldIdentity = deferred(); p.state.next = oldIdentity; const previous = p.load(); p.context.demoAs = 'viewer'; p.context.ver = 'v-old'; p.context.previewAs = 'U2';
assert.equal(p.rows.value.length, 0, 'old identity rows disappear before watchers run'); await settle();
assert.equal(p.requests.at(-1).options.headers['X-Demo-As'], 'viewer'); assert.equal(p.requests.at(-1).options.query.ver, 'v-old'); assert.equal(p.requests.at(-1).options.query.previewAs, 'U2');
oldIdentity.reject(Error('old private failure')); await previous; assert.equal(p.errors.length, 0);
const oldSelected = deferred(); p.active.value = ''; p.state.next = oldSelected; const selecting = p.load(); p.active.value = 'S19'; oldSelected.resolve(p.result(p.requests.at(-1).q)); await selecting; assert.equal(p.active.value, 'S19');
p.page.value = 99; await settle(); assert.equal(p.page.value, Math.ceil(104 / p.pageSize.value)); assert.ok(p.rows.value.length);
p.state.total = (p.page.value - 1) * p.pageSize.value; await p.load(); await settle(); assert.equal(p.page.value, Math.ceil(p.state.total / p.pageSize.value), 'empty final page converges');
p.state.total = 0; await p.load(); await settle(); assert.equal(p.page.value, 1); assert.equal(p.rows.value.length, 0);
const oldShortcut = deferred(); p.state.shortcuts = oldShortcut; const shortcutLoad = p.loadShortcuts(); p.state.shortcuts = null; p.context.demoAs = 'another'; await settle();
oldShortcut.resolve({ items: [{ question: 'PRIVATE', name: 'PRIVATE' }] }); await shortcutLoad; assert.equal(p.favTop.value[0], 'DB favorite'); assert.equal(p.pageTop.value[0], 'DB page');
const oldUnmount = deferred(); p.state.next = oldUnmount; const leaving = p.load(); p.close(); assert.equal(p.observers[0].targets.length, 0); oldUnmount.resolve({ items: [{ id: 'LEAVING' }], total: 1 }); await leaving; assert.ok(!p.rows.value.some(r => r.id === 'LEAVING'));
const mock = setup(false); await settle(); mock.mount(); await settle(); assert.equal(mock.requests.length, 0); assert.equal(mock.rows.value.length, 18); assert.equal(mock.active.value, 'S7'); mock.close();
const calls = [], apiExports = {};
const apiSource = readFileSync(new URL('../src/api/qa.ts', import.meta.url), 'utf8');
const compiled = ts.transpileModule(apiSource, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } }).outputText;
runInNewContext(compiled, { exports: apiExports, require: key => key === './http' ? { apiClient: () => ({ get: (path, opts) => { calls.push({ path, opts }); return Promise.resolve({ items: [] }); } }) } : (() => { throw Error(key); })() });
const options = { headers: { 'X-Demo-As': '' }, readonlyParams: false, query: { ver: 'v1', previewAs: 'U2' } };
await apiExports.listSessions({ page: 2, size: 18, range: 'all' }, options); await apiExports.listFavorites({ page: 1, size: 3 }, options); await apiExports.listPagesBrief({ page: 1, size: 2 }, options);
assert.deepEqual(calls.map(x => x.opts.query.ver), ['v1', 'v1', 'v1']); assert.ok(calls.every(x => x.opts.headers['X-Demo-As'] === '' && x.opts.readonlyParams === false)); assert.equal(calls[0].opts.query.size, 18); assert.equal(calls[0].opts.query.range, undefined);
assert.match(source, /qa-ss-list \{ flex: 1; min-height: 0; overflow: hidden/); assert.match(source, /qa-ss-bottom \{ flex: none/); assert.ok(!source.includes('overflow-y: auto'));
console.log('QA session layout checks passed (actual row/height capacity, bounded API size, pinned identity, resize/selection/unmount races, pagination convergence, shortcuts, unchanged mock).');
