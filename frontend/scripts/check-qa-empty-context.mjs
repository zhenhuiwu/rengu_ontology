// Run without a server/provider: actual conversation script + actual answer template.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import ts from 'typescript';
import * as vue from 'vue';
import { compileTemplate, parse } from '@vue/compiler-sfc';
import { renderToString } from 'vue/server-renderer';

const filename = 'QaConversation.vue';
const { descriptor, errors } = parse(readFileSync(new URL('../src/pages/qa/components/QaConversation.vue', import.meta.url), 'utf8'), { filename });
assert.equal(errors.length, 0);
const defaultRange = '按已发布指标默认最近7天：2026-10-22 至 2026-10-28';
const turn = { id: 'T1', kind: 'answer', question: '验收订单实付金额汇总', answer: { card: 'table', text: `金额：123。${defaultRange}`, basis: { defaultRange }, table: { columns: [{ key: 'c0', title: '金额' }], items: [{ c0: 123 }] } } };
const detail = vue.ref({ scope: '', caliber: null, turns: [turn] });
const session = { detail, isNew: vue.ref(false), activeTurns: vue.ref([]), activeId: vue.ref('S1'), draft: vue.ref(''), live: vue.ref(true), sending: vue.ref(false), loading: vue.ref(false), streamStatus: vue.ref(''), send() {} };
const output = {};
const imports = {
  vue: { ...vue, onMounted() {}, watch() {} },
  'vue-router': { useRoute: () => ({ query: {} }) },
  '@ant-design/icons-vue': {}, '@/mock': {},
  '@/composables/useReadonly': { useReadonly: () => ({ readonly: vue.ref(false), reason: vue.ref('') }) },
  '../useQaSession': { useQaSession: () => session, clarifyFollowUp() {} },
  '@/stores/app': { useAppStore: () => ({ user: { name: '测试人' } }) },
};
runInNewContext(ts.transpileModule(`${descriptor.scriptSetup.content}\nObject.assign(exports,{chainMeta,tableCols,tableRows,showTable});`, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } }).outputText, {
  exports: output, defineProps: () => ({ applied: false, tips: [] }), defineEmits: () => () => {},
  require: name => { if (name in imports) return imports[name]; if (name.endsWith('.vue')) return {}; throw new Error(name); },
});
const answerTemplate = descriptor.template.content.match(/<template v-else-if="isChain\(t\)">([\s\S]*?)<\/template>/)[1];
const compiled = compileTemplate({ source: `<div>${answerTemplate}</div>`, filename, id: filename });
assert.equal(compiled.errors.length, 0);
const rendered = {};
runInNewContext(ts.transpileModule(compiled.code, { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText, { exports: rendered, require: () => vue });
async function html() {
  const app = vue.createSSRApp({ setup: () => ({ ...output, t: turn }), render: rendered.render });
  app.component('ApartmentOutlined', { render: () => null });
  app.component('a-table', { render: () => null });
  return renderToString(app);
}
for (const scope of ['', '   ', null]) {
  detail.value.scope = scope;
  detail.value.caliber = null;
  assert.equal(output.chainMeta.value, '');
  const markup = await html();
  assert.ok(!markup.includes('qa-b-meta') && !markup.includes('—') && !markup.includes('大区 ='));
  assert.ok(markup.includes(defaultRange), 'applied default time remains in the actual answer');
}
detail.value.scope = '华东';
assert.equal(output.chainMeta.value, '按 范围 = 华东 回答');
assert.ok((await html()).includes('范围 = 华东'));
detail.value.scope = '';
detail.value.caliber = '不含税';
assert.equal(output.chainMeta.value, '按 口径 = 不含税 回答');
detail.value.scope = '华东';
assert.equal(output.chainMeta.value, '按 范围 = 华东、口径 = 不含税 回答');
console.log('QA empty context, real scope/caliber and applied default-range rendering passed; provider calls: 0.');
