// Run: node scripts/check-qa-session-context.mjs. Actual module, controlled promises; no model/HTTP calls.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import ts from 'typescript';
import * as vue from 'vue';
const context = vue.reactive({ demoAs: 'admin', ver: '', previewAs: '' });
const errors = [], reads = [], asks = [], creates = [];
let fallbackAsks = 0;
function deferred() { let resolve, reject; const promise = new Promise((r, j) => { resolve = r; reject = j; }); return { promise, resolve, reject }; }
const options = signal => ({ headers: { 'X-Demo-As': context.demoAs }, query: { ver: context.ver, previewAs: context.previewAs }, readonlyParams: false, signal });
const deps = {
  vue, 'ant-design-vue': { message: { error: m => errors.push(m) } }, '@/mock': { QA_SESSION_LIST: [], QA_TODAY: '2026-10-25' },
  '@/api/http': { currentApiContext: () => context, isApiError: () => false }, './qaFallback': { qaLive: vue.ref(true), qaFallBackIfDown: () => false },
  '@/api/qa': { qaReal: () => true, qaRequestOptions: options,
    getSession: (id, opts) => { const d = deferred(); reads.push({ id, opts, ...d }); return d.promise; },
    QaStreamUnsupported: class QaStreamUnsupported extends Error {},
    askTurnStream: (id, q, onEvent) => { const d = deferred(); asks.push({ id, q, onEvent, ...d }); return d.promise; },
    askTurn: (id, q, opts) => { fallbackAsks++; const d = deferred(); asks.push({ id, q, opts, ...d }); return d.promise; },
    createSession: (body, opts) => { const d = deferred(); creates.push({ body, opts, ...d }); return d.promise; },
  },
};
const source = readFileSync(new URL('../src/pages/qa/useQaSession.ts', import.meta.url), 'utf8');
const js = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } }).outputText;
const exports = {}, scope = vue.effectScope();
scope.run(() => runInNewContext(js, { exports, AbortController, require: k => { assert.ok(k in deps, k); return deps[k]; } }));
const session = exports.useQaSession();
const detail = id => ({ id, title: '新会话', scope: '', caliber: '', turns: [] });
async function tick() { await Promise.resolve(); await vue.nextTick(); await Promise.resolve(); }
session.activeId.value = 'A'; await tick(); const old = reads.at(-1); assert.equal(old.id, 'A');
session.activeId.value = 'B'; await tick(); assert.equal(old.opts.signal.aborted, true);
reads.at(-1).resolve(detail('B')); await tick(); old.resolve(detail('A')); await tick(); assert.equal(session.detail.value.id, 'B');
context.demoAs = 'viewer'; assert.equal(session.detail.value, null, 'old identity detail hides synchronously'); assert.equal(session.activeId.value, '');
session.activeId.value = 'C'; await tick(); assert.equal(reads.at(-1).opts.headers['X-Demo-As'], 'viewer');
reads.at(-1).resolve(detail('C')); await tick(); assert.equal(session.detail.value.id, 'C');
session.send('controlled question'); const lateAsk = asks.at(-1); assert.equal(session.sending.value, true);
lateAsk.onEvent({ event: 'status', data: { text: 'current phase' } }); assert.equal(session.streamStatus.value, 'current phase');
session.activeId.value = 'D'; await tick(); reads.at(-1).resolve(detail('D')); await tick();
lateAsk.onEvent({ event: 'status', data: { text: 'PRIVATE OLD PHASE' } }); assert.notEqual(session.streamStatus.value, 'PRIVATE OLD PHASE', 'late stream events cannot replace another session status');
lateAsk.reject(Error('old question failure')); await tick(); assert.equal(session.draft.value, ''); assert.equal(session.sending.value, false); assert.equal(errors.length, 0);
session.send('controlled second question'); const lateSuccess = asks.at(-1); context.ver = 'old'; context.previewAs = 'U2';
lateSuccess.resolve({ turn: { id: 'OLD-TURN' } }); await tick(); assert.equal(session.detail.value, null); assert.equal(session.sending.value, false);
session.activeId.value = 'E'; await tick(); reads.at(-1).resolve(detail('E')); await tick();
const firstCreate = session.newSessionReal('actual', 'actual'); assert.equal(await session.newSessionReal('actual', 'actual'), '', 'busy prevents duplicate creation');
session.activeId.value = 'F'; await tick(); reads.at(-1).resolve(detail('F')); await tick();
creates.at(-1).resolve(detail('CREATED-OLD')); assert.equal(await firstCreate, ''); assert.equal(session.activeId.value, 'F'); assert.equal(session.detail.value.id, 'F');
const lateDetail = session.loadDetail('F'); const pending = reads.at(-1); context.demoAs = 'other'; pending.reject(Error('old private failure')); await lateDetail; assert.equal(errors.length, 0); assert.equal(session.detail.value, null);
assert.equal(fallbackAsks, 0, 'ordinary stream errors must not trigger a duplicate synchronous question');
assert.ok(!reads.some(x => !x.opts || x.opts.readonlyParams !== false));
// Actual Workbench scope watcher: quiet synchronization sends no PATCH; late PATCH cannot replace another session.
context.ver = ''; context.previewAs = ''; session.activeId.value = 'WB'; await tick(); reads.at(-1).resolve(detail('WB')); await tick();
const patches = [], unmounts = [];
const wbSource = readFileSync(new URL('../src/pages/qa/Workbench.vue', import.meta.url), 'utf8').match(/<script setup lang="ts">([\s\S]*?)<\/script>/)[1];
const wbJs = ts.transpileModule(`${wbSource}\nexport { scope, caliber };`, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } }).outputText;
const wb = {};
const wbDeps = {
  '@/pages/ops/actionReview': { reviewFields: () => [] },
  vue: { ...vue, onMounted() {}, onBeforeUnmount: cb => unmounts.push(cb) },
  'vue-router': { useRoute: () => ({ query: {} }), useRouter: () => ({ replace() {} }) },
  'ant-design-vue': { message: { error: m => errors.push(m) } }, '@ant-design/icons-vue': {}, '@/mock': {},
  '@/composables/useDrawer': { useDrawer: () => ({}) }, '@/stores/confirm': { useConfirmStore: () => ({}) },
  './useQueryParam': { useQueryParam: () => vue.ref('') },
  '@/composables/useReadonly': { useReadonly: () => ({ readonly: vue.ref(false) }) },
  '@/composables/usePreview': { usePreview: () => ({ limited: vue.ref([]), active: vue.ref(false) }) },
  './useQaSession': { useQaSession: () => session, errText: String },
  './qaScope': { ensureScopeOptions: async () => {}, useScopeCaliber: () => ({ normScope: x => x ?? '', defaultScope: () => '', caliberOpts: vue.ref([]) }) },
  '@/pages/objects/objMeta': { metaContextKey: () => JSON.stringify(context) },
  '@/api/qa': { qaRequestOptions: options, patchSession: (id, body, opts) => { const d = deferred(); patches.push({ id, body, opts, ...d }); return d.promise; } },
};
scope.run(() => runInNewContext(wbJs, { exports: wb, require: k => k.endsWith('.vue') ? {} : (() => { assert.ok(k in wbDeps, k); return wbDeps[k]; })() }));
await tick(); await tick(); await tick(); assert.equal(patches.length, 0, 'loading/synchronizing a session is read-only');
wb.scope.value = 'actual new scope'; await tick(); assert.equal(patches.length, 1); assert.equal(patches[0].id, 'WB');
session.activeId.value = 'CURRENT'; await tick(); reads.at(-1).resolve(detail('CURRENT')); await tick();
patches[0].resolve({ ...detail('WB'), scope: 'actual new scope' }); await tick(); assert.equal(session.detail.value.id, 'CURRENT');
wb.scope.value = 'another actual scope'; await tick(); const oldPatch = patches.at(-1); context.demoAs = 'changed'; oldPatch.reject(Error('old PATCH error')); await tick(); assert.equal(errors.length, 0); assert.equal(session.detail.value, null);
unmounts.forEach(fn => fn()); scope.stop();
console.log('QA session context checks passed (cancelled GET, identity/selection guards, stale send/create/error isolation, no duplicate creation, Workbench quiet sync and late PATCH).');
