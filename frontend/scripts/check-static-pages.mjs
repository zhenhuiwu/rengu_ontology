// Run: node scripts/check-static-pages.mjs. Real 5 Body scripts + actual page helper; no HTTP/DB writes.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import ts from 'typescript';
import * as vue from 'vue';
const read = p => readFileSync(new URL(`../src/${p}`, import.meta.url), 'utf8');
const context = vue.reactive({ demoAs: 'DB-user', ver: '', previewAs: '' });
const key = () => JSON.stringify(context);
const pending = []; const notices = [];
const tick = async () => { for (let i = 0; i < 8; i++) { await Promise.resolve(); await vue.nextTick(); } };
function execute(path, deps, names = '', globals = {}) {
  let source = read(path); if (path.endsWith('.vue')) source = source.match(/<script setup lang="ts">([\s\S]*?)<\/script>/)[1];
  const js = ts.transpileModule(`${source}\n${names ? `export { ${names} };` : ''}`, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } }).outputText;
  const exports = {}; runInNewContext(js, { exports, structuredClone, AbortController, setTimeout, clearTimeout, ...globals, require: name => name.endsWith('.vue') ? {} : name in deps ? deps[name] : (() => { throw Error(`Missing ${name}`); })() }); return exports;
}
const head = (kind, i) => ({ id: `${kind}${i}`, name: `DB ${kind}${i}`, api: `api_${kind}${i}`, group: 'DB group', status: '已发布', props: 3, count: 'actual', version: 'v2', owner: 'DB owner', unit: '件', type: '术语', synonyms: [], levels: [{ name: 'level', api: 'lv', source: 'DB', count: 3 }], kind: '维度', objectName: 'DB object', valueCount: 35 });
const value = i => ({ key: `v${i}`, code: `v${i}`, name: `value ${i}`, level: 'level', parent: '', synonyms: [], status: '启用', note: '' });
const property = i => ({ api: `p${i}`, name: `property ${i}`, type: 'string', group: 'DB', source: `col${i}`, desc: '', sensitive: false });
const mapping = i => ({ key: `map${i}`, phrase: `phrase${i}`, targets: [`value ${i}`], match: '精确', source: '业务知识', hits: null, conflictText: '' });
const dim = { ...head('D', 1), object: 'O1', attr: 'DB attr', column: 'col', valueSrc: 'DB', sync: 'DB', desc: 'DB definition', levels: [{ name: 'level', api: 'lv', count: 35 }], qa: { defaultLevel: 'level', unspecified: '', nullAs: '', sort: '', note: 'saved' }, values: Array.from({ length: 35 }, (_, i) => value(i + 1)), mappings: [mapping(1)], metricDims: [], usage: [], history: [], since: 'v1' };
const knowledge = { ...head('K', 1), definition: 'saved DB definition', calc: '', applies: [], period: '', maps: '', filters: [], tries: [], refs: [], history: [], updatedAt: 'actual', since: 'v1' };
const link = i => ({ id: `L${i}`, name: `DB link ${i}`, api: `link_${i}`, from: `O${i}`, to: `O${i+1}`, card: 'N : 1', key: 'id = id', fromProp: 'id', toProp: 'id', join: '', reverse: '', nullable: false, desc: '', status: '已发布', owner: 'DB owner', usage: [], history: [], since: 'v1' });
const lists = { '/object-types': 'O', '/metrics': 'M', '/dimensions': 'D', '/knowledge': 'K', '/link-types': 'L' };
const calls = [], details = [];
let held, empty = false;
const staticApi = {
  isStaticReal: () => true, staticContextKey: key,
  fetchStaticPage: (path, query, signal) => {
    calls.push({ path, query: { ...query }, who: context.demoAs, signal });
    let rows;
    if (path in lists) rows = Array.from({ length: 40 }, (_, i) => lists[path] === 'L' ? link(i + 1) : head(lists[path], i + 1));
    else if (path.endsWith('/properties')) rows = Array.from({ length: 35 }, (_, i) => property(i + 1));
    else if (path.endsWith('/values')) rows = dim.values;
    else if (path.includes('/dimensions/') && path.endsWith('/mappings')) rows = Array.from({ length: 35 }, (_, i) => mapping(i + 1));
    else rows = Array.from({ length: 35 }, (_, i) => ({ name: `DB row ${i + 1}`, id: String(i + 1), ver: `v${i+1}`, status: '已发布', release: 'DB', type: '指标', note: '' }));
    if (empty) rows = [];
    const page = Math.min(query.page, Math.max(1, Math.ceil(rows.length / query.size)));
    const result = { items: rows.slice((page-1)*query.size, page*query.size), total: rows.length, page, size: query.size, groups: ['DB group'], summary: 'DB values summary', levels: ['level'], conflictCount: 0 };
    if (held) { const h = held; held = undefined; h.result = result; return h.promise; }
    return Promise.resolve(result);
  },
  fetchStaticDetail: async path => {
    details.push(path);
    if (empty && path === '/link-types/catalog') return { objects: [], links: [], paths: [] };
    if (path === '/link-types/catalog') return { objects: Array.from({ length: 41 }, (_, i) => head('O', i+1)), links: Array.from({ length: 40 }, (_, i) => link(i+1)), paths: [] };
    if (path.startsWith('/dimensions/')) return structuredClone(dim);
    if (path.startsWith('/knowledge/')) return structuredClone(knowledge);
    if (path.startsWith('/link-types/')) return link(Number(path.split('/').at(-1).slice(1)));
    if (path.startsWith('/metrics/')) return { ...head('M', 1), formula: 'actual', def: [], dims: [] };
    return [];
  },
  fetchKnowledgeChoices: async () => [], fetchKnowledgeConflicts: async () => ({ conflicts: [], rule: {} }),
  matchDimension: async () => ({ exact: [], contains: [], more: 0, groups: [], crossDim: false, rule: null }),
};
const lifecycle = [];
const hooks = { ...vue, onMounted() {}, onBeforeUnmount: cb => lifecycle.push(cb) };
const pageHelper = execute('pages/ontology/parts/useStaticPage.ts', { vue: hooks, '@/api/static': staticApi });
const realCr = execute('pages/ontology/parts/useStaticCr.ts', { vue, '@/api/http': { currentApiContext: () => context }, 'ant-design-vue': {}, '@/api/static': staticApi, './useDraftView': {} });
const bodyImports = (route, selected, kind, draftFlag, cr) => ({
  vue: hooks, 'vue-router': { useRoute: () => route, useRouter: () => ({ replace: ({ query }) => { route.query = { ...query }; return Promise.resolve(); } }) },
  'ant-design-vue': { message: { error: e => notices.push(e), success() {} } }, '@/api/http': { currentApiContext: () => context },
  '@/api/static': staticApi, './parts/useStaticPage': pageHelper,
  './parts/useDraftView': { useDraftView: () => ({ draft: vue.ref({ id: 'CR-DB' }), isDraft: draftFlag, readonly: vue.ref(false), reason: vue.ref(''), editTip: vue.ref(''), setView() {} }), draftOf: () => undefined },
  './parts/useStaticCr': { ...realCr, useStaticCr: () => cr, useRealCrRecords: () => ({ rows: vue.ref([]), reload: async () => {} }) },
  '@/composables/useReadonly': { useReadonly: () => ({ readonly: vue.ref(false), reason: vue.ref('') }) },
  '@/composables/useUnsavedGuard': { useUnsavedGuard: () => fn => fn() }, '@/composables/useDrawer': { useDrawer: () => ({}) },
  '@/stores/confirm': { useConfirmStore: () => ({}) }, '@/stores/app': { useAppStore: () => ({ ontologyVersion: 'DB-current', viewVersion: undefined }) },
  '@/mock': { DAILY_GMV: [] }, '@/mock/changes': { CHANGE_REQUESTS: [], CURRENT_USER: 'mock-must-not-load' },
  '@/mock/links': { LINKS: [], LINK_OBJECTS: [], PATH_GROUPS: [], CARD_API: {}, defaultLabel: () => '', objName: id => id, objById: () => undefined },
  '@/mock/objectProps': { OBJECT_PROPS: {}, ORDER_MAP_ROWS: [] }, '@/mock/quality': { QUALITY_CHECKS: [], Q_OBJECT_TYPES: [] }, '@/mock/roles': {}, '@/mock/rules': {}, '@/mock/objectHistory': {},
  '@/mock/metricCatalog': { METRIC_LIST: [], METRIC_DEF: [], METRIC_DIMS: [], METRIC_VERSIONS: [], METRIC_REFS: [] }, '@/mock/metrics': { METRICS: [] },
  '@/mock/dimensions': { DIM_ITEMS: [] }, '@/mock/knowledge': { KNOWLEDGE: [], K_TYPES: [], CONFLICT_RULE: {} }, '@/theme': { GRAY: {}, BRAND: '' },
  '@/api/objectOverview': { fetchObjectOverviewRuntime: async () => ({ metrics: [] }) },
  '@/api/objectImport': { recollectObjectMetadata: async () => ({ added: [], deleted: [] }) },
  '@/api/definitionForms': { definitionDetail: async () => ({ fields: { ...head('O', 1), table: 'actual' }, properties: Array.from({ length: 35 }, (_, i) => property(i+1)) }) },
  './parts/object/objectGov': { crsOf: () => [], metricsOf: () => [] },
});
for (const [file, param, id, kind, listPath, expose] of [
  ['ObjectTypesBody', 'type', 'O1', 'object', '/object-types', 'current, addForm, addOpen, ppage, propPage, realProps, recollectMetadata, metadataOpen, metadataDiff'],
  ['MetricsBody', 'metric', 'M1', 'metric', '/metrics', 'cur, definitionOpen, verPage, versionRows, verSize'],
  ['DimensionsBody', 'dim', 'D1', 'dimension', '/dimensions', 'cur, form, editing, vEdit, vp, valuesPage, valueSize'],
  ['GlossaryBody', 'k', 'K1', 'knowledge', '/knowledge', 'cur, form, editing, beginEdit, rpage, refsPage'],
  ['LinkTypesBody', 'link', 'L1', 'link', '/link-types', 'cur, form, editing, beginEdit, graphNodes, graphLinks, positions'],
]) {
  const route = vue.reactive({ query: { [param]: id } }), draftFlag = vue.ref(false), scope = vue.effectScope();
  const cr = { head: vue.ref({ id: 'CR-DB', status: '草稿', lockVersion: 1, targets: [{ kind, id, isNew: false }] }), items: vue.ref([]), loading: vue.ref(false), error: vue.ref(''), reload: async () => {} };
  let page; scope.run(() => { page = execute(`pages/ontology/${file}.vue`, bodyImports(route, id, kind, draftFlag, cr), `lpage, list, listTotal, tab, ${expose}`); });
  await tick();
  assert.equal(page.list.value.length > 0, true, file); assert.equal(page.listTotal.value, 40);
  const selected = page.cur ?? page.current;
  assert.equal(selected.value.id, id, file); const original = selected.value;
  const beforeDetails = details.length;
  if (page.form) { page.form.name = 'unsaved editor text'; page.form.note = 'unsaved note'; page.editing.value = true; }
  if (page.addForm) { page.addForm.name = 'unsaved property'; page.addOpen.value = true; }
  if (page.definitionOpen) page.definitionOpen.value = true;
  page.lpage.value = 2; await tick();
  assert.equal(calls.filter(c => c.path === listPath).at(-1).query.page, 2, file);
  assert.equal(selected.value.id, id, 'page change must not select another resource'); assert.equal(selected.value, original, 'page change must not rebuild its complete editor baseline');
  assert.equal(details.length, beforeDetails, 'page change must not preload any resource detail');
  if (page.form) assert.equal(page.form.name, 'unsaved editor text');
  if (page.addForm) assert.equal(page.addForm.name, 'unsaved property');
  if (page.definitionOpen) assert.equal(page.definitionOpen.value, true);
  if (file === 'ObjectTypesBody') {
    await page.recollectMetadata(); assert.equal(page.metadataOpen.value, true); assert.deepEqual(Array.from(page.metadataDiff.value.added), []);
    const previousContext = context.demoAs; context.demoAs = 'different-metadata-user'; await tick(); assert.equal(page.metadataOpen.value, false); context.demoAs = previousContext; await tick();
    page.tab.value = 'properties'; await tick(); assert.equal(page.realProps.value.O1.length, 35, 'complete selected baseline is retained for validation');
    page.ppage.value = 2; await tick(); assert.equal(page.propPage.items.value[0].api, 'p13');
    draftFlag.value = true; await tick(); assert.equal(calls.filter(c => c.path.endsWith('/properties')).at(-1).query.crId, 'CR-DB');
  }
  if (file === 'MetricsBody') { page.tab.value = 'versions'; await tick(); page.verSize.value = 20; await tick(); assert.equal(calls.filter(c => c.path.endsWith('/versions')).at(-1).query.size, 20); }
  if (file === 'DimensionsBody') { page.tab.value = 'values'; await tick(); const editRow = selected.value.values[15]; page.vEdit.value = editRow; page.vp.value = 2; await tick(); assert.equal(page.valuesPage.items.value[0].key, 'v11'); assert.equal(page.vEdit.value, editRow); assert.equal(selected.value.values[15], editRow, 'visible values page cannot replace the complete edit row identity'); draftFlag.value = true; await tick(); assert.equal(calls.filter(c => c.path.endsWith('/values')).at(-1).query.crId, 'CR-DB'); }
  if (file === 'GlossaryBody') { page.tab.value = 'refs'; await tick(); page.rpage.value = 2; await tick(); assert.equal(page.refsPage.items.value[0].name, 'DB row 9'); }
  if (file === 'LinkTypesBody') { assert.equal(page.graphLinks.value.length, 40); assert.ok(page.positions.value.O41, 'new DB object IDs get positions without mock layout keys'); }
  scope.stop(); lifecycle.splice(0).forEach(fn => fn());
}
// A new unpublished target with disabled history must not inherit the old resource's rows.
const historyScope = vue.effectScope(), resource = vue.ref('/metrics/M1/versions'), enabled = vue.ref(true);
let historical;
historyScope.run(() => { historical = pageHelper.useStaticPage(() => resource.value, () => ({}), vue.ref(1), vue.ref(10), () => enabled.value); });
await tick(); assert.equal(historical.items.value.length, 10);
const callsBeforeNew = calls.length; enabled.value = false; resource.value = '/metrics/NEW/versions';
assert.equal(historical.items.value.length, 0, 'resource switch clears old rows synchronously');
await tick(); assert.equal(calls.length, callsBeforeNew, 'unpublished target history is not fetched');
historyScope.stop(); lifecycle.splice(0).forEach(fn => fn());
// All 5 fresh real pages tolerate an actual empty DB without a sample/default resource or detail fanout.
empty = true;
for (const [file, kind] of [['ObjectTypesBody','object'],['MetricsBody','metric'],['DimensionsBody','dimension'],['GlossaryBody','knowledge'],['LinkTypesBody','link']]) {
  const route = vue.reactive({ query: {} }), scope = vue.effectScope();
  const cr = { head: vue.ref(undefined), items: vue.ref([]), loading: vue.ref(false), error: vue.ref('') };
  const before = details.length; let page;
  scope.run(() => { page = execute(`pages/ontology/${file}.vue`, bodyImports(route, '', kind, vue.ref(false), cr), `list, listTotal, ${file === 'ObjectTypesBody' ? 'selected, addErr' : file === 'MetricsBody' ? 'selected' : 'cur'}`); });
  await tick(); assert.equal(page.list.value.length, 0, file); assert.equal(page.listTotal.value, 0, file);
  if (page.selected) assert.equal(page.selected.value, '', 'empty data must not select a demo resource'); else assert.equal(page.cur.value, undefined);
  assert.ok(details.slice(before).every(path => path === '/link-types/catalog'), 'empty lists must not fan out selected details');
  scope.stop(); lifecycle.splice(0).forEach(fn => fn());
}
assert.equal(notices.length, 0, String(notices));
console.log('Static Body pages passed (5 real list requests, selection/buffer identity, complete selected baselines, nested paging/draft IDs, real size, complete graph).');
