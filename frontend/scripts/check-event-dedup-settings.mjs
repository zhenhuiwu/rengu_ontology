// Exercise the real two-field save handler without HTTP or database writes.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import ts from 'typescript';
import { parse, compileTemplate } from '@vue/compiler-sfc';
const file = new URL('../src/pages/ontology/EventTypes.vue', import.meta.url);
const { descriptor } = parse(readFileSync(file, 'utf8'));
assert.deepEqual(compileTemplate({ source: descriptor.template.content, filename: file.pathname, id: 'event-dedup' }).errors, []);
const ast = ts.createSourceFile('event.ts', descriptor.scriptSetup.content, ts.ScriptTarget.Latest, true);
const fn = ast.statements.find(node => ts.isFunctionDeclaration(node) && node.name.text === 'saveDedup').getText();
const dedupOpen = { value: true }, dedupSaving = { value: false }, crChanges = {};
let context = 'scope2', result = false, sent, finish;
const exports = {};
runInNewContext(ts.transpileModule(`${fn}\nexports.save=saveDedup;`, { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText, {
  exports, dedupOpen, dedupSaving, crChanges, actionContextKey: () => context,
  canEditDedup: { value: true }, dedupDirty: { value: true },
  dedupForm: { dedupKey: '事件ID', dedupWindow: '24 小时' },
  dedupValues: { value: { dedupKey: '事件ID', dedupWindow: '' } },
  cur: { value: { id: 'event1', dedupKey: '', dedupWindow: '' } },
  dedupCr: { head: { value: { id: 'CR-8', changes: 11 } }, save: async (...args) => { sent = args; return result === 'pending' ? new Promise(resolve => { finish = resolve; }) : result; } },
  message: { success() {} },
});
await exports.save();
assert.equal(dedupOpen.value, true);
assert.equal(sent[0], 'def');
assert.deepEqual(JSON.parse(JSON.stringify(sent[1])), [{ key: 'dedupWindow', op: 'update', before: '', after: '24 小时' }]);
result = true; await exports.save();
assert.equal(dedupOpen.value, false); assert.equal(crChanges['CR-8'], 11);
dedupOpen.value = true; delete crChanges['CR-8']; result = 'pending';
const pending = exports.save(); context = 'scope3'; finish(true); await pending;
assert.equal(dedupOpen.value, true); assert.deepEqual(crChanges, {});
assert.equal(dedupSaving.value, false);
console.log('Event dedup save: only changed scalar fields, failure retention, and stale-context isolation passed.');
