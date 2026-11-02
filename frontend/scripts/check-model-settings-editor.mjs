// Controlled editor races only: no HTTP, database, model inference or persisted settings.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import { parse, compileScript, compileTemplate } from '@vue/compiler-sfc';
import ts from 'typescript';
import * as vue from 'vue';

for (const name of ['ModelSettings', 'Settings']) {
  const { descriptor, errors } = parse(readFileSync(new URL(`../src/pages/admin/${name}.vue`, import.meta.url), 'utf8'));
  assert.equal(errors.length, 0);
  compileScript(descriptor, { id: name });
  assert.deepEqual(compileTemplate({ source: descriptor.template.content, filename: name, id: name, compilerOptions: { expressionPlugins: ['typescript'] } }).errors, []);
}
const source = parse(readFileSync(new URL('../src/pages/admin/ModelSettings.vue', import.meta.url), 'utf8')).descriptor.scriptSetup.content;
const deferred = () => { let resolve; return { promise: new Promise(done => { resolve = done; }), resolve }; };
const original = { usage: 'qa', label: '问数解析', provider: 'openai', model: 'current-model', baseUrl: 'https://example.test/v1', timeoutSec: 30, enabled: true, authMode: 'key', hasKey: true, source: 'database', keySource: 'database', lockVersion: 7, updatedAt: null };
let confirmation, directory, failSave = false;
const saves = [], probes = [], context = vue.reactive({ demoAs: 'admin', ontologyId: 16 });
const dependencies = {
  vue: { ...vue, onBeforeUnmount() {} },
  'ant-design-vue': { message: { error() {}, info() {}, success() {} } },
  '@/api/http': { currentApiContext: () => context, isApiError: value => value?.code != null },
  '@/api/settings': {
    loadModelSettings: async () => ({ items: [{ ...original }], canManage: true }),
    saveModelSettings: async (usage, body) => { saves.push({ usage, body }); if (failSave) throw { code: 'VERSION_CONFLICT', message: 'conflict' }; return { ...original, ...body, lockVersion: 8 }; },
    testModelConnection: (usage, body) => { probes.push({ usage, body }); return directory.promise; },
  },
  '@/composables/useUnsavedGuard': { confirmDiscard: async () => true },
  '@/stores/confirm': { useConfirmStore: () => ({ ask: () => confirmation.promise }) },
};
const page = {}, effect = vue.effectScope();
effect.run(() => runInNewContext(ts.transpileModule(`${source}\nObject.assign(exports, { items, saved, error, connections, fields, payload, validation, reusableKey, changeProvider, testConnection, save, discard });`, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } }).outputText, {
  exports: page, URL, defineProps: () => ({ readonly: false, reason: '' }), defineEmits: () => () => {},
  require: name => { assert.ok(name in dependencies, name); return dependencies[name]; },
}));
await vue.nextTick();
await new Promise(setImmediate);
assert.equal(page.error.value, '');
const row = page.items.value[0];
assert.equal(page.fields({ ...original, provider: undefined }).provider, 'openai');
assert.equal(page.reusableKey(row), true);
row.baseUrl = 'https://other.test/v1';
assert.equal(page.reusableKey(row), false);
assert.match(page.validation(row), /密钥/);
page.changeProvider(row, 'ollama');
assert.equal(row.baseUrl, 'http://localhost:11434');
assert.equal(row.authMode, 'none');
assert.equal('apiKey' in page.payload(row), false);
row.model = '';
assert.equal(page.validation(row, false), '');
assert.match(page.validation(row), /模型/);
directory = deferred();
let work = page.testConnection(row);
assert.equal(saves.length, 0);
assert.equal('lockVersion' in probes[0].body, false);
directory.resolve({ ok: true, provider: 'ollama', baseUrl: 'http://localhost:11434/v1', modelOptions: [{ value: 'real-model:latest', label: 'real-model:latest' }], selectedModelAvailable: null, error: null });
await work;
assert.equal(page.connections.value.qa.modelOptions[0].value, 'real-model:latest');
row.model = 'real-model:latest';
await vue.nextTick();
assert.equal(page.connections.value.qa.modelOptions.length, 1, 'typing/selecting must preserve discovered choices');
directory = deferred();
work = page.testConnection(row);
row.baseUrl = 'http://localhost:11435';
await vue.nextTick();
directory.resolve({ ok: true, modelOptions: [{ value: 'stale' }] });
await work;
assert.equal(page.connections.value.qa, undefined, 'changed draft rejects late connection result');
confirmation = deferred();
work = page.save(row);
row.model = 'changed-after-confirm-open';
confirmation.resolve(true);
await work;
assert.equal(saves.length, 0, 'confirmation must not silently save changed draft');
confirmation = deferred();
work = page.save(row);
context.ontologyId = 4;
context.ontologyId = 16;
await vue.nextTick();
confirmation.resolve(true);
await work;
assert.equal(saves.length, 0, 'context round trip rejects pending confirmation');
const current = page.items.value[0];
current.model = 'new-model';
confirmation = deferred();
work = page.save(current);
confirmation.resolve(true);
await work;
assert.equal(saves.length, 1);
assert.equal(saves[0].body.lockVersion, 7);
assert.equal(saves[0].body.model, 'new-model');
assert.equal('apiKey' in saves[0].body, false);
current.model = 'keep-on-conflict';
failSave = true;
confirmation = deferred();
work = page.save(current);
confirmation.resolve(true);
await work;
assert.equal(current.model, 'keep-on-conflict');
effect.stop();
console.log('Model settings editor: SFC compilation, providers, real directory choices, confirmation snapshots and stale-response isolation passed (in memory only).');
