// Run: node scripts/check-admin-db.mjs (real API/page modules, no server writes).
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import ts from 'typescript';
import * as vue from 'vue';
import { parse, compileTemplate } from '@vue/compiler-sfc';

function load(path, imports, expose = '', globals = {}) {
  let source = readFileSync(new URL(`../src/${path}`, import.meta.url), 'utf8');
  if (path.endsWith('.vue')) {
    const parsed = parse(source, { filename: path });
    assert.equal(parsed.errors.length, 0);
    assert.equal(compileTemplate({ source: parsed.descriptor.template.content, filename: path, id: path }).errors.length, 0);
    source = parsed.descriptor.scriptSetup.content;
  }
  const exports = {};
  const { outputText } = ts.transpileModule(`${source}\n${expose}`, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } });
  runInNewContext(outputText, { exports, ...globals, require: (name) => {
    if (name in imports) return imports[name];
    if (name.endsWith('.vue')) return {};
    throw new Error(`Missing dependency ${name}`);
  } });
  return exports;
}
const tick = () => new Promise(setImmediate);
const defer = () => { let resolve; const promise = new Promise((r) => { resolve = r; }); return { promise, resolve }; };
let real = true;
const context = vue.reactive({ demoAs: 'admin', ver: null });
const network = { code: 'NETWORK_ERROR', message: 'offline' };
let read = async () => { throw network; };
const api = { isReal: () => real, get: (...args) => read(...args) };
const http = { apiClient: () => api, currentApiContext: () => context, isApiError: (e) => !!e?.code };
const admin = load('api/admin.ts', {
  './http': http, '@/mock/admin': { ADMIN_DEPTS: ['演示部门'], AUDIT_LOGS: [] },
  '@/mock/paginate': { paginateCursor: () => ({ items: [], nextCursor: null }) },
});
const rolesApi = load('api/roles.ts', { './http': http });
const tenants = load('api/tenants.ts', { './http': http, vue, '@/stores/app': { TENANTS: [{ value: 'demo' }] } });
const scopeApi = load('api/rowScope.ts', { './http': http });
assert.equal(tenants.tenantOptions.value.length, 0);
for (const request of [
  () => admin.queryAuditLogs({}), admin.fetchAuditActions, () => admin.queryUsers({}), admin.fetchDepts,
  admin.fetchUserOptions, rolesApi.loadRolesState, rolesApi.loadRolesMeta, scopeApi.loadMyRowScope, tenants.loadTenants,
]) await assert.rejects(request(), (e) => e === network, 'real failure must propagate without demo data');
assert.equal(tenants.tenantOptions.value.length, 0);
real = false;
assert.equal(await admin.queryUsers({}), null);
assert.equal(await rolesApi.loadRolesState(), null);
assert.equal(await rolesApi.loadRolesMeta(), null);
assert.equal(await scopeApi.loadMyRowScope(), null);
assert.equal((await admin.fetchDepts())[0], '演示部门');
real = true;

const route = vue.reactive({ query: { user: 'new' } });
let roleRows = [];
let failed = true;
read = async (path) => {
  if (failed) throw network;
  if (path === '/roles') return roleRows;
  if (path === '/users/options') return { depts: ['数据库部门'], roles: roleRows, canAssignRole: true };
  if (path === '/depts') return ['数据库部门'];
  if (path === '/users') return { items: [], total: 0 };
  throw new Error(path);
};
const userScope = vue.effectScope();
const userWarnings=[];
const users = userScope.run(() => load('pages/admin/Users.vue', {
  vue, 'vue-router': { useRoute: () => route, useRouter: () => ({ replace() {} }) },
  'ant-design-vue': { message: { error() {}, warning(message){userWarnings.push(message);} } }, '@/api/http': http, '@/api/admin': admin,
  '@/stores/confirm': { useConfirmStore: () => ({}) },
  '@/composables/useReadonly': { useReadonly: () => ({ readonly: vue.ref(false), reason: vue.ref('') }) },
  '@/composables/usePagedQuery': { usePagedQuery: () => ({ page: vue.ref(1), size: vue.ref(17) }) },
  '@/composables/useUnsavedGuard': { useUnsavedGuard() {}, confirmDiscard: async () => true },
  '@/mock': { ADMIN_USERS: [{ id: 'DEMO' }], ADMIN_DEPTS: ['演示部门'], paginate() {}, EMPTY: '—' },
  '@/mock/admin': { pendingApprovals: () => 99 }, '@/mock/roles': { ROLES: [{ name: '演示角色' }] },
}, 'export { remote, users, roleOptions, depts, loadError, optionsError, readonly, reload, loadOptions, form, formOk, toggle };'));
await tick();
assert.equal(users.remote.value.items.length, 0);
assert.equal(users.users.value.length, 0);
assert.equal(users.depts.value.length, 0);
assert.equal(users.roleOptions.value.length, 0);
assert.equal(users.readonly.value, true);
assert.ok(users.loadError.value && users.optionsError.value);
failed = false;
await users.reload(); await users.loadOptions();
assert.equal(users.remote.value.total, 0);
assert.equal(users.roleOptions.value.length, 0, 'a successful empty DB must stay empty');
assert.equal(users.formOk.value, false);
roleRows = [{ name: '数据库角色' }];
await users.loadOptions();
assert.equal(users.roleOptions.value[0].name, '数据库角色');
assert.equal(users.form.role, '', 'real users explicitly choose a role');
assert.equal(users.depts.value[0], '数据库部门');
assert.equal(users.form.dept, '', 'real users explicitly enter a department');
await users.toggle({id:'BUSY',status:'启用',pending:2});assert.match(userWarnings.at(-1),/完成待审批事项/);
userScope.stop();

const props = vue.reactive({ objectId: 'db-object', readonly: false });
const dbRole = { id: 'DB-R', name: '数据库角色', memberCount:1, objs: [{ objType: '库对象', api:'DbObject', view: true, export: false, actions: [] }], scopes: [], defaultDeny: true };
const permissionHead={id:'db-object',name:'库对象',api:'DbObject'};
const permissionCalls=[];
let roleState = async () => ({ items: [dbRole], object:permissionHead, total:1, page:1, size:10 });
const permissionsScope = vue.effectScope();
const permissions = permissionsScope.run(() => load('pages/ontology/parts/object/PermissionsTab.vue', {
  vue, 'ant-design-vue': { message: { success() { assert.fail('must not claim a real draft write'); } } },
  '@/api/http': http,
  '@/api/static': { OBJECT_TYPE_LIST: [{ id: 'db-object', name: '库对象' }] },
  '@/api/roles': { rolesApiOn: () => true, queryObjectPermissions: (_id,page,size) => {permissionCalls.push({page,size});return roleState(page,size);} },
  '@/mock/paginate': { paginate: (items) => ({ items }) },
  '@/mock/roles': { ROLES: [{ id: 'DEMO' }], PERM_OBJECTS: [], scopeText: () => '数据库范围' },
  '@/mock/objectProps': { OBJECT_PROPS: { 'db-object': [{ api: 'secret', sensitive: true }] } },
  '@/stores/confirm': { useConfirmStore: () => ({ ask: async () => true }) },
  '../useDraftView': { useDraftView: () => ({ draft: vue.ref({ id: 'CR-real' }), editTip: vue.ref('') }) },
  './objectGov': { objLabel: () => '库对象' },
}, 'export { roles, rowAll, cell, toggleSens, sensEdits, loadPermissions, loadError, rowPage, rowSize, rowTotal, rowItems, mode, colCols };', { defineProps: () => props }));
await tick();
assert.equal(permissions.roles.value[0].id, 'DB-R');
assert.equal(permissions.rowAll.value[0].members, 1);
assert.equal(permissions.rowAll.value[0].view, false, 'default deny without scope must stay denied');
dbRole.defaultDeny = false;
await permissions.loadPermissions();
assert.equal(permissions.cell({ api: 'secret', sensitive: true }, permissions.rowAll.value[0]).text, '—');
await permissions.toggleSens({ api: 'secret', sensitive: true });
assert.equal(Object.keys(permissions.sensEdits.value).length, 0);
assert.equal(permissions.rowSize.value,10);
const permLater=defer();roleState=()=>permLater.promise;permissions.rowPage.value=2;assert.equal(permissions.rowTotal.value,1,'row paging retains total');
permLater.resolve({items:[dbRole],object:permissionHead,total:23,page:2,size:10});await tick();assert.equal(permissions.rowTotal.value,23);
roleState=async(page,size)=>({items:[dbRole],object:permissionHead,total:23,page:Math.min(page,3),size});permissions.rowPage.value=99;await tick();assert.equal(permissions.rowPage.value,3);
roleState=async(page,size)=>({items:Array.from({length:Math.min(size,401-(page-1)*size)},(_,i)=>({...dbRole,id:'DB-R'+((page-1)*size+i)})),object:permissionHead,total:401,page,size});
const beforeColumns=permissionCalls.length;permissions.mode.value='col';await tick();assert.equal(permissions.roles.value.length,401,'column matrix keeps every lightweight role column');assert.equal(permissionCalls.length-beforeColumns,3);assert.equal(permissions.colCols.value.length,404);
permissions.mode.value='row';await tick();
const stale = defer();
roleState = () => stale.promise;
const pending = permissions.loadPermissions();
roleState = async () => ({ items:[],object:permissionHead,total:0,page:1,size:10 });
context.demoAs = 'other'; context.demoAs = 'admin';
await tick();
stale.resolve({items:[dbRole],object:permissionHead,total:1,page:1,size:10});
await pending;
assert.equal(permissions.roles.value.length, 0, 'old context responses must not restore roles after away-and-back');
roleState = async () => { throw network; };
await permissions.loadPermissions();
assert.equal(permissions.roles.value.length, 0);
assert.ok(permissions.loadError.value);
const afterDispose=defer();roleState=()=>afterDispose.promise;const disposedPermission=permissions.loadPermissions();
permissionsScope.stop();
afterDispose.resolve({items:[dbRole],object:permissionHead,total:999,page:1,size:10});await disposedPermission;assert.equal(permissions.roles.value.length,0,'disposed permission reads cannot update the page');
// Without management permission, real API key shortcuts and direct calls cannot fabricate keys.
const keyScope = vue.effectScope();
const keyPage = keyScope.run(() => load('pages/admin/ApiKeys.vue', {
  vue, 'vue-router': { useRoute: () => ({ query: vue.reactive({ form: 'create', key: 'DEMO', from: 'DEMO' }) }), useRouter: () => ({ replace() {} }) },
  'ant-design-vue': { message: { success() { assert.fail('no real key operation exists'); } } },
  '@ant-design/icons-vue': {}, '@/api/admin': { adminApiOn: () => true },
  '@/api/http': http, '@/api/roles': { loadRolesState: async () => ({ roles: [] }) },
  '@/api/apikeys': { queryApiKeys: async () => ({ items: [], total: 0, counts: {}, page: 1 }), apiKeysMeta: async () => ({ canManage: false, scopes: [], scopeOptions: [], serviceAccounts: [] }), apiKeyDetail: async () => { throw network; }, apiKeyCalls: async () => ({ items: [], total: 0, page: 1 }), createApiKey: () => assert.fail('no management permission'), revokeApiKey: () => assert.fail('no management permission') },
  '@/stores/confirm': { useConfirmStore: () => ({ ask() { assert.fail('must not offer revoke'); } }) },
  '@/composables/useReadonly': { useReadonly: () => ({ readonly: vue.ref(false), reason: vue.ref('') }) },
  '@/composables/useUnsavedGuard': { useUnsavedGuard() {}, confirmDiscard: async () => true },
  '@/composables/usePagedQuery': { usePagedQuery: () => ({ page: vue.ref(1), size: vue.ref(18), overflow: vue.ref(false) }) },
  '@/mock': { paginate: (items) => ({ items, total: items.length }) },
  '@/mock/apikeys': { API_KEYS: [{ id: 'DEMO' }], KEY_SCOPES: [], SERVICE_ACCOUNTS: [], SA_SCOPES: [], newSecret() { assert.fail('must not generate a fake key'); } },
  '@/mock/roles': { ROLES: [] },
}, 'export { keys, createOpen, detail, create, revoke, copySecret, readonly, form, secret };'));
assert.equal(keyPage.keys.value.length, 0);
await tick();
assert.equal(keyPage.createOpen.value, true, 'URL may open form, permission still blocks creation');
assert.equal(keyPage.detail.value, null);
assert.equal(keyPage.readonly.value, true);
keyPage.form.name = 'valid-name'; keyPage.form.scopes = ['查询']; keyPage.form.role = 'role';
keyPage.create();
await keyPage.revoke({ id: 'DEMO' }); await keyPage.copySecret();
assert.equal(keyPage.secret.value, null);
assert.equal(keyPage.keys.value.length, 0);
keyScope.stop();
console.log('Admin DB checks passed (errors, empty data, DB candidates, permissions, stale contexts, unsupported writes).');

// Actual role member loader requests server pages without modifying the complete edit baseline.
const roleSource=readFileSync(new URL('../src/pages/admin/Roles.vue',import.meta.url),'utf8').match(/<script setup lang="ts">([\s\S]*?)<\/script>/)[1];
const roleAst=ts.createSourceFile('roles.ts',roleSource,ts.ScriptTarget.Latest,true,ts.ScriptKind.TS);
const names=['memberPage','memberLoading','memberKey','memberSeq','loadMemberPage','mpaged'];
const selectedStatements=roleAst.statements.filter(s=>ts.isVariableStatement(s)&&names.includes(s.declarationList.declarations[0].name.getText(roleAst))||ts.isFunctionDeclaration(s)&&names.includes(s.name?.text)).map(s=>s.getText(roleAst)).join('\n');
let delayedMembers=defer();const memberCalls=[];const memberRole=vue.ref({id:'R-DB'}),memberPageNo=vue.ref(1);let delayed=true;
const moduleCode=ts.transpileModule(`${selectedStatements}; export {memberPage,mpaged,loadMemberPage};`,{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2020}}).outputText;
const memberVm={};runInNewContext(moduleCode,{exports:memberVm,...vue,realRoles:true,currentApiContext:()=>context,role:memberRole,loaded:vue.ref(true),tab:vue.ref('members'),mpq:{page:memberPageNo,size:vue.ref(10)},mkw:vue.ref('search'),
 queryRoleMembers:async(id,page,size,kw)=>{memberCalls.push({id,page,size,kw});if(delayed)return delayedMembers.promise;return {items:[{id:'PAGE-2'}],total:23,page,size};}});
const firstMembers=memberVm.loadMemberPage();memberPageNo.value=2;delayed=false;await memberVm.loadMemberPage();delayedMembers.resolve({items:[{id:'OLD-PAGE'}],total:99,page:1,size:10});await firstMembers;
assert.equal(memberVm.mpaged.value.items[0].id,'PAGE-2');assert.equal(memberCalls.at(-1).size,10);assert.equal(memberCalls.at(-1).page,2);assert.equal(memberCalls.at(-1).kw,'search');
delayedMembers=defer();delayed=true;const waitingMembers=memberVm.loadMemberPage();assert.equal(memberVm.mpaged.value.total,23,'member totals remain during paging');context.demoAs='new-role-context';assert.equal(memberVm.mpaged.value.items.length,0);delayedMembers.resolve({items:[{id:'PRIVATE'}],total:100,page:2,size:10});await waitingMembers;assert.equal(memberVm.mpaged.value.items.length,0);
const stateReads=[];read=async(path,options)=>{stateReads.push({path,options});return {roles:[],members:{},users:[]};};await rolesApi.loadRolesState();assert.deepEqual(stateReads.map(r=>r.path),['/roles/state']);assert.equal(stateReads[0].options.headers['X-Demo-As'],'new-role-context');
const projectionReads=[];read=async(path,options)=>{projectionReads.push({path,options});return {items:[],object:{id:'obj',name:'Object',api:'Object'},total:0,page:1,size:10};};
await rolesApi.queryObjectPermissions('obj',2,10);assert.equal(projectionReads[0].path,'/roles/object-permissions');assert.equal(projectionReads[0].options.query.objectId,'obj');assert.equal(projectionReads[0].options.query.page,2);assert.equal(projectionReads[0].options.query.size,10);assert.equal(projectionReads[0].options.headers['X-Demo-As'],context.demoAs);assert.equal(projectionReads[0].options.readonlyParams,false);
