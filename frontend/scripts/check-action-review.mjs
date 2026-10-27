// Real Vue rendering of review/summary disclosures; no server, model, or database writes.
import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { runInNewContext } from 'node:vm';
import { compileScript, parse, registerTS } from '@vue/compiler-sfc';
import ts from 'typescript';
import * as vue from 'vue';
import { renderToString } from 'vue/server-renderer';
registerTS(() => ts);

const helper = {};
runInNewContext(ts.transpileModule(readFileSync(new URL('../src/pages/ops/actionReview.ts', import.meta.url), 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS },
}).outputText, { exports: helper });
const slot = { setup: (_, { slots }) => () => vue.h('section', null, Object.values(slots).flatMap((s) => s())) };
function component(path) {
  const filename = fileURLToPath(new URL(`../src/${path}`, import.meta.url));
  const { descriptor, errors } = parse(readFileSync(filename, 'utf8'), { filename });
  assert.equal(errors.length, 0);
  const code = compileScript(descriptor, { id: path, inlineTemplate: true,
    fs: { fileExists: existsSync, readFile: (file) => readFileSync(file, 'utf8') } }).content;
  const exports = {};
  runInNewContext(ts.transpileModule(code, { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText, {
    exports,
    require: (name) => {
      if (name === 'vue') return vue;
      if (name === './actionReview') return helper;
      if (name.endsWith('ObjRef.vue')) return { default: slot };
      throw new Error(`Unexpected dependency: ${name}`);
    },
  });
  return exports.default;
}
async function render(c, props) {
  const app = vue.createSSRApp(c, props);
  for (const name of ['a-alert', 'a-collapse', 'a-collapse-panel', 'a-descriptions', 'a-descriptions-item', 'a-tag']) {
    app.component(name, slot);
  }
  return renderToString(app);
}
const review = component('pages/ops/ActionReview.vue');
const checks = [{ name: '核对资料', rule: '<script>private()</script>' }];
const declared = [{ kind: '修改', obj: '维修工单', field: '状态' }];
const html = await render(review, { humanChecks: checks, declaredEdits: declared });
assert.ok(html.includes('核对资料') && html.includes('维修工单') && html.includes('由外部系统承担'));
assert.ok(html.includes('&lt;script&gt;') && !html.includes('<script>private()'));
assert.equal(helper.reviewFields({ humanChecks: checks, declaredEdits: declared }).length, 2);
assert.equal(helper.reviewFields({ reviewUnavailable: '历史未记录', humanChecks: checks }).length, 1);
const hidden = await render(review, { reviewUnavailable: '历史未记录', humanChecks: checks, declaredEdits: declared });
assert.ok(!hidden.includes('private') && !hidden.includes('维修工单'));
const summary = component('pages/qa/components/QaDynamicSummary.vue');
const brief = (id, available) => ({ objKind: 'device', id, objLabel: id,
  alerts: { available, openCount: 0 }, ruleHits: { available, total: 0 },
  lifecycle: { available, defined: false }, actionRuns: { available, total: 0 } });
const summaries = await render(summary, { items: [brief('可见设备', true), brief('受限设备', false)] });
assert.ok(summaries.includes('可见设备') && summaries.includes('受限设备'));
assert.ok(summaries.includes('当前身份不可查看') && summaries.includes('未定义'));
assert.ok(summaries.includes('>0<'));
console.log('Action review and multi-object summary render checks passed (5 scenarios).');
