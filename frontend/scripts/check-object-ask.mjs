// Run: node scripts/check-object-ask.mjs (real scoped ask page state, no server writes).
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import ts from 'typescript';
import * as vue from 'vue';
import { parse, compileTemplate } from '@vue/compiler-sfc';

const path = 'pages/objects/parts/ObjAskModal.vue';
const parsed = parse(readFileSync(new URL(`../src/${path}`, import.meta.url), 'utf8'), { filename: path });
assert.equal(parsed.errors.length, 0);
assert.equal(compileTemplate({ source: parsed.descriptor.template.content, filename: path, id: path }).errors.length, 0);
const { outputText } = ts.transpileModule(`${parsed.descriptor.scriptSetup.content}\nexport { send, answer, error, loading, q };`, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } });
const context = vue.reactive({ demoAs: 'admin', previewAs: null });
const open = vue.ref(true);
const defer = () => { let resolve; const promise = new Promise((r) => { resolve = r; }); return { promise, resolve }; };
const tick = () => new Promise(setImmediate);
let read = async () => { throw { code: 'DATA_NOT_CONFIGURED', message: '范围分析尚未配置' }; };
const imports = {
  vue, '@ant-design/icons-vue': {},
  '@/composables/useReadonly': { useReadonly: () => ({ readonly: vue.ref(false), reason: vue.ref('') }) },
  '@/composables/usePreview': { usePreview: () => ({ active: vue.ref(false) }) },
  '@/mock/objects-qa': { answerFor() { assert.fail('real mode must not generate a demo answer'); } },
  '@/mock/format': { fmtInt: String },
  'ant-design-vue': { message: { error() {} } },
  '@/api/qaObjects': { scopedAsk: (...args) => read(...args) },
  '@/api/qa': { qaReal: () => true },
  '../../qa/useQaSession': { errText: (e) => e.message }, '@/api/http': { currentApiContext: () => context },
};
const exports = {};
const scope = vue.effectScope();
scope.run(() => runInNewContext(outputText, {
  exports, defineProps: () => ({ scope: { kind: 'store', label: '数据库范围', ids: [] }, suggestions: [] }), defineModel: () => open,
  require: (name) => { if (name in imports) return imports[name]; if (name.endsWith('.vue')) return {}; throw new Error(name); },
}));
exports.send('销售额'); await tick();
assert.equal(exports.answer.value, null);
assert.equal(exports.error.value, '范围分析尚未配置');
assert.equal(exports.q.value, '销售额', 'unsupported input remains available to edit');
assert.equal(exports.loading.value, false);
const old = defer(); read = () => old.promise;
exports.send('实付金额合计');
context.demoAs = 'viewer'; context.demoAs = 'admin';
old.resolve({ text: 'PRIVATE OLD ANSWER', rows: [], basis: 'old' }); await tick();
assert.equal(exports.answer.value, null);
const closed = defer(); read = () => closed.promise;
exports.send('实付金额合计'); open.value = false; await vue.nextTick();
closed.resolve({ text: 'OLD CLOSED ANSWER', rows: [] }); await tick();
assert.equal(exports.answer.value, null);
open.value = true; await vue.nextTick();
read = async () => ({ text: '数据库基表汇总', rows: [], basis: '可见记录' });
exports.send('实付金额合计'); await tick();
assert.equal(exports.answer.value.text, '数据库基表汇总');
scope.stop();
console.log('Scoped ask checks passed (explicit unavailable state, retained input, real-only answers, stale context/closed modal).');
