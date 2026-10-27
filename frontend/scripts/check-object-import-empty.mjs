// Actual import empty hint + navigation; no data requests or external connection.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import ts from 'typescript';
import * as vue from 'vue';
import { compile } from '@vue/compiler-dom';
import { renderToString } from 'vue/server-renderer';
import { parse } from '@vue/compiler-sfc';
const source = readFileSync(new URL('../src/pages/ontology/parts/ObjectImportDrawer.vue', import.meta.url), 'utf8');
const { descriptor, errors } = parse(source);
assert.equal(errors.length, 0);
const statement = ts.createSourceFile('import.ts', descriptor.scriptSetup.content, ts.ScriptTarget.Latest, true)
  .statements.find((node) => ts.isFunctionDeclaration(node) && node.name.text === 'goToSource').getText();
const calls = [], open = vue.ref(true), datasource = vue.ref('WF-SOURCE'), dirty = vue.ref(false);
const router = { currentRoute: { value: { query: { ontology: '2', ver: 'v1' } } }, push: async (value) => calls.push(value) };
const out = {};
runInNewContext(ts.transpileModule(`${statement}\nexports.navigate=goToSource;`, { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText,
  { exports: out, datasource, dirty, open, router, confirmDiscard: async () => false });
await out.navigate();
assert.equal(open.value, false);
assert.equal(calls[0].path, '/build/data/connections');
assert.equal(calls[0].query.ontology, '2');
assert.equal(calls[0].query.ver, 'v1');
assert.equal(calls[0].query.ds, 'WF-SOURCE');
dirty.value = true; open.value = true; await out.navigate();
assert.equal(calls.length, 1); assert.equal(open.value, true);
const hint = source.split('\n').find((line) => line.includes('class="t-12 c-3 import-empty"'));
const render = Function('Vue', compile(hint, { mode: 'function', prefixIdentifiers: true }).code)(vue);
async function html(tables, loading = false) {
  const app = vue.createSSRApp({ setup: () => ({ datasource: 'WF-SOURCE', tables, loading, error: '', goToSource() {} }), render });
  app.component('a-button', { setup: (_, { slots }) => () => vue.h('button', slots.default?.()) });
  return renderToString(app);
}
assert.ok((await html([])).includes('尚未获取库表，请先测试连接'));
assert.ok(!(await html(['store'])).includes('尚未获取库表'));
assert.ok(!(await html([], true)).includes('尚未获取库表'));
console.log('Object import empty-state and scoped navigation checks passed.');
