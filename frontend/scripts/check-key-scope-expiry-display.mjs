// Actual display expressions only; no API, stored credentials, or browser.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import ts from 'typescript';
import { parse, compileScript } from '@vue/compiler-sfc';
const { descriptor, errors } = parse(readFileSync(new URL('../src/pages/admin/ApiKeys.vue', import.meta.url), 'utf8'));
assert.equal(errors.length, 0);
compileScript(descriptor, { id: 'ApiKeys', inlineTemplate: true });
for (const [id, label] of [['ak-create-rate', '每分钟请求数'], ['ak-create-quota', '每日请求数'], ['ak-policy-rate', '每分钟请求数'], ['ak-policy-quota', '每日请求数']]) {
  assert.ok(descriptor.template.content.includes(`<label for="${id}">${label}</label>`));
  assert.match(descriptor.template.content, new RegExp(`id="${id}"[^>]+aria-label="${label}"`));
}
const ast = ts.createSourceFile('ApiKeys.ts', descriptor.scriptSetup.content, ts.ScriptTarget.Latest, true, ts.ScriptKind.TS);
const code = ast.statements.filter(n => ts.isVariableStatement(n) && n.declarationList.declarations.some(d => ['daysLeft', 'realBinding'].includes(d.name.getText(ast)))).map(n => n.getText(ast)).join('\n');
const now = new Date(2026, 10, 1, 20, 30).getTime();
const state = {
  real: true, TODAY: '2026-11-01', Date: class extends Date { static now() { return now; } },
  meta: { value: { binding: { kind: '用户', scope: '仅元数据', userName: 'inert-user' } } },
  form: { principal: '用户', scopes: ['meta:read', 'objects:read'] },
  scopesText: values => values.map(s => s === 'meta:read' ? '元数据读取' : '对象读取').join('、'),
};
runInNewContext(ts.transpileModule(`${code}\nthis.daysLeft = daysLeft; this.realBinding = realBinding;`, { compilerOptions: { target: ts.ScriptTarget.ES2020 } }).outputText, state);
assert.match(state.realBinding().scope, /元数据读取、对象读取/);
assert.doesNotMatch(state.realBinding().scope, /仅元数据/);
state.form.scopes = ['meta:read'];
assert.match(state.realBinding().scope, /^元数据读取；/);
assert.equal(state.daysLeft('2026-12-01 20:30:00'), 30);
assert.equal(state.daysLeft('2026-11-01 20:31:00'), 1);
assert.equal(state.daysLeft('2026-11-01 20:29:00'), 0);
assert.equal(state.daysLeft('2026-11-01 20:30:00'), 0);
console.log('Key scope and remaining duration display passed (offline expressions only).');
