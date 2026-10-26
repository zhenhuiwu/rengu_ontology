// Run: node scripts/check-user-import.mjs (real Users.vue script, no server writes).
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import ts from 'typescript';
import * as vue from 'vue';

const deferred = () => {
  let resolve;
  const promise = new Promise((r) => { resolve = r; });
  return { promise, resolve };
};
const tick = () => new Promise(setImmediate);
const source = readFileSync(new URL('../src/pages/admin/Users.vue', import.meta.url), 'utf8')
  .match(/<script setup lang="ts">([\s\S]*?)<\/script>/)[1];
const { outputText } = ts.transpileModule(`${source}\nexport { openImport, onUpload, doImport, importOpen, importLoading, importing, preview, remote };`, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
});
const context = vue.reactive({ demoAs: 'U010001', ver: null, previewAs: null });
const readonly = vue.ref(false);
const messages = [];
const previewCalls = [];
const writes = [];
let previewResult;
let confirmation;
let realMode = true;
const row = { name: '真实用户', account: 'user@example.com', dept: 'dept', role: 'role', error: null };
const imports = {
  vue, 'vue-router': { useRoute: () => ({ query: vue.reactive({}) }), useRouter: () => ({ replace() {} }) },
  'ant-design-vue': { message: { error: (m) => messages.push(m), info: (m) => messages.push(m), success: (m) => messages.push(m) } },
  '@/api/http': { currentApiContext: () => context, isApiError: () => false },
  '@/stores/confirm': { useConfirmStore: () => ({ ask: () => confirmation.promise }) },
  '@/components/AppPagination.vue': {},
  '@/composables/usePagedQuery': { usePagedQuery: () => ({ page: vue.ref(1), size: vue.ref(17), defaultSize: 17 }) },
  '@/composables/useReadonly': { useReadonly: () => ({ readonly, reason: vue.ref('') }) },
  '@/composables/useUnsavedGuard': { useUnsavedGuard() {}, confirmDiscard: async () => true },
  '@/mock': { ADMIN_USERS: [], ADMIN_DEPTS: ['dept'], paginate: () => ({ items: [], total: 0 }), EMPTY: '—' },
  '@/mock/admin': { pendingApprovals: () => 0 }, '@/mock/roles': { ROLES: [{ name: 'role' }] },
  '@/api/admin': {
    adminApiOn: () => realMode, fetchUserRoles: async () => [{ name: 'role' }],
    queryUsers: async () => ({ items: [], total: 0 }), fetchDepts: async () => ['dept'],
    previewImport: async (body) => { previewCalls.push(body); return previewResult; },
    importUsers: async (rows) => { writes.push(rows); return rows.filter((r) => !r.error).length; },
  },
};
const exports = {};
function setup(exports) {
runInNewContext(outputText, {
  exports, require: (name) => { assert.ok(name in imports, `Missing ${name}`); return imports[name]; },
  FileReader: class {
    readAsDataURL(file) {
      if (file.failRead) { this.onerror(); return; }
      this.result = `data:application/octet-stream;base64,${file.base64}`;
      this.onload();
    }
  },
});
}
setup(exports);
const csv = (name, value) => ({ name, size: value.length, text: async () => value });
await tick(); // Initial real list request resolves.
exports.openImport();
previewResult = [row];
await exports.onUpload(csv('用户.csv', '\ufeff姓名,账号,部门,角色\n真实用户,user@example.com,dept,role'));
assert.equal(previewCalls[0].csv.startsWith('\ufeff姓名'), true, 'CSV must still send the original text');
assert.equal(exports.preview.value[0].name, '真实用户');
assert.equal(exports.importLoading.value, false);

const old = deferred();
previewResult = old.promise;
const staleFile = exports.onUpload(csv('old.csv', 'old'));
await tick();
assert.equal(exports.importLoading.value, true);
assert.equal(exports.preview.value.length, 0, 'a new file immediately clears the old preview');
previewResult = [row];
await exports.onUpload({ name: 'users.xlsx', size: 100, base64: 'eGxzeA==' });
assert.equal(previewCalls.at(-1).xlsxBase64, 'eGxzeA==');
old.resolve([{ ...row, name: 'STALE' }]);
await staleFile;
assert.equal(exports.preview.value[0].name, '真实用户');

const reading = deferred();
const before = previewCalls.length;
const staleRead = exports.onUpload({ name: 'read.csv', size: 1, text: () => reading.promise });
context.demoAs = 'U010006'; context.demoAs = 'U010001';
reading.resolve('late');
await staleRead;
assert.equal(previewCalls.length, before, 'switching away and back while reading must prevent a request');
assert.equal(exports.preview.value.length, 0);

previewResult = [row];
await exports.onUpload(csv('confirm.csv', 'rows'));
confirmation = deferred();
const canceledContext = exports.doImport();
assert.equal(exports.importing.value, true);
context.previewAs = 'U010006';
confirmation.resolve(true);
await canceledContext;
assert.equal(writes.length, 0, 'confirmation after a context switch must not write');
assert.equal(exports.importing.value, false);
context.previewAs = null;

previewResult = [row];
await exports.onUpload(csv('closed.csv', 'rows'));
confirmation = deferred();
const closed = exports.doImport();
exports.importOpen.value = false;
confirmation.resolve(true);
await closed;
assert.equal(writes.length, 0, 'closing the import modal invalidates a pending confirmation');
exports.openImport();
await exports.onUpload(csv('good.csv', 'rows'));
confirmation = deferred();
const good = exports.doImport();
await exports.doImport(); // Busy prevents duplicate confirmation and writes.
confirmation.resolve(true);
await good;
assert.equal(writes.length, 1);
assert.equal(writes[0][0].name, '真实用户');
assert.equal(exports.importOpen.value, false);

exports.openImport();
previewResult = Promise.reject(new Error('解析失败'));
await exports.onUpload(csv('broken.csv', 'broken'));
assert.equal(exports.importLoading.value, false);
assert.equal(exports.preview.value.length, 0);
assert.ok(messages.some((m) => m === '解析失败' || m === '预览失败'));
const afterFailure = previewCalls.length;
await exports.onUpload({ name: 'oversize.xlsx', size: 2 * 1024 * 1024 + 1 });
await exports.onUpload({ name: 'malware.xlsm', size: 1 });
assert.equal(previewCalls.length, afterFailure);
assert.equal(exports.preview.value.length, 0);
readonly.value = true;
await exports.onUpload(csv('readonly.csv', 'rows'));
await exports.doImport();
assert.equal(previewCalls.length, afterFailure);
readonly.value = false;
realMode = false;
const mockPage = {};
setup(mockPage);
mockPage.openImport();
await mockPage.onUpload(csv('mock.csv', 'anything'));
assert.equal(previewCalls.length, afterFailure);
assert.equal(mockPage.preview.value.length, 0, 'mock must not fabricate SAMPLE users from arbitrary files');
assert.ok(messages.some((m) => m.includes('演示模式')));
console.log('User CSV/XLSX upload, busy, stale file/context, confirmation and mock checks passed.');
