// Offline: workspace-local identifiers must never travel through cross-workspace copy.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import ts from 'typescript';
import { parse, compileScript } from '@vue/compiler-sfc';

const source = readFileSync(new URL('../src/pages/admin/apiKeyCopy.ts', import.meta.url), 'utf8');
const exports = {};
runInNewContext(ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText, { exports });
const { canCopyKeyWorkspace } = exports;
assert.equal(canCopyKeyWorkspace([1], '1'), true);
assert.equal(canCopyKeyWorkspace([1], undefined), true);
for (const [ids, target] of [
  [[11], '1'], [[1, 11], '1'], [[], '1'], [undefined, '1'],
  [[1], 'invalid'], [[1], '0'], [['1'], '1'], [[1.5], '1.5'],
  [[9007199254740992], '9007199254740992'],
]) assert.equal(canCopyKeyWorkspace(ids, target), false);
const filename = 'ApiKeys.vue';
const text = readFileSync(new URL('../src/pages/admin/ApiKeys.vue', import.meta.url), 'utf8');
const { descriptor, errors } = parse(text, { filename });
assert.equal(errors.length, 0);
compileScript(descriptor, { id: filename, inlineTemplate: true });
assert.ok(text.includes('src && !cloneBlocked.value'), 'Copy assignment must use the workspace guard');
assert.ok(text.includes('if (cloneBlocked.value) e.push'), 'Submission must reject blocked copy');
assert.ok(text.includes(':disabled="busy || !!cloneBlocked"'), 'Blocked form must remain disabled');
assert.ok(descriptor.template.content.includes(':value="mask(secret.key)"'), 'One-time key input must show only the existing mask');
assert.doesNotMatch(descriptor.template.content, /secret(?:\.value)?\.text/, 'Raw credentials must never bind to the template');
assert.ok(!text.includes('请手动选中复制'), 'Clipboard failure must not tell users to copy the mask');

// Exercise the real page functions with inert rows and promises; no HTTP client is loaded.
const page = ts.createSourceFile(filename, descriptor.scriptSetup.content, ts.ScriptTarget.Latest, true, ts.ScriptKind.TS);
const copy = page.statements.find(node => ts.isFunctionDeclaration(node) && node.name?.text === 'copySecret').getText(page);
let copied, copyFailure;
const copyState = {
  secret: { value: { text: 'inert-copy-fixture' } },
  navigator: { clipboard: { writeText: async value => { copied = value; } } },
  message: { success() {}, warning: value => { copyFailure = value; } },
};
runInNewContext(ts.transpileModule(copy, { compilerOptions: { target: ts.ScriptTarget.ES2020 } }).outputText, copyState);
await copyState.copySecret();
assert.equal(copied, 'inert-copy-fixture', 'Copy must still receive the credential rather than the displayed mask');
copyState.navigator.clipboard.writeText = async () => { throw new Error('inert permission denial'); };
await copyState.copySecret();
assert.match(copyFailure, /剪贴板权限.*重试.*掩码/, 'Failed copy must explain retrying without exposing the credential');
const functions = page.statements.filter(node => ts.isFunctionDeclaration(node)
  && ['loadDetail', 'revoke', 'savePolicy', 'revokeAccount'].includes(node.name?.text)).map(node => node.getText(page)).join('\n');
const defer = () => { let resolve; const promise = new Promise(done => { resolve = done; }); return { promise, resolve }; };
let currentContext = { ontologyId: '1' };
let readKey;
let fresh = { id: 'inert-key', name: 'example', version: 2, status: '已吊销', scopes: ['meta:read'], binding: {}, calls7d: 0 };
let accountRevokes = 0;
const state = {
  real: true, detailSeq: 0, remoteDetail: { value: null }, remoteCalls: { value: null }, detailError: { value: '' }, cpage: { value: 1 }, CSIZE: 10,
  readonly: { value: false }, busy: { value: false }, canManageSa: { value: true },
  q: key => key === 'key' ? 'inert-key' : '', currentApiContext: () => currentContext,
  rk: key => key, errorText: String, scopesText: () => '', bindText: () => '', mask: () => 'masked', fmtInt: String,
  confirm: { ask: async () => true }, message: { success() {}, error() {} },
  apiKeyDetail: () => readKey, apiKeyCalls: async () => ({ items: [{ id: 'inert-call' }], total: 1, page: 1 }),
  revokeApiKey: async () => fresh, updateApiKey: async () => fresh,
  revokeServiceAccount: async () => { accountRevokes++; },
  reload: async () => {}, loadAccounts: async () => {}, refreshMeta: async () => {},
  policyKey: { value: null }, policy: { name: 'example', note: '', ip: '', changeExpiry: false, scopes: ['meta:read'], rate: null, quota: null },
};
runInNewContext(ts.transpileModule(functions, { compilerOptions: { target: ts.ScriptTarget.ES2020 } }).outputText, state);
const oldRead = defer(); readKey = oldRead.promise;
const loadingBeforeRevoke = state.loadDetail();
await state.revoke({ ...fresh, version: 1, status: '有效' });
oldRead.resolve({ ...fresh, version: 1, status: '有效' }); await loadingBeforeRevoke;
assert.equal(state.remoteDetail.value.version, 2, 'An older detail response must not overwrite the revocation');
assert.equal(state.remoteCalls.value.total, 1, 'Discarding stale key data must retain the call response');
const olderPolicyRead = defer(); readKey = olderPolicyRead.promise;
const loadingBeforeSave = state.loadDetail();
state.policyKey.value = { ...fresh }; fresh = { ...fresh, version: 3, name: 'example-renamed' };
await state.savePolicy();
olderPolicyRead.resolve({ ...fresh, version: 2 }); await loadingBeforeSave;
assert.equal(state.remoteDetail.value.version, 3, 'A completed save must keep the returned version');
readKey = Promise.resolve(fresh);
state.remoteDetail.value = { ...fresh, status: '有效' };
await state.revokeAccount({ id: 'inert-account', name: 'example', activeKeys: 1 });
assert.equal(state.remoteDetail.value.status, '已吊销', 'Account revocation must refresh an open key detail');
const confirmation = defer(); state.confirm.ask = () => confirmation.promise;
const confirming = state.revokeAccount({ id: 'inert-account', name: 'example', activeKeys: 1 });
currentContext = { ontologyId: '2' }; confirmation.resolve(true); await confirming;
assert.equal(accountRevokes, 1, 'A context switch during confirmation must cancel account revocation');
console.log('API key copy and maintenance recovery checks passed (offline; no API calls).');
