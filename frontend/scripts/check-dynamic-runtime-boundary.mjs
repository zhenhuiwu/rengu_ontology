// Run: node scripts/check-dynamic-runtime-boundary.mjs — real Vue setup, no network writes.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import ts from 'typescript';
import * as vue from 'vue';
import { parse, compileTemplate } from '@vue/compiler-sfc';

const tpl = { code: 'db', engine: 'db', scope: [], drivers: [], groups: [], impact: { columns: [] }, metrics: [], noDriverHint: '' };
const route = vue.reactive({ query: {} });
const messages = [];
const emptyPage = { items: [], total: 0, counts: { pending: 0, records: 0, unknown: 0 } };
let writes = 0, plans = 0;
const imports = {
  vue: { ...vue, onBeforeUnmount: () => {} },
  'vue-router': { useRoute: () => route, useRouter: () => ({ replace: () => {} }), onBeforeRouteUpdate: () => {} },
  'ant-design-vue': { message: { error: (x) => { throw Error(x); }, success: (x) => messages.push(x), warning: () => {} } },
  '@ant-design/icons-vue': {},
  '@/api/fallback': { backend: vue.reactive({ down: false }), fallBackIfDown: () => false },
  '@/api/http': { isApiError: () => false },
  '@/api/static': { getObjectTypeFacets: async () => [] },
  '@/stores/app': { useAppStore: () => ({ user: { name: '数据库用户' } }) },
  '@/stores/confirm': { useConfirmStore: () => ({ ask: async () => true }) },
  '@/composables/useReadonly': { useReadonly: () => ({ readonly: vue.ref(false), reason: vue.ref('') }) },
  '@/composables/usePagedQuery': { usePagedQuery: () => ({ page: vue.ref(1), size: vue.ref(12) }) },
  '@/composables/useUnsavedGuard': { useUnsavedGuard: () => {}, confirmDiscard: async () => true },
  '@/theme': { BRAND: '#123', GRAY: {} },
  '@/mock/paginate': { paginate: () => emptyPage },
  '@/mock/actions': { ACTIONS: [], ACTION_NAMES: [], ACTION_SYSTEMS: [], ACTION_META: {}, OBJ_KIND_LABEL: {}, stageAction: () => { throw Error('mock write'); } },
  '@/mock/scenarios': { SCENARIOS: [], scenarioById: () => undefined },
  '@/mock/scenarioTemplates': { SC_TEMPLATES: [tpl], SC_ENGINES: { db: {} }, templateOf: () => tpl, allFields: () => [], defaultsOf: () => ({}), noDriver: () => false },
  '@/api/dynamic': {
    dynamicApi: { isReal: () => true }, dynamicContextKey: () => 'db-current', listActionRuns: async () => emptyPage,
    getActionMeta: async () => ({ actionNames: [], actionSystems: [], actionMeta: {} }),
    getActionRun: async () => ({ id: '', status: '待确认' }),
    confirmActionRun: async (id) => ({ id, action: '真实免审动作', status: '成功' }),
    listScenarios: async () => emptyPage,
    getScenariosMeta: async () => ({ executorConfigured: false, unavailableReason: '尚未配置真实执行器', templates: [tpl] }),
    getScenario: async () => undefined, listScenarioImpacts: async () => emptyPage,
    runScenario: async () => { writes++; }, stageScenario: async () => { writes++; },
    getScenarioStagePlan: async () => { plans++; return []; },
  },
};
function load(file, expose) {
  const source = readFileSync(new URL(`../src/pages/ops/${file}.vue`, import.meta.url), 'utf8');
  const { descriptor, errors } = parse(source);
  assert.equal(errors.length, 0);
  assert.equal(compileTemplate({ source: descriptor.template.content, filename: file, id: file }).errors.length, 0);
  const { outputText } = ts.transpileModule(`${descriptor.scriptSetup.content}\n${expose}`, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } });
  const exports = {};
  runInNewContext(outputText, { exports, AbortController, window: { setTimeout }, require: (name) => {
    if (name in imports) return imports[name];
    if (name.endsWith('.vue')) return {};
    throw Error(`Missing dependency ${name}`);
  } });
  return exports;
}
const scope = vue.effectScope();
const scenarios = scope.run(() => load('Scenarios', 'export { remote, executorUnavailable, runBlocked, convertBlocked, run, loadPlans, doStage };'));
await new Promise(setImmediate);
assert.match(scenarios.executorUnavailable.value, /未配置/);
scenarios.remote.detail = { id: 'DB1', name: '数据库历史', owner: '数据库用户', status: '草稿', staged: [], scope: {}, drivers: {} };
await vue.nextTick();
assert.match(scenarios.runBlocked.value, /未配置/);
assert.match(scenarios.convertBlocked.value, /未配置/);
await scenarios.run(); await scenarios.loadPlans(); await scenarios.doStage();
assert.equal(writes, 0); assert.equal(plans, 0);
scope.stop();

const actionScope = vue.effectScope();
const actions = actionScope.run(() => load('Actions', 'export { remote, callOf, submit, pinned };'));
await new Promise(setImmediate);
actions.remote.meta = { actionNames: [], actionSystems: [{ id: 'DBSYS', name: '真实系统' }], actionMeta: { A1: { call: '/one' }, A2: { call: '/two' } } };
assert.equal(actions.callOf('重名动作', 'A2'), '/two');
assert.equal(actions.callOf('重名动作'), '--');
actions.remote.meta.actionMeta['重名动作'] = { call: '/other-type' };
assert.equal(actions.callOf('重名动作', 'retired-missing-id'), '--');
actions.pinned.value = 'DBACT';
actions.remote.detail = { id: 'DBACT', typeId: 'A2', action: '真实免审动作', status: '待确认', source: { type: '手动' }, log: [], objKind: 'product', objLabel: '真实商品' };
await actions.submit();
assert.equal(messages.at(-1), '已提交：真实免审动作，成功');
actionScope.stop();
console.log('dynamic runtime boundary checks passed');
