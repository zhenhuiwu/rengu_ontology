// Actual shared definition-delete boundary; controlled promises only, no HTTP/DB/model.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import ts from 'typescript';
import * as vue from 'vue';

const requests = [], confirmations = [], failures = [], successes = [], invalidations = [], cleaned = [];
const context = vue.ref('ontology-4'), ontology = vue.ref('4'), target = vue.ref({ id: 'FN-1', name: '合成函数' }), enabled = vue.ref(true);
const activeRoute = vue.reactive({ path: '/build/ontology/functions' }), leaveHandlers = [];
function deferred() { let resolve, reject; const promise = new Promise((a, b) => { resolve = a; reject = b; }); return { promise, resolve, reject }; }
async function flush() { for (let i = 0; i < 8; i++) await Promise.resolve(); }
const api = {
  get: (path, options) => { const call = { path, options, ...deferred() }; requests.push(call); return call.promise; },
  post: (path, body, options) => { const call = { path, body, options, ...deferred() }; requests.push(call); return call.promise; },
};
const deps = {
  vue,
  'vue-router': { useRoute: () => activeRoute, onBeforeRouteLeave: callback => leaveHandlers.push(callback) },
  'ant-design-vue': { message: { error: text => failures.push(text), success: text => successes.push(text) } },
  '@/api/http': { apiClient: () => api, currentApiContext: () => ({ ontologyId: ontology.value, demoAs: 'admin', ver: null, previewAs: null }), isApiError: e => typeof e?.code === 'string' },
  '@/api/static': { invalidateStaticAfterWrite: version => invalidations.push(version) },
  '@/stores/confirm': { useConfirmStore: () => ({ ask: request => { const task = { request, ...deferred() }; confirmations.push(task); return task.promise; } }) },
};
const source = readFileSync(new URL('../src/composables/useDefinitionDelete.ts', import.meta.url), 'utf8');
const js = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } }).outputText;
const exports = {};
runInNewContext(js, { exports, Error, require: key => { assert.ok(key in deps, key); return deps[key]; } });
const scope = vue.effectScope();
let deletion;
scope.run(() => { deletion = exports.useDefinitionDelete('function', {
  target: () => target.value, enabled: () => enabled.value, context: () => context.value,
  afterDelete: async (id, current) => { assert.equal(current(), true); cleaned.push(id); target.value = undefined; },
}); });
enabled.value = false; await deletion.run(); assert.equal(requests.length, 0, 'readonly/local unsaved form cannot delete the selected persisted definition');
enabled.value = true;
const lateCheck = deletion.run(); const check = requests.at(-1);
context.value = 'ontology-5'; ontology.value = '5'; check.resolve({ lockVersion: 1, targetVersion: 2 }); await lateCheck;
assert.equal(confirmations.length, 0, 'late check cannot open a confirmation in another ontology');

const changedSelection = deletion.run(); requests.at(-1).resolve({ lockVersion: 3, targetVersion: 4 }); await flush();
const confirmation = confirmations.at(-1); target.value = { id: 'FN-2', name: '另一函数' }; confirmation.resolve(true); await changedSelection;
assert.equal(requests.filter(r => r.body).length, 0, 'selection changed during confirmation sends no deletion');

const knownReferences = deletion.run();
requests.at(-1).resolve({ lockVersion: 3, targetVersion: 4, references: ['规则 R-TEMP 正在引用该函数'] }); await flush();
assert.match(confirmations.at(-1).request.blocked, /存在引用/);
assert.match(confirmations.at(-1).request.fields[1].value, /R-TEMP/);
confirmations.at(-1).resolve(true); await knownReferences;
assert.equal(requests.filter(r => r.body).length, 0, 'known references block the POST even if confirmation is forced');
assert.equal(target.value.id, 'FN-2');

const conflict = deletion.run(); requests.at(-1).resolve({ lockVersion: 5, targetVersion: 6 }); await flush();
confirmations.at(-1).resolve(true); await flush();
assert.equal(requests.at(-1).path, '/functions/FN-2:delete');
assert.equal(requests.at(-1).body.targetVersion, 6);
requests.at(-1).reject({ code: 'VERSION_CONFLICT', message: '版本已变化' }); await conflict;
assert.equal(target.value.id, 'FN-2'); assert.equal(cleaned.length, 0); assert.match(deletion.error.value, /版本已变化/);

const lateDelete = deletion.run(); requests.at(-1).resolve({ lockVersion: 7, targetVersion: 8 }); await flush();
confirmations.at(-1).resolve(true); await flush(); const pending = requests.at(-1);
context.value = 'ontology-6'; ontology.value = '6'; pending.resolve({ ver: 'old-version' }); await lateDelete;
assert.equal(invalidations.length, 0); assert.equal(cleaned.length, 0, 'late success cannot clear new scope or alter its release');

const leavingDelete = deletion.run(); requests.at(-1).resolve({ lockVersion: 7, targetVersion: 8 }); await flush();
confirmations.at(-1).resolve(true); await flush(); const leavingResponse = requests.at(-1);
for (const leave of leaveHandlers) leave();
assert.equal(activeRoute.path, '/build/ontology/functions', 'navigation is still awaiting global guards; old component is mounted');
leavingResponse.resolve({ ver: 'already-deleted-on-server' }); await leavingDelete;
assert.equal(invalidations.length, 0); assert.equal(cleaned.length, 0, 'pending route leave blocks cleanup/navigation before route commit or unmount');

const blocked = deletion.run(); requests.at(-1).reject({ code: 'DEFINITION_DELETE_BLOCKED', message: '存在引用', details: { references: [{ kind: 'rule', id: 'R-1', name: '合成引用' }] } }); await blocked;
assert.match(deletion.error.value, /合成引用/); assert.equal(target.value.id, 'FN-2');
const success = deletion.run(); requests.at(-1).resolve({ lockVersion: 9, targetVersion: 10 }); await flush();
confirmations.at(-1).resolve(true); await flush(); requests.at(-1).resolve({ ver: 'new-version' }); await success;
assert.deepEqual(cleaned, ['FN-2']); assert.deepEqual(invalidations, ['new-version']); assert.equal(deletion.busy.value, false);
assert.equal(successes.length, 1); assert.equal(failures.length, 2);
assert.equal(requests.at(-1).options.headers['X-Ontology-Id'], '6', 'write remains pinned to the checked ontology');
scope.stop();
console.log('Definition delete passed: readonly/new-form guard, late check/confirmation/response isolation, version/reference failure preservation, scoped cleanup.');
