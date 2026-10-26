// Run: node scripts/check-real-data.mjs (no server or database writes).
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import ts from 'typescript';
import * as vue from 'vue';
function load(path, imports, expose = '', globals = {}) {
  let source = readFileSync(new URL(`../src/${path}`, import.meta.url), 'utf8');
  if (path.endsWith('.vue')) source = source.match(/<script setup lang="ts">([\s\S]*?)<\/script>/)[1];
  source = source.replaceAll('import.meta.env', '__testEnv');
  const { outputText } = ts.transpileModule(`${source}\n${expose}`, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } });
  const exports = {};
  runInNewContext(outputText, { exports, console, AbortController, __testEnv: {}, ...globals, require: (name) => {
    if (name in imports) return imports[name];
    if (name.endsWith('.vue')) return {};
    throw new Error(`Missing dependency: ${name}`);
  } });
  return exports;
}
const defer = () => { let resolve, reject; const promise = new Promise((r, j) => { resolve = r; reject = j; }); return { promise, resolve, reject }; };
let fail = false;
let list = { items: [], current: null };
let cur = { current: null };
const releases = load('api/releases.ts', {
  vue, '@/mock/releases': { RELEASES: [{ ver: 'demo' }] },
  './http': { apiClient: () => ({ isReal: () => true, get: async (path) => {
    if (fail) throw new Error('NETWORK_ERROR');
    return path.endsWith('/current') ? cur : list;
  } }) },
});
assert.equal(releases.RELEASES.length, 0);
assert.equal((await releases.loadReleases()).kind, 'empty');
list = { items: [{ ver: 'db', publishedAt: 'now', by: 'Owner', note: '' }], current: 'db' }; cur = { current: 'db', next: 'next' };
assert.equal((await releases.loadReleases()).kind, 'ok');
assert.equal(releases.RELEASES[0].ver, 'db');
fail = true; await assert.rejects(releases.loadReleases(), /NETWORK_ERROR/);
assert.equal(releases.RELEASES.length, 0, 'failed release reloads must not expose demo or stale records');

const ctx = vue.reactive({ demoAs: 'U1' });
let registeredChanges = 0;
const pending = [];
let validateCalls = 0;
let crPageReply;
const crPageCalls = [];
const workflowOptions = [], diffPageCalls = [];
const diffRows = Array.from({ length: 25 }, (_, i) => ({ key: `DB-DIFF-${i}`, type: '指标', field: `字段${i}`, before: '', after: `数据库改动${i}` }));
let heldDiff;
const messages = [];
const shared = {
  vue: { ...vue, watch() {} }, 'vue-router': { useRoute: () => ({ query: {} }), useRouter: () => ({ replace() {} }) },
  'ant-design-vue': { message: { success: (x) => messages.push(x), error: (x) => messages.push(x) } },
};
const staticApi = {
  isStaticReal: () => true, staticContextKey: () => JSON.stringify(ctx),
  fetchWorkflowRecords: (options) => {
    assert.equal(options.query?.page, undefined, 'the workflow directory must remain complete');
    workflowOptions.push(options);
    const p = defer(); pending.push(p); return p.promise;
  },
  fetchCR: async (id, options) => { assert.equal(options.query.includeDiffs, false); return { id, detail: {}, events: [] }; },
  fetchStaticPage: async (path, q) => {
    if (path.endsWith('/diffs')) {
      diffPageCalls.push({ ...q, who: ctx.demoAs });
      if (heldDiff) { const p = heldDiff; heldDiff = undefined; return p.promise; }
      const page = Math.min(q.page, Math.ceil(diffRows.length / q.size));
      return { items: diffRows.slice((page - 1) * q.size, page * q.size), total: diffRows.length, page, size: q.size };
    }
    crPageCalls.push(q); return crPageReply ?? { items: q.focusId ? [{ id: q.focusId }] : [], total: q.focusId ? 1 : 0, page: 1, size: q.size };
  },
  validateCR: async () => { validateCalls++; return { summary: 'DB checked' }; },
};
const staticPage = load('pages/ontology/parts/useStaticPage.ts', { vue: { ...vue, onBeforeUnmount() {} }, '@/api/static': staticApi });
const page = load('pages/ontology/Releases.vue', {
  '@/api/profile': { loadMyProfile: async () => ({ id: 'U-OWNER', name: 'Owner', capabilities: { publish: false, rollback: false } }) },
  '@/api/action': { invalidateActionSnapshot() {} },
  ...shared, '@/api/http': { currentApiContext: () => ctx },
  '@/api/static': staticApi, './parts/useStaticPage': staticPage,
  '@/mock/changes': { CHANGE_REQUESTS: [{ id: 'DEMO' }], ONTOLOGY_ADMINS: [], crDetail: () => ({ diffs: [{ key: 'DEMO-DETAIL' }], checks: [], impact: {}, times: {} }), reviewLog: () => [], checkCounts: () => ({}), registerChange() { registeredChanges++; } },
  '@/composables/useReadonly': { useReadonly: () => ({ readonly: vue.ref(false) }) },
  '@/stores/confirm': { useConfirmStore: () => ({}) }, '@/stores/app': { useAppStore: () => ({ user: { name: 'Owner' } }) },
}, 'export { CRS, loadCRs, crFallback, cur, recheck, detail, log, loadPage, lpage, kw, list, totalRows, approved, dpage, diffPage, drows };');
assert.equal(page.CRS.length, 0); assert.equal(page.cur.value.id, '');
const old = page.loadCRs(); const fresh = page.loadCRs();
pending[1].resolve([{ id: 'DB-CR', owner: 'Owner', status: '草稿', events: [], detail: {} }]); await fresh;
pending[0].resolve([{ id: 'STALE', events: [], detail: {} }]); await old;
assert.equal(page.CRS[0].id, 'DB-CR');
assert.equal(registeredChanges, 0, 'real CR rows never mutate the global mock registry');
assert.equal(page.detail.value.diffs.length, 0, 'partial real details do not merge mock details');
ctx.demoAs='U2';assert.equal(page.cur.value.id, '', 'prior identity CRs are hidden before reload');assert.equal(page.detail.value.diffs.length,0);ctx.demoAs='U1';
const validation = page.recheck(); await new Promise(setImmediate);
assert.equal(validateCalls, 1, 'real recheck must call the backend, not a success timer');
pending[2].resolve([{ id: 'DB-CR', owner: 'Owner', events: [], detail: {} }]); await validation;
assert.ok(messages.some((m) => m.includes('DB checked')));
const failed = page.loadCRs(); pending[3].reject(new Error('NETWORK_ERROR')); await failed;
assert.equal(page.crFallback.value, true); assert.equal(page.CRS.length, 0);
const headers = Array.from({ length: 22 }, (_, i) => ({ id: `DB-${i + 1}`, title: `CR title ${i + 1}`, status: i < 20 ? '已通过' : '草稿' }));
crPageReply = { items: headers.slice(0, 17), total: 22, page: 1, size: 17 };
const reloaded = page.loadCRs(); pending[4].resolve(headers); await reloaded;
assert.equal(page.approved.value.length, 20, 'the publication batch must include approved CRs outside the visible page');
crPageReply = { items: headers.slice(17), total: 22, page: 2, size: 17 };
page.lpage.value = 2;
await page.loadPage();
assert.equal(crPageCalls.at(-1).page, 2);
assert.equal(crPageCalls.at(-1).size, 17);
assert.equal(page.list.value.length, 5, 'a server page must not be sliced again');
assert.equal(page.totalRows.value, 22);
page.kw.value = 'server filter';
await page.loadPage();
assert.equal(crPageCalls.at(-1).kw, 'server filter');
assert.ok(workflowOptions.every((options) => options.headers['X-Demo-As'] === 'U1'));
const tick = async () => { for (let i = 0; i < 5; i++) { await Promise.resolve(); await vue.nextTick(); } };
await tick();
assert.equal(page.drows.value.length, 10); assert.equal(page.diffPage.total.value, 25);
page.dpage.value = 2; await tick();
assert.equal(diffPageCalls.at(-1).page, 2); assert.equal(diffPageCalls.at(-1).size, 10);
assert.equal(page.drows.value[0].key, 'DB-DIFF-10', 'real differences use the API page without a second slice');
const lateDiff = defer(); heldDiff = lateDiff; page.dpage.value = 3; await tick();
ctx.demoAs = 'U2'; assert.equal(page.diffPage.items.value.length, 0, 'the real composable clears another identity synchronously');
lateDiff.resolve({ items: [{ key: 'PRIVATE-OLD-DIFF' }], total: 1, page: 1, size: 10 }); await tick();
assert.equal(page.diffPage.items.value.length, 0, 'late difference pages cannot reveal the old identity');
ctx.demoAs = 'U1'; await tick();

// Actual three-tab + useStaticPage checks cover views, metric references and CR history,
// including network/403 empty states, stale contexts, page/size/filter and demo isolation.
await import('./check-object-usage-pages.mjs');
console.log('Real data checks passed (empty/error releases, stale CR loads, persisted validation, database views, no demo fallback).');


let draftContext = { demoAs: 'A' };
const draftRequests = [];
const draftRows = vue.reactive([{ id: 'DEMO' }]);
const drafts = load('pages/ontology/parts/useDraftView.ts', {
  ...shared, '@/api/http': { currentApiContext: () => draftContext, apiClient: () => ({ isReal: () => true, get: (path, options) => { const p = defer(); draftRequests.push({ path, options, ...p }); return p.promise; } }) },
  '@/mock/releases': { DRAFTS: draftRows, DRAFT_TARGETS: { DEMO: { object: ['store'] } } },
  '@/mock/changes': { CHANGE_REQUESTS: [], CURRENT_USER: 'Tester' },
  '@/composables/useReadonly': { useReadonly: () => ({ readonly: vue.ref(false), reason: vue.ref('') }) },
});
const draftOptions = vue.computed(() => drafts.allDrafts().map((d) => d.id));
assert.equal(draftOptions.value.length, 0, 'real drafts must start without example CRs');
const oldDrafts = drafts.loadDraftState();
draftContext = { demoAs: 'B' }; const freshDrafts = drafts.loadDraftState();
draftRequests[2].resolve([{ id: 'DB-DRAFT' }]); draftRequests[3].resolve({ 'DB-DRAFT': { object: ['store'] } }); await freshDrafts;
draftRequests[0].resolve([{ id: 'OLD-DRAFT' }]); draftRequests[1].resolve({ 'OLD-DRAFT': { object: ['store'] } }); await oldDrafts;
assert.equal(drafts.draftOf('object', 'store').id, 'DB-DRAFT');
assert.equal(draftOptions.value[0], 'DB-DRAFT', 'async real records must refresh already computed dropdowns');
assert.ok(draftRequests.slice(0, 2).every((r) => r.options.headers['X-Demo-As'] === 'A'));
const inFlight = drafts.loadDraftState(true);
const view = drafts.useDraftView('object', () => 'order');
draftRows.push({ id: 'NEW-DRAFT' }); view.start({ join: 'NEW-DRAFT' });
draftRequests[4].resolve([{ id: 'DB-DRAFT' }]); draftRequests[5].resolve({ 'DB-DRAFT': { object: ['store'] } }); await inFlight;
assert.equal(drafts.draftOf('object', 'order').id, 'NEW-DRAFT', 'late restores must not erase a draft created meanwhile');
draftContext = { demoAs: 'C' }; const failedDrafts = drafts.loadDraftState();
draftRequests[6].reject(new Error('NETWORK_ERROR')); draftRequests[7].resolve({}); await assert.rejects(failedDrafts);
assert.equal(drafts.allDrafts().length, 0); assert.equal(drafts.draftOf('object', 'store'), undefined);
console.log('Shared database draft checks passed (no demo registration, pinned identity, late reads and writes, failed loads).');


const gov = load('pages/ontology/parts/object/objectGov.ts', {
  '@/mock/metrics': { METRICS: [] }, '@/mock/changes': { CHANGE_REQUESTS: [{ id: 'DEMO' }], crDetail: () => ({ touches: ['object:store'], diffs: [] }) },
  '@/mock/quality': { qObjType: () => ({ name: 'Demo label' }) }, '@/mock/links': { LINK_OBJECTS: [{ id: 'store', name: 'Database Store' }] },
  '@/api/config': { isReal: () => true }, '../useDraftView': { draftOf: () => null, allDrafts: () => [] },
});
assert.equal(gov.objLabel('store'), 'Database Store'); assert.equal(gov.objLabel('new-type'), 'new-type');
assert.equal(gov.crsOf('store', []).length, 0, 'empty real change rows must not fall back to example history');
const dbChange = { id: 'ACTUAL', title: 'Actual change', updatedAt: 'now', detail: { touches: ['property:store.code'], diffs: [] } };
assert.equal(gov.crsOf('store', [dbChange])[0].cr.id, 'ACTUAL');
console.log('Object governance checks passed (database labels, metric references, real CR history, empty/error states without synthetic revisions).');

const config = load('api/config.ts', {});
assert.equal(config.API_MODE, 'real');
assert.equal(config.realModules().length, config.SWITCH_KEYS.length, 'an unconfigured start must connect every module to the backend');
const demoConfig = load('api/config.ts', {}, '', { __testEnv: { VITE_API_MODE: 'mock' } });
assert.equal(demoConfig.anyReal(), false, 'development samples require an explicit opt-in');
console.log('Default real-mode configuration checks passed.');
