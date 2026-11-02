// Run: node scripts/check-assistant-context.mjs. In-memory context contracts, no model or HTTP.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import ts from 'typescript';
import * as vue from 'vue';

const app = vue.reactive({ ontologyId: 'device-a', ontologyVersion: 'v1', ontologyNextVersion: '', workspaceLoading: false, workspaceError: '', persona: 'admin' });
const identity = vue.ref('user-a');
const route = vue.reactive({ name: 'object-types', fullPath: '/objects?ontology=device-a', query: {} });
const storage = {};
const sessionStorage = {
  getItem: key => storage[key] ?? null,
  setItem: (key, value) => { storage[key] = value; },
  removeItem: key => { delete storage[key]; },
};
const ctx = () => ({ ontologyId: app.ontologyId, demoAs: identity.value });
function loadModule(path, dependencies) {
  const source = readFileSync(new URL(path, import.meta.url), 'utf8');
  const output = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } }).outputText;
  const exports = {};
  runInNewContext(output, { exports, require: name => { assert.ok(name in dependencies, name); return dependencies[name]; }, sessionStorage });
  return exports;
}
let releaseFailed = false, finishGenerated;
const releases = loadModule('../src/api/releases.ts', {
  vue, '@/mock/releases': { RELEASES: [] }, './http': { currentApiContext: ctx, apiClient: () => ({ isReal: () => true, get: async path => {
    if (releaseFailed) throw Error('release read failed');
    return path === '/releases' ? { current: null, items: [] } : { current: null, next: 'v2026.11.1', release: null };
  } }) },
});
const assistantApi = loadModule('../src/api/assistant.ts', {
  './http': { currentApiContext: ctx, apiClient: () => ({}) }, '@/stores/app': { useAppStore: () => app }, './releases': releases,
});
let prefill, received, rejectNext = false;
const imports = {
  vue: { ...vue, onMounted() {}, onBeforeUnmount() {} },
  'vue-router': { useRoute: () => route, useRouter: () => ({ push: async ({ name }) => {
    route.name = name; route.fullPath = `/${name}?ontology=${app.ontologyId}`;
  } }) },
  '@ant-design/icons-vue': {},
  '@/stores/app': { useAppStore: () => app },
  '@/api/http': { currentApiContext: ctx },
  '@/api/functions': { getFunction: async () => { throw Error('Unexpected API'); } },
  '@/api/assistant': {
    ...assistantApi,
    clearAssistantPrefill: () => { prefill = undefined; assistantApi.clearAssistantPrefill(); },
    putAssistantPrefill: value => { prefill = value; assistantApi.putAssistantPrefill(value); },
    generateAssistantDraft: () => new Promise(resolve => { finishGenerated = resolve; }),
  },
};
const raw = readFileSync(new URL('../src/components/BuildAssistant.vue', import.meta.url), 'utf8');
const script = raw.match(/<script setup lang="ts">([\s\S]*?)<\/script>/)[1];
const output = ts.transpileModule(`${script}\nexport { prompt, history, result, resultScope, resultContextError, apply, generate, loadSession, editing, editFunction };`, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
}).outputText;
const page = {};
const scope = vue.effectScope();
scope.run(() => runInNewContext(output, {
  exports: page, require: name => { assert.ok(name in imports, name); return imports[name]; }, sessionStorage,
  window: { dispatchEvent: () => {
    const value = assistantApi.consumeAssistantPrefill('function', app.ontologyId);
    if (rejectNext) rejectNext = false; else received = value;
    prefill = undefined;
  } },
  CustomEvent: class { constructor(type, init) { this.type = type; this.detail = init.detail; } },
}));
page.prompt.value = 'device object draft';
page.history.value = [{ role: 'user', content: 'first requirement' }];
page.result.value = { target: 'function', message: 'test transport only', draft: { name: 'device_label' }, warnings: [] };
page.resultScope.value = { path: route.fullPath, version: app.ontologyVersion };
await page.apply();
assert.equal(received?.draft.name, 'device_label', 'cross-module navigation must preserve handoff');
assert.equal(received?.ontologyId, 'device-a');
page.result.value = { target: 'function', message: 'test retry only', draft: { name: 'retry_label' }, warnings: [] };
page.resultScope.value = { path: route.fullPath, version: app.ontologyVersion };
received = undefined; rejectNext = true;
await page.apply();
assert.equal(received, undefined, 'controlled receiver declines the first handoff');
assert.equal(page.result.value.draft.name, 'retry_label', 'declining a handoff must retain the suggestion');
await page.apply();
assert.equal(received?.draft.name, 'retry_label', 'the retained suggestion can be adopted again');
app.ontologyVersion = '—'; app.ontologyNextVersion = 'stale-next-from-another-load';
page.resultScope.value = { path: route.fullPath, version: '—' };
received = undefined;
assert.match(page.resultContextError.value, /尚未确认/);
await page.apply();
assert.equal(received, undefined, 'unknown version cannot hand off even with a stale next version');
await releases.loadReleases();
assert.equal(assistantApi.assistantVersionReady(), true, 'successful empty release lookup establishes an explicit scoped baseline');
page.loadSession('ontology:assistant:v1:user-a:device-a:functions');
assert.equal(page.resultScope.value.version, '—', 'restoration must retain the original empty baseline');
assert.equal(page.resultContextError.value, '');
await page.apply();
assert.equal(received?.draft.name, 'retry_label', 'restored empty-workspace draft can reach the real prefill consumer');
releaseFailed = true;
await assert.rejects(releases.loadReleases(), /release read failed/);
assert.match(page.resultContextError.value, /尚未确认/);
received = undefined; await page.apply();
assert.equal(received, undefined, 'a failed reload invalidates prior empty-baseline confirmation');
releaseFailed = false; await releases.loadReleases();
app.workspaceLoading = true;
assert.equal(assistantApi.assistantVersionReady(), false);
app.workspaceLoading = false;
app.ontologyVersion = 'v2026.11.1';
assert.match(page.resultContextError.value, /尚无发布.*v2026.11.1/);
received = undefined; await page.apply();
assert.equal(received, undefined, 'a real release invalidates the old empty-baseline proposal');
assert.equal(page.resultScope.value.version, '—', 'do not relabel an old proposal as the new release');
app.ontologyVersion = 'v1';
route.name = 'object-types'; route.fullPath = '/objects?ontology=device-a';
await vue.nextTick();
assert.equal(page.prompt.value, 'device object draft', 'page bucket restores unsent input');
assert.equal(page.history.value[0].content, 'first requirement');
route.name = 'functions'; route.query.fn = 'FN-1'; route.fullPath = '/functions?fn=FN-1';
await vue.nextTick();
const edit = { targetId: 'FN-1', lockVersion: 1, version: 'v1.0.0', name: 'device_label', base: {} };
page.editFunction({ detail: edit });
page.prompt.value = 'edit requirement';
assert.equal(page.editing.value.targetId, 'FN-1');
app.ontologyId = 'device-b';
assert.equal(page.editing.value, null, 'workspace switch must discard old edit binding synchronously');
assert.equal(page.prompt.value, '', 'new workspace must not show old edit input');
assert.equal(storage['ontology:assistant:v1:user-a:device-a:functions:edit:FN-1:v1.0.0:1'] && JSON.parse(storage['ontology:assistant:v1:user-a:device-a:functions:edit:FN-1:v1.0.0:1']).prompt, 'edit requirement');
page.editFunction({ detail: edit });
identity.value = 'user-b';
assert.equal(page.editing.value, null, 'identity switch must discard old edit binding synchronously');
assert.equal(page.prompt.value, '');
page.prompt.value = 'create function';
const pending = page.generate();
app.ontologyId = 'device-c'; app.ontologyId = 'device-b';
finishGenerated({ target: 'function', message: 'stale', draft: { name: 'late_result' }, warnings: [] });
await pending;
assert.equal(page.result.value, null, 'an in-flight proposal is rejected even after switching away and back to the same logical context');
scope.stop();
console.log('assistant context contracts passed (in-memory only; no model/API/DB)');
