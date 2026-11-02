// Offline: public diagnostics only, including legacy records and capped lists.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import ts from 'typescript';
import * as vue from 'vue';
import { renderToString } from 'vue/server-renderer';
import { compileScript, parse } from '@vue/compiler-sfc';

function evaluate(source, imports) {
  const exports = {};
  runInNewContext(ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } }).outputText,
    { exports, require: name => { assert.ok(name in imports, name); return imports[name]; } });
  return exports;
}
const presentation = evaluate(readFileSync(new URL('../src/components/deliveryPresentation.ts', import.meta.url), 'utf8'), {});
const filename = 'DeliveryErrorDetails.vue';
const { descriptor, errors } = parse(readFileSync(new URL(`../src/components/${filename}`, import.meta.url), 'utf8'), { filename });
assert.equal(errors.length, 0);
const component = evaluate(compileScript(descriptor, { id: filename, inlineTemplate: true }).content,
  { vue, './deliveryPresentation': presentation }).default;
async function render(details) {
  return renderToString(vue.createSSRApp(component, { details }));
}
const details = {
  code: 'VALIDATION_FAILED', phase: 'preflight', status: 422, valuesOmitted: true,
  fieldsCount: 2, fields: [{ index: 0, field: 'params.value', reason: 'FIELD_VALIDATION_FAILED', input: 'PRIVATE_INPUT', message: 'PRIVATE_MESSAGE' }, { index: 1, reason: 'FIELD_VALIDATION_FAILED' }],
  failedCount: 1, failed: [{ index: 0, name: '对象状态校验', reason: 'SUBMISSION_CRITERION_FAILED' }],
  payload: 'PRIVATE_PAYLOAD', message: 'PRIVATE_EXCEPTION',
};
const html = await render(details);
for (const text of ['投递前检查', 'HTTP 422', '字段问题 2 项', '校验项失败 1 项', 'params.value', '字段 #2（名称未提供）', '对象状态校验', '提交条件未满足', '字段值已省略']) assert.ok(html.includes(text), text);
assert.ok(!html.includes('PRIVATE_'));
assert.equal(presentation.deliveryErrorPresentation(undefined), null);
assert.ok(!(await render(undefined)).includes('字段问题'));
const legacy = await render({ code: 'VALIDATION_FAILED' });
assert.ok(legacy.includes('未提供字段或校验项明细'));
assert.ok(!legacy.includes('字段校验未通过') && !legacy.includes('params.value'));
const execution = await render({ phase: 'execution', fields: [{ index: 0, reason: 'PRIVATE_REASON' }] });
assert.ok(execution.includes('投递执行') && execution.includes('原因未提供') && !execution.includes('PRIVATE_REASON'));
const capped = presentation.deliveryErrorPresentation({ fieldsCount: 25, fields: Array.from({ length: 25 }, (_, index) => ({ index })) });
assert.equal(capped.fields.length, 20);
assert.equal(capped.fieldsTruncated, true);
assert.equal(presentation.deliveryErrorPresentation({ fieldsCount: -1, status: 200 }).fieldsCount, undefined);
assert.equal(presentation.deliveryErrorPresentation({ status: 200 }).status, undefined);
for (const name of ['DeliveryReview.vue', 'DeliveryExceptions.vue', 'EventDeliveries.vue']) {
  const parsed = parse(readFileSync(new URL(`../src/components/${name}`, import.meta.url), 'utf8'), { filename: name });
  assert.equal(parsed.errors.length, 0);
  compileScript(parsed.descriptor, { id: name, inlineTemplate: true });
}
const review = readFileSync(new URL('../src/components/DeliveryReview.vue', import.meta.url), 'utf8');
assert.ok(review.includes(':details="detail.errorDetails"') && review.includes(':details="item.detail.errorDetails"'));
assert.ok(review.includes('detail.errorMessage ?? (detail.error ? deliveryErrorLabels[detail.error]'));
assert.equal(presentation.deliveryErrorLabels.ACTION_RELEASE_CHANGED_REQUIRES_REVIEW, '当前本体发布版本不可用或与入站绑定版本不同；须人工核查');
assert.equal(presentation.deliveryErrorLabels.ACTION_EXECUTOR_UNAVAILABLE, undefined);

// Exercise the actual list request and reject an old API that silently ignores the filter.
const actionSource = ts.createSourceFile('action.ts', readFileSync(new URL('../src/api/action.ts', import.meta.url), 'utf8'), ts.ScriptTarget.Latest, true);
const listStatement = actionSource.statements.find(node => ts.isVariableStatement(node)
  && node.declarationList.declarations.some(declaration => declaration.name.getText(actionSource) === 'listDeliveryExceptions'));
assert.ok(listStatement);
let request, response = { items: [], total: 0, page: 1, size: 10 };
const listExports = {};
runInNewContext(ts.transpileModule(listStatement.getText(actionSource), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } }).outputText, {
  exports: listExports, enc: encodeURIComponent, api: { get: async (...args) => { request = args; return response; } },
});
await listExports.listDeliveryExceptions('sample/type', 'failed', 'sample', 2);
assert.equal(request[0], '/event-types/sample%2Ftype/delivery-exceptions');
assert.deepEqual(JSON.parse(JSON.stringify(request[1].query)), { state: 'failed', kw: 'sample', page: 2, size: 10 });
const code = 'ACTION_RELEASE_CHANGED_REQUIRES_REVIEW';
await assert.rejects(listExports.listDeliveryExceptions('sample', '', '', 1, code), /当前服务尚不支持原因筛选/);
response = { ...response, errorFilter: code, total: 21, page: 2 };
assert.equal(await listExports.listDeliveryExceptions('sample', 'failed', '', 2, code), response);
assert.equal(request[1].query.error, code);
assert.equal(request[1].query.page, 2);
response = { ...response, errorFilter: 'OTHER_ERROR' };
await assert.rejects(listExports.listDeliveryExceptions('sample', '', '', 1, code), /当前服务尚不支持原因筛选/);
console.log('Delivery diagnostics: SFC compile, safe SSR, legacy fallback, phase/count/cap, exact filter request and old-API rejection passed (offline).');
