// Run: node scripts/check-qa-eval-real.mjs (real tuning/eval scripts, no LLM calls).
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import ts from 'typescript';
import * as vue from 'vue';
const tick = () => new Promise(setImmediate);
const deferred = () => { let resolve; const promise = new Promise((r) => { resolve = r; }); return { promise, resolve }; };
function load(path, imports, expose, globals = {}) {
  const raw = readFileSync(new URL(`../src/${path}`, import.meta.url), 'utf8');
  const source = path.endsWith('.vue') ? raw.match(/<script setup lang="ts">([\s\S]*?)<\/script>/)[1] : raw;
  const { outputText } = ts.transpileModule(`${source}\n${expose}`, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } });
  const exports = {};
  runInNewContext(outputText, { exports, defineProps: () => ({}), setTimeout: () => 1, clearTimeout() {}, ...globals, require: (name) => {
    if (name.endsWith('.vue')) return {};
    assert.ok(name in imports, `Missing dependency ${name}`); return imports[name];
  } });
  return exports;
}
const context = vue.reactive({ demoAs: 'U010001', ver: null, previewAs: null });
const readonly = vue.ref(false);
const tuningTick = vue.ref(0);
const query = vue.reactive({});
const messages = [];
const confirms = [];
const started = [];
let confirmation;
let summary = { version: 'v2099.01.1', dataAsOf: '', recertCount: 0, openFeedback: 0, uncertFreq: 0, evalTotal: 2 };
let questions = { items: [], page: 1, size: 1, total: 2, groupCounts: [{ group: '履约', n: 2 }] };
const mock = { EVAL_QS: [{ group: 'MOCK' }], EVAL_GROUPS: ['MOCK'], T_METRICS: { mock: { key: 'MOCK', name: 'MOCK', version: 'v1' } }, TUNING_CUTOFF: 'MOCK', TUNING_DIMS: ['MOCK'], TUNING_TIMES: ['MOCK'], planText: () => 'plan' };
const state = { tuningTick, tuning: { certs: [], feedbacks: [], freq: [], runs: [] }, addCert: () => { throw new Error('mock create'); }, startRun: () => { throw new Error('mock run'); } };
const http = { currentApiContext: () => context, ApiError: class extends Error {} };
const catalog = load('pages/ontology/qa-tuning/catalog.ts', { vue, '@/api/qa': { qaReal: () => true }, '@/mock/qa-tuning': mock }, '');
const common = {
  './catalog': catalog, './qa-tuning/catalog': catalog,
  vue: { ...vue, onBeforeUnmount() {} }, 'vue-router': { useRoute: () => ({ query }), useRouter: () => ({ replace() {} }) },
  'ant-design-vue': { message: { error: (m) => messages.push(m), warning: (m) => messages.push(m), success: (m) => messages.push(m) } },
  '@/api/http': http, '@/mock/qa-tuning': mock,
  '@/pages/qa/qaFallback': { qaLive: vue.ref(true), qaFallBackIfDown: () => false },
  '@/mock': { paginate: (items) => ({ items, total: items.length }) },
};
const qa = {
  qaReal: () => true, qaRequestOptions: () => ({ headers: { 'X-Demo-As': context.demoAs }, readonlyParams: false, query: { ver: context.ver, previewAs: context.previewAs } }), getTuningSummary: async () => summary, listEvalQuestions: async () => questions,
  getPlanOptions: async () => ({ metrics: [{ code: 'M01', key: 'DB', name: '数据库指标', version: 'v8' }], dims: ['数据库维度'], times: ['数据库时间'] }),
  startEvalRun: async (group) => { started.push(group); return { id: 'ER-DB', scope: '履约相关 2 题' }; },
};
const page = load('pages/ontology/QaTuning.vue', {
  ...common, '@/api/qa': qa, './qa-tuning/state': state,
  '@/pages/qa/useQaSession': { useQaSession: () => ({ activeId: vue.ref('DB-SESSION') }) },
  '@/composables/useReadonly': { useReadonly: () => ({ readonly, reason: vue.ref('') }) },
  '@/stores/app': { useAppStore: () => ({ ontologyVersion: 'MOCK' }) },
  '@/stores/confirm': { useConfirmStore: () => ({ ask: (card) => { confirms.push(card); return confirmation.promise; } }) },
}, 'export { viewVer, dataCutoff, runOptions, runEval, runGroup, planOpts, evalStarting, evalTotal };');
assert.equal(page.viewVer.value, '未知');
assert.equal(page.planOpts.value.metrics.length, 0, 'real loading must not expose static metric options');
await tick();
assert.equal(page.viewVer.value, 'v2099.01.1');
assert.equal(page.dataCutoff.value, '未知');
assert.equal(catalog.metricLabel('M01'), '数据库指标 v8');
assert.equal(catalog.metricOf('missing').drawerId, undefined);
assert.match(catalog.metricLabel('mock'), /不可用/);
assert.ok(page.runOptions.value.some((g) => g.value === '履约' && g.label.includes('2')));
assert.ok(!page.runOptions.value.some((g) => g.value === 'MOCK'));
page.runGroup.value = '履约';
confirmation = deferred();
const changed = page.runEval();
assert.equal(confirms.at(-1).target, '履约相关 2 题');
assert.equal(confirms.at(-1).fields[1].value, '2 题');
context.demoAs = 'other';
confirmation.resolve(true);
await changed;
assert.equal(started.length, 0);
await tick();
confirmation = deferred();
const good = page.runEval();
confirmation.resolve(true);
await good;
assert.deepEqual(started, ['履约']);
assert.equal(page.evalStarting.value, false);
await tick();
const old = deferred();
summary = old.promise;
context.ver = 'v1';
summary = { version: 'v2100.01.1', dataAsOf: '', evalTotal: 2 };
context.ver = 'v2';
await tick();
old.resolve({ version: 'STALE', dataAsOf: 'MOCK', evalTotal: 999 });
await tick();
assert.equal(page.viewVer.value, 'v2100.01.1');
summary = { version: '', dataAsOf: '', evalTotal: 0 };
questions = { ...questions, total: 0, groupCounts: [] };
tuningTick.value++;
await tick();
await page.runEval();
assert.equal(page.evalTotal.value, 0);
assert.equal(started.length, 1, 'empty DB must not start a fake static question set');

let detail;
let detailPending;
const runCalls = [];
query.run = 'ER-1'; query.ev = 'questions';
questions = { ...questions, total: 2, groupCounts: [{ group: '履约', n: 2 }] };
const detailOf = (id, page = 1) => ({ id, status: '已完成', total: 2, passed: 1, scope: 'DB', fails: { items: [], total: 20, page, size: 12 }, kindStats: [], replayQuestions: [], replayHints: {} });
const tab = load('pages/ontology/qa-tuning/EvalTab.vue', {
  ...common, './state': state,
  '@/composables/usePagedQuery': { usePagedQuery: () => ({ page: vue.ref(1), size: vue.ref(15) }) },
  '@/api/qa': { ...qa, listEvalRuns: async () => ({ items: [], total: 0, page: 1, size: 15 }),
    getEvalRun: async (id, q) => { runCalls.push([id, q.page]); return id === 'ER-2' ? detailPending.promise : detailOf(id, q.page); } },
}, 'export { groupNames, dRemote, fpage, replayQs };');
await tick();
assert.deepEqual(Array.from(tab.groupNames.value), ['履约']);
assert.equal(tab.dRemote.value.id, 'ER-1');
tab.fpage.value = 2;
await tick();
assert.equal(tab.fpage.value, 2, 'paging must not clear the current detail and reset itself');
assert.equal(runCalls.at(-1)[1], 2);
detailPending = deferred();
query.run = 'ER-2';
await tick();
query.run = 'ER-3';
await tick();
detailPending.resolve(detailOf('ER-2'));
await tick();
assert.equal(tab.dRemote.value.id, 'ER-3');
assert.equal(tab.replayQs.value.length, 0);
// Actual CertTab: a previous context's late list cannot replace current rows.
const staleCerts = deferred();
let certReads = 0;
let certWrites = 0;
const certTab = load('pages/ontology/qa-tuning/CertTab.vue', {
  ...common, './state': state,
  '@/api/qa': { ...qa, listCerts: async () => ++certReads === 1 ? staleCerts.promise : { items: [{ id: 'DB-CERT' }], total: 1, page: 1, size: 15 },
    getCert: async () => undefined, certifyCert: async () => { certWrites++; return { status: '已认证' }; } },
  '@/stores/app': { useAppStore: () => ({ user: { name: '数据库用户' } }) },
  '@/stores/confirm': { useConfirmStore: () => ({ ask: () => confirmation.promise }) },
  '@/composables/usePagedQuery': { usePagedQuery: () => ({ page: vue.ref(1), size: vue.ref(15) }) },
}, 'export { remote, certify };');
assert.equal(certTab.remote.value.items.length, 0);
context.demoAs = 'catalog-next-user';
await tick();
assert.equal(certTab.remote.value.items[0].id, 'DB-CERT');
staleCerts.resolve({ items: [{ id: 'STALE-CERT' }], total: 1, page: 1, size: 15 });
await tick();
assert.equal(certTab.remote.value.items[0].id, 'DB-CERT');
confirmation = deferred();
const certifying = certTab.certify({ id: 'DB-CERT', question: '数据库问题', plan: { metric: 'DB', dims: [], time: '上周' }, status: '待认证' });
context.demoAs = 'after-confirm';
confirmation.resolve(true);
await certifying;
assert.equal(certWrites, 0, 'confirmation from previous identity must not write as current identity');

// FreqTab stores a real certification record and never promises an unimplemented answer fast path.
let freqCalls = 0;
const freqResult = deferred();
const freqTab = load('pages/ontology/qa-tuning/FreqTab.vue', {
  ...common, './state': state,
  '@/api/qa': { ...qa, listFreq: async () => ({ items: [], total: 0, page: 1, size: 15 }),
    certifyFreq: async () => { freqCalls++; return freqResult.promise; } },
  '@/stores/app': { useAppStore: () => ({ user: { name: '数据库用户' } }) },
  '@/stores/confirm': { useConfirmStore: () => ({ ask: (card) => { confirms.push(card); return confirmation.promise; } }) },
  '@/composables/usePagedQuery': { usePagedQuery: () => ({ page: vue.ref(1), size: vue.ref(15) }) },
}, 'export { certify, busyId };');
confirmation = deferred();
const frequency = { id: 'F-DB', question: '实际高频问题', plan: { metric: 'DB', dims: [], time: '上周' }, consistency: 0.5, hits7: 1, hits30: 2, users: 1 };
const savingFreq = freqTab.certify(frequency);
await freqTab.certify(frequency);
assert.equal(freqTab.busyId.value, 'F-DB');
assert.ok(confirms.at(-1).impact.includes('尚未直接消费认证记录'));
confirmation.resolve(true);
await tick();
assert.equal(freqCalls, 1, 'duplicate click must not send duplicate writes');
const successesBefore = messages.length;
context.demoAs = 'freq-next-user';
freqResult.resolve({ id: 'CQ-DB', status: '已认证' });
await savingFreq;
assert.equal(freqTab.busyId.value, undefined);
assert.equal(messages.length, successesBefore, 'late certification must not announce success in another identity');

// Real scope options: static failure must not pick residual demo dimensions.
let scopeKey = 'first';
let options = { scopes: [], calibers: [] };
let staticFail = true;
let optionsFail = true;
const scope = load('pages/qa/qaScope.ts', {
  vue, '@/api/rowScope': { loadMyRowScope: async () => ({ items: [{ objType: 'DB source', visible: true, unrestricted: true, conditions: [] }] }) },
  '@/pages/objects/objMeta': { metaContextKey: () => scopeKey, loadMeta: async () => null },
  '@/api/static': { isStaticReal: () => true, fetchStaticPage: async (path) => {
    if (staticFail) throw Error('down');
    return { items: path === '/dimensions' ? [{ id: 'db-dim', object: 'db-source', objectName: 'DB source', kind: '维度', group: '组织', status: '已发布', levels: [{ name: 'Area' }, { name: 'Site' }] }] : [], total: path === '/dimensions' ? 1 : 0, page: 1, size: 200 };
  } },
  '@/api/qa': { qaReal: () => true, getSessionOptions: async () => { if (optionsFail) throw Error('down'); return options; } },
  '@/mock': { QA_SCOPE_OPTIONS: [{ value: 'MOCK' }], QA_CALIBER_OPTIONS: [{ value: 'MOCK' }] },
  '@/mock/dimensions': { DIM_ITEMS: [{ id: 'org', kind: '维度', group: '组织', status: '已发布', levels: [{ name: '大区' }, { name: '门店' }], values: [{ level: '大区', name: 'STALE', synonyms: [] }] }] },
}, '');
const sc = scope.useScopeCaliber();
assert.equal(sc.scopeOpts.value.length, 0);
await scope.ensureScopeOptions(true);
assert.equal(sc.scopeOpts.value.length, 0, 'failed static load must not expose stale DIM_ITEMS');
assert.equal(sc.caliberOpts.value.length, 0);
options = { scopes: [{ value: 'DB', label: '范围：数据库范围' }], calibers: [{ value: 'DBCaliber', label: '口径：数据库口径' }] };
optionsFail = false; staticFail = false; // fallback choices require an identified, explicitly unrestricted source.
scopeKey = 'second';
await scope.ensureScopeOptions(true);
assert.equal(sc.scopeOpts.value[0].value, 'DB');
assert.equal(sc.caliberOpts.value[0].value, 'DBCaliber');
optionsFail = true; staticFail = true;
scopeKey = 'third';
await scope.ensureScopeOptions(true);
assert.equal(sc.scopeOpts.value.length, 0, 'failed W17 in new context must clear previous DB options');
assert.equal(sc.caliberOpts.value.length, 0);
console.log('QA DB catalog, unknown metric, eval counts/context and real scope empty/failure checks passed.');

// Actual AppPagination + list scripts: a pending second page must not zero total/reset to page one.
for (const [name, fn] of [['CertTab', 'listCerts'], ['FeedbackTab', 'listFeedbacks'], ['FreqTab', 'listFreq']]) {
  const pq = { page: vue.ref(1), size: vue.ref(10), overflow: vue.ref(false), defaultSize: 10 };
  const calls = []; let held, summaryCalls = 0;
  const response = q => ({ items: [{ id: `${name}-PAGE-${Math.min(q.page, Math.ceil(40/q.size))}` }], total: 40, page: Math.min(q.page, Math.ceil(40/q.size)), size: q.size });
  const list = load(`pages/ontology/qa-tuning/${name}.vue`, {
    ...common, './state': state,
    '@/api/qa': { ...qa, getTuningSummary: () => { summaryCalls++; return new Promise(() => {}); },
      [fn]: (q, options) => { calls.push({ ...q, options }); return held ? held.promise : Promise.resolve(response(q)); },
      getCert: async () => undefined, getFeedback: async () => undefined },
    '@/mock/changes': {}, '../parts/useDraftView': { useDraftView: () => ({}), allDrafts: () => [] },
    '@/stores/app': { useAppStore: () => ({ user: { name: 'DB user' } }) },
    '@/stores/confirm': { useConfirmStore: () => ({}) },
    '@/composables/usePagedQuery': { usePagedQuery: () => pq },
  }, 'export { remote, listLoading };');
  const pgProps = vue.reactive({ variant: 'full', get total() { return list.remote.value.total; } });
  load('components/AppPagination.vue', {
    vue, 'ant-design-vue': {}, '@ant-design/icons-vue': {}, '@/mock/format': {},
  }, '', { defineProps: () => pgProps, withDefaults: p => p, defineModel: name => name === 'page' ? pq.page : pq.size, defineEmits: () => () => {} });
  await tick(); assert.equal(list.remote.value.items.length, 1); assert.equal(summaryCalls, 0, `${name} must render without any global summary wait`);
  held = deferred(); pq.page.value = 2; await tick();
  assert.equal(pq.page.value, 2, `${name} must not bounce back to first page during loading`);
  assert.equal(list.remote.value.total, 40); assert.equal(list.listLoading.value, true);
  assert.equal(calls.at(-1).page, 2); assert.equal(calls.at(-1).size, 10);
  assert.equal(calls.at(-1).options.headers['X-Demo-As'], context.demoAs);
  const pending = held; held = undefined; pending.resolve(response(calls.at(-1))); await tick();
  assert.equal(list.remote.value.items[0].id, `${name}-PAGE-2`);
  pq.size.value = 5; await tick(); assert.equal(calls.at(-1).size, 5);
  pq.page.value = 999; await tick(); assert.equal(pq.page.value, 8); assert.equal(list.remote.value.items[0].id, `${name}-PAGE-8`);
}
query.ev = undefined; query.run = undefined;
let runReads = 0, questionReads = 0;
const lazy = load('pages/ontology/qa-tuning/EvalTab.vue', {
  ...common, './state': state, '@/composables/usePagedQuery': { usePagedQuery: () => ({ page: vue.ref(1), size: vue.ref(10) }) },
  '@/api/qa': { ...qa, listEvalRuns: async q => { runReads++; return { items: [], total: 0, page: 1, size: q.size }; },
    listEvalQuestions: async q => { questionReads++; return { items: [], total: 0, page: 1, size: q.size, groupCounts: [] }; } },
}, '');
await tick(); assert.equal(runReads, 1); assert.equal(questionReads, 0, 'hidden question table must not preload');
query.ev = 'questions'; await tick(); assert.equal(questionReads, 1); assert.equal(runReads, 1, 'switching tables only requests the visible table');
console.log('QA real pagination checks passed (actual pager, no zero-total reset, no summary waterfall, real page/size requests, server clamp, lazy eval views).');
