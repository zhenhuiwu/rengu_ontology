// Run: node scripts/check-object-automation.mjs. No server or database writes.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import ts from 'typescript';
import * as vue from 'vue';
const defer = () => { let resolve; const promise = new Promise((r) => { resolve = r; }); return { promise, resolve }; };
const context = vue.reactive({ demoAs: 'old', previewAs: 'preview-old', ver: '' });
const contextKey = (ctx = context) => JSON.stringify([ctx.demoAs, ctx.previewAs, ctx.ver]);
function load(path, dependencies, expose = '', globals = {}) {
  let source = readFileSync(new URL(`../src/${path}`, import.meta.url), 'utf8');
  if (path.endsWith('.vue')) source = source.match(/<script setup lang="ts">([\s\S]*?)<\/script>/)[1];
  const { outputText } = ts.transpileModule(`${source}\n${expose}`, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } });
  const exports = {};
  runInNewContext(outputText, { exports, ...globals, require: (key) => key in dependencies ? dependencies[key] : key.endsWith('.vue') ? {} : (() => { throw Error(key); })() });
  return exports;
}
const oldRules = defer(); const requests = [];
let failure = false, empty = false;
const paged = (n, create, total = 201) => ({ items: Array.from({ length: Math.max(0, Math.min(200, total-(n-1)*200)) }, (_, i) => create((n-1)*200+i)), total });
const client = { isReal: () => true, get: async (path, opts) => {
  requests.push({ path, opts });
  if (failure) throw new Error('403 FORBIDDEN');
  const page = opts.query.page;
  if (path === '/object-types/store') return { name: 'Database store', api: 'StoreV2' };
  if (path === '/rules') {
    if (opts.headers['X-Demo-As'] === 'old' && page === 1) return oldRules.promise;
    return paged(page, (i) => ({ id: `R${i}`, objKind: i === 0 || i === 200 ? 'store' : 'other' }), empty ? 0 : 201);
  }
  if (path.endsWith('/behavior/stats')) return {rules:{R0:{hits7d:201,alerts7d:88,pending:200}},centerStatusCounts:{},actions30d:{},actions:[]};
  if (path.endsWith('/automation/hits')) return {items:[{id:'H',ruleId:'R0'}],total:31,page:Math.min(opts.query.page,4),size:opts.query.size};
  if (path.endsWith('/hits')) { assert.equal(opts.query.range, '7d'); return paged(page, (i) => ({ id: `H${i}`, ruleId: 'R0', at: '2026-10-25', result: '待处理', alertId: 'AL' }), path.includes('R0/') ? 201 : 0); }
  if (path === '/action-runs') { assert.equal(opts.query.scope, 'all'); return paged(page, (i) => ({ status: i < 200 ? '待确认' : '审批中' }), opts.query.ruleId === 'R0' ? 201 : 0); }
  if (path.endsWith('/stats')) return { weekly: 3 };
  throw Error(path);
} };
const events = [{ id: 'Ecode', objKind: 'store' }, { id: 'Ename', object: 'Database store' }, { id: 'Eapi', objectApi: 'StoreV2' }, { id: 'EX', objKind: 'other' }];
const api = load('api/objectAutomation.ts', {
  './http': { apiClient: () => client, currentApiContext: () => context }, './dynamic': { dynamicApi: client },
  './action': { actionApi: client, actionContextKey: contextKey, loadSnapshot: async () => {}, loadEventSubscribers: async () => {}, actionTypes: () => [{ id: 'A', name: 'DB Action' }], eventTypes: () => empty ? [] : events, subscribersOf: () => [{}, {}] },
});
const old = api.fetchObjectAutomation('store');
Object.assign(context, { demoAs: 'new', previewAs: 'preview-new', ver: '' });
const current = await api.fetchObjectAutomation('store');
assert.equal(current.rules.length, 2); assert.equal(current.hitCounts.R0, 201); assert.equal(current.pending.R0, 200);
assert.ok(!requests.some(r=>/\/rules\/[^/]+\/hits$/.test(r.path)||r.path==='/action-runs'),'summary never loads full hits or action records');
assert.ok(requests.filter(r=>r.path==='/rules').every(r=>r.opts.query.objKind==='store'),'rule directory is scoped to object');
const onePage=await api.fetchAutomationHits('store','R0',2,10);assert.equal(onePage.total,31);assert.equal(requests.at(-1).opts.query.ruleId,'R0');
assert.equal(current.events.length, 3); assert.ok(current.events.every((e) => e.weekly === 3 && e.subscriptionCount === 2));
oldRules.resolve(paged(1, (i) => ({ id: `R${i}`, objKind: 'store' })));
assert.equal(await old, null);
assert.ok(requests.filter((r) => r.opts.headers['X-Demo-As'] === 'old').every((r) => r.opts.query.ver === '' && r.opts.query.previewAs === 'preview-old' && r.opts.readonlyParams === false));
failure = true; await assert.rejects(api.fetchObjectAutomation('store'), /403/);
context.ver = 'v3'; const beforeHistory = requests.length; await assert.rejects(api.fetchObjectAutomation('store'), /历史规则配置尚未接入/); assert.equal(requests.length, beforeHistory); context.ver = '';
failure = false; empty = true; const none = await api.fetchObjectAutomation('store'); assert.equal(none.rules.length + none.events.length, 0);

const pending = []; const props = vue.reactive({ objectId: 'store', readonly: false });
const page = load('pages/ontology/parts/object/ObjBehaviorAutomation.vue', {
  vue: { ...vue, watch() {}, onScopeDispose() {} }, 'vue-router': { useRoute: () => ({ query: {} }), useRouter: () => ({}), RouterLink: {} },
  '@/composables/usePagedQuery': { usePagedQuery: () => ({ page: vue.ref(1), size: vue.ref(10) }) },
  '@/mock/paginate': { paginate: (items) => ({ items, total: items.length }) },
  '@/mock/actionEvent': { ACTION_TYPES: [], EVENT_TYPES: [{ id: 'DEMO' }] }, '@/mock/rules': { RULE_DEFS: [{ id: 'DEMO' }] },
  '@/mock/lifecycle': { OBJ_TYPE_KIND: {}, OBJ_TYPE_NAME: {} }, '@/mock/changes': {},
  '../useDraftView': { useDraftView: () => ({}), draftOf: () => { throw Error('real mode must not derive mock CR status'); } },
  '@/api/action': { actionContextKey: contextKey }, '@/api/http': { currentApiContext: () => context },
  '@/api/objectAutomation': { isAutomationReal: () => true, fetchObjectAutomation: () => { const d = defer(); pending.push(d); return d.promise; } },
}, 'export { loadAutomation, remote, rules, events, objName, ruleStatus, historyUnsupported };', { defineProps: () => props });
assert.equal(page.rules.value.length + page.events.value.length, 0);
const previous = page.loadAutomation(); context.demoAs = 'another'; const fresh = page.loadAutomation();
pending[1].resolve(current); await fresh; pending[0].resolve({ ...current, objectName: 'PRIVATE OLD' }); await previous;
assert.equal(page.objName.value, 'Database store');
assert.equal(page.ruleStatus({ status: '已发布', displayStatus: '审核中' }).text, '审核中');
context.ver = 'v3'; assert.equal(page.historyUnsupported.value, true); assert.equal(page.rules.value.length + page.events.value.length, 0);
console.log('Object automation checks passed (scoped rule directory and backend hit/pending aggregates, DB object matching, runtime stats/subscribers, pinned and late contexts, true empty/403, no mock CR status).');
const pagingRoute=vue.reactive({query:{}}),pagingScope=vue.effectScope();let hitRead=async(_id,_rule,page,size)=>({items:[{id:'HP'+page,ruleId:'R0',at:'2026-10-25'}],total:31,page:Math.min(page,4),size});
const hitCalls=[];let summaryCalls=0;
const paging=pagingScope.run(()=>load('pages/ontology/parts/object/ObjBehaviorAutomation.vue',{
 vue,'vue-router':{useRoute:()=>pagingRoute,useRouter:()=>({replace:({query})=>{pagingRoute.query=query;}})},
 '@/composables/usePagedQuery':{usePagedQuery:()=>({page:vue.ref(1),size:vue.ref(10),defaultSize:10})},
 '@/mock/paginate':{paginate:items=>({items,total:items.length})},'@/mock/actionEvent':{},'@/mock/rules':{},'@/mock/lifecycle':{},'@/mock/changes':{},
 '../useDraftView':{useDraftView:()=>({readonly:vue.ref(false)}),draftOf:()=>{assert.fail('no mock CR state');}},
 '@/api/action':{actionContextKey:contextKey},'@/api/http':{currentApiContext:()=>context},
 '@/api/objectAutomation':{isAutomationReal:()=>true,fetchObjectAutomation:async()=>{summaryCalls++;return current;},fetchAutomationHits:async(...args)=>{hitCalls.push(args);return hitRead(...args);}},
},'export {pq,paged,loadHits,totals,hitCount,ruleFilter};',{defineProps:()=>props}));
context.ver='';await new Promise(setImmediate);
assert.equal(paging.paged.value.total,31);assert.equal(paging.hitCount('R0'),201);assert.equal(paging.totals.value.pending,200,'summary counts do not come from page');
const summaryBefore=summaryCalls,nextHits=defer();hitRead=()=>nextHits.promise;paging.pq.page.value=2;assert.equal(paging.paged.value.total,31,'plain page request keeps known total');
nextHits.resolve({items:[{id:'PAGE2'}],total:31,page:2});await new Promise(setImmediate);assert.equal(paging.paged.value.items[0].id,'PAGE2');assert.equal(summaryCalls,summaryBefore,'paging does not reload summary or candidate directories');
hitRead=async(_id,_rule,page)=>({items:[{id:'CLAMPED'}],total:31,page:Math.min(page,4)});paging.pq.page.value=99;await new Promise(setImmediate);assert.equal(paging.pq.page.value,4);
pagingRoute.query.aurule='R0';await new Promise(setImmediate);assert.equal(hitCalls.at(-1)[1],'R0','filter is sent to backend');
const oldPage=defer();hitRead=()=>oldPage.promise;paging.pq.page.value=2;context.previewAs='different';assert.equal(paging.paged.value.total,0,'preview switch clears old rows/count immediately');pagingScope.stop();oldPage.resolve({items:[{id:'PRIVATE'}],total:999,page:2});await new Promise(setImmediate);assert.notEqual(paging.paged.value.total,999);
console.log('Object automation SFC pagination passed (backend page/filter, stable totals/clamp, aggregate counts, no summary reload, stale preview/unmount).');
