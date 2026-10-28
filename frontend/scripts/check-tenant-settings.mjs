// Run: node scripts/check-tenant-settings.mjs (Vue state checks; no server or database writes).
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import ts from 'typescript';
import * as vue from 'vue';
import { parse, compileTemplate } from '@vue/compiler-sfc';

function load(path, imports, expose = '') {
  let source = readFileSync(new URL(`../src/${path}`, import.meta.url), 'utf8');
  if (path.endsWith('.vue')) {
    const parsed = parse(source, { filename: path });
    assert.equal(parsed.errors.length, 0);
    assert.equal(compileTemplate({ source: parsed.descriptor.template.content, filename: path, id: path }).errors.length, 0);
    source = parsed.descriptor.scriptSetup.content;
  }
  const exports = {};
  const { outputText } = ts.transpileModule(`${source}\n${expose}`, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } });
  runInNewContext(outputText, { exports, require: (name) => {
    if (name in imports) return imports[name];
    if (name.endsWith('.vue')) return {};
    throw new Error(`Missing test dependency: ${name}`);
  } });
  return exports;
}
const plain = (value) => JSON.parse(JSON.stringify(value));
const defer = () => { let resolve; const promise = new Promise((r) => { resolve = r; }); return { promise, resolve }; };
const quietVue = { ...vue, onMounted() {}, onBeforeUnmount() {} };
const mockOptions = [{ value: 'hd', label: 'mock / East' }, { value: 'hn', label: 'mock / South' }];
const remote = { canManage: true, tenantId: 'TN-1', name: '数据库租户', shortName: '简称', industry: '测试行业', defaultOrg: 'hd',
  orgOptions: [{ value: 'hd', label: '东部' }, { value: 'hn', label: '南部' }], lockVersion: 7, updatedAt: '2026-10-26 10:00' };
let real = true;
let failure;
let loadFailure;
let reads = 0;
const puts = [];
const profileReads = [];
const profileContext = vue.reactive({ demoAs: 'SETTINGS-DB-USER', previewAs: '', ver: '' });
const slowOptions = defer();
const api = {
  isReal: () => real,
  get: async (path, options) => {
    if (path === '/me') { profileReads.push(options); return { id: 'SETTINGS-DB-USER', name: '配置数据库用户', roleNames: ['数据库角色'], roles: ['R-DB'], func: {}, capabilities: { publish: false, rollback: false } }; }
    if (path === '/tenants') return slowOptions.promise;
    assert.equal(path, '/settings/tenant'); reads += 1;
    if (loadFailure) throw loadFailure;
    return plain(remote);
  },
  put: async (path, body) => {
    assert.equal(path, '/settings/tenant'); puts.push(plain(body));
    if (failure) throw failure;
    assert.equal(body.lockVersion, remote.lockVersion);
    Object.assign(remote, body, { lockVersion: remote.lockVersion + 1 });
    return plain(remote);
  },
};
const http = { currentApiContext: () => profileContext, apiClient: () => api, isBackendDown: (e) => e.code === 'NETWORK_ERROR', isApiError: (e) => typeof e?.code === 'string' };
const tenants = load('api/tenants.ts', { vue, '@/stores/app': { TENANTS: mockOptions }, './http': http });
const settingsApi = load('api/settings.ts', { './http': http, './tenants': tenants });
const profileApi = load('api/profile.ts', { './http': http });
const oldOptions = tenants.loadTenants();
await settingsApi.loadTenantSettings();
slowOptions.resolve([{ value: 'old', label: 'stale' }]);
await oldOptions;
assert.equal(tenants.tenantOptions.value[0].label, '简称 / 东部', 'late old GET must not overwrite saved labels');

const app = vue.reactive({ tenant: 'hn' });
const topScope = vue.effectScope();
topScope.run(() => load('layouts/TopBar.vue', {
  vue: quietVue, 'vue-router': { useRouter: () => ({ currentRoute: vue.ref({ name: 'settings' }) }) }, 'ant-design-vue': {},
  '@/api/workspaces': { workspaceCatalog: async () => ({ items: [] }) }, '@/api/config': { anyReal: () => real },
  '@/api/notifications': { isNotificationsReal: () => false, notificationContextKey: () => 'settings' },
  '@/stores/app': { useAppStore: () => app }, '@/api/tenants': tenants, '@/nav': {},
  '@/api/admin': { adminApiOn: () => real }, '@/api/profile': profileApi, '@/api/http': http,
}));
tenants.setTenantOptions([{ value: 'hd', label: 'new / East', default: true }, { value: 'hn', label: 'new / South' }]);
await vue.nextTick();
assert.equal(app.tenant, 'hn', 'save must preserve a valid current selection');
await new Promise(setImmediate);
assert.equal(app.user.name, '配置数据库用户'); assert.equal(app.user.role, '数据库角色');
assert.equal(profileReads[0].headers['X-Demo-As'], 'SETTINGS-DB-USER'); assert.equal(profileReads[0].readonlyParams, false);
tenants.setTenantOptions([{ value: 'hd', label: 'new / East', default: true }]);
await vue.nextTick();
assert.equal(app.tenant, 'hd', 'an invalid selection uses the configured fallback');

const mock = load('mock/settings.ts', {});
const route = vue.reactive({ query: {} });
const readOnly = vue.ref(false);
let allowDiscard = false;
const messages = [];
const imports = {
  '@/stores/app': { useAppStore: () => vue.reactive({ get viewMode() { return readOnly.value ? 'history' : 'current'; } }) },
  '@/stores/preview': { usePreviewStore: () => ({ active: false }) },
  vue: quietVue, 'vue-router': { useRoute: () => route, useRouter: () => ({ replace: ({ query }) => { route.query = query; } }) },
  'ant-design-vue': { message: { error: (s) => messages.push(s), success: (s) => messages.push(s) } },
  '@/stores/confirm': { useConfirmStore: () => ({ ask: async () => true }) },
  '@/composables/useReadonly': { useReadonly: () => ({ readonly: readOnly, reason: vue.ref('') }) },
  '@/composables/useUnsavedGuard': { useUnsavedGuard: () => () => {}, confirmDiscard: async () => allowDiscard },
  '@/mock': { ADMIN_DEPTS: [] }, '@/mock/settings': mock, '@/api/settings': settingsApi, '@/api/http': http,
};
const exposed = 'export { loadTenant, reloadTenant, save, draft, saved, dirty, errors, tenantBase, tenantState, fieldDis, channel, llmKey };';
const scope = vue.effectScope();
const page = scope.run(() => load('pages/admin/Settings.vue', imports, exposed));
assert.equal(page.draft.tenant.name, '');
assert.equal(page.fieldDis.value, true);
await page.loadTenant();
assert.equal(page.tenantBase.value.lockVersion, 7);
page.draft.tenant.name = '保留的编辑';
page.draft.security.ipAllow = 'invalid';
page.draft.llm.defaults['问数解析'] = 'missing';
assert.equal(page.errors.value.length, 0, 'unsupported mock groups must not block tenant validation');
failure = { status: 409, code: 'VERSION_CONFLICT', message: 'conflict' };
await page.save();
assert.equal(puts[0].lockVersion, 7);
assert.equal(page.draft.tenant.name, '保留的编辑');
assert.equal(page.saved.value.tenant.name, '数据库租户');
assert.equal(page.tenantBase.value.lockVersion, 7);
assert.equal(page.dirty.value, true);
assert.match(page.tenantState.error, /输入已保留/);
const beforeReload = reads;
await page.reloadTenant();
assert.equal(reads, beforeReload, 'cancelled reload must preserve input and frozen version');
Object.assign(remote, { name: '他人保存', lockVersion: 8 });
allowDiscard = true;
await page.reloadTenant();
assert.equal(page.draft.tenant.name, '他人保存');
page.draft.tenant.name = '重新编辑'; page.draft.tenant.shortName = '新简称';
failure = undefined;
await page.save();
assert.deepEqual(Object.keys(puts[1]).sort(), ['defaultOrg', 'lockVersion', 'name', 'shortName']);
assert.equal(puts[1].lockVersion, 8);
assert.equal(page.tenantBase.value.lockVersion, 9);
assert.equal(page.dirty.value, false);
assert.equal(page.draft.security.ipAllow, 'invalid');
assert.notEqual(page.saved.value.security.ipAllow, 'invalid', 'tenant save must not clone unsupported drafts');
assert.notEqual(page.saved.value.llm.defaults['问数解析'], 'missing');
assert.equal(tenants.tenantOptions.value[0].label, '新简称 / 东部');
route.query = { group: 'llm', llm: 'new' }; await vue.nextTick();
assert.equal(page.llmKey.value, '', 'direct URL must not open an unimplemented real-mode editor');
route.query = { group: 'notify', channel: 'email' }; await vue.nextTick();
assert.equal(page.channel.value, null);
page.draft.tenant.name = '只读不可保存'; readOnly.value = true;
await page.save(); assert.equal(puts.length, 2);
readOnly.value = false;

loadFailure = { code: 'FORBIDDEN', message: 'denied' };
const failedScope = vue.effectScope();
const failedPage = failedScope.run(() => load('pages/admin/Settings.vue', imports, exposed));
await failedPage.loadTenant();
assert.equal(failedPage.fieldDis.value, true);
assert.equal(failedPage.tenantState.ready, false);
assert.equal(failedPage.draft.tenant.name, '', 'load errors must not expose editable mock defaults');
real = false; loadFailure = undefined; route.query = {};
const mockScope = vue.effectScope();
const mockPage = mockScope.run(() => load('pages/admin/Settings.vue', imports, exposed));
mockPage.draft.tenant.name = '演示修改';
await mockPage.save();
assert.equal(mockPage.saved.value.tenant.name, '演示修改');
assert.equal(puts.length, 2, 'mock saves must not issue backend requests');
for (const s of [scope, failedScope, mockScope, topScope]) s.stop();
console.log('Tenant settings checks passed (frozen versions, 409 preservation, isolated saves, shared labels, stale GET, fallback, readonly, mock).');
