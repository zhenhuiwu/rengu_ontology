// Real SFC state/render regression: a failed retry retains records and hides implementation diagnostics.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import ts from 'typescript';
import * as vue from 'vue';
import dayjs from 'dayjs';
import utc from 'dayjs/plugin/utc.js';
import { renderToString } from 'vue/server-renderer';
import { compileTemplate, parse } from '@vue/compiler-sfc';

const filename = 'ActionEffects.vue';
const parsed = parse(readFileSync(new URL('../src/components/ActionEffects.vue', import.meta.url), 'utf8'), { filename });
assert.equal(parsed.errors.length, 0);
const context = vue.reactive({ ontologyId: 1, demoAs: 'U010001' });
const history = [{ attempt: 1, at: '2026-10-25T07:42:00+00:00', httpStatus: 503, error: 'EXTERNAL_HTTP_503' }];
const rows = [{ id: '2', state: 'failed', attempts: 1, canRetry: true, history, manualRetries: [] },
  { id: '3', state: 'unknown', attempts: 1, canRetry: false, history, manualRetries: [] }];
const source = `${parsed.descriptor.scriptSetup.content}\nObject.assign(exports,{items,total,error,operationError,loading,busy,page,labels,load,retry,timeText,attemptRows,effectsEnabled,readonly});`;
const output = {};
const imports = {
  vue, dayjs: { default: dayjs }, 'dayjs/plugin/utc': { default: utc },
  '@/api/http': { currentApiContext: () => context, isApiError: e => typeof e?.code === 'string' },
  '@/api/effects': { effectsEnabled: () => true, listActionEffects: async () => ({ items: rows, total: 2, page: 1 }),
    retryActionEffect: async () => { throw { status: 503, code: 'RECONCILE_UNAVAILABLE', message: 'RENGU_INTEGRATION_REAL_CONNECT / ADAPTERS secret diagnostic' }; } },
  '@/composables/useReadonly': { useReadonly: () => ({ readonly: vue.ref(false) }) },
  '@/stores/confirm': { useConfirmStore: () => ({ ask: async () => true }) },
  './AppPagination.vue': { default: {} },
};
runInNewContext(ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } }).outputText, {
  exports: output, defineProps: () => ({ runId: 'UICHECK-FIX-RUN' }),
  require: name => { assert.ok(name in imports, name); return imports[name]; },
});
await new Promise(setImmediate);
assert.equal(output.items.value.length, 2);
await output.retry(output.items.value[0]);
assert.equal(output.items.value.length, 2);
assert.equal(output.total.value, 2);
assert.equal(output.timeText(history[0].at), '2026-10-25 15:42:00');
assert.equal(output.operationError.value, '当前执行通道不可用，未重新排队，请联系管理员');
const template = compileTemplate({ source: parsed.descriptor.template.content, filename, id: filename });
assert.equal(template.errors.length, 0);
const rendered = {};
runInNewContext(ts.transpileModule(template.code, { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText,
  { exports: rendered, require: name => { assert.equal(name, 'vue'); return vue; } });
const app = vue.createSSRApp({ setup: () => output, render: rendered.render });
const slot = { setup: (_, { slots }) => () => vue.h('div', slots.default?.()) };
for (const tag of ['a-button', 'a-empty', 'a-collapse', 'AppPagination']) app.component(tag, slot);
app.component('a-alert', { props: ['message'], setup: props => () => vue.h('aside', props.message) });
app.component('a-collapse-panel', { props: ['header'], setup: (props, { slots }) => () => vue.h('section', [vue.h('h3', props.header), slots.default?.()]) });
app.component('a-table', { props: ['dataSource'], setup: props => () => vue.h('table', props.dataSource.map(row => vue.h('tr', row.at))) });
const html = await renderToString(app);
assert.ok(html.includes('明确失败') && html.includes('结果未知'));
assert.ok(html.includes('当前执行通道不可用') && html.includes('2026-10-25 15:42:00'));
assert.ok(!html.includes('RENGU_') && !html.includes('secret diagnostic'));
console.log('Action effects retry-state, safe error and local-time render regression passed.');
