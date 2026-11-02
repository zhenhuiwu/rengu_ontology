// Actual dynamic API module, synthetic in-memory CR revisions; no HTTP/DB/model calls.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import ts from 'typescript';

const context = { ontologyId: '17', demoAs: 'admin', ver: null, previewAs: null };
const puts = [], reads = [];
let revision = 1, holdDetail;
const row = () => ({ id: 'R-TEMP', cr: 'CR-TEMP', crLockVersion: revision, displayStatus: '草稿中', draft: { expr: '1 == 0' } });
const api = {
  get: async path => {
    reads.push(path);
    if (path === '/releases/current') return { lockVersion: 100 };
    if (path === '/rules/R-TEMP') return holdDetail ? holdDetail.promise : row();
    if (path === '/change-requests/CR-TEMP') return { id: 'CR-TEMP', lockVersion: revision };
    throw Error(path);
  },
  post: async path => {
    if (path === '/change-requests') return { id: 'CR-TEMP', lockVersion: revision };
    if (path === '/rules' || path === '/rules/R-TEMP/draft') { revision += 3; return row(); }
    throw Error(path);
  },
  put: async (path, body) => {
    puts.push({ path, body });
    if (body.lockVersion !== revision) throw Object.assign(Error('真实并发版本冲突'), { code: 'VERSION_CONFLICT' });
    revision++;
    return { cr: 'CR-TEMP', lockVersion: revision, ver: 'synthetic-release', draft: row().draft };
  },
};
const drafts = [];
const deps = {
  './http': { apiClient: () => api, currentApiContext: () => ({ ...context }), isApiError: e => typeof e?.code === 'string' },
  '@/mock/releases': { DRAFTS: drafts },
  './static': { registerDraft: value => { drafts.push(value); } },
};
const source = readFileSync(new URL('../src/api/dynamic.ts', import.meta.url), 'utf8').replaceAll('import.meta.env.VITE_DYNAMIC_CR_WRITE', "'1'");
const js = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } }).outputText;
const exports = {};
runInNewContext(js, { exports, Error, require: key => { assert.ok(key in deps, key); return deps[key]; } });
const body = { cr: 'CR-TEMP', autoStage: { enabled: false }, thresholds: [], expr: '1 == 0' };
await exports.crOpen({ title: 'TEMP_DELETE' });
await exports.createRule({ cr: 'CR-TEMP', name: 'TEMP_DELETE_RULE' });
assert.equal(revision, 4, 'creation writes the CR after crOpen');
await exports.saveRuleDraft('R-TEMP', body);
assert.equal(puts.at(-1).body.lockVersion, 4, 'first save uses revision returned by create, not cached crOpen revision 1');
await exports.openRuleDraft('R-TEMP', 'CR-TEMP');
const opened = revision;
await exports.saveRuleDraft('R-TEMP', body);
assert.equal(puts.at(-1).body.lockVersion, opened, 'opening a draft also refreshes its exact returned revision');

const observed = revision;
revision++; // A different writer changes the CR after the response snapshot.
await assert.rejects(exports.saveRuleDraft('R-TEMP', body), /真实并发版本冲突/);
assert.equal(puts.at(-1).body.lockVersion, observed, 'never fetch latest revision silently to bypass genuine concurrent changes');
assert.equal(reads.filter(path => path === '/change-requests/CR-TEMP').length, 0);
await exports.getRule('R-TEMP');
await exports.saveRuleDraft('R-TEMP', body);
assert.equal(puts.at(-1).body.lockVersion, observed + 1, 'explicit detail reload restores the revision matching the loaded draft');

let resolve;
holdDetail = { promise: new Promise(done => { resolve = done; }) };
const oldRead = exports.getRule('R-TEMP');
context.ontologyId = '18';
holdDetail = undefined; revision = 30;
await exports.createRule({ cr: 'CR-TEMP' });
resolve({ ...row(), crLockVersion: 999 });
await assert.rejects(oldRead, /上下文已切换/);
await exports.saveRuleDraft('R-TEMP', body);
assert.equal(puts.at(-1).body.lockVersion, 33, 'old ontology detail cannot poison the current CR revision cache');
console.log('Rule CR revisions passed: first create/open save, real concurrent conflict, explicit reload, late context isolation.');
