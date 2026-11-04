/** Run the actual editor's pure formatter/insertion code without a browser or API. */
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import { parse } from '@vue/compiler-sfc';
import ts from 'typescript';

const source = parse(readFileSync('src/pages/ontology/parts/DefinitionEditor.vue', 'utf8')).descriptor.scriptSetup.content;
const tree = ts.createSourceFile('DefinitionEditor.ts', source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TS);
const names = new Set(['buildMetricExpression', 'insertMetricReference']);
const selected = tree.statements.filter(node => ts.isFunctionDeclaration(node) && names.has(node.name?.text));
assert.equal(selected.length, 2);
const code = ts.transpileModule(selected.map(node => node.getText(tree)).join('\n'), {
  compilerOptions: { target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.None },
}).outputText;
const state = {
  readonly: { value: false }, loading: { value: false }, busy: { value: false }, error: { value: '' },
  metricExpression: { value: "METRIC('sales','v3')" }, visible: { value: true }, props: { kind: 'metric' },
  values: { formula: '' }, formulaSelection: { value: undefined }, formulaElement: { value: undefined },
  nextTick: async () => {},
};
vm.createContext(state);
vm.runInContext(code, state);
const metric = { id: 'M-1', api: 'sales', name: '销售额', version: 'v3', status: '已发布' };
const base = "METRIC('sales','v3')";
assert.equal(state.buildMetricExpression(metric, ''), base);
for (const op of ['PREV', 'MOVING_AVG', 'MOVING_SUM', 'DELTA', 'PCT_CHANGE']) {
  assert.equal(state.buildMetricExpression(metric, op, 7), `${op}(${base},7)`);
  for (const value of [null, 0, 94, 1.5, Infinity]) assert.equal(state.buildMetricExpression(metric, op, value), '');
}
for (const op of ['WOW', 'RUNNING_SUM', 'ABS']) assert.equal(state.buildMetricExpression(metric, op), `${op}(${base})`);
assert.equal(state.buildMetricExpression(metric, 'ROUND', 0), `ROUND(${base},0)`);
assert.equal(state.buildMetricExpression(metric, 'ROUND', 10), `ROUND(${base},10)`);
assert.equal(state.buildMetricExpression(metric, 'ROUND', 11), '');
assert.equal(state.buildMetricExpression(metric, 'COALESCE'), `COALESCE(${base},0)`);
assert.equal(state.buildMetricExpression({ ...metric, api: "sale's", version: "v'3" }, ''), "METRIC('sale''s','v''3')");
for (const changed of [{ version: '' }, { version: 'current' }, { status: '草稿' }]) {
  assert.equal(state.buildMetricExpression({ ...metric, ...changed }, ''), '');
}
assert.equal(state.buildMetricExpression(metric, 'UNKNOWN'), '');
await state.insertMetricReference();
assert.equal(state.values.formula, base);
state.values.formula = '1 + OLD * 2'; state.formulaSelection.value = { start: 4, end: 7 };
await state.insertMetricReference();
assert.equal(state.values.formula, `1 + ${base} * 2`);
const preserved = state.values.formula;
state.readonly.value = true;
await state.insertMetricReference();
assert.equal(state.values.formula, preserved);
state.readonly.value = false; state.visible.value = false;
await state.insertMetricReference();
assert.equal(state.values.formula, preserved);
console.log('Metric inserter passed: fixed published versions, escaped literals, all operations and numeric bounds, selected-range insertion, readonly/closed guards.');
