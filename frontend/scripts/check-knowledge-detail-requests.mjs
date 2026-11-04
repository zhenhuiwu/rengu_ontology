/** Actual Knowledge watcher with Vue reactivity and deferred reads; no browser/HTTP. */
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import { parse } from '@vue/compiler-sfc';
import ts from 'typescript';
import { nextTick, ref, reactive, watch } from 'vue';

const content = parse(readFileSync('src/pages/ontology/GlossaryBody.vue', 'utf8')).descriptor.scriptSetup.content;
const tree = ts.createSourceFile('knowledge.ts', content, ts.ScriptTarget.Latest, true, ts.ScriptKind.TS);
const key = tree.statements.find(node => ts.isVariableStatement(node) && node.declarationList.declarations.some(d => d.name.getText(tree) === 'knowledgeDetailKey'));
const requestWatch = tree.statements.find(node => ts.isExpressionStatement(node) && ts.isCallExpression(node.expression) && node.expression.expression.getText(tree) === 'watch' && node.expression.arguments[0]?.getText(tree) === 'knowledgeDetailKey');
assert.ok(key && requestWatch);
const pending = [], context = ref('workspace-8');
const state = {
  ref, watch, selectedId: ref('K-1'), staticContextKey: () => context.value,
  route: reactive({ query: {} }), workflow: { loading: ref(true) }, draft: ref(undefined),
  draftOnly: ref(false), cr: { loading: ref(false), head: ref(undefined), error: ref('') },
  definitionSeq: 0, definitionError: ref(''), baseline: ref([]), isStaticReal: () => true,
  fetchStaticDetail: path => new Promise(resolve => pending.push({ path, resolve })),
};
vm.createContext(state);
vm.runInContext(ts.transpileModule(`${key.getText(tree)}\n${requestWatch.getText(tree)}`, {
  compilerOptions: { target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.None },
}).outputText, state);
const settle = async () => { await nextTick(); await Promise.resolve(); await nextTick(); };
assert.equal(pending.length, 1);
state.workflow.loading.value = false;
await settle();
assert.equal(pending.length, 1, 'published detail must not reload when workflow finishes');
pending[0].resolve({ id: 'K-1', name: 'Initial real response' });
await settle();
assert.equal(state.baseline.value[0].name, 'Initial real response');

state.route.query.view = 'draft'; state.workflow.loading.value = true;
await settle();
assert.equal(pending.length, 1, 'draft view waits for workflow');
state.workflow.loading.value = false;
await settle();
assert.equal(pending.length, 2, 'draft view refreshes after workflow completes');
state.draft.value = { id: 'CR-1' }; state.cr.loading.value = true;
await settle();
state.cr.head.value = { targets: [{ kind: 'knowledge', id: 'K-1', isNew: true }] };
state.cr.loading.value = false;
await settle();
assert.equal(pending.length, 2, 'new draft does not fetch a nonexistent published record');
state.cr.head.value = { targets: [{ kind: 'knowledge', id: 'K-1', isNew: false }] };
await settle();
assert.equal(pending.length, 3, 'draft target change still forces detail refresh');
context.value = 'workspace-17';
await settle();
assert.equal(pending.length, 4);
pending[1].resolve({ id: 'K-1', name: 'Late old draft' });
pending[2].resolve({ id: 'K-1', name: 'Late old context' });
await settle();
assert.equal(state.baseline.value[0].name, 'Initial real response');
pending[3].resolve({ id: 'K-1', name: 'Current context response' });
await settle();
assert.equal(state.baseline.value[0].name, 'Current context response');
console.log('Knowledge detail passed: no workflow duplicate on published view; draft/new-target refresh and late-context guards preserved.');
