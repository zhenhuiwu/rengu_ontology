import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import ts from 'typescript';
import * as vue from 'vue';

// Exercises the actual form validator without a browser, database or action execution.
const source = readFileSync(new URL('../src/pages/ontology/parts/ActionTrialForm.vue', import.meta.url), 'utf8').match(/<script setup lang="ts">([\s\S]*?)<\/script>/)[1];
const context = { demoAs: 'admin' }, calls = [];
const imports = {
  vue: { ...vue, watch() {}, onScopeDispose() {} },
  '@/api/http': { currentApiContext: () => context, apiClient: () => ({ isReal: () => true }) },
  '@/api/action': { actionContextKey: () => JSON.stringify(context), dryRunAction: async (...args) => { calls.push(args); throw new Error('REQUEST_FAILED'); } },
  '@/api/static': {}, '@/api/dynamic': {}, '@/api/qaObjects': {}, './useStaticCr': {},
};
const { outputText } = ts.transpileModule(`${source}\nexport { params, values, target, errors, validate, submit, resultError };`, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } });
const exports = {};
runInNewContext(outputText, {
  exports, Error, defineProps: () => ({ action: { id: 'A', name: 'Actual action' }, disabled: false }), defineModel: () => vue.ref(false), defineEmits: () => () => {},
  require: key => key.endsWith('.vue') ? {} : imports[key] ?? (() => { throw new Error(`Missing boundary: ${key}`); })(),
});
const { params, values, target, errors, validate, submit, resultError } = exports;
params.value = [
  { api: 'count', name: '数量', type: '整数', required: true, def: '按当前对象计算' },
  { api: 'enabled', name: '启用', type: '布尔', required: true, def: '是' },
  { api: 'tags', name: '标签', type: '枚举列表', required: false },
];
target.kind = 'order';
assert.equal(validate(), null);
assert.ok(errors.count && errors.enabled && errors.$target);
assert.equal(values.count, undefined, 'descriptive defaults are never user inputs');
Object.assign(values, { count: 0, enabled: false, tags: ['实际值'] }); target.id = 'REAL-ID';
assert.equal(JSON.stringify(validate()), '{"count":0,"enabled":false,"tags":["实际值"]}');
values.count = 1.5;
assert.equal(validate(), null, 'fractional integer is rejected');
values.count = 1;
await submit();
assert.equal(calls.length, 1);
assert.equal(calls[0][5].objId, 'REAL-ID');
assert.equal(resultError.value, 'REQUEST_FAILED');
assert.equal(values.enabled, false, 'request failure retains exact user input');
assert.equal(target.id, 'REAL-ID');
console.log('Action trial form input checks passed.');
