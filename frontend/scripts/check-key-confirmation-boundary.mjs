// Offline only: real page function, inert confirmation/API; no credentials or network.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import ts from 'typescript';
import { parse, compileScript } from '@vue/compiler-sfc';

const pages = ['admin/ApiKeys.vue', 'ontology/ActionTypes.vue', 'ontology/parts/object/ObjBehaviorActions.vue'];
let create;
for (const path of pages) {
  const { descriptor, errors } = parse(readFileSync(new URL(`../src/pages/${path}`, import.meta.url), 'utf8'));
  assert.equal(errors.length, 0);
  compileScript(descriptor, { id: path, inlineTemplate: true });
  if (path === pages[0]) {
    const ast = ts.createSourceFile(path, descriptor.scriptSetup.content, ts.ScriptTarget.Latest, true, ts.ScriptKind.TS);
    create = ast.statements.find(n => ts.isFunctionDeclaration(n) && n.name?.text === 'create').getText(ast);
  } else {
    assert.ok(descriptor.template.content.includes("'审批中' ? '历史待处理'"));
  }
}
for (const change of ['form', 'drawer', 'busy', 'none']) {
  let resolve, calls = 0;
  const confirmation = new Promise(done => { resolve = done; });
  const state = {
    real: true, readonly: { value: false }, busy: { value: false }, tried: {}, canCreate: { value: true },
    formBinding: { value: {} }, meta: { value: { binding: { userId: 'inert-user' } } }, createOpen: { value: true },
    form: { name: 'inert-test', env: '测试', scopes: ['meta:read'], life: '30', ip: '127.0.0.1', note: '', principal: '用户' },
    currentApiContext: () => ({ ontologyId: '16' }), confirm: { ask: () => confirmation },
    scopesText: () => '', bindText: () => '', LIFE: [], secret: {}, saved: {},
    createApiKey: async () => { calls++; return { secret: 'inert-secret', id: 'inert-id' }; },
    reload: async () => {}, message: { error: assert.fail }, errorText: String,
  };
  runInNewContext(ts.transpileModule(create, { compilerOptions: { target: ts.ScriptTarget.ES2020 } }).outputText, state);
  const pending = state.create();
  if (change === 'form') state.form.scopes = ['objects:read'];
  if (change === 'drawer') state.createOpen.value = false;
  if (change === 'busy') state.busy.value = true;
  resolve(true); await pending;
  assert.equal(calls, change === 'none' ? 1 : 0, change);
}
console.log('Key confirmation and legacy status display contracts passed (in-memory only).');
