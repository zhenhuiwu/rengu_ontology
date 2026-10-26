import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import ts from 'typescript';
import * as vue from 'vue';
const context = vue.reactive({ demoAs: 'admin', ver: 'v1' });
const backend = vue.reactive({ down: false });
const pending = [];
let failure;
let confirms = 0;
const dryCalls = [], notices = [];
let dryFailure;
const type = { id: 'A', name: 'Action', usedBy: ['R'], specific: [] };
function loadPage(name, real = true) {
  const api = {
    actionApi: { isReal: () => real }, actionContextKey: () => JSON.stringify(context), snapshotReady: () => true, actionDetailReady: () => true, eventDetailReady: () => true, crWriteOn: () => false,
    crWriter: () => ({}), loadWritebackSystems: async () => {}, actionTypes: () => [type], eventTypes: () => [type],
    dryRunAction: async (...args) => { dryCalls.push(args); if (dryFailure) throw dryFailure; return { passed: false, checked: 2, failed: [{ name: '真实参数', fail: '未提供参数' }] }; },
    getActionUsedBy: () => {
      if (failure) return Promise.reject(failure);
      let resolve; const promise = new Promise((r) => { resolve = r; }); pending.push(resolve); return promise;
    },
  };
  const imports = {
    vue: { ...vue, watch() {}, onScopeDispose() {} }, 'vue-router': { useRoute: () => ({ query: {} }), useRouter: () => ({}) },
    'ant-design-vue': { message: { success: m => notices.push(['success', m]), warning: m => notices.push(['warning', m]), error: m => notices.push(['error', m]) } }, '@ant-design/icons-vue': {},
    '@/api/action': api, '@/api/http': { currentApiContext: () => context, isApiError: () => false },
    '@/api/fallback': { backend, fallBackIfDown: () => false },
    '@/mock/actionEvent': { ACTION_TYPES: [type], EVENT_TYPES: [type], RULE_ACTION: {}, ruleConfirmer: () => '', withSystem: (x) => x },
    '@/mock/dynamic': { RULES: { R: { name: 'Demo', trigger: '定时' } }, ALERTS: [] }, '@/mock/extsys': {},
    '@/mock/paginate': {}, '@/mock/format': {}, '@/theme': {},
    '@/composables/useDrawer': { useDrawer: () => ({}) },
    '@/composables/usePagedQuery': { usePagedQuery: () => ({ page: vue.ref(1), size: vue.ref(12) }) },
    '@/stores/confirm': { useConfirmStore: () => ({ ask: async () => { confirms++; return true; } }) },
    './parts/useDraftView': { useDraftView: () => ({ draft: vue.ref(null), isDraft: vue.ref(false), readonly: vue.ref(true) }) },
    './useDraftEdits': { useDraftEdits: () => ({ section: () => ({ rows: vue.ref([]) }) }) },
  };
  const source = readFileSync(new URL(`../src/pages/ontology/${name}.vue`, import.meta.url), 'utf8').match(/<script setup lang="ts">([\s\S]*?)<\/script>/)[1];
  const expose = name === 'ActionTypes' ? 'loadUsedBy, usedState, usedBy, dryRun, dryRunning' : 'simulate';
  const { outputText } = ts.transpileModule(`${source}\nexport { ${expose} };`, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } });
  const exports = {};
  runInNewContext(outputText, { exports, require: (key) => {
    if (key in imports) return imports[key];
    if (key.endsWith('.vue')) return {};
    throw new Error(`Missing boundary: ${key}`);
  } });
  return exports;
}
const page = loadPage('ActionTypes');
const old = page.loadUsedBy();
assert.equal(page.usedState.loading, true);
context.ver = 'v2';
const current = page.loadUsedBy();
pending[1]([{ ruleId: 'REAL', ruleName: 'Current version', alerts: 3, openAlerts: 1 }]); await current;
pending[0]([{ ruleId: 'OLD', ruleName: 'Old private data' }]); await old;
assert.equal(page.usedBy.value[0].id, 'REAL');
assert.equal(page.usedState.loading, false);
context.demoAs = 'viewer';
assert.equal(page.usedBy.value.length, 0);
failure = new Error('403 FORBIDDEN'); await page.loadUsedBy();
assert.match(page.usedState.error, /403/);
assert.equal(page.usedBy.value.length, 0);
failure = new Error('404 NOT_FOUND'); await page.loadUsedBy();
assert.match(page.usedState.error, /404/);
assert.equal(page.usedBy.value.length, 0, 'business failures never return demo relationships');
backend.down = true;
assert.equal(page.usedBy.value.length, 0, 'unavailable backend must not restore demo relationships in real mode');
await page.dryRun(); await loadPage('EventTypes').simulate();
assert.equal(confirms, 1, 'real dry-run confirms actual read-only checking; simulate is blocked in read-only context');
assert.equal(dryCalls.length, 1);
assert.equal(JSON.stringify(dryCalls[0][1]), '{}', 'no sample defaults may impersonate actual parameters');
assert.equal(dryCalls[0][4].headers['X-Demo-As'], 'viewer');
assert.equal(dryCalls[0][4].query.ver, context.ver);
assert.ok(notices.some(([kind, m]) => kind === 'warning' && m.includes('2') && m.includes('未提供参数')));
assert.ok(!notices.some(([kind]) => kind === 'success'), 'failed/missing context cannot become all-passed');
dryFailure = new Error('REAL_503');
await page.dryRun();
assert.ok(notices.some(([kind, m]) => kind === 'error' && m.includes('REAL_503')));
const before = dryCalls.length;
const lateConfirm = page.dryRun();
context.demoAs = 'changed-before-confirm';
await lateConfirm;
assert.equal(dryCalls.length, before, 'a confirmation from an old context cannot run as the new identity');
assert.equal(page.dryRunning.value, false);
assert.equal(loadPage('ActionTypes', false).usedBy.value[0].name, 'Demo');
console.log('Action used-by and real dry-run checks passed (scope, honest failures, no samples, stale confirmation, read-only simulate).');
