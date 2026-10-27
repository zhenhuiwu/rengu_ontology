import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import { parse } from '@vue/compiler-sfc';
import { ref } from 'vue';
import ts from 'typescript';

const { descriptor } = parse(readFileSync(new URL('../src/pages/admin/Roles.vue', import.meta.url), 'utf8'));
const source = ts.createSourceFile('Roles.ts', descriptor.scriptSetup.content, ts.ScriptTarget.Latest, true);
const names = ['objActions', 'actionRefs', 'actionKey', 'selectedActions', 'actionOptions', 'updateActions'];
const statements = source.statements.filter((s) =>
  ts.isVariableStatement(s) && s.declarationList.declarations.some((d) => names.includes(d.name.getText(source))) ||
  ts.isFunctionDeclaration(s) && names.includes(s.name.text));
const oldAction = { id: 'AT-OLD', api: 'wf_record_review', name: '原复核动作' };
const newAction = { id: 'AT-NEW', api: 'new_action', name: '新验证动作' };
const permObjects = ref([{ name: '订单', actions: [oldAction.name, newAction.name], actionRefs: [oldAction, newAction] }]);
const output = {};
const code = `${statements.map((s) => s.getText(source)).join('\n')}\nObject.assign(exports,{selectedActions,actionOptions,updateActions});`;
runInNewContext(ts.transpileModule(code, { compilerOptions: { target: ts.ScriptTarget.ES2020 } }).outputText,
  { realRoles: true, permObjects, exports: output });
const row = { objType: '订单', actions: [oldAction.api, oldAction.id, 'retired-value'] };
assert.deepEqual(Array.from(output.selectedActions(row)), [oldAction.id, 'legacy:retired-value']);
assert.equal(output.actionOptions(row).find((option) => option.value === 'legacy:retired-value').disabled, false);
output.updateActions(row, [oldAction.id, newAction.id, 'legacy:retired-value']);
assert.deepEqual(Array.from(row.actions), [oldAction.api, oldAction.id, 'retired-value', newAction.id]);
output.updateActions(row, [newAction.id, 'legacy:retired-value']);
assert.deepEqual(Array.from(row.actions), ['retired-value', newAction.id]);
output.updateActions(row, [newAction.id]);
assert.deepEqual(Array.from(row.actions), [newAction.id]);
assert.equal(output.actionOptions(row).some((option) => option.value === 'legacy:retired-value'), false, 'removed stale grants cannot be selected again');
assert.equal(output.actionOptions({ objType: '商品', actions: [] }).length, 0, 'another object must not gain the order choices');
permObjects.value[0].actionRefs.push({ id: 'AT-DUPLICATE-NAME', api: 'other_action', name: oldAction.name });
const ambiguous = { objType: '订单', actions: [oldAction.name] };
assert.deepEqual(Array.from(output.selectedActions(ambiguous)), [`legacy:${oldAction.name}`]);
assert.equal(output.actionOptions(ambiguous).find((option) => option.value === `legacy:${oldAction.name}`).disabled, false);
console.log('Role checkbox aliases stay visibly selected, retain old spellings and stale values, and add stable codes only.');
