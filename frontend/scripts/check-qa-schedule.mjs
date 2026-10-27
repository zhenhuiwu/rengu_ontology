// Actual PageDrawer script with a controlled API boundary; no server or database writes.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import ts from 'typescript';
import * as vue from 'vue';
import { parse, compileTemplate } from '@vue/compiler-sfc';

const file = readFileSync(new URL('../src/pages/qa/pages/PageDrawer.vue', import.meta.url), 'utf8');
const { descriptor, errors } = parse(file);
assert.equal(errors.length, 0);
assert.equal(compileTemplate({ source: descriptor.template.content, filename: 'PageDrawer.vue', id: 'schedule' }).errors.length, 0);
const props = vue.reactive({ tab: 'schedule', readonly: false, reason: '', page: {
  id: 'REAL-PAGE', owner: '真实拥有者', ownerId: 'OWNER', name: '真实页面', cards: [], canSchedule: true,
  refresh: { freq: '手动', time: '--', onFail: '保留上次结果' }, sub: null,
  share: { mode: '指定成员', members: [{ userId: 'MEMBER', name: '真实共享成员', perm: '可查看' }], link: false, allowExport: false },
  scheduleTimezone: 'Asia/Shanghai', nextRun: null,
} });
const context = vue.reactive({ ontologyId: '1', demoAs: 'OWNER' });
const events = [], calls = [], failures = [];
let response;
const api = { access: (page) => page, saveSchedule: async (id, refresh, sub) => {
  calls.push({ id, refresh: structuredClone(vue.toRaw(refresh)), sub: structuredClone(vue.toRaw(sub)) });
  if (response) return response;
  return { refresh: { ...refresh }, sub: { ...sub }, nextRun: refresh.freq === '手动' ? '不自动刷新' : '2031-01-02 23:30', scheduleTimezone: 'Asia/Shanghai' };
} };
const dependencies = {
  'vue-router': { onBeforeRouteUpdate() {} }, '@/api/http': { currentApiContext: () => context },
  'ant-design-vue': { message: { success() {}, warning() {} } }, '@ant-design/icons-vue': {},
  '@/composables/useUnsavedGuard': { useUnsavedGuard() {}, confirmDiscard: async () => true },
  '@/stores/confirm': { useConfirmStore: () => ({ ask: async () => true }) },
  '@/api/qaPages': api, '../qaFallback': { qaWriteReal: vue.ref(true), qaWriteFail: (...args) => failures.push(args) },
  '@/mock/qa-pages': { PEOPLE: [{ name: 'DEMO' }], freqLabel: (plan) => plan.freq },
};
const exports = {};
const source = descriptor.scriptSetup.content + '\nexport { plan, sub, nextRun, scheduleDisabled, subscriptionPeople, schedDirty, saveSchedule, resetDrafts };';
runInNewContext(ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } }).outputText, {
  exports, defineProps: () => props, defineEmits: () => (...args) => events.push(args),
  require: (name) => name === 'vue' ? vue : name in dependencies ? dependencies[name] : name.endsWith('.vue') ? {} : (() => { throw Error(name); })(),
});
const state = exports;
assert.equal(state.schedDirty.value, false, 'a page without a subscription starts clean');
assert.equal(state.nextRun.value, '不自动刷新');
assert.equal(state.subscriptionPeople.value.map((p) => p.value).join(','), '真实拥有者,真实共享成员');
assert.ok(!state.subscriptionPeople.value.some((p) => p.value === 'DEMO' || p.value === '王敏'));
state.plan.value = { freq: '每天', time: '23:30', onFail: '保留上次结果' };
state.sub.value.on = true;
assert.equal(state.nextRun.value, '保存后按计划排定');
await state.saveSchedule();
assert.equal(calls[0].refresh.freq, '每天');
assert.equal(calls[0].sub.channel, '站内信');
assert.equal(props.page.sub.on, true);
assert.equal(props.page.nextRun, '2031-01-02 23:30');
assert.equal(props.page.subscriptionActive, true);
assert.equal(state.schedDirty.value, false);
state.sub.value.on = false; state.resetDrafts();
assert.equal(state.sub.value.on, true, 'reset restores persisted subscription state');
props.readonly = true; await state.saveSchedule();
props.readonly = false; props.page.canSchedule = false; await state.saveSchedule();
assert.equal(calls.length, 1, 'readonly and non-owner cannot save even by direct handler call');
props.page.canSchedule = true;
let finish;
response = new Promise((resolve) => { finish = resolve; });
state.sub.value.on = false;
const pending = state.saveSchedule();
context.ontologyId = '2';
finish({ refresh: state.plan.value, sub: { ...state.sub.value }, nextRun: '2031-01-03 23:30', scheduleTimezone: 'Asia/Shanghai' });
await pending;
assert.equal(props.page.sub.on, true, 'a late response cannot mutate a different workspace');
assert.equal(events.length, 1);
assert.equal(failures.length, 0);
console.log('Page schedule checks passed (save/reset, real recipients, next-run/timezone, readonly/owner, context race).');
