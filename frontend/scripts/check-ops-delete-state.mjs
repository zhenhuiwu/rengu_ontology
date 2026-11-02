// Actual page handlers with controlled state: no HTTP, database or business deletion.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import { parse, compileScript, compileTemplate } from '@vue/compiler-sfc';
import ts from 'typescript';

const deferred = () => { let resolve; return { promise: new Promise(done => { resolve = done; }), resolve }; };
const ref = value => ({ value });
function pageSource(name) {
  const { descriptor, errors } = parse(readFileSync(new URL(`../src/pages/ops/${name}.vue`, import.meta.url), 'utf8'));
  assert.equal(errors.length, 0); compileScript(descriptor, { id: name });
  assert.equal(compileTemplate({ source: descriptor.template.content, filename: name, id: name, compilerOptions: { expressionPlugins: ['typescript'] } }).errors.length, 0);
  const script = descriptor.scriptSetup.content;
  const tree = ts.createSourceFile(name + '.ts', script, ts.ScriptTarget.Latest, true);
  const handler = tree.statements.find(node => ts.isFunctionDeclaration(node) && node.name?.text === 'removeCurrent');
  const reason = tree.statements.flatMap(node => ts.isVariableStatement(node) ? [...node.declarationList.declarations] : []).find(node => node.name.getText(tree) === 'deleteReason');
  assert.ok(handler && reason);
  return `const ${reason.getText(tree)};\n${handler.getText(tree)}\nexports.removeCurrent = removeCurrent;`;
}
for (const name of ['Actions', 'Alerts']) {
  const source = pageSource(name), key = name === 'Actions' ? 'act' : 'alert';
  for (const scenario of ['cancel', 'readonly', 'blocked', 'missing-version', 'changed-version', 'changed-selection', 'changed-context', 'failure', 'success', 'late-context']) {
    const record = { id: 'synthetic-record', action: 'synthetic action', objLabel: 'synthetic object', ruleId: 'synthetic rule', status: name === 'Actions' ? '成功' : '已解决', idemKey: 'synthetic-idem', lockVersion: 8, can: { delete: true, reasons: {} } };
    const confirmation = deferred(), deletion = deferred();
    const current = ref(record), readonly = ref(scenario === 'readonly'), deleting = ref('');
    const page = ref(3), size = ref(10), selectedKeys = ref([record.id, 'other']), pinned = ref(record.id);
    const route = { query: { [key]: record.id, ontology: '16', retained: 'yes' } };
    const remote = { detail: record, items: [record], total: 21 }, seen = new Map([[record.id, record]]);
    let ctx = 'scope16', calls = 0, listLoads = 0, facets = 0, notices = 0, failures = 0, confirmations = 0;
    if (scenario === 'blocked') { record.can.delete = false; record.can.reasons.delete = 'active external call'; }
    if (scenario === 'missing-version') delete record.lockVersion;
    const api = async (id, version) => { calls++; assert.equal(id, record.id); assert.equal(version, 8); if (scenario === 'failure') throw new Error('controlled conflict'); if (scenario === 'late-context') return deletion.promise; };
    const exports = {};
    runInNewContext(ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2020 } }).outputText, {
      exports, current, readonly, roReason: ref(''), deleting, live: ref(true),
      computed: callback => ({ get value() { return callback(); } }), dynamicContextKey: () => ctx,
      confirm: { ask: request => { confirmations++; assert.equal(request.danger, true); assert.equal(request.irreversible, true); assert.ok(request.dangerLabel); return confirmation.promise; } },
      deleteActionRun: api, deleteAlert: api, detailSeq: 0, remote, pinned, route,
      router: { replace: async target => { route.query = target.query; } },
      loadList: async () => { listLoads++; remote.items = []; remote.total = 20; },
      pq: { page, size }, page, pageSize: size, selectedKeys, seen,
      loadFacets: async () => { facets++; }, message: { success: () => notices++ }, fail: () => failures++,
    });
    const work = exports.removeCurrent();
    if (scenario === 'changed-version') record.lockVersion++;
    if (scenario === 'changed-selection') current.value = { ...record };
    if (scenario === 'changed-context') ctx = 'scope4';
    confirmation.resolve(scenario !== 'cancel');
    if (scenario === 'late-context') { await new Promise(setImmediate); ctx = 'scope4'; deletion.resolve(); }
    await work;
    assert.equal(deleting.value, '');
    if (scenario === 'success') {
      assert.equal(calls, 1); assert.equal(listLoads, 1); assert.equal(notices, 1);
      assert.equal(remote.detail, null); assert.equal(pinned.value, undefined); assert.equal(route.query[key], undefined);
      assert.equal(route.query.retained, 'yes'); assert.equal(page.value, 2, 'empty last page must return to the new last page');
      if (name === 'Alerts') { assert.deepEqual(selectedKeys.value, ['other']); assert.equal(seen.has(record.id), false); assert.equal(facets, 1); }
    } else {
      assert.equal(listLoads, 0); assert.equal(notices, 0); assert.equal(remote.detail, record); assert.equal(route.query[key], record.id);
      assert.equal(calls, ['failure', 'late-context'].includes(scenario) ? 1 : 0);
      assert.equal(failures, scenario === 'failure' ? 1 : 0);
    }
    if (['readonly', 'blocked', 'missing-version'].includes(scenario)) assert.equal(confirmations, 0);
  }
}
console.log('Ops delete state: both SFCs and 20 controlled confirmation/version/context/failure/reload cases passed; no real requests.');
