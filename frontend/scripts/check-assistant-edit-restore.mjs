// Run: node scripts/check-assistant-edit-restore.mjs. Local edit-pointer recovery, no real APIs or model.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import { parse, compileScript, compileTemplate } from '@vue/compiler-sfc';
import ts from 'typescript';
import * as vue from 'vue';

const raw = readFileSync(new URL('../src/components/BuildAssistant.vue', import.meta.url), 'utf8');
const { descriptor, errors } = parse(raw);
assert.equal(errors.length, 0); compileScript(descriptor, { id: 'assistant' });
assert.equal(compileTemplate({ source: descriptor.template.content, filename: 'BuildAssistant.vue', id: 'assistant', compilerOptions: { expressionPlugins: ['typescript'] } }).errors.length, 0);
const script = descriptor.scriptSetup.content;
const output = ts.transpileModule(`${script}\nexport { editing, prompt, history, result, resultScope, restoreBlocked, editFunction, openAssistant, clearConversation, apply };`, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } }).outputText;
const app = vue.reactive({ ontologyId: '16', ontologyVersion: 'v2026.11.1', persona: 'admin' });
const actor = vue.ref('user-a');
const route = vue.reactive({ name: 'functions', query: { fn: 'FN-001', tab: 'test' }, fullPath: '/functions?ontology=16&fn=FN-001&tab=test' });
const context = () => ({ ontologyId: app.ontologyId, demoAs: actor.value });
const data = {};
const storage = { getItem: key => data[key] ?? null, setItem: (key, value) => { data[key] = value; }, removeItem: key => { delete data[key]; } };
const pointerKey = 'ontology:assistant-edit:v1:user-a:16:functions';
const source = { id: 'FN-001', version: 'v1.0.0', lockVersion: 1, status: '已发布', name: 'inspection_label', title: '巡检标签', desc: 'source description', timeoutS: 5, code: 'def inspection_label(score: int = None) -> str:\n    return "待检"' };
let getCount = 0, getSource = async () => source, applied = 0;
function mount() {
  const scope = vue.effectScope(), page = {};
  const imports = {
    vue: { ...vue, onMounted() {}, onBeforeUnmount() {} },
    'vue-router': { useRoute: () => route, useRouter: () => ({}) },
    '@ant-design/icons-vue': {}, '@/stores/app': { useAppStore: () => app }, '@/api/http': { currentApiContext: context },
    '@/api/assistant': { assistantVersionReady: () => true, clearAssistantPrefill() {}, putAssistantPrefill() {}, generateAssistantDraft: () => { throw Error('No model'); } },
    '@/api/functions': { getFunction: async id => { assert.equal(id, 'FN-001'); getCount++; return getSource(); } },
  };
  scope.run(() => runInNewContext(output, { exports: page, sessionStorage: storage, window: { dispatchEvent: () => { applied++; } }, require: name => { assert.ok(name in imports, name); return imports[name]; } }));
  return { page, stop: () => scope.stop() };
}
const first = mount();
first.page.editFunction({ detail: { targetId: source.id, version: source.version, lockVersion: source.lockVersion, name: source.name, base: {} } });
first.page.history.value = [{ role: 'user', content: 'only update the description' }];
first.page.prompt.value = 'unsent edit requirement';
first.page.result.value = { target: 'function', message: 'description patch', draft: { desc: 'new description' }, warnings: [], edit: { targetId: source.id, version: source.version, lockVersion: source.lockVersion } };
first.page.resultScope.value = { path: route.fullPath, version: app.ontologyVersion };
assert.deepEqual(Object.keys(JSON.parse(data[pointerKey])).sort(), ['lockVersion', 'targetId', 'version']);
first.stop();
const second = mount();
assert.equal(second.page.editing.value, null, 'fresh component must not trust a pointer without a source lookup');
await second.page.openAssistant();
assert.equal(second.page.editing.value.name, source.name);
assert.equal(second.page.editing.value.base.desc, source.desc, 'comparison base must come from the fresh trusted source');
assert.equal(second.page.prompt.value, 'unsent edit requirement');
assert.equal(second.page.result.value.draft.desc, 'new description');
second.page.clearConversation();
assert.equal(second.page.editing.value.targetId, source.id, 'new conversation retains the existing edit mode');
assert.ok(data[pointerKey]);
const count = getCount;
app.ontologyId = '4'; await second.page.openAssistant();
assert.equal(getCount, count, 'same function ID in another workspace must not look up the old pointer');
app.ontologyId = '16'; actor.value = 'user-b'; await second.page.openAssistant();
assert.equal(getCount, count, 'another actor must not look up the old pointer');
actor.value = 'user-a'; getSource = async () => ({ ...source, lockVersion: 2 });
await second.page.openAssistant();
assert.equal(second.page.editing.value, null);
assert.match(second.page.restoreBlocked.value, /版本已变化/);
await second.page.apply(); assert.equal(applied, 0, 'failed recovery cannot adopt a normal or stale edit result');
second.stop();
let finish;
getSource = () => new Promise(resolve => { finish = resolve; });
const third = mount(), pending = third.page.openAssistant();
app.ontologyId = '4'; app.ontologyId = '16';
finish(source); await pending;
assert.equal(third.page.editing.value, null, 'a round-trip scope switch must reject an in-flight source response');
third.page.clearConversation();
assert.equal(data[pointerKey], undefined, 'explicit ordinary new conversation clears only its edit pointer');
assert.ok(Object.keys(data).some(key => key.includes(':edit:FN-001:')), 'edit-session data remains stored');
third.stop();
const functions = readFileSync(new URL('../src/pages/functions/RealFunctions.vue', import.meta.url), 'utf8');
assert.match(functions, /key="versions" tab="版本历史"/);
console.log('assistant trusted edit restoration and scope/version rejection passed (no API/DB/model)');
