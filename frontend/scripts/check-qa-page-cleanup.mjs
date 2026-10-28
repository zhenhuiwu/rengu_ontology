// Run: node scripts/check-qa-page-cleanup.mjs. Real SFC scripts and CardPreview template; no HTTP/DB writes.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import ts from 'typescript';
import * as vue from 'vue';
import { compile } from '@vue/compiler-dom';
import { renderToString } from '@vue/server-renderer';
function load(path, deps, props = {}, names = '', globals = {}) {
  const file = readFileSync(new URL(`../src/pages/qa/${path}`, import.meta.url), 'utf8');
  const source = file.match(/<script setup lang="ts">([\s\S]*?)<\/script>/)[1];
  const { outputText } = ts.transpileModule(`${source}\nexport { ${names} };`, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } });
  const exports = {};
  runInNewContext(outputText, { exports, defineProps: () => props, defineEmits: () => globals.emit ?? (() => {}), ...globals, require: (name) => name === 'vue' ? { ...vue, watch() {}, onMounted() {}, onBeforeUnmount() {} } : name in deps ? deps[name] : name.endsWith('.vue') ? {} : (() => { throw Error(name); })() });
  return { ...exports, template: file.slice(file.indexOf('<template>') + '<template>'.length, file.lastIndexOf('</template>')) };
}
const context = vue.reactive({ demoAs: 'old', ver: '' });
const real = vue.ref(true); const old = {};
old.promise = new Promise((resolve) => { old.resolve = resolve; });
const writes = [], emits = [], calls = [];
let fail = false;
const props = vue.reactive({ open: true, card: 'Real answer', turnId: 'REAL-TURN' });
const page = load('components/QaPinModal.vue', {
  '@/mock': { QA_PAGE_LIST: [{ id: 'DEMO', name: 'Demo' }] },
  '@/api/http': { currentApiContext: () => context }, '../useQaSession': { errText: String },
  '../qaFallback': { qaWriteReal: real, qaWriteFail() {} }, 'ant-design-vue': { message: { error() {} } },
  '@/api/qa': { listPagesBrief: async (q) => {
    calls.push({ ...q, who: context.demoAs }); if (fail) throw Error('403');
    if (context.demoAs === 'old') return old.promise;
    return { items: Array.from({ length: q.page === 1 ? 200 : 1 }, (_, i) => ({ id: `REAL-${(q.page-1)*200+i}`, name: 'DB Page', cards: 1 })), total: 201 };
  } },
  '@/api/qaPages': { pinTitle: (s) => s, createPage: async () => ({ id: 'CREATED' }), pinToPage: async (...args) => writes.push(args), deletePage: async () => {} },
}, props, 'loadPages, list, pageId, newName, ok, submit, loadError', { emit: (...args) => emits.push(args) });
assert.equal(page.pageId.value, ''); assert.equal(page.newName.value, ''); assert.equal(page.list.value.length, 0);
const previous = page.loadPages(); context.demoAs = 'new'; const current = page.loadPages(); await current;
assert.equal(page.list.value.length, 201); assert.equal(page.pageId.value, 'REAL-0');
old.resolve({ items: [{ id: 'OLD', name: 'private' }], total: 1 }); await previous;
assert.equal(page.pageId.value, 'REAL-0'); assert.equal(calls.filter((r) => r.who === 'old').length, 1, 'stale pagination stops before querying with another identity');
await page.submit(); assert.equal(writes[0][0], 'REAL-0'); assert.equal(writes[0][1].turnId, 'REAL-TURN'); assert.equal(emits.length, 1);
context.demoAs = 'another'; assert.equal(page.list.value.length, 0); assert.equal(page.ok.value, false); await page.submit(); assert.equal(writes.length, 1);
fail = true; await page.loadPages(); assert.equal(page.list.value.length, 0); assert.match(page.loadError.value, /403/);
props.turnId = undefined; await page.submit(); assert.equal(writes.length, 1);
const templateEvents = [];
const template = load('pages/UseTemplatePop.vue', { 'ant-design-vue': { message: {} }, '@/api/http': { currentApiContext: () => context }, '@/composables/useUnsavedGuard': { useUnsavedGuard() {} }, '@/api/qaPages': { templateAccess: () => ({ canCreate: false }), createPage: () => assert.fail('inaccessible template cannot write') }, '@/api/qa': { qaReal: () => true }, '@/mock/qa-pages': { PAGE_SCOPES: [] } }, { tpl: { name: 'DB Template' } }, 'ok, name', { emit: (...args) => templateEvents.push(args) });
template.name.value = 'New page'; await template.ok(); assert.equal(templateEvents.length, 0, 'inaccessible real template is guarded even if invoked directly');
const card = { title: 'DB card structure', viz: '指标卡', metric: 'GMV', version: 'v1', value: 'SAMPLE-NUMBER', unit: '元', labels: ['demo'], series: [999] };
async function renderCard(structureOnly) {
  const cardProps = { card, structureOnly, index: 1 };
  const module = load('pages/CardPreview.vue', { '@ant-design/icons-vue': {} }, cardProps, 'option');
  if (structureOnly) assert.equal(module.option.value.series[0].data.length, 0);
  const render = Function('Vue', compile(module.template, { mode: 'function', prefixIdentifiers: true }).code)(vue);
  const app = vue.createSSRApp({ setup: () => ({ ...cardProps, option: module.option }), render });
  for (const name of ['InfoTip', 'LockOutlined', 'EChart', 'a-tag']) app.component(name, { render: () => null });
  return renderToString(app);
}
assert.ok((await renderCard(false)).includes('SAMPLE-NUMBER'), 'saved-page preview retains its normal values');
assert.ok(!(await renderCard(true)).includes('SAMPLE-NUMBER'), 'structure preview never renders demonstration values');
class FixedDate extends Date { constructor(...args) { super(...(args.length ? args : ['2030-02-03T12:00:00'])); } }
function session(realMode) {
  return load('components/QaSessionPanel.vue', {
    'ant-design-vue': {}, '@ant-design/icons-vue': {}, '@/mock': { QA_TODAY: '2026-10-25', QA_FAVORITES: [], QA_MY_PAGES: [] },
    '../useQaSession': { useQaSession: () => ({ sessions: vue.ref([]), activeId: vue.ref(''), real: realMode, live: vue.ref(realMode), detail: vue.ref(null), listTick: vue.ref(0), favTick: vue.ref(0) }) },
    '@/api/http': { currentApiContext: () => ({}) }, '../qaFallback': {}, '@/api/qa': { listPagesBrief: async () => ({ items: [] }) }, '../useQueryParam': { useQueryParam: () => vue.ref('all'), useQueryPage: () => vue.ref(1) },
    '@/composables/useReadonly': { useReadonly: () => ({}) }, '@/composables/usePreview': { usePreview: () => ({}) }, '../qaScope': { useScopeCaliber: () => ({}) },
  }, {}, 'when', { Date: FixedDate });
}
assert.equal(session(true).when('2030-02-03'), '今天'); assert.equal(session(true).when('2026-10-25'), '10-25'); assert.equal(session(false).when('2026-10-25'), '今天');
console.log('QA cleanup checks passed (Pin full pages/context/real IDs/errors, template guard, actual preview rendering, local today and unchanged mock date).');
