// Run: node scripts/check-object-behavior.mjs (real Vue scripts, no server writes).
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import ts from 'typescript';
import * as vue from 'vue';
import { parse, compileTemplate } from '@vue/compiler-sfc';

function load(file, imports, props, expose) {
  const parsed = parse(readFileSync(new URL(`../src/${file}`, import.meta.url), 'utf8'), { filename: file });
  assert.equal(parsed.errors.length, 0);
  assert.equal(compileTemplate({ source: parsed.descriptor.template.content, filename: file, id: file }).errors.length, 0);
  const { outputText } = ts.transpileModule(`${parsed.descriptor.scriptSetup.content}\n${expose}`, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } });
  const exports = {};
  runInNewContext(outputText, { exports, defineProps: () => props, require: (name) => {
    if (name in imports) return imports[name];
    if (name.endsWith('.vue')) return {};
    throw new Error(`Missing dependency ${name}`);
  } });
  return exports;
}
const tick = () => new Promise(setImmediate);
const graphEdges = {};
runInNewContext(ts.transpileModule(readFileSync(new URL('../src/components/graphEdges.ts', import.meta.url), 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
}).outputText, { exports: graphEdges });
const defer = () => { let resolve; const promise = new Promise((r) => { resolve = r; }); return { promise, resolve }; };
const context = vue.reactive({ demoAs: 'admin', ver: null, previewAs: null });
const props = vue.reactive({ objectId: 'db-order', readonly: false });
const route = vue.reactive({ query: {} });
const pageQuery = () => ({ page: vue.ref(1), size: vue.ref(10), defaultSize: 10 });
const draftView = { readonly: vue.ref(false), reason: vue.ref(''), draft: vue.ref(null), isDraft: vue.ref(false), editTip: vue.ref('') };
const objects = [{ id: 'db-order', name: '数据库订单', api: 'DbOrder' }];
const calls = [];
let fail = false;
let listRead;
const behaviorStats = (actions = [{ id: 'A1', name: '数据库动作', targetApi: 'DbOrder', usedBy: [] }, { id: 'A2', name: '数据库动作2', targetApi: 'DbOrder', usedBy: [] }]) => ({ actions, actions30d: { A1: { total: 6, rate: 4/6, failed: 1, unknown: 1 } }, centerStatusCounts: { '待确认': 71, '结果未知': 9 }, rules: {} });
let behaviorRead = async () => behaviorStats();
let centerRead;

let actionLifecycle = async () => ({ transitions: [{ id: 'DB-TR', actionId: 'A1' }] });
const actionApi = {
  isReal: () => true,
  get: async (path, options) => {
    calls.push({ path, query: options?.query });
    if (fail) throw new Error('offline');
    if (path === '/action-types') {
      if (listRead) return listRead();
      return options.query.page === 1
        ? { items: [{ id: 'unrelated', targetApi: 'Other' }, { id: 'A1', targetApi: 'DbOrder' }], total: 3 }
        : { items: [{ id: 'A2', targetApi: 'DbOrder' }], total: 3 };
    }
    if (path.endsWith('/params')) return { items: [{ api: 'dbParam', name: '数据库参数' }], total: 1 };
    return { id: path.split('/').at(-1), targetApi: 'DbOrder', target: '数据库订单', name: '数据库动作', usedBy: [] };
  },
};
const dynamicApi = { isReal: () => true, get: async (path, options) => {
  calls.push({ path, query: options.query });
  assert.equal(options.query.objKind, props.objectId);
  assert.equal(options.query.scope, 'center');
  if (centerRead) return centerRead(options.query);
  return { items: [{ id: `DB-${options.query.page}`, source: { type: '手动' }, status: '成功', objKind: 'db-order' }], total: 2 };
} };
const shared = {
  vue, 'vue-router': { useRoute: () => route, useRouter: () => ({ replace() {} }) },
  '@ant-design/icons-vue': {}, '@/api/http': { currentApiContext: () => context, isApiError: (e) => typeof e?.status === 'number' },
  '@/api/static': { OBJECT_TYPE_LIST: objects, isStaticReal: () => true },
  '@/composables/usePagedQuery': { usePagedQuery: pageQuery },
  '@/mock/paginate': { paginate: (items) => ({ items, total: items.length }) },
  '../useDraftView': { useDraftView: () => draftView },
};
const actionsScope = vue.effectScope();
const actions = actionsScope.run(() => load('pages/ontology/parts/object/ObjBehaviorActions.vue', {
  ...shared,
  '@/api/action': { actionApi },
  '@/api/objectAutomation': { fetchBehaviorStats: async () => { if (fail) throw Error('offline'); return behaviorRead(); } },
  '@/api/dynamic': { dynamicApi, getLifecycle: () => actionLifecycle() },
  '@/mock/actionEvent': { ACTION_TYPES: [{ id: 'DEMO' }], paramsOf() { assert.fail('no mock params'); }, runStats() { assert.fail('no mock stats'); } },
  '@/mock/actions': { ACTIONS: [{ id: 'DEMO' }], OBJ_KIND_LABEL: {}, ACTION_OBJ_KIND: {} },
  '@/mock/lifecycle': { OBJ_TYPE_KIND: {}, OBJ_TYPE_NAME: {}, lifecycleOf() { assert.fail('no mock lifecycle'); } },
}, props, 'export { types, all, paramsOf, runStats, loadActions, loadError, objName, drives, pq, paged, loadRecords, status, actName, pendingN, unknownN, loadParams };'));
assert.equal(actions.types.value.length, 0);
await tick();
assert.equal(actions.types.value.length, 2);
assert.equal(actions.types.value.some((t) => t.id === 'unrelated'), false);
assert.equal(actions.objName.value, '数据库订单');
assert.equal(calls.some(c=>c.path.endsWith('/params')),false,'closed drawers do not preload parameters');
route.query.actype = 'A1'; await tick(); assert.equal(actions.paramsOf(actions.types.value[0])[0].api, 'dbParam');
assert.ok(calls.filter(c=>c.path.endsWith('/params')).every(c=>c.path.includes('/A1/')),'only the opened type loads params');
assert.equal(actions.runStats('A1').total, 6);
assert.equal(actions.runStats('A1').rate, 4 / 6);
assert.equal(actions.drives(actions.types.value[0])[0].id, 'DB-TR');
assert.equal(actions.all.value.length, 1, 'center stores only current page');
assert.equal(actions.paged.value.total,2);assert.equal(actions.pendingN.value,71);assert.equal(actions.unknownN.value,9);
assert.equal(calls.filter(c=>c.path==='/action-runs').length,1,'no full center loop');
const centerLater=defer();centerRead=()=>centerLater.promise;actions.pq.page.value=2;assert.equal(actions.paged.value.total,2);
centerLater.resolve({items:[{id:'CENTER2'}],total:23,page:2});await tick();assert.equal(actions.paged.value.items[0].id,'CENTER2');
centerRead=async q=>({items:[{id:'LAST'}],total:23,page:Math.min(q.page,3)});actions.pq.page.value=99;await tick();assert.equal(actions.pq.page.value,3);
actions.status.value='失败';actions.actName.value='A2';await tick();const lastCenter=calls.filter(c=>c.path==='/action-runs').at(-1);assert.equal(lastCenter.query.status,'失败');assert.equal(lastCenter.query.actionType,'A2');assert.equal(actions.pendingN.value,71,'filtering never recomputes header counts from current page');
const oldList = defer();
behaviorRead = () => oldList.promise;
const pending = actions.loadActions();
behaviorRead = async () => behaviorStats([]);
context.demoAs = 'viewer'; context.demoAs = 'admin';
await tick();
oldList.resolve(behaviorStats([{id:'OLD'}]));
await pending;
assert.equal(actions.types.value.length, 0, 'away-and-back must invalidate old reads');
fail = true;
await actions.loadActions();
assert.equal(actions.types.value.length, 0);
assert.equal(actions.all.value.length, 0);
assert.ok(actions.loadError.value.includes('offline'));
fail = false; behaviorRead = async () => behaviorStats([{ id: 'A1', name: '数据库动作', targetApi: 'DbOrder' }]);
actionLifecycle = async () => { throw { status: 404 }; };
await actions.loadActions();
assert.equal(actions.loadError.value, '');
assert.equal(actions.types.value.length, 1);
assert.equal(actions.drives(actions.types.value[0]).length, 0, 'missing lifecycle is unconfigured, never demo transitions');
actionsScope.stop();

const emptyDraft = () => ({ addStates: [], addTransitions: [], removeTransitions: [], editTransitions: {} });
const lifecycle = { objectId: 'db-order', objectName: '数据库订单', states: [{ key: 'DB', name: '数据库状态', count: 1 }], transitions: [], versions: [], draft: null };
let lcRead = async () => lifecycle;
let illegalRead = async () => [{ from: 'DB', to: 'BAD', count: 2 }];
let changeRead = async () => ({ items: [{ objId: 'DB' }], total: 1 });
route.query.lcv = 'changes';
const lcScope = vue.effectScope();
const lc = lcScope.run(() => load('pages/ontology/parts/object/ObjBehaviorLifecycle.vue', {
  ...shared,
  '@/components/graphEdges': graphEdges,
  '@/stores/app': { useAppStore: () => vue.reactive({ viewVersion: null }) },
  '@/stores/confirm': { useConfirmStore: () => ({}) },
  '@/composables/useUnsavedGuard': { useUnsavedGuard() {}, confirmDiscard: async () => true },
  '@/api/releases': { releaseByVer: () => null }, '@/theme': { GRAY: {}, STATUS: {} },
  '@/components/objectRegistry': { canResolve: () => false },
  '@/api/action': { actionApi: { isReal: () => false }, actionTypes: () => [], eventTypes: () => [] },
  '@/api/dynamic': { dynamicApi, getLifecycle: () => lcRead(), listLifecycleIllegal: () => illegalRead(), listLifecycleChanges: () => changeRead() },
  'ant-design-vue': { message: { error() {} } },
  '@/mock/lifecycle': { OBJ_TYPE_KIND: {}, OBJ_TYPE_NAME: {}, LC_DRAFT_EDITS: {}, emptyLcDraft: emptyDraft, lifecycleAsOf() { assert.fail('no mock lifecycle'); }, illegalOf() { assert.fail('no mock illegal'); } },
}, props, 'export { base, illegal, changePage, loadLc, loadChanges, loadError, editable, remote, pq };'));
await tick();
assert.equal(lc.base.value.states[0].key, 'DB');
assert.equal(lc.illegal.value.length, 1);
assert.equal(lc.changePage.value.total, 1);
const laterChanges = defer(); changeRead = () => laterChanges.promise;
lc.pq.page.value = 2;
assert.equal(lc.changePage.value.total, 1, 'page request retains known total while loading');
laterChanges.resolve({ items: [{ objId: 'PAGE2' }], total: 23, page: 2 }); await tick();
assert.equal(lc.changePage.value.total, 23); assert.equal(lc.changePage.value.items[0].objId, 'PAGE2');
changeRead = async () => ({ items: [{ objId: 'CLAMPED' }], total: 23, page: 3 });
lc.pq.page.value = 99; assert.equal(lc.changePage.value.total, 23); await tick();
assert.equal(lc.pq.page.value, 3, 'backend clamped page is applied'); assert.equal(lc.changePage.value.total, 23);
const staleLc = defer(); const staleChanges = defer();
lcRead = () => staleLc.promise; changeRead = () => staleChanges.promise;
const lcPending = lc.loadLc(); const changePending = lc.loadChanges();
lcRead = async () => ({ ...lifecycle, states: [] }); illegalRead = async () => []; changeRead = async () => ({ items: [], total: 0 });
context.previewAs = 'viewer'; context.previewAs = null;
assert.equal(lc.base.value, undefined, 'context switch clears old definition immediately');
assert.equal(lc.illegal.value.length, 0);
assert.equal(lc.changePage.value.total, 0);
await tick();
staleLc.resolve(lifecycle); staleChanges.resolve({ items: [{ objId: 'OLD' }], total: 1 });
await lcPending; await changePending;
assert.equal(lc.base.value.states.length, 0);
assert.equal(lc.changePage.value.total, 0);
lcRead = async () => { throw new Error('LC offline'); };
await lc.loadLc();
assert.equal(lc.base.value, undefined);
assert.equal(lc.editable.value, false);
assert.ok(lc.loadError.value.includes('LC offline'));
const disposedChanges = defer(); changeRead = () => disposedChanges.promise;
const disposedPending = lc.loadChanges();
lcScope.stop();
disposedChanges.resolve({ items: [{ objId: 'UNMOUNTED' }], total: 999, page: 1 }); await disposedPending;
assert.equal(lc.changePage.value.total, 0, 'unmount rejects in-flight changes');
console.log('Object behavior DB checks passed (target API, pagination, params/stats, errors, empty data, stale identity/preview reads).');
