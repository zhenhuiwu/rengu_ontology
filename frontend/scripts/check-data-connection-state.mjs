// Exercise the actual connection-test handler with separate detail/list rows; no network.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import ts from 'typescript';
import * as vue from 'vue';
import { parse } from '@vue/compiler-sfc';
const source = readFileSync(new URL('../src/pages/data/Connections.vue', import.meta.url), 'utf8');
const script = parse(source).descriptor.scriptSetup.content;
const fn = ts.createSourceFile('connection.ts', script, ts.ScriptTarget.Latest, true).statements
  .find((node) => ts.isFunctionDeclaration(node) && node.name.text === 'testConn').getText();
const list = vue.ref([{ id: 'WF', status: '未测试' }]), detail = { id: 'WF', status: '未测试' };
const testing = vue.ref(false), readonly = vue.ref(false), real = vue.ref(true), selected = vue.ref(detail);
let context = 'scope2', finish;
let test = async () => ({ ok: true, ms: 41 });
const exports = {};
runInNewContext(ts.transpileModule(`${fn}\nexports.run=testConn;`, { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText, {
  exports, list, testing, readonly, real, selected, listSeq: 1, contextKey: () => context,
  testDatasource: (...args) => test(...args), getDatasource: async () => ({ id: 'WF', status: '正常' }),
  loadTables: async () => {}, message: { success() {}, error() {} }, apiFail: () => { throw Error('unexpected failure'); },
});
await exports.run(detail);
assert.equal(detail.status, '正常'); assert.equal(list.value[0].status, '正常');
list.value[0].status = '未测试'; detail.status = '未测试';
test = () => new Promise((resolve) => { finish = resolve; });
const pending = exports.run(detail); context = 'other-scope'; finish({ ok: true, ms: 1 }); await pending;
assert.equal(detail.status, '未测试'); assert.equal(list.value[0].status, '未测试');
console.log('Connection detail/sidebar state and stale-context checks passed.');
