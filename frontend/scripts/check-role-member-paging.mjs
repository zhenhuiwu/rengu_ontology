import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import * as vue from 'vue';
import ts from 'typescript';
import { parse } from '@vue/compiler-sfc';

const route = vue.reactive({ query: { ontology: '1', tab: 'members', mpage: '2' } });
const router = { replace: ({ query }) => { route.query = query; } };
const transpile = (source) => ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } }).outputText;
const composable = {};
runInNewContext(transpile(readFileSync(new URL('../src/composables/usePagedQuery.ts', import.meta.url), 'utf8')),
  { exports: composable, require: (name) => name === 'vue' ? vue : { useRoute: () => route, useRouter: () => router } });
const { descriptor } = parse(readFileSync(new URL('../src/pages/admin/Roles.vue', import.meta.url), 'utf8'));
const parsed = ts.createSourceFile('Roles.ts', descriptor.scriptSetup.content, ts.ScriptTarget.Latest, true);
const selected = parsed.statements.filter((s) =>
  ts.isVariableStatement(s) && s.declarationList.declarations.some((d) => ['mkw', 'mpq'].includes(d.name.getText(parsed))) ||
  ts.isExpressionStatement(s) && s.getText(parsed).startsWith('watch(() => role.value?.id, (next, previous)'));
const roles = vue.ref([]);
const role = vue.computed(() => roles.value.find((r) => r.id === route.query.role) ?? roles.value[0]);
const output = {};
runInNewContext(transpile(`${selected.map((s) => s.getText(parsed)).join('\n')}\nObject.assign(exports,{mpq,mkw});`),
  { ...vue, role, realRoles: true, usePagedQuery: composable.usePagedQuery, exports: output });
roles.value = [{ id: 'R-01' }, { id: 'R-02' }];
await vue.nextTick();
assert.equal(output.mpq.page.value, 2, 'initial async role loading must preserve a page deep link');
output.mkw.value = 'some user';
await vue.nextTick();
assert.equal(output.mpq.page.value, 1, 'keyword changes reset the page');
output.mpq.page.value = 2;
await vue.nextTick();
assert.equal(output.mpq.page.value, 2, 'page navigation must remain on the selected page');
route.query = { ...route.query, role: 'R-02' };
await vue.nextTick();
assert.equal(output.mpq.page.value, 1, 'switching an already loaded role resets the page');
assert.equal(output.mkw.value, 'some user');
console.log('Role member deep links survive initial loading; keyword and role changes reset pagination.');
