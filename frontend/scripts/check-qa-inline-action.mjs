// Run: node scripts/check-qa-inline-action.mjs (pure UI logic; no server or database writes).
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import ts from 'typescript';
import * as vue from 'vue';
import { parse, compileScript, compileTemplate } from '@vue/compiler-sfc';

const source = readFileSync(new URL('../src/pages/qa/Workbench.vue', import.meta.url), 'utf8');
const script = source.match(/<script setup lang="ts">([\s\S]*?)<\/script>/)[1];
const compiled = ts.transpileModule(`${script}\nexport { runAction, actionBusy, actionRuns, actionIssues, refreshAction, restoreActions, matchesAction };`, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
}).outputText;
const deferred = () => { let resolve; const promise = new Promise((r) => { resolve = r; }); return { promise, resolve }; };
const tick = () => new Promise(setImmediate);
const action = () => ({ type: 'safe', name: 'Update note', canStage: true, stageVersion: 1, dryRun: { passed: true, checked: 0, failed: [] }, stage: { typeId: 'safe', objKind: 'item', objId: 'item-1', params: '{"note":"hello"}' } });
const run = (patch = {}) => ({ id: 'ACT-1', typeId: 'safe', action: 'Update note', objKind: 'item', objId: 'item-1', objLabel: 'Item 1', alertId: null, params: '{"note":"hello"}', status: '待确认', can: { confirm: true }, ...patch });
function harness(overrides = {}) {
  const calls = [], messages = [];
  const readonly = vue.ref(false), context = vue.ref('workspace-1');
  const session = { real: true, activeId: vue.ref('session-1'), detail: vue.ref({ id: 'session-1', turns: [{ id: 'turn-1', kind: 'answer', answer: { card: 'action', action: action() } }] }) };
  const route = { query: {} }, options = { frozen: true };
  const api = {
    actionsReal: () => true, qaRequestOptions: () => options,
    stageTurnAction: async (...args) => { calls.push(['stage', ...args]); return run(); },
    getAction: async (...args) => { calls.push(['get', ...args]); return run(); },
    confirmAction: async (...args) => { calls.push(['confirm', ...args]); return run({ status: '执行中', can: { confirm: false } }); },
    withdrawAction: async (...args) => { calls.push(['withdraw', ...args]); return run({ status: '已撤回' }); },
    ...overrides,
  };
  const confirmation = { ask: async (request) => { calls.push(['dialog', request]); return true; } };
  const imports = {
    vue: { ...vue, watch() {}, onMounted() {}, onBeforeUnmount() {} },
    'vue-router': { useRoute: () => route, useRouter: () => ({ replace: ({ query }) => { route.query = query; } }) },
    'ant-design-vue': { message: Object.fromEntries(['error', 'warning', 'success', 'info'].map((name) => [name, (text) => messages.push([name, text])])) },
    '@ant-design/icons-vue': {}, '@/pages/ops/actionReview': { reviewFields: () => [] },
    '@/composables/useDrawer': { useDrawer: () => ({}) }, '@/stores/confirm': { useConfirmStore: () => confirmation },
    '@/mock': {}, './useQueryParam': { useQueryParam: () => vue.ref('') },
    '@/composables/useReadonly': { useReadonly: () => ({ readonly, reason: vue.ref('') }) },
    '@/composables/usePreview': { usePreview: () => ({}) },
    './useQaSession': { useQaSession: () => session, errText: String }, './qaScope': { useScopeCaliber: () => ({}) },
    '@/pages/objects/objMeta': { metaContextKey: () => context.value }, '@/api/qa': api,
  };
  const exports = {};
  runInNewContext(compiled, { exports, require: (name) => {
    if (name in imports) return imports[name];
    if (name.endsWith('.vue')) return {};
    throw new Error(`Missing test dependency ${name}`);
  } });
  return { ...exports, calls, messages, readonly, context, session, api, confirmation, options };
}
const a = action();
const h = harness();
const sessionRoute = { query: { ontology: '16', sessQ: '设备' } };
const selectedSession = vue.ref('S0002');
runInNewContext(script.match(/watch\(sess\.activeId, id => \{[\s\S]*?\}, \{ immediate: true \}\);/)[0], {
  watch: vue.watch, sess: { activeId: selectedSession }, route: sessionRoute,
  router: { replace: ({ query }) => { sessionRoute.query = query; } },
});
assert.equal(sessionRoute.query.session, 'S0002', 'the selected historical session must survive refresh in the URL');
selectedSession.value = 'S0003'; await vue.nextTick();
assert.deepEqual({ ...sessionRoute.query }, { ontology: '16', sessQ: '设备', session: 'S0003' }, 'selection preserves workspace and list filters');
selectedSession.value = ''; await vue.nextTick();
assert.equal(sessionRoute.query.session, undefined, 'context reset removes the previous workspace session');
assert.equal(h.matchesAction(run({ params: '{"score":90}' }), { ...a, params: { score: 90 }, stage: { ...a.stage, params: 'score=90' } }), true, 'compare the structured parameters used by the stage endpoint, not the display summary');
assert.equal(h.matchesAction(run({ params: '{"score":91}' }), { ...a, params: { score: 90 }, stage: { ...a.stage, params: 'score=91' } }), false, 'a matching summary cannot override different structured card parameters');
assert.equal(h.matchesAction(run({ params: '{"b":[2,1],"a":{"c":1}}' }), { stage: { ...a.stage, params: '{"a":{"c":1},"b":[2,1]}' } }), true);
assert.equal(h.matchesAction(run({ params: '{}' }), { stage: { ...a.stage, params: null } }), true);
assert.equal(h.matchesAction(run({ params: 'null' }), { stage: { ...a.stage, params: null } }), false);
assert.equal(h.matchesAction(run({ alertId: 'ALERT-1' }), { ...a, decision: { alertId: 'ALERT-1' } }), true);
assert.equal(h.matchesAction(run({ alertId: 'ALERT-1' }), { ...a, decision: { alertId: 'ALERT-2' } }), false);
assert.equal(h.matchesAction(run({ alertId: 'ALERT-1' }), a), false);
assert.equal(h.matchesAction(run({ alertId: undefined }), a), false, 'missing alert metadata must not be inferred as no alert');
for (const patch of [{ typeId: 'other' }, { objKind: 'other' }, { objId: 'other' }, { params: '{"note":"other"}' }]) {
  const mismatch = harness({ stageTurnAction: async () => run(patch) });
  await mismatch.runAction(undefined, action(), 'turn-1');
  assert.equal(mismatch.calls.length, 0, 'mismatched stage record must not open confirmation or dispatch');
  assert.match(mismatch.actionIssues.value['turn-1'], /不一致/);
  assert.equal(mismatch.actionRuns.value['turn-1'].id, 'ACT-1');
}
for (const patch of [{ params: '{"note":"from another card"}' }, { alertId: 'ALERT-other' }, { alertId: undefined }]) {
  const restored = harness({ getAction: async () => run({ status: '成功', ...patch }) });
  restored.session.detail.value.turns[0].answer.action.runId = 'ACT-1';
  await restored.restoreActions();
  assert.ok(restored.actionIssues.value['turn-1'], 'restoration must validate ownership before presenting a terminal status');
  assert.equal(restored.actionRuns.value['turn-1'].status, '成功', 'raw record remains available but marked as unrelated');
  await restored.refreshAction('turn-1');
  assert.ok(restored.actionIssues.value['turn-1'], 'manual refresh must not clear a content/context mismatch');
  await restored.refreshAction('turn-1', true);
  assert.ok(restored.actionIssues.value['turn-1'], 'poll refresh must use the same content/context checks');
  await restored.runAction(undefined, restored.session.detail.value.turns[0].answer.action, 'turn-1');
  assert.equal(restored.calls.length, 0, 'a restored mismatch can never open confirmation or dispatch');
}
const dialog = deferred(), dispatch = deferred();
h.confirmation.ask = async (request) => { h.calls.push(['dialog', request]); return dialog.promise; };
h.api.confirmAction = async (...args) => { h.calls.push(['confirm', ...args]); return dispatch.promise; };
const first = h.runAction(undefined, a, 'turn-1');
await tick();
assert.equal(h.actionBusy.value, true);
assert.equal(a.runId, undefined, 'test input is detached from stored card');
assert.equal(h.session.detail.value.turns[0].answer.action.runId, 'ACT-1');
await h.runAction(undefined, action(), 'turn-1');
assert.equal(h.calls.filter(([name]) => name === 'stage').length, 1, 'double click while dialog is open must not stage again');
dialog.resolve(true);
await tick();
await h.runAction(undefined, action(), 'turn-1');
assert.equal(h.calls.filter(([name]) => name === 'confirm').length, 1, 'dispatch must remain locked until the response');
assert.equal(h.calls.find(([name]) => name === 'confirm')[2], h.options, 'dispatch keeps frozen request context');
dispatch.resolve(run({ status: '结果未知', can: { confirm: false } }));
await first;
assert.equal(h.actionRuns.value['turn-1'].status, '结果未知');
assert.equal(h.messages.some(([name]) => name === 'success'), false, 'unknown result is never a success notification');
assert.equal(h.actionBusy.value, false);

for (const status of ['审批中', '执行中', '成功', '失败', '结果未知', '已撤回']) {
  const restored = harness({ getAction: async () => run({ status, can: { confirm: false } }) });
  await restored.runAction(undefined, { ...action(), runId: 'ACT-1' }, 'turn-1');
  assert.equal(restored.calls.length, 0, 'existing nonpending run never stages or confirms again');
  assert.equal(restored.actionRuns.value['turn-1'].status, status);
}
const denied = harness({ stageTurnAction: async () => run({ can: { confirm: false, reasons: { confirm: 'Permission denied' } } }) });
await denied.runAction(undefined, action(), 'turn-1');
assert.equal(denied.calls.length, 0, 'server confirm permission remains authoritative');
const legacy = harness();
await legacy.runAction(undefined, { ...action(), stageVersion: undefined }, 'turn-1');
assert.equal(legacy.calls.length, 0, 'old card without runId and stageVersion cannot be resent');
const failedCard = { ...action(), dryRun: { passed: false, checked: 1, failed: [{ msg: 'Preflight failed' }] } };
const failedCheck = harness();
await failedCheck.runAction(undefined, failedCard, 'turn-1');
assert.equal(failedCheck.calls.length, 0, 'failed dry-run cannot stage or confirm a new run');
for (const status of ['待确认', '成功']) {
  const recovered = harness({ getAction: async (...args) => { recovered.calls.push(['get', ...args]); return run({ status }); } });
  recovered.session.detail.value.turns[0].answer.action = { ...failedCard, runId: 'ACT-1' };
  await recovered.runAction(undefined, recovered.session.detail.value.turns[0].answer.action, 'turn-1');
  await recovered.refreshAction('turn-1');
  assert.equal(recovered.actionRuns.value['turn-1'].status, status, 'failed card still reads and refreshes the original run');
  assert.deepEqual(recovered.calls.map(([name]) => name), ['get', 'get'], 'failed card never confirms an existing pending run');
  await recovered.runAction('ACT-1');
  assert.deepEqual(recovered.calls.map(([name]) => name), ['get', 'get', 'get'], 'URL recovery uses the original card check');
}
const conversationSource = readFileSync(new URL('../src/pages/qa/components/QaConversation.vue', import.meta.url), 'utf8');
const actionButtonDisabled = conversationSource.split('\n').find((line) => line.includes("emit('suggest', t.answer.action, t.id)")).match(/:disabled="([^"]+)"/)[1];
const buttonDisabled = (card, record) => runInNewContext(actionButtonDisabled, { t: { id: 'turn-1', answer: { action: card } }, actionRuns: { 'turn-1': record }, actionIssues: {}, readonly: false, actionBusy: false });
assert.equal(buttonDisabled(failedCard), true, 'failed new card disables confirmation');
assert.equal(buttonDisabled({ ...failedCard, runId: 'ACT-1' }), false, 'unread run still offers viewing its state');
assert.equal(buttonDisabled({ ...failedCard, runId: 'ACT-1' }, run()), true, 'failed pending card disables confirmation');
assert.equal(buttonDisabled(action()), false, 'passed new card can proceed');

const retry = harness({ confirmAction: async () => { throw new Error('network lost after accept'); }, getAction: async () => run({ status: '执行中', can: { confirm: false } }) });
await retry.runAction(undefined, action(), 'turn-1');
assert.equal(retry.actionRuns.value['turn-1'].status, '执行中', 'network error retrieves the original run');
await retry.runAction(undefined, retry.session.detail.value.turns[0].answer.action, 'turn-1');
assert.equal(retry.calls.filter(([name]) => name === 'stage').length, 1, 'network retry reuses saved runId');
const unavailable = harness({ getAction: async () => { throw new Error('offline'); } });
await unavailable.runAction(undefined, { ...action(), runId: 'ACT-1' }, 'turn-1');
assert.equal(unavailable.calls.length, 0, 'unreadable run never falls back to creating a new one');
for (const changed of ['context', 'readonly']) {
  const switched = harness();
  switched.confirmation.ask = async () => { switched[changed].value = changed === 'context' ? 'workspace-2' : true; return true; };
  await switched.runAction(undefined, action(), 'turn-1');
  assert.equal(switched.calls.filter(([name]) => name === 'confirm').length, 0, `${changed} change while confirming must stop dispatch`);
}
const cancelled = harness();
cancelled.confirmation.ask = async () => false;
await cancelled.runAction(undefined, action(), 'turn-1');
assert.equal(cancelled.calls.filter(([name]) => name === 'withdraw').length, 1);
assert.equal(cancelled.calls.filter(([name]) => name === 'confirm').length, 0);

// A generation failure stays visible after the toast, and belongs only to its session/context.
const sessionSource = readFileSync(new URL('../src/pages/qa/useQaSession.ts', import.meta.url), 'utf8');
const sessionCompiled = ts.transpileModule(sessionSource, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } }).outputText;
const sessionExports = {}, watchers = [];
let askFailure = true;
const sessionImports = {
  vue: { ...vue, watch: (source, callback) => { watchers.push(callback); } },
  'ant-design-vue': { message: { error() {} } }, '@/mock': {},
  '@/api/qa': { qaReal: () => true, qaRequestOptions: () => ({}), QaStreamUnsupported: class extends Error {}, askTurnStream: async () => {
    if (askFailure) throw new Error('HTTP 500');
    return { turn: { id: 'safe-answer', kind: 'empty' } };
  } },
  '@/api/http': { currentApiContext: () => ({ ontologyId: 'safe' }), isApiError: () => false },
  './qaFallback': { qaLive: vue.ref(true), qaFallBackIfDown: () => false },
};
runInNewContext(sessionCompiled, { exports: sessionExports, require: (name) => sessionImports[name] });
const session = sessionExports.useQaSession();
session.activeId.value = 'session-1';
session.detail.value = { id: 'session-1', title: 'Session', turns: [] };
session.send('safe question');
await tick();
assert.match(session.sendError.value, /HTTP 500/);
assert.equal(session.draft.value, 'safe question');
assert.equal(session.sending.value, false);
askFailure = false;
session.send();
assert.equal(session.sendError.value, '', 'retry clears the previous failure while pending');
await tick();
assert.equal(session.sendError.value, '');
session.sendError.value = 'old failure';
watchers[1]();
assert.equal(session.sendError.value, '', 'switching session clears its previous error');
session.sendError.value = 'old workspace failure';
watchers[0]();
assert.equal(session.sendError.value, '', 'switching workspace/identity clears previous error');

for (const file of ['Workbench.vue', 'components/QaConversation.vue']) {
  const filename = `src/pages/qa/${file}`;
  const { descriptor, errors } = parse(readFileSync(new URL(`../${filename}`, import.meta.url), 'utf8'), { filename });
  assert.deepEqual(errors, []);
  const scriptResult = compileScript(descriptor, { id: file });
  assert.deepEqual(compileTemplate({ source: descriptor.template.content, filename, id: file, compilerOptions: { bindingMetadata: scriptResult.bindings } }).errors, []);
}
console.log('QA inline action guards and SFC compilation passed.');
