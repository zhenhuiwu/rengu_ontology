// Actual shared pagination state and component; no network or database writes.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import { compileScript, parse } from '@vue/compiler-sfc';
import ts from 'typescript';
import * as vue from 'vue';

function load(source, imports) {
  const exports = {};
  const { outputText } = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS } });
  runInNewContext(outputText, { exports, require: (name) => imports[name] ?? (() => { throw Error(name); })() });
  return exports;
}
const route = vue.reactive({ query: {} });
const query = load(readFileSync(new URL('../src/composables/usePagedQuery.ts', import.meta.url), 'utf8'), {
  vue, 'vue-router': { useRoute: () => route, useRouter: () => ({ replace: ({ query }) => { route.query = query; } }) },
}).usePagedQuery({ defaultSize: 16 });
assert.equal(query.size.value, 10);
query.page.value = 4;
query.size.value = 20;
assert.equal(query.page.value, 1);
assert.equal(route.query.size, '20');
query.size.value = 10;
assert.equal(route.query.size, undefined);
route.query = { size: '50', page: '2' };
assert.equal(query.size.value, 50);
assert.equal(query.page.value, 2);

const file = readFileSync(new URL('../src/components/AppPagination.vue', import.meta.url), 'utf8');
const component = load(compileScript(parse(file).descriptor, { id: 'pagination-test' }).content, {
  vue, 'ant-design-vue': { Empty: {} }, '@ant-design/icons-vue': {}, '@/mock/format': { fmtInt: String },
}).default;
component.render = () => vue.h('div');
const renderer = vue.createRenderer({
  createElement: () => ({}), createText: () => ({}), createComment: () => ({}),
  insert() {}, remove() {}, setText() {}, setElementText() {}, patchProp() {},
  parentNode: () => null, nextSibling: () => null,
});
const state = vue.reactive({ page: 4, total: 80 });
const app = renderer.createApp({ render: () => vue.h(component, {
  page: state.page, size: 10, total: state.total, 'onUpdate:page': (page) => { state.page = page; },
}) });
app.mount({});
const setup = app._instance.subTree.component.setupState;
assert.equal(setup.optionText({ value: 10 }), '10 条/页');
assert.equal(setup.optionText({ value: 20 }), '20 条/页');
state.total = 0;
await vue.nextTick();
assert.equal(state.page, 4, 'a temporary empty total during loading must not jump to page one');
state.total = 12;
await vue.nextTick();
assert.equal(state.page, 2, 'a confirmed smaller nonempty result still clamps the last page');
app.unmount();
console.log('Shared pagination checks passed (10 rows, URL changes, labels, transient empty totals, last-page clamp).');
