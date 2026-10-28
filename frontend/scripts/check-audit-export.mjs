// Run: node scripts/check-audit-export.mjs (transpiles the real HTTP/API/page modules).
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import ts from 'typescript';
import * as vue from 'vue';
import dayjs from 'dayjs';

function load(path, imports, globals = {}, expose = '') {
  let source = readFileSync(new URL(`../src/${path}`, import.meta.url), 'utf8');
  if (path.endsWith('.vue')) source = source.match(/<script setup lang="ts">([\s\S]*?)<\/script>/)[1];
  const { outputText } = ts.transpileModule(`${source}\n${expose}`, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
  });
  const exports = {};
  runInNewContext(outputText, { exports, URLSearchParams, Headers, AbortController, Response, DOMException, console, ...globals, require: (name) => {
    assert.ok(name in imports, `Missing dependency ${name}`);
    return imports[name];
  } });
  return exports;
}
const deferred = () => {
  let resolve;
  const promise = new Promise((r) => { resolve = r; });
  return { promise, resolve };
};
let real = true;
let context = { demoAs: 'U010001', ver: 'v1', previewAs: 'U010006' };
const calls = [];
let respond = () => new Response('CSV', { headers: { 'Content-Type': 'text/csv' } });
const http = load('api/http.ts', { './config': { isReal: () => real, SWITCH_PREFIX: { admin: 'admin' } } }, {
  fetch: async (url, options) => { calls.push({ url, options }); return respond(url); },
});
http.setApiContext(() => context);
const api = http.apiClient('admin');
assert.equal(await (await api.getBlob('/file', { query: { x: 1 } })).text(), 'CSV');
assert.equal(calls[0].options.headers.get('X-Demo-As'), 'U010001');
assert.equal(calls[0].url, '/api/admin/v1/file?x=1&ver=v1&previewAs=U010006');
context = { demoAs: 'viewer' };
for (const headers of [{ 'X-Demo-As': 'admin' }, { 'x-demo-as': 'admin' }, { 'X-Demo-As': '' }]) {
  await api.getBlob('/file', { headers });
  const sent = Object.fromEntries(calls.at(-1).options.headers);
  const names = Object.keys(sent).filter((name) => name.toLowerCase() === 'x-demo-as');
  assert.equal(names.length, 1);
  assert.equal(sent[names[0]], Object.values(headers)[0], 'explicit identity, even empty, must win');
}
respond = () => new Response(JSON.stringify({ code: 'NO_PERMISSION', message: '拒绝' }), { status: 403 });
await assert.rejects(api.getBlob('/file'), (e) => e instanceof http.ApiError && e.code === 'NO_PERMISSION');
real = false;
const before = calls.length;
await assert.rejects(api.getBlob('/file'), (e) => e.code === 'MOCK_ONLY');
assert.equal(calls.length, before, 'mock must not fetch');
real = true;

const admin = load('api/admin.ts', {
  './http': http, '@/mock/admin': { ADMIN_DEPTS: [], AUDIT_LOGS: [] },
  '@/mock/paginate': { paginateCursor: () => ({ items: [], nextCursor: null }) },
});
context = { demoAs: 'U010001' };
const detail = deferred();
respond = () => detail.promise;
const started = JSON.stringify(context);
const pending = admin.downloadAuditExport('EXP-AL1', () => {
  if (JSON.stringify(context) !== started) throw new Error('context changed');
});
const count = calls.length;
context = { demoAs: 'U010006' };
detail.resolve(new Response(JSON.stringify({ status: 'ready' })));
await assert.rejects(pending, /context changed/);
assert.equal(calls.length, count, 'context change after metadata must prevent the download request');

// Actual page: an identity switch, including switching away and back, invalidates late responses.
context = vue.reactive({ demoAs: 'U010001', ver: null, previewAs: null });
const readonly = vue.ref(false);
let post;
let blob;
let downloads = 0;
let requests = 0;
const errors = [];
const empty = { items: [], nextCursor: null };
const page = load('pages/admin/AuditLog.vue', {
  vue, dayjs: { default: dayjs }, 'ant-design-vue': { message: { error: (e) => errors.push(e), info() {}, success() {} } },
  '@/api/http': http, '@/components/AppPagination.vue': {},
  '@/composables/useReadonly': { useReadonly: () => ({ readonly, reason: vue.ref('') }) },
  '@/composables/usePagedQuery': { useCursorQuery: () => ({ cursor: vue.ref(null), size: 20 }) },
  '@/api/admin': {
    adminApiOn: () => true,
    mockAuditActions: () => [], queryAuditLogsMock: () => empty, queryAuditLogs: async () => empty,
    fetchAuditActions: async () => [], exportAuditLogs: () => post.promise,
    downloadAuditExport: async () => { requests++; return blob.promise; },
  },
}, {
  document: { createElement: () => ({ click: () => { downloads++; } }) },
  URL: { createObjectURL: () => 'blob:test', revokeObjectURL() {} }, setTimeout() {},
}, 'export { onExport, exporting };');

post = deferred(); blob = deferred();
const stalePost = page.onExport();
assert.equal(page.exporting.value, true);
context.demoAs = 'U010006'; context.demoAs = 'U010001';
post.resolve('EXP-AL1');
await stalePost;
assert.equal(requests, 0);
assert.equal(downloads, 0);
assert.equal(page.exporting.value, false);

post = deferred(); blob = deferred();
const staleBlob = page.onExport();
post.resolve('EXP-AL2');
await new Promise(setImmediate);
context.ver = 'v2';
blob.resolve(new Blob(['CSV']));
await staleBlob;
assert.equal(downloads, 0, 'late blob must not download after changing version');
assert.equal(errors.length, 2);
context.ver = null;
post = deferred(); blob = deferred();
const good = page.onExport();
post.resolve('EXP-AL3');
await new Promise(setImmediate);
blob.resolve(new Blob(['CSV']));
await good;
assert.equal(downloads, 1);
assert.equal(page.exporting.value, false);
console.log('Audit export HTTP, identity/readonly propagation, failures and late-response checks passed.');
