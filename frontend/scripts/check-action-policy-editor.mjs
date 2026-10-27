// Actual policy editor model: no writes, no auto-enablement and no silent normalization of old records.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import ts from 'typescript';
import * as vue from 'vue';
import { parse, compileTemplate } from '@vue/compiler-sfc';
const filename = 'ActionPolicyEditor.vue';
const sfc = parse(readFileSync(new URL('../src/pages/ontology/parts/ActionPolicyEditor.vue', import.meta.url), 'utf8'));
assert.equal(sfc.errors.length, 0);
assert.equal(compileTemplate({ source: sfc.descriptor.template.content, filename, id: filename }).errors.length, 0);
const code = ts.transpileModule(`${sfc.descriptor.scriptSetup.content}\nexport { text, policy, mode, limit, validate };`, { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText;
function editor(initial) {
  const exports = {};
  runInNewContext(code, { exports, defineProps: () => ({ initial, params: [{ api: 'amount', name: '金额', type: '金额' }] }), defineExpose() {}, require: () => vue });
  return exports;
}
const original = { mode: 'confirm', unknown: { preserve: null } };
const invalid = editor(original);
assert.equal(JSON.stringify(JSON.parse(invalid.text.value)), JSON.stringify(original));
assert.equal(invalid.validate(), false, 'unknown old fields are retained, not silently normalized');
const edit = editor({ mode: 'confirm', overflow: 'stage' });
assert.equal(edit.policy.value.mode, 'confirm');
edit.mode('auto'); assert.equal(edit.validate(), false, 'auto needs an explicit daily limit');
edit.limit('dailyCount', 3); assert.equal(edit.validate(), true);
edit.text.value = JSON.stringify({ mode: 'auto', limits: { dailyCount: 3, amount: { param: 'amount', max: 0 } } });
assert.equal(edit.validate(), true, 'zero amount cap is legal');
edit.text.value = JSON.stringify({ mode: 'auto', limits: { dailyCount: 0 } }); assert.equal(edit.validate(), false);
edit.text.value = JSON.stringify({ mode: 'confirm', callers: [] }); assert.equal(edit.validate(), false);
edit.text.value = JSON.stringify({ mode: 'confirm', callers: null, limits: null }); assert.equal(edit.validate(), true);
edit.text.value = JSON.stringify({ mode: 'auto', limits: { dailyCount: 3 } }); edit.mode('suggest');
assert.equal(edit.policy.value.limits, undefined); assert.equal(edit.validate(), true);
console.log('Action policy editor passed: raw preservation, safe confirm, explicit auto limits, JSON/control roundtrip, nullable optional fields.');
