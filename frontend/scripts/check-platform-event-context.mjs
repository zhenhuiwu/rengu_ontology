// Execute the real creation form; no HTTP, model, DB or saved definition.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import ts from 'typescript';
import * as vue from 'vue';
import { parse, compileTemplate } from '@vue/compiler-sfc';

const filename = new URL('../src/pages/ontology/parts/NewActionEventForm.vue', import.meta.url);
const { descriptor } = parse(readFileSync(filename, 'utf8'));
const compiled = compileTemplate({ source: descriptor.template.content, filename: filename.pathname, id: 'platform-event' });
assert.deepEqual(compiled.errors, []);
const source = `${descriptor.scriptSetup.content}\nexports.page={form,choices,platformActionEvent,load};`;
const js = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
const imports = {
  vue, 'vue-router': { useRoute: () => ({ query: {} }), useRouter: () => ({ replace() {} }) },
  'ant-design-vue': { message: {} }, '@/api/http': { apiClient: () => ({ get: async path => path === '/build/owners' ? [] : { items: [], total: 0 } }), currentApiContext: () => ({ ontologyId: '2' }) },
  '@/api/action': { actionContextKey: () => 'scope2', crWriteOn: () => true },
  '@/composables/useReadonly': { useReadonly: () => ({ readonly: vue.ref(false), reason: vue.ref('') }) },
  '@/stores/confirm': { useConfirmStore: () => ({}) },
};
const exports = {}, scope = vue.effectScope();
scope.run(() => runInNewContext(js, {
  exports, require: name => { assert.ok(name in imports, name); return imports[name]; },
  defineProps: () => ({ kind: 'event', options: {}, rowForms: {} }), defineModel: () => vue.ref(false),
}));
const { form, choices } = exports.page;
await exports.page.load();
assert.equal(form.dedupWindow, '24 小时');
form.api = 'ActionExecuted'; form.source = '动作';
await vue.nextTick();
assert.equal(choices({ k: 'objKind' }).some(item => item.value === 'platform:ActionRun'), true);
assert.equal(choices({ k: 'targetKind' }).some(item => item.value === 'platform:ActionRun'), false);
form.objKind = 'platform:ActionRun'; form.source = '外部系统'; await vue.nextTick();
assert.equal(form.objKind, null);
assert.equal(choices({ k: 'objKind' }).length, 0);
form.source = '动作'; form.api = 'OtherEvent';
assert.equal(choices({ k: 'objKind' }).length, 0);
scope.stop();
console.log('ActionExecuted/action-only platform context + stale selection reset: passed');
