// Run: node scripts/check-action-snapshot.mjs. Executes the real action API module; no network/DB writes.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import ts from 'typescript';
import * as vue from 'vue';
const defer = () => { let resolve, reject; const promise = new Promise((a, b) => { resolve = a; reject = b; }); return { promise, resolve, reject }; };
const tick = () => new Promise(setImmediate);
let context = { demoAs: 'old', previewAs: 'old-preview', ver: 'v1' };
let real = true;
const oldActions = defer(), oldEvents = defer(), newEvents = defer(), staleFailure = defer(), fourth = defer(), oldRuns = defer(), oldSystems = defer();
const requests = [];
const page = (prefix, n, count = 201) => ({ items: Array.from({ length: Math.min(200, count - (n - 1) * 200) }, (_, i) => ({ id: `${prefix}${(n - 1) * 200 + i}`, api: `f${i}` })), total: count });
const get = async (path, opts = {}) => {
  const who = opts.headers?.['X-Demo-As'];
  requests.push({ path, opts });
  if (path === '/ext-systems') return who === 'old' ? oldSystems.promise : { items: [{ id: who }] };
  if (path === '/action-runs') return who === 'old' && opts.query.page === 1 ? oldRuns.promise : page(who, opts.query.page);
  if (who === 'denied') throw new Error('403 FORBIDDEN');
  if (path === '/action-types' && opts.query.page === 1) {
    if (who === 'old') return oldActions.promise;
    if (who === 'fourth') return fourth.promise;
  }
  if (path === '/event-types' && opts.query.page === 1) {
    if (who === 'old') return oldEvents.promise;
    if (who === 'new') return newEvents.promise;
    if (who === 'failure') return staleFailure.promise;
  }
  if (path === '/action-types' || path === '/event-types') return page(path.includes('action') ? 'A' : 'E', opts.query.page, who === 'fourth' || who === 'failure' ? 0 : 201);
  if (/\/(params|payload-fields)$/.test(path)) return page('field', opts.query.page, /\/(A0|E0)\//.test(path) ? 201 : 0);
  if (/\/(submission-criteria|versions|subscribers)$/.test(path)) return { items: [] };
  if (path.endsWith('/writeback')) return { config: {}, edits: [], effects: [] };
  return { id: path.split('/').at(-1), name: who, sysId: null };
};
const client = { isReal: () => real, get };
const dependencies = {
  vue, './http': { apiClient: () => client, currentApiContext: () => context },
  './integration': { integrationApi: client }, './static': { registerDraft() {} }, '@/mock/releases': { DRAFTS: [] },
  '@/mock/actionEvent': { ACTION_TYPES: [{ id: 'DEMO-A' }], EVENT_TYPES: [{ id: 'DEMO-E' }], runsOf: () => [{ id: 'DEMO-RUN' }], writebackSystems: () => [{ id: 'DEMO-SYS' }] },
};
const source = readFileSync(new URL('../src/api/action.ts', import.meta.url), 'utf8').replaceAll('import.meta.env', '({})');
const { outputText } = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } });
const action = {};
runInNewContext(outputText, { exports: action, require: (key) => { assert.ok(key in dependencies, key); return dependencies[key]; } });
const old = action.loadSnapshot();
context = { demoAs: 'new', previewAs: 'new-preview', ver: 'v2' };
const current = action.loadSnapshot(); await tick();
assert.equal(action.snap.state, 'loading'); assert.equal(action.snap.actions.length, 0, 'action results must wait for events before atomic publication');
newEvents.resolve(page('E', 1)); await current;
assert.equal(action.actionTypes().length, 201); assert.equal(action.eventTypes().length, 201);
assert.equal(action.paramsOf({ id: 'A0' }).length, 0); assert.equal(action.payloadOf({ id: 'E0' }).length, 0);
assert.ok(requests.every(r => ['/action-types','/event-types'].includes(r.path)), 'directory must not issue per-type detail requests');
await action.loadActionDetail('A0'); assert.equal(action.actionDetailReady('A0'), true); assert.equal(action.actionDetailReady('A0', true), false);
assert.equal(action.paramsOf({id:'A0'}).length,0,'published details do not eagerly fetch full parameter lists');
await action.loadActionDetail('A0', true); await action.loadEventDetail('E0', true);
assert.equal(action.actionDetailReady('A0',true),true); assert.equal(action.eventDetailReady('E0',true),true);
assert.equal(action.paramsOf({ id: 'A0' }).length, 201); assert.equal(action.payloadOf({ id: 'E0' }).length, 201);
assert.ok(!requests.some(r => /\/(A1|E1)(?:\/|$)/.test(r.path)), 'unselected types never fetch sections');
oldActions.resolve(page('A', 1)); oldEvents.resolve(page('E', 1)); await old;
assert.equal(action.snap.actions[0].name, 'new');
assert.ok(requests.filter((r) => r.opts.headers?.['X-Demo-As'] === 'old').every((r) => r.opts.query.ver === 'v1' && r.opts.query.previewAs === 'old-preview' && r.opts.readonlyParams === false));
context = { demoAs: 'failure', ver: 'v3' }; const failed = action.loadSnapshot(); const caught = failed.catch((e) => e); await tick();
context = { demoAs: 'fourth', ver: 'v4' }; const latest = action.loadSnapshot();
staleFailure.reject(new Error('old failure')); await caught;
assert.equal(action.snap.state, 'loading', 'stale failure must not turn the new generation into an error');
fourth.resolve({ items: [], total: 0 }); await latest;
assert.equal(action.snap.state, 'ready'); assert.equal(action.actionTypes().length, 0, 'true empty lists never use demo definitions');
context = { demoAs: 'denied', ver: 'v5' }; await assert.rejects(action.loadSnapshot(), /403/);
assert.equal(action.snap.state, 'error'); assert.equal(action.actionTypes().length, 0);
context = { demoAs: 'old', ver: 'v1' }; const runs = action.listActionRuns('same', {page:1,size:10,status:'失败'}); const systems = action.loadWritebackSystems();
context = { demoAs: 'new', previewAs: 'new-preview', ver: 'v2' }; const runPage = await action.listActionRuns('same', {page:2,size:10,status:'成功'}); await action.loadWritebackSystems();
oldRuns.resolve(page('old', 1)); oldSystems.resolve({ items: [{ id: 'old' }] }); await runs; await systems;
assert.equal(runPage.total,201); assert.equal(runPage.items[0].id,'new200');
assert.equal(requests.filter(r=>r.path==='/action-runs').length,2,'runs issue one request per requested page, never fetch every page');
assert.equal(requests.filter(r=>r.path==='/action-runs').at(-1).opts.query.status,'成功');
assert.equal(action.writebackSystems()[0].id, 'new');
context = { demoAs: 'other', ver: 'v2' }; assert.equal(action.runsOf('same').length, 0); assert.equal(action.writebackSystems().length, 0);
real = false; assert.equal(action.actionTypes()[0].id, 'DEMO-A'); assert.equal(action.runsOf('same')[0].id, 'DEMO-RUN');
real = true; action.invalidateActionSnapshot();
assert.equal(action.snapshotReady(), false); assert.equal(action.actionTypes().length, 0);
context = { demoAs: 'fresh', ver: 'v6' }; await action.loadSnapshot();
assert.equal(action.actionTypes()[0].id, 'A0', 'a new release must load current definitions instead of the previous publication cache');
console.log('Action snapshot checks passed (complete pagination, atomic publication, pinned context, stale success/error, true empty/403, runs/systems isolation, explicit mock).');
