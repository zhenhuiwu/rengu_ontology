// Run: node scripts/check-qa-integration.mjs (no server or database writes).
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import ts from 'typescript';
import * as vue from 'vue';

function load(path, imports, expose = '') {
  let source = readFileSync(new URL(`../src/${path}`, import.meta.url), 'utf8');
  if (path.endsWith('.vue')) source = source.match(/<script setup lang="ts">([\s\S]*?)<\/script>/)[1];
  const exports = {};
  const { outputText } = ts.transpileModule(`${source}\n${expose}`, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } });
  runInNewContext(outputText, { exports, require: (name) => {
    if (name in imports) return imports[name];
    if (name.endsWith('.vue')) return {};
    throw new Error(`Missing test dependency: ${name}`);
  } });
  return exports;
}
const defer = () => { let resolve; const promise = new Promise((r) => { resolve = r; }); return { promise, resolve }; };
let facetError;
const meta = load('pages/objects/objMeta.ts', {
  '@/api/http': { apiClient: () => ({ isReal: () => true, get: async () => { if (facetError) throw facetError; return []; } }), isBackendDown: (e) => e.code === 'NETWORK_ERROR' },
  '@/api/static': {}, '@/mock/links': {}, '@/mock/objectProps': {}, '@/mock/actionEvent': {}, '@/mock/metrics': {}, '@/mock/format': {},
});
for (const code of ['VALIDATION_FAILED', 'UNAUTHORIZED', 'FORBIDDEN']) {
  facetError = { code };
  await assert.rejects(meta.loadStaticFacets('store', {}), (e) => e === facetError);
}
facetError = { code: 'NETWORK_ERROR' };
assert.equal(await meta.loadStaticFacets('store', {}), null);
facetError = null;
assert.equal((await meta.loadStaticFacets('store', {})).length, 0);
const slowSearch = defer();
const objRows = load('pages/objects/objRows.ts', {
  '@/mock/stores': {}, '@/mock/products': {}, '@/mock/orders': {}, '@/mock/objects-qa': {},
  '@/api/qaObjects': { exploreSearch: () => slowSearch.promise }, './objMeta': meta,
});
facetError = { code: 'VALIDATION_FAILED' };
await assert.rejects(objRows.searchRows({ id: 'store' }, { kw: '', filters: {} }, true), (e) => e === facetError);
slowSearch.resolve({}); // The rejected facet is observed while the other request is still pending.

const rowCalls = [];
let facetCalls = 0;
const pagedObjects = load('pages/objects/objRows.ts', {
  '@/mock/stores': {}, '@/mock/products': {}, '@/mock/orders': {}, '@/mock/objects-qa': {},
  '@/api/qaObjects': { exploreSearch: async (_id, query) => {
    rowCalls.push(query);
    return { items: [{ id: String(query.page) }], total: 30, page: query.page, bound: true, boundProps: [], facets: [] };
  } },
  './objMeta': { loadStaticFacets: async () => { facetCalls++; return [{ key: 'stored', kind: 'enum', values: [] }]; } },
});
const initialRows = await pagedObjects.searchRows({ id: 'order' }, { kw: '', filters: {}, page: 1, size: 10 }, true);
assert.equal(initialRows.facets[0].key, 'stored');
assert.equal(rowCalls[0].withFacets, false, 'do not aggregate duplicate facets in both endpoints');
assert.equal(facetCalls, 1);
const nextRows = await pagedObjects.searchRows({ id: 'order' }, { kw: '', filters: {}, page: 2, size: 10, withFacets: false }, true);
assert.equal(nextRows.items[0].id, '2');
assert.equal(rowCalls[1].page, 2);
assert.equal(rowCalls[1].size, 10);
assert.equal(rowCalls[1].withFacets, false);
assert.equal(facetCalls, 1, 'turning a page must not reload unchanged facet counts');

const route = vue.reactive({ query: {} });
const qaLive = vue.ref(true);
const pagesQuery = { page: vue.ref(3), size: vue.ref(15) };
const requests = [];
const api = { listPages: (query) => { const pending = defer(); requests.push({ query, ...pending }); return pending.promise; }, deletePage: async () => {} };
const myPages = load('pages/qa/MyPages.vue', {
  vue: { ...vue, watch() {} }, 'vue-router': { useRoute: () => route, useRouter: () => ({ replace: ({ query }) => { route.query = query; } }) },
  'ant-design-vue': { message: { error() {}, success() {} } },
  '@/composables/usePagedQuery': { usePagedQuery: () => pagesQuery }, '@/composables/useReadonly': { useReadonly: () => ({}) },
  '@/composables/usePreview': { usePreview: () => ({ active: vue.ref(false), userName: vue.ref('User') }) },
  '@/stores/confirm': { useConfirmStore: () => ({ ask: async () => true }) }, '@/mock': {},
  '@/mock/qa-pages': { MY_PAGES: [], OTHER_PAGES: [], PAGE_TEMPLATES: [{ id: 'DEMO', scene: 'DEMO' }], PAGE_SCOPES: ['DEMO'] }, '@/api/qaPages': api,
  '@/api/http': { currentApiContext: () => ({}) }, '@/api/qa': { getSessionOptions: async () => ({ scopes: [{ value: 'DB Scope' }] }) },
  './qaFallback': { qaLive, qaWriteReal: vue.ref(true) }, './useQaSession': {},
}, 'export { loadList, loadCur, remove, live, loadPageOptions, scopeChoices, tplCur, tplCount, cutoff };');
assert.equal(myPages.scopeChoices.value.length, 0);
assert.equal(myPages.tplCount.value, 0);
assert.equal(myPages.cutoff.value, '—');
await myPages.loadPageOptions();
assert.equal(myPages.scopeChoices.value[0], 'DB Scope');
const settle = (offset, total, id) => {
  requests[offset].resolve({ items: id ? [{ id }] : [], total });
  requests[offset + 1].resolve({ total });
};
const removing = myPages.remove({ id: 'last', name: 'Last', cards: [], share: { mode: '仅自己' }, sub: { on: false } });
await new Promise(setImmediate);
assert.equal(requests[0].query.page, 3);
settle(0, 30);
await removing;
assert.equal(pagesQuery.page.value, 2, 'deleting the final record must return to the last valid page');
const reloaded = myPages.loadList();
settle(2, 30, 'previous-page');
await reloaded;
assert.equal(myPages.live.list.items[0].id, 'previous-page');
const older = myPages.loadList();
route.query = { kw: 'filtered' };
pagesQuery.page.value = 1;
const newer = myPages.loadList();
settle(6, 1, 'filtered');
await newer;
settle(4, 0);
await older;
assert.equal(myPages.live.list.items[0].id, 'filtered', 'stale response must not overwrite a new filter');
assert.equal(myPages.live.count, 1);
assert.equal(pagesQuery.page.value, 1);
const pending = myPages.loadList();
qaLive.value = false;
await myPages.loadList();
settle(8, 0);
await pending;
assert.equal(myPages.live.list, null, 'turning off live mode must clear real data and invalidate pending responses');
qaLive.value = true;
route.query = { page_id: 'P1' };
const detail = defer();
api.getPage = () => detail.promise;
const opening = myPages.loadCur();
route.query = {};
await myPages.loadCur();
detail.resolve({ id: 'P1' });
await opening;
assert.equal(myPages.live.cur, null, 'a late detail response must not reopen a closed drawer');

const state = defer();
const metadata = defer();
let mounted;
let identityChanged;
let roleWrites = 0;
const roleErrors = [];
const role = { id: 'R-1', func: { test: 'view' }, objs: [], scopes: [] };
const roleApi = { rolesApiOn: () => true, loadRolesState: () => state.promise, loadRolesMeta: () => metadata.promise, saveRolePermissions: async () => { roleWrites++; } };
const roles = load('pages/admin/Roles.vue', {
  vue: { ...vue, onMounted: (fn) => { mounted = fn; }, watch: (_src, fn, opts) => { if (opts?.immediate) fn(); if (opts?.flush === 'sync') identityChanged = fn; } },
  'vue-router': { useRoute: () => route, useRouter: () => ({}) },
  'ant-design-vue': { message: { error: (e) => roleErrors.push(e) } },
  '@/stores/confirm': { useConfirmStore: () => ({ ask: async () => true }) }, '@/stores/preview': { usePreviewStore: () => ({}) },
  '@/composables/useReadonly': { useReadonly: () => ({ readonly: vue.ref(false), reason: vue.ref('') }) },
  '@/composables/useUnsavedGuard': { useUnsavedGuard: () => (fn) => fn() },
  '@/composables/usePagedQuery': { usePagedQuery: () => pagesQuery }, '@/api/http': { isApiError: () => false, currentApiContext: () => ({}) },
  '@/api/roles': roleApi,
  '@/mock': { ADMIN_USERS: [] }, '@/mock/roles': { ROLES: [role], FUNC_PAGES: [{ key: 'test' }], PERM_OBJECTS: [], SCOPE_PROPS: [], roleMembers: () => [] },
}, 'export { draft, dirty, scopeForm, scopeProps, call, saveDraft, loaded, locked };');
const mounting = mounted();
assert.equal(roles.locked.value, true, 'real mode starts empty and cannot edit before loading');
roles.scopeForm.key = 'Object.region';
roles.scopeForm.values = ['East'];
state.resolve({ roles: [role], members: { 'R-1': [] }, users: [] });
metadata.resolve({ permObjects: [], scopeProps: [{ objType: 'Object', prop: 'region', values: ['East'] }] });
await mounting;
assert.equal(roles.draft.func.test, 'view');
roles.draft.func.test = 'manage';
assert.equal(roles.dirty.value, true);
assert.equal(roles.scopeProps.value.length, 1);
assert.deepEqual([...roles.scopeForm.values], ['East'], 'scope metadata updates must preserve selected values');
assert.equal(roleErrors.length, 0);
roleApi.loadRolesMeta = async () => { throw new Error('metadata failed'); };
identityChanged();
assert.equal(roles.loaded.value, false, 'identity changes must block writing synchronously');
await new Promise(setImmediate);
assert.equal(roles.locked.value, true);
await roles.call(async () => { roleWrites++; });
await roles.saveDraft();
assert.equal(roleWrites, 0, 'metadata failure must block both the shared writer and permission saving');
assert.ok(roleErrors.length >= 3);
const staleMeta = defer();
roleApi.loadRolesMeta = () => staleMeta.promise;
identityChanged();
await new Promise(setImmediate);
roleApi.loadRolesMeta = async () => ({ permObjects: [], scopeProps: [{ prop: 'current' }] });
identityChanged();
await new Promise(setImmediate);
staleMeta.resolve({ permObjects: [], scopeProps: [{ prop: 'stale' }] });
await new Promise(setImmediate);
assert.equal(roles.scopeProps.value[0].prop, 'current', 'old identity metadata must not overwrite the new identity');
console.log('QA integration checks passed (facets, pagination, stale requests, closed drawer, role edits/write readiness).');
// Real scope module: use the singular published row-scope contract, never widen denied access.
let scopeKey='scope-initial';const scopeRequests=[];
const dimension={id:'scope-dim',kind:'维度',group:'组织',status:'已发布',object:'source-42',objectName:'Warehouse',levels:[{name:'Area'},{name:'Site'}]};
const topValues=[{name:'EastScope',code:'E',synonyms:['east-alias'],level:'Area',status:'启用'},{name:'WestScope',code:'W',synonyms:[],level:'Area',status:'启用'}];
let scopeResponse=async()=>({items:[{objType:'Warehouse',visible:true,unrestricted:false,conditions:[{values:['east-alias']}]}]});
const scopeModule=load('pages/qa/qaScope.ts',{
 vue,'@/api/rowScope':{loadMyRowScope:()=>scopeResponse()},
 '@/pages/objects/objMeta':{metaContextKey:()=>scopeKey,loadMeta:async()=>({hasData:true,metrics:[{variants:[{label:'CaliberDB'}]}]})},
 '@/api/static':{isStaticReal:()=>true,fetchStaticPage:async(path,query)=>{scopeRequests.push({path,query});return {items:path==='/dimensions'?[dimension]:topValues,total:path==='/dimensions'?1:2,page:1,size:200};}},
 '@/api/qa':{qaReal:()=>true,getSessionOptions:async()=>({scopes:[{value:'fallback-any',label:'Any'}],calibers:[]})},
 '@/mock/dimensions':{DIM_ITEMS:[]},'@/mock':{QA_SCOPE_OPTIONS:[{value:'mock-scope',label:'Mock'}],QA_CALIBER_OPTIONS:[]},
});
const scopeChoices=scopeModule.useScopeCaliber();await scopeModule.ensureScopeOptions(true);
assert.deepEqual(Array.from(scopeChoices.scopeOpts.value,o=>o.value),['EastScope']);assert.equal(scopeChoices.defaultScope(()=>null),'EastScope');assert.equal(scopeModule.normScope('east-alias'),'EastScope');assert.equal(scopeModule.normScope('WestScope'),'');
assert.deepEqual(scopeRequests.map(r=>r.path),['/dimensions','/dimensions/scope-dim/values']);assert.equal(scopeRequests[1].query.level,'Area');assert.equal(scopeRequests[1].query.status,'启用');
for(const denied of [false,'missing','unmatched']){
 scopeKey+='-next';scopeResponse=async()=>({items:denied==='missing'?[]:[{objType:'Warehouse',visible:denied!==false,unrestricted:false,conditions:[{values:['not-a-top-value']}]}]});await scopeModule.ensureScopeOptions(true);assert.equal(scopeChoices.scopeOpts.value.length,0);assert.equal(scopeChoices.defaultScope(()=> 'Area = WestScope'),'');
}
scopeKey+='-unrestricted';scopeResponse=async()=>({items:[{objType:'Warehouse',visible:true,unrestricted:true,conditions:[]}]});await scopeModule.ensureScopeOptions(true);assert.equal(scopeChoices.scopeOpts.value.length,2);assert.equal(scopeChoices.defaultScope(()=> 'Area = WestScope'),'WestScope');
const staleScope=defer();scopeKey+='-old';scopeResponse=()=>staleScope.promise;const oldScope=scopeModule.ensureScopeOptions(true);await new Promise(setImmediate);
scopeKey+='-current';scopeResponse=async()=>({items:[{objType:'Warehouse',visible:false,unrestricted:false,conditions:[]}]});await scopeModule.ensureScopeOptions(true);staleScope.resolve({items:[{objType:'Warehouse',visible:true,unrestricted:true,conditions:[]}]});await oldScope;assert.equal(scopeChoices.scopeOpts.value.length,0,'old identity cannot restore unrestricted scopes');
scopeKey+='-failed';scopeResponse=async()=>{throw Error('403');};await scopeModule.ensureScopeOptions(true);assert.equal(scopeChoices.scopeOpts.value.length,0,'failure never expands to full scopes');
const beforeMock=scopeRequests.length;scopeResponse=()=>{assert.fail('mock must not request row scope');};await scopeModule.ensureScopeOptions(false);assert.equal(scopeRequests.length,beforeMock);assert.equal(scopeChoices.scopeOpts.value[0].value,'mock-scope');
console.log('QA scopes passed (published source name, visible/unrestricted/conditions, denied/missing/failure fail closed, stale identity, selected top-values only, mock no requests).');
