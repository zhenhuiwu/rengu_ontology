// Run: node scripts/check-quality-integration.mjs. Execute the real SFC with HTTP boundary stubs.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import ts from 'typescript';
import * as vue from 'vue';
const context = vue.reactive({ demoAs: 'admin', ver: '' });
const route = vue.reactive({ query: {} });
const readonly = vue.ref(false);
const pending = [];
let failure;
let creates = 0; const updates=[], retires=[];
let runs = 0;
const messages = [];
const meta = { objects: [{ key: 'order', name: 'Order', kinds: ['新鲜度'] }] };
const demo = { id: 'DEMO', kind: '新鲜度', result: '通过', history: [], typeKey: 'order', lastRun: '' };
const imports = {
  vue: { ...vue, watch() {} }, 'vue-router': { useRoute: () => route, useRouter: () => ({ replace() {} }) },
  'ant-design-vue': { message: { success: (x) => messages.push(x), error: (x) => messages.push(x) } },
  '@/stores/confirm': { useConfirmStore: () => ({ ask: async () => true }) },
  '@/composables/useUnsavedGuard': { useUnsavedGuard() {}, confirmDiscard: async () => true },
  '@/composables/useDrawer': { useDrawer: () => ({}) },
  '@/composables/useReadonly': { useReadonly: () => ({ readonly }) },
  '@/composables/usePagedQuery': { usePagedQuery: () => ({ page: vue.ref(1), size: vue.ref(15) }) },
  '@/api/static': { fetchCR: async()=>({lockVersion:4}) },
  '@/api/http': { currentApiContext: () => context },
  '@/mock': { paginate: (items) => ({ items, total: items.length }) },
  '@/mock/quality': { QUALITY_CHECKS: [demo], Q_OBJECT_TYPES: [], DATA_CUTOFF: 'DEMO DATE' },
  '@/api/quality': {
    isQualityReal: () => true, fetchQualityMeta: async () => meta,
    fetchQualityChecks: () => { if (failure) return Promise.reject(failure); let resolve; const p = new Promise((r) => { resolve = r; }); pending.push(resolve); return p; },
    fetchQualityRuns: async () => ({ items: [{ id: 'RUN', result: '失败' }], total: 1 }),
    updateQualityCheck:async(id,body)=>{updates.push({id,body});throw Error('409 VERSION_CONFLICT');},
    retireQualityCheck:async(id,body)=>{retires.push({id,body});throw Error('409 VERSION_CONFLICT');},
    createQualityCheck: async () => { creates++; throw new Error('422 invalid threshold'); },
    runQualityCheck: async () => { runs++; return { completed: true }; }, runAllQualityChecks: async () => { runs++; return { completed: true }; },
  },
};
const source = readFileSync(new URL('../src/pages/data/DataQuality.vue', import.meta.url), 'utf8').match(/<script setup lang="ts">([\s\S]*?)<\/script>/)[1];
const { outputText } = ts.transpileModule(`${source}\nexport { loadList, loadHistory, state, checks, totalCount, selected, hist, runNow, openNew, nc, ncOpen, saveNew, saveDefinition, openEdit, editingCheck, changeOpen, deleteCheck };`, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } });
const page = {};
runInNewContext(outputText, { exports: page, require: (key) => key in imports ? imports[key] : key.endsWith('.vue') ? {} : (() => { throw Error(key); })() });
assert.equal(page.checks.value.length, 0, 'real mode never starts with demo checks');
const old = page.loadList(); context.demoAs = 'viewer'; const current = page.loadList();
const result = (id) => ({ items: [{ ...demo, id }], total: 1, dataCutoff: 'REAL', summary: { total: 1, passed: 0, failed: 1, unrun: 0, lastRun: 'REAL' } });
pending[1](result('REAL')); await current;
pending[0](result('OLD')); await old;
assert.equal(page.checks.value[0].id, 'REAL');
await page.loadHistory(); assert.equal(page.hist.value.items[0].id, 'RUN');
context.ver = 'v2';
assert.equal(page.checks.value.length, 0); assert.equal(page.totalCount.value, 0); assert.equal(page.hist.value.items.length, 0);
failure = new Error('403 FORBIDDEN'); await page.loadList();
assert.equal(page.checks.value.length, 0); assert.match(page.state.error, /403/);
failure = new Error('Network down'); await page.loadList();
assert.equal(page.checks.value.length, 0, 'unavailable backend must not return demo health');
readonly.value = true; await page.runNow(true); assert.equal(runs, 0);
readonly.value = false; failure=undefined; const formLoad=page.loadList(); pending.at(-1)(result('REAL')); await formLoad; page.openNew(); page.nc.name = 'Preserve my input'; page.nc.threshold = 'bad'; await page.saveNew(); assert.equal(page.changeOpen.value,true); await page.saveDefinition({title:'Quality CR'});
assert.equal(creates, 1); assert.equal(page.ncOpen.value, true); assert.equal(page.nc.name, 'Preserve my input');
assert.ok(messages.some((x) => x.includes('422')));
console.log('Quality integration checks passed (no demo fallback, stale identities/versions, history, readonly writes, failed draft preservation).');

const stored={...demo,id:'QC-DB',name:'Published name',threshold:'≤ 1 小时',schedule:'手动',hasPublished:true,pending:{crId:'CR-DB',lockVersion:7,status:'草稿',isNew:false,definition:{name:'Saved draft',typeKey:'order',kind:'新鲜度',threshold:'≤ 2 小时',schedule:'手动'}}};
page.openEdit(stored);assert.equal(page.nc.name,'Saved draft','editor restores persisted CR data');page.nc.name='Unsaved user input';await page.saveNew();assert.equal(updates.length,1);assert.equal(updates[0].body.lockVersion,7);assert.equal(updates[0].body.crId,'CR-DB');assert.equal(page.nc.name,'Unsaved user input');assert.equal(page.ncOpen.value,true);assert.equal(stored.threshold,'≤ 1 小时','failed draft write cannot mutate live runtime definition');
await page.deleteCheck(stored);assert.equal(retires[0].body.lockVersion,7);assert.equal(stored.retired,undefined,'delete only stages a CR and never retires locally');
page.state.items=[{...stored,hasPublished:false}];page.state.key=JSON.stringify([JSON.stringify(context),undefined,undefined,'全部',1,15]);const beforeRuns=runs;await page.runNow(false);assert.equal(runs,beforeRuns,'unpublished new drafts cannot run');

const oldTotal=page.state.total;const waitingPage=page.loadList();assert.equal(page.state.total,oldTotal,'same-context quality paging retains total');pending.at(-1)(result('REAL'));await waitingPage;
