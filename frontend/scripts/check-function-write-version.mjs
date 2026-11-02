// Run: node scripts/check-function-write-version.mjs. Local SFC check and one controlled successful save.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import { parse, compileScript, compileTemplate } from '@vue/compiler-sfc';
import ts from 'typescript';
import * as vue from 'vue';

for (const path of ['components/BuildAssistant.vue', 'pages/functions/RealFunctions.vue']) {
  const filename = new URL(`../src/${path}`, import.meta.url);
  const { descriptor, errors } = parse(readFileSync(filename, 'utf8'), { filename: path });
  assert.equal(errors.length, 0);
  compileScript(descriptor, { id: path });
  const template = compileTemplate({ source: descriptor.template.content, filename: path, id: path, compilerOptions: { expressionPlugins: ['typescript'] } });
  assert.equal(template.errors.length, 0);
}
const context = { ontologyId: 'synthetic-device', demoAs: 'synthetic-user' };
const route = vue.reactive({ query: { form: 'new' } });
const invalidated = [];
const functionRow = { id: 'FN-SYNTHETIC', name: 'inspection_label', title: '巡检标签', desc: '', code: 'def inspection_label(score: int) -> str:\n    return "正常"', timeoutS: 5, sampleIn: {}, inputs: [], output: { type: 'str' }, version: 'v1.0.0', lockVersion: 1 };
const imports = {
  vue: { ...vue, onMounted() {}, onBeforeUnmount() {} },
  'vue-router': { useRoute: () => route, useRouter: () => ({ replace: async ({ query }) => { Object.assign(route.query, query); } }) },
  'ant-design-vue': { message: { info() {}, warning() {}, success() {}, error() {} } },
  '@/api/http': { currentApiContext: () => context, isApiError: () => false },
  '@/api/assistant': { consumeAssistantPrefill: () => null },
  '@/api/static': { invalidateStaticAfterWrite: ver => invalidated.push(ver) },
  '@/api/functions': {
    listFunctions: async () => ({ items: [], total: 0, page: 1 }),
    getFunction: async () => functionRow,
    functionVersions: async () => ({ items: [], total: 0, page: 1 }),
    createFunction: async () => ({ id: functionRow.id, status: '已发布', ver: 'v2026.11.1' }),
  },
  '@/stores/confirm': { useConfirmStore: () => ({}) },
  '@/composables/useReadonly': { useReadonly: () => ({ readonly: vue.ref(false), reason: vue.ref('') }) },
  '@/composables/useUnsavedGuard': { useUnsavedGuard: () => fn => fn(), confirmDiscard: async () => true },
};
const raw = readFileSync(new URL('../src/pages/functions/RealFunctions.vue', import.meta.url), 'utf8');
const script = raw.match(/<script setup lang="ts">([\s\S]*?)<\/script>/)[1];
const output = ts.transpileModule(`${script}\nexport { form, persist, intentContext };`, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } }).outputText;
const page = {}, scope = vue.effectScope();
scope.run(() => runInNewContext(output, { exports: page, window: {}, require: name => {
  if (name.endsWith('.vue')) return {};
  assert.ok(name in imports, name); return imports[name];
} }));
page.intentContext.value = JSON.stringify(context);
await page.persist({ kind: 'save', fresh: true, name: functionRow.name, body: { title: functionRow.title, desc: '', timeoutS: 5, code: functionRow.code }, formText: JSON.stringify(page.form) });
assert.deepEqual(invalidated, ['v2026.11.1'], 'successful function save must invalidate with the returned ontology checkpoint, not function semver v1.0.0');
scope.stop();
console.log('assistant/functions SFC and successful function checkpoint invalidation passed (no API/DB/model)');
