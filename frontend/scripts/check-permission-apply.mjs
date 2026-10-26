// Run: node scripts/check-permission-apply.mjs (no server or database writes).
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import ts from 'typescript';
import * as vue from 'vue';
function load(path, imports, names, globals = {}) {
  const source = readFileSync(new URL(`../src/${path}`, import.meta.url), 'utf8').match(/<script setup lang="ts">([\s\S]*?)<\/script>/)[1];
  const { outputText } = ts.transpileModule(`${source}\nexport { ${names} };`, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } });
  const exports = {};
  runInNewContext(outputText, { exports, console, ...globals, require: (key) => {
    if (key in imports) return imports[key];
    if (key.endsWith('.vue')) return {};
    throw new Error(`Missing dependency: ${key}`);
  } });
  return exports;
}
const emitted = [];
const formProps = vue.reactive({ open: true, submitting: false, readonly: false });
const form = load('pages/qa/components/QaApplyDrawer.vue', {
  vue: { ...vue, watch() {} }, '@/api/qa': { qaReal: () => true },
  '@/mock': { QA_APPLY: { scope: 'DEMO SCOPE', object: 'DEMO METRIC', durations: ['30 天', '90 天', '180 天'] } },
}, 'scope, object, reason, submit, canSubmit', { defineProps: () => formProps, defineEmits: () => (...args) => emitted.push(args) });
assert.equal(form.scope.value, ''); assert.equal(form.object.value, ''); assert.equal(form.canSubmit.value, false);
form.scope.value = ' 新业务范围 '; form.object.value = ' 新业务指标 '; form.reason.value = ' 工作需要 ';
form.submit(); assert.equal(emitted.length, 1);
assert.equal(emitted[0][1].scope, '新业务范围'); assert.equal(emitted[0][1].object, '新业务指标');
formProps.readonly = true; form.submit(); assert.equal(emitted.length, 1);
const ro = vue.ref(false);
const requests = [];
let staged = 0;
const details = [];
const session = { real: true, live: vue.ref(true), detail: vue.ref({ id: 'S-1', title: '真实会话', turns: [{ id: 'T-080', kind: 'denied', code: 'OUT_OF_SCOPE' }] }),
  activeId: vue.ref('S-1'), sessions: vue.ref([]), isNew: vue.ref(false), activeTurns: vue.ref([]), draft: vue.ref(''), sending: vue.ref(false), loading: vue.ref(false), loadDetail: async (id) => details.push(id) };
const imports = {
  vue: { ...vue, watch() {}, onMounted() {} },
  'vue-router': { useRoute: () => ({ query: {} }), useRouter: () => ({ replace() {}, push() {} }) },
  'ant-design-vue': { message: { success() {}, error() {}, warning() {} } }, '@ant-design/icons-vue': {},
  '@/composables/useDrawer': { useDrawer: () => ({}) }, '@/stores/confirm': { useConfirmStore: () => ({}) },
  '@/composables/useReadonly': { useReadonly: () => ({ readonly: ro, reason: vue.ref('') }) },
  '@/composables/usePreview': { usePreview: () => ({ active: vue.ref(false), limited: vue.ref([]) }) },
  '@/mock': {}, './useQueryParam': { useQueryParam: () => vue.ref('') }, './useQaSession': { useQaSession: () => session, errText: String },
  './qaScope': { useScopeCaliber: () => ({}) }, '@/pages/objects/objMeta': { metaContextKey: () => 'same-user' },
  '@/api/qa': { createPermissionRequest: async (body) => requests.push(body), actionsReal: () => true, stageAction: async () => { staged++; } },
};
const page = load('pages/qa/Workbench.vue', imports, 'onApplied, runAction');
await page.onApplied({ scope: '新业务范围', object: '新业务指标', reason: '工作需要', duration: '90 天' });
assert.equal(requests.length, 1); assert.equal(requests[0].sourceTurnId, 'T-080');
assert.equal(requests[0].scope, '新业务范围'); assert.equal(requests[0].object, '新业务指标'); assert.equal(requests[0].duration, 90);
await page.runAction(); assert.equal(staged, 0, 'a generic suggestion cannot stage the fixed demo customer segment');
ro.value = true; await page.onApplied({ scope: 'x', object: 'y', reason: 'z', duration: '30 天' }); assert.equal(requests.length, 1);
const conversation = load('pages/qa/components/QaConversation.vue', {
  ...imports, '../useQaSession': { useQaSession: () => session }, '@/stores/app': { useAppStore: () => ({ user: { name: 'User' } }) }, '@/api/qaPages': {},
}, 'canApply', { defineProps: () => ({ tips: [], applied: false }), defineEmits: () => () => {} });
assert.equal(conversation.canApply({ code: 'OUT_OF_SCOPE' }), true);
assert.equal(conversation.canApply({ code: 'NO_PERMISSION' }), true);
assert.equal(conversation.canApply({ code: 'QA_LLM_UNAVAILABLE' }), false);
console.log('Permission apply checks passed (OUT_OF_SCOPE, real targets, source ownership context, readonly/busy guards, no demo action staging).');
