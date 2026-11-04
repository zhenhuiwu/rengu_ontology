// Execute the page's real loaders with delayed reads; no HTTP, database or timers.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import ts from 'typescript';
import { ref } from 'vue';

const source = readFileSync(new URL('../src/pages/data/Connections.vue', import.meta.url), 'utf8').match(/<script setup lang="ts">([\s\S]*?)<\/script>/)[1];
const tree = ts.createSourceFile('Connections.ts', source, ts.ScriptTarget.Latest, true);
const names = new Set(['cdcSeq', 'mirrorSeq', 'schedulerSeq', 'cdcTimer', 'mirrorTimer', 'schedulerTimer']);
const pieces = tree.statements.filter(node =>
  ts.isFunctionDeclaration(node) && ['loadCdc', 'loadMirrors', 'loadScheduler'].includes(node.name?.text)
  || ts.isVariableStatement(node) && node.declarationList.declarations.some(d => names.has(d.name.getText(tree)))
  || ts.isExpressionStatement(node) && node.getText(tree).startsWith('onScopeDispose(')
).map(node => node.getText(tree)).join('\n');
const js = ts.transpileModule(`${pieces}\nexport { loadCdc, loadMirrors, loadScheduler };`, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } }).outputText;
const calls = [], timers = [], cleanups = [], document = { hidden: false };
let scope = 'A';
const deps = { real: ref(true), selected: ref({ id: 'DS-1', type: 'MySQL' }), tab: ref('sync'), mirrorObjects: ref([{ objectId: 'Device' }]), contextKey: () => scope,
  cdcStates: ref({}), cdcErrors: ref({}), mirrorStates: ref({}), mirrorErrors: ref({}), schedulerState: ref(null) };
const request = kind => new Promise((resolve, reject) => calls.push({ kind, resolve, reject }));
const exports = {};
runInNewContext(js, { exports, ...deps, document, Promise, isApiError: () => false,
  getObjectCdc: () => request('cdc'), getObjectMirror: () => request('mirror'), getMirrorScheduler: () => request('scheduler'),
  onScopeDispose: fn => cleanups.push(fn), window: { setInterval: fn => (timers.push(fn), timers.length), clearInterval() {} } });
const fixtures = [
  ['cdc', exports.loadCdc, deps.cdcStates, deps.cdcErrors],
  ['mirror', exports.loadMirrors, deps.mirrorStates, deps.mirrorErrors],
  ['scheduler', exports.loadScheduler, deps.schedulerState],
];
const value = (kind, marker) => ({ marker, enabled: true, active: true, status: 'running' });
const read = (kind, state) => kind === 'scheduler' ? state.value : state.value.Device;
for (const [kind, load, state, errors] of fixtures) {
  const initial = calls.length, old = load();
  await load(true); await load(true);
  assert.equal(calls.length, initial + 1, `${kind}: slow reads cannot overlap periodic polls`);
  const current = load();
  assert.equal(calls.length, initial + 2, `${kind}: explicit refresh can supersede the pending read`);
  calls.at(-1).resolve(value(kind, 'new')); await current;
  calls[initial].resolve(value(kind, 'old')); await old;
  assert.equal(read(kind, state).marker, 'new');
  const failed = load(true); calls.at(-1).reject(new Error('synthetic refusal')); await failed;
  if (errors) assert.ok(errors.value.Device); else assert.equal(state.value.active, false);
  const departed = load(); scope = `${scope}-next`;
  const next = load(); calls.at(-2).resolve(value(kind, 'departed')); await departed;
  calls.at(-1).resolve(value(kind, 'next')); await next;
  assert.equal(read(kind, state).marker, 'next');
  const leaveTab = load(); deps.tab.value = 'params'; calls.at(-1).resolve(value(kind, 'hidden-tab')); await leaveTab;
  assert.equal(read(kind, state).marker, 'next'); deps.tab.value = 'sync';
}
const before = calls.length;
document.hidden = true; timers.forEach(fn => fn()); assert.equal(calls.length, before, 'hidden pages do not poll');
document.hidden = false; timers.forEach(fn => fn()); assert.equal(calls.length, before + 3);
cleanups.forEach(fn => fn());
for (const call of calls.slice(before)) call.resolve(value(call.kind, 'unmounted'));
await Promise.resolve(); await Promise.resolve(); await Promise.resolve();
for (const [kind, load, state] of fixtures) { await load(); assert.notEqual(read(kind, state)?.marker, 'unmounted'); }
assert.equal(calls.length, before + 3, 'unmounted pages do not start reads');
console.log('Connection polling passed: pending polls, explicit refresh, failure, scope/tab change, hidden page and teardown.');
