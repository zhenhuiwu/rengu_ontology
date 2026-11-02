// Run: node scripts/check-assistant-async-boundaries.mjs. Two controlled async races, no API/DB/model.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import { parse, compileScript, compileTemplate } from '@vue/compiler-sfc';
import ts from 'typescript';
import * as vue from 'vue';

const deferred = () => { let resolve; return { promise: new Promise(done => { resolve = done; }), resolve }; };
const tick = () => new Promise(setImmediate);
function load(path, dependencies, extra = {}, exports = '') {
  const raw = readFileSync(new URL(`../src/${path}`, import.meta.url), 'utf8');
  let source = raw;
  if (path.endsWith('.vue')) {
    const { descriptor, errors } = parse(raw);
    assert.equal(errors.length, 0); compileScript(descriptor, { id: path });
    assert.equal(compileTemplate({ source: descriptor.template.content, filename: path, id: path, compilerOptions: { expressionPlugins: ['typescript'] } }).errors.length, 0);
    source = descriptor.scriptSetup.content;
  }
  const output = ts.transpileModule(`${source}\n${exports}`, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } }).outputText;
  const page = {}, scope = vue.effectScope();
  scope.run(() => runInNewContext(output, { exports: page, require: name => {
    if (name.endsWith('.vue')) return {};
    assert.ok(name in dependencies, name); return dependencies[name];
  }, ...extra }));
  return { page, stop: () => scope.stop() };
}
const source = { id: 'FN-001', name: 'inspection_label', version: 'v1.0.1', lockVersion: 2, status: '已发布', title: '巡检标签', desc: 'original', code: 'def inspection_label(score: int = None) -> str:\n    return "待检"', timeoutS: 5, inputs: [], output: { type: 'str' }, sampleIn: {} };
for (const changed of ['scope-round-trip', 'version']) {
  const context = vue.reactive({ ontologyId: '16', demoAs: 'user-a' });
  const app = vue.reactive({ ontologyVersion: 'v2026.11.2', persona: 'admin' });
  const route = vue.reactive({ fullPath: '/functions?ontology=16&fn=FN-001&form=edit', query: { fn: 'FN-001', form: 'edit' } });
  const confirmation = deferred(), readonly = vue.ref(false), navigated = [];
  const assistant = load('api/assistant.ts', { './http': { currentApiContext: () => context, apiClient: () => ({}) }, '@/stores/app': { useAppStore: () => app }, './releases': { hasConfirmedEmptyRelease: () => false } }).page;
  let proposal;
  const functions = load('pages/functions/RealFunctions.vue', {
    vue: { ...vue, onMounted() {}, onBeforeUnmount() {} },
    'vue-router': { useRoute: () => route, useRouter: () => ({ replace: async value => { navigated.push(value); Object.assign(route.query, value.query); } }) },
    'ant-design-vue': { message: { info() {}, warning() {}, success() {}, error() {} } },
    '@/api/http': { currentApiContext: () => context, isApiError: () => false },
    '@/api/assistant': { isAssistantPrefillCurrent: assistant.isAssistantPrefillCurrent, consumeAssistantPrefill: () => { const value = proposal; proposal = null; return value; } },
    '@/api/static': { invalidateStaticAfterWrite() {} },
    '@/api/functions': { getFunction: async () => source, listFunctions: async () => ({ items: [], total: 0, page: 1 }), functionVersions: async () => ({ items: [], total: 0, page: 1 }) },
    '@/stores/confirm': { useConfirmStore: () => ({}) },
    '@/composables/useReadonly': { useReadonly: () => ({ readonly, reason: vue.ref('') }) },
    '@/composables/useUnsavedGuard': { useUnsavedGuard: () => fn => fn(), confirmDiscard: () => confirmation.promise },
  }, {}, 'export { form, receiveAssistantDraft };');
  await tick();
  functions.page.form.title = 'unsaved title';
  proposal = { target: 'function', ontologyId: '16', identity: 'user-a', version: 'v2026.11.2', draft: { desc: 'old-scope proposal' }, ...(changed === 'version' ? { edit: { targetId: source.id, version: source.version, lockVersion: source.lockVersion } } : {}) };
  const pending = functions.page.receiveAssistantDraft();
  if (changed === 'scope-round-trip') { context.ontologyId = '4'; context.ontologyId = '16'; }
  else app.ontologyVersion = 'v2026.11.3';
  confirmation.resolve(true); await pending;
  assert.equal(navigated.length, 0, `${changed}: a stale confirmation must not open any proposal form`);
  assert.notEqual(functions.page.form.desc, 'old-scope proposal');
  functions.stop();
}
const app = vue.reactive({ ontologyId: '16', ontologyVersion: 'v2026.11.2', persona: 'admin' });
const route = vue.reactive({ name: 'functions', fullPath: '/functions?ontology=16&fn=FN-001', query: { fn: 'FN-001' } });
const lookup = deferred(), model = deferred();
const data = {};
let modelCalls = 0;
const build = load('components/BuildAssistant.vue', {
  vue: { ...vue, onMounted() {}, onBeforeUnmount() {} },
  'vue-router': { useRoute: () => route, useRouter: () => ({}) }, '@ant-design/icons-vue': {},
  '@/stores/app': { useAppStore: () => app }, '@/api/http': { currentApiContext: () => ({ ontologyId: app.ontologyId, demoAs: 'user-a' }) },
  '@/api/functions': { getFunction: () => lookup.promise },
  '@/api/assistant': { assistantVersionReady: () => true, clearAssistantPrefill() {}, putAssistantPrefill() {}, generateAssistantDraft: () => { modelCalls++; return model.promise; } },
}, { sessionStorage: { getItem: key => data[key] ?? null, setItem: (key, value) => { data[key] = value; }, removeItem: key => { delete data[key]; } } }, 'export { prompt, editing, generate, loading };');
build.page.prompt.value = '修改当前函数说明';
const old = build.page.generate();
route.query.form = 'new'; route.fullPath += '&form=new';
build.page.prompt.value = '创建函数';
const newer = build.page.generate();
assert.equal(modelCalls, 1);
lookup.resolve(source); await old;
assert.equal(build.page.editing.value, null, 'late implicit source lookup must not rebind edit mode on the new-function form');
assert.equal(build.page.loading.value, true, 'old lookup finally must not clear a newer generation loading state');
assert.equal(data['ontology:assistant-edit:v1:user-a:16:functions'], undefined);
model.resolve({ target: 'function', message: 'controlled', draft: {}, warnings: [] }); await newer;
build.stop();
console.log('assistant discard-confirmation and implicit-edit async boundaries passed (no API/DB/model)');
