// Run: node scripts/check-functions-real.mjs. Executes actual component script with controlled APIs.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import ts from 'typescript';
import * as vue from 'vue';

const tick = () => new Promise(setImmediate);
const deferred = () => { let resolve; const promise = new Promise(r => { resolve = r; }); return { promise, resolve }; };
const ctx = vue.reactive({ demoAs: 'first', ver: null, previewAs: null });
const query = vue.reactive({ fn: 'FN-DB' });
const readonly = vue.ref(false), messages = [], posted = [];
const firstList = deferred(), firstDetail = deferred(), result = deferred();
let lists = 0, details = 0, confirmation, createFailure = false;
const functionOf = title => ({ id: 'FN-DB', name: 'actual_db', title, code: 'def actual_db(value):\n    return value * 2', inputs: [{ name: 'value', type: 'Any', desc: '' }], output: { name: 'result', type: 'Any', desc: '' }, sampleIn: {}, sampleOut: 'REGISTERED_SAMPLE', usedBy: [], timeoutS: 5, desc: '', lockVersion: 1, status: '已发布', version: 'v1.0.0', pending: { cr: 'CR-DB', crLockVersion: 9, isNew: false, definition: {} } });
const api = {
  listFunctions: async () => ++lists === 1 ? firstList.promise : { items: [functionOf('CURRENT')], total: 1, page: 1, size: 12 },
  getFunction: async () => ++details === 1 ? firstDetail.promise : functionOf('CURRENT'),
  functionVersions: async () => ({ items: [], total: 0, page: 1, size: 12 }),
  testFunction: async (id, input, options) => { posted.push({ kind: 'test', id, input, options }); return result.promise; },
  createFunction: async (body, options) => { posted.push({ kind: 'create', body, options }); if (createFailure) throw new Error('DB unavailable'); return { id: 'FN-NEW', crId: 'CR-NEW' }; },
  saveFunction: async (id, body, options) => { posted.push({ kind: 'save', id, body, options }); return { id, crId: 'CR-DB' }; },
  rollbackFunction: async () => { throw new Error('not called'); },
};
const imports = {
  vue: { ...vue, onBeforeUnmount() {} },
  'vue-router': { useRoute: () => ({ query }), useRouter: () => ({ replace: async ({ query: next }) => { Object.assign(query, next); } }) },
  'ant-design-vue': { message: Object.fromEntries(['error', 'warning', 'success'].map(k => [k, m => messages.push([k, m])])) },
  '@/api/http': { currentApiContext: () => ctx, isApiError: () => false },
  '@/api/functions': api,
  '@/api/static': { fetchCR: async () => ({ lockVersion: 42 }) },
  '@/stores/confirm': { useConfirmStore: () => ({ ask: () => confirmation.promise }) },
  '@/composables/useReadonly': { useReadonly: () => ({ readonly, reason: vue.ref('readonly') }) },
  '@/composables/useUnsavedGuard': { confirmDiscard: async () => true, useUnsavedGuard: () => action => action() },
};
const raw = readFileSync(new URL('../src/pages/functions/RealFunctions.vue', import.meta.url), 'utf8');
const script = raw.match(/<script setup lang="ts">([\s\S]*?)<\/script>/)[1];
const output = ts.transpileModule(script + '\nexport { items, fn, form, formDirty, save, persist, intent, intentContext, testInput, testOut, testError, runTest, saving, changeOpen, total, lpage, loadList, versions, versionTotal, vpage, loadVersions };', { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } }).outputText;
const page = {};
runInNewContext(output, { exports: page, require: name => { if (name.endsWith('.vue')) return {}; assert.ok(name in imports, name); return imports[name]; } });
assert.equal(page.items.value.length, 0, 'real initial state must not use mock functions');
assert.equal(page.fn.value, undefined);
ctx.demoAs = 'current';
await tick();
assert.equal(page.items.value[0].title, 'CURRENT');
firstList.resolve({ items: [functionOf('STALE')], total: 1, page: 1 });
firstDetail.resolve(functionOf('STALE'));
await tick();
assert.equal(page.fn.value.title, 'CURRENT');
page.testInput.value = '{"value": 3}';
const testing = page.runTest();
await page.runTest();
assert.equal(posted.length, 1, 'busy blocks duplicate execution');
assert.equal(posted[0].options.headers['X-Demo-As'], 'current');
ctx.demoAs = 'next';
await tick();
result.resolve({ output: 6, ms: 1, at: 'REAL_TIME' });
await testing;
assert.equal(page.testOut.value, null, 'late result cannot appear for a different identity');
readonly.value = true;
await page.runTest();
assert.equal(posted.length, 1);
readonly.value = false;
query.form = 'edit';
await tick();
page.form.title = 'Changed';
confirmation = deferred();
const saving = page.save();
await page.save();
assert.equal(page.saving.value, true);
ctx.demoAs = 'changed-during-confirm';
confirmation.resolve(true);
await saving; await tick();
assert.equal(posted.filter(x => x.kind === 'save').length, 0, 'old confirmation cannot write with new identity');
query.form = 'new';
await tick();
Object.assign(page.form, { name: 'new_actual', title: 'New', code: 'def new_actual():\n    return 8' });
await page.save();
assert.equal(page.changeOpen.value, true);
assert.ok(page.intent.value.fresh);
await page.persist(page.intent.value, { title: 'New CR', desc: 'Actual reason' });
const created = posted.find(x => x.kind === 'create');
assert.equal(created.body.crId, undefined, 'fresh function must not reuse selected function pending CR');
assert.equal(created.body.reason, 'Actual reason');
query.form = 'new';
await tick();
Object.assign(page.form, { name: 'failure', title: 'Keep', code: 'def failure():\n    return 1' });
await page.save(); createFailure = true;
await page.persist(page.intent.value, { title: 'Fail CR' });
assert.equal(query.form, 'new');
assert.equal(page.form.title, 'Keep');
assert.equal(page.formDirty.value, true);
assert.ok(messages.some(([kind, text]) => kind === 'error' && text.includes('DB unavailable')));
assert.equal(page.saving.value, false);
// Same-scope pages keep the known count until the requested SQL page arrives.
const listPages=[], versionPages=[];
let listDelay, versionDelay;
api.listFunctions=async (q) => { listPages.push(q);if(listDelay)return listDelay.promise;return {items:Array.from({length:Math.min(10,23-(q.page-1)*10)},(_,i)=>({...functionOf('PAGED'),id:`PAGE-${(q.page-1)*10+i}`})),total:23,page:q.page,size:q.size}; };
api.functionVersions=async (_id,options) => {const q=options.query;versionPages.push(q);if(versionDelay)return versionDelay.promise;return {items:Array.from({length:Math.min(10,23-(q.page-1)*10)},(_,i)=>({ver:`v1.0.${(q.page-1)*10+i}`,status:'历史'})),total:23,page:q.page,size:q.size};};
await page.loadList();await page.loadVersions();
assert.equal(page.total.value,23);assert.equal(page.versionTotal.value,23);
listDelay=deferred();page.lpage.value=2;await tick();
assert.equal(listPages.at(-1).page,2);assert.equal(listPages.at(-1).size,10);
assert.equal(page.total.value,23);assert.equal(page.items.value.length,0);
const waitedList=listDelay;listDelay=undefined;waitedList.resolve({items:[{...functionOf('PAGE TWO'),id:'PAGE-10'}],total:23,page:2,size:10});await tick();
assert.equal(page.lpage.value,2);assert.equal(page.items.value[0].id,'PAGE-10');
versionDelay=deferred();page.vpage.value=2;await tick();
assert.equal(versionPages.at(-1).page,2);assert.equal(versionPages.at(-1).size,10);
assert.equal(page.versionTotal.value,23);assert.equal(page.versions.value.length,0);
const waitedVersion=versionDelay;versionDelay=undefined;waitedVersion.resolve({items:[{ver:'v1.0.10',status:'历史'}],total:23,page:2,size:10});await tick();
assert.equal(page.vpage.value,2);assert.equal(page.versions.value[0].ver,'v1.0.10');
// A new identity must clear old private counts while new results are pending.
listDelay=deferred();versionDelay=deferred();ctx.demoAs='new-functions-identity';await tick();
assert.equal(page.total.value,0);assert.equal(page.versionTotal.value,0);
listDelay.resolve({items:[],total:0,page:1,size:10});versionDelay.resolve({items:[],total:0,page:1,size:10});listDelay=undefined;versionDelay=undefined;await tick();
assert.equal(page.lpage.value,1);assert.equal(page.vpage.value,1);
console.log('Real function pagination/count/context, DB empty/loading, no fake result, readonly, busy and write-failure checks passed.');
