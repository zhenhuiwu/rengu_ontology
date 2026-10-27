// Actual shared draft helpers: rehydrate a persisted zero-change new target after same-context reset.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import ts from 'typescript';
import * as vue from 'vue';

const context = vue.reactive({ ontologyId: '2', demoAs: 'U010001', ver: null, previewAs: null });
const drafts = vue.reactive([]), errors = [], requests = [];
const header = { id: 'CR-0002', title: '新建关系', owner: '王敏', updatedAt: 'now', changes: 0, status: '草稿', targets: [{ kind: 'link', id: 'L-0001', isNew: true, name: '未命名关系' }] };
const key = () => JSON.stringify(context);
const tick = async () => { for (let i = 0; i < 12; i++) { await Promise.resolve(); await vue.nextTick(); } };
function execute(file, imports) {
  const source = readFileSync(new URL(`../src/pages/ontology/parts/${file}.ts`, import.meta.url), 'utf8');
  const output = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
  const exports = {};
  runInNewContext(output, { exports, require: name => { if (name in imports) return imports[name]; throw Error(name); } });
  return exports;
}
const scope = vue.effectScope();
let registry, view, cr;
scope.run(() => {
  registry = execute('useDraftView', {
    vue, 'ant-design-vue': { message: { error: error => errors.push(error) } },
    '@/api/http': { currentApiContext: () => context, apiClient: () => ({ isReal: () => true, get: async (path, options) => {
      requests.push([path, options.headers['X-Ontology-Id']]);
      const own = options.headers['X-Ontology-Id'] === '2';
      return path.endsWith('draft-targets') ? own ? { 'CR-0002': { link: ['L-0001'] } } : {} : own ? [structuredClone(header)] : [];
    } }) },
    'vue-router': { useRoute: () => ({ query: { view: 'draft' } }), useRouter: () => ({ replace() {} }) },
    '@/mock/releases': { DRAFTS: drafts, DRAFT_TARGETS: {} }, '@/mock/changes': { CHANGE_REQUESTS: [], CURRENT_USER: '王敏' },
    '@/composables/useReadonly': { useReadonly: () => ({ readonly: vue.ref(false), reason: vue.ref('') }) },
  });
  view = registry.useDraftView('link', () => 'L-0001');
  const shared = execute('useStaticCr', {
    vue, 'ant-design-vue': { message: { error: error => errors.push(error) } }, '@/api/http': { currentApiContext: () => context },
    '@/api/static': { isStaticReal: () => true, staticContextKey: key, crErr: String,
      fetchCR: async () => structuredClone(header), fetchCRItems: async () => [], registerDraft: () => {} },
  });
  cr = shared.useStaticCr('link', () => ({ id: 'L-0001' }), view);
});
await tick();
assert.equal(view.draft.value.id, 'CR-0002');
assert.equal(cr.head.value.targets[0].isNew, true);
assert.equal(cr.items.value.length, 0);
registry.resetDraftState();
assert.equal(view.draft.value, undefined);
await tick();
assert.equal(view.draft.value.id, 'CR-0002', 'same-context reset must reload persistent target registry');
assert.equal(cr.head.value.targets[0].id, 'L-0001');
assert.equal(cr.head.value.targets[0].isNew, true);
context.ontologyId = '3'; await tick();
assert.equal(view.draft.value, undefined, 'another workspace cannot reuse the old draft');
assert.equal(cr.head.value, undefined);
context.ontologyId = '2'; await tick();
assert.equal(cr.head.value.targets[0].isNew, true);
assert.ok(requests.filter(([path, id]) => path.endsWith('draft-targets') && id === '2').length >= 3);
assert.deepEqual(errors, []);
scope.stop();
console.log('shared draft reset/zero-change new target/workspace isolation: passed; no HTTP or DB writes');
