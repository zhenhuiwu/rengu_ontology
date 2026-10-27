// Render the actual MyPages table-cell template against real DTO shapes, without HTTP/DB calls.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import ts from 'typescript';
import * as vue from 'vue';
import { compile } from '@vue/compiler-dom';
import { renderToString } from 'vue/server-renderer';

const source = readFileSync(new URL('../src/pages/qa/MyPages.vue', import.meta.url), 'utf8');
const cell = ['refresh', 'sub'].map((key) => source.split('\n').find((line) => line.includes(`column.key === '${key}'`)).replace('v-else-if', 'v-if')).join('\n');
const frequencySource = readFileSync(new URL('../src/mock/qa-pages.ts', import.meta.url), 'utf8').match(/const freqLabel =[^\n]+;/)[0];
const helpers = {};
runInNewContext(ts.transpileModule(`${frequencySource}\nexports.freqLabel=freqLabel;`, { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText, { exports: helpers });
const render = Function('Vue', compile(cell, { mode: 'function', prefixIdentifiers: true }).code)(vue);
async function text(key, record) {
  const app = vue.createSSRApp({ setup: () => ({ column: { key }, record, qaWriteReal: true, pagesApi: { access: (p) => p }, freqLabel: helpers.freqLabel }), render });
  for (const name of ['a-tooltip', 'a-button']) app.component(name, { render: () => null });
  return renderToString(app);
}
const active = { refresh: { freq: '每天', time: '08:00' }, scheduleActive: true, subscriptionActive: true,
  sub: { on: true, channel: '站内信', receivers: ['真实用户'] } };
assert.ok((await text('sub', active)).includes('站内信 · 1 人'));
assert.ok((await text('refresh', active)).includes('每天 08:00'));
assert.ok(!(await text('refresh', active)).includes('未排定'));
assert.ok((await text('sub', { ...active, sub: { ...active.sub, on: false } })).includes('未开启'));
assert.ok((await text('sub', { ...active, sub: null })).includes('未开启'));
assert.ok((await text('sub', { ...active, subscriptionActive: false, sub: { ...active.sub, channel: '邮件' } })).includes('邮件（未启用）'));
assert.ok((await text('sub', { ...active, sub: { ...active.sub, receivers: [] } })).includes('页面拥有者'));
assert.ok((await text('refresh', { ...active, scheduleActive: false })).includes('未排定'));
assert.ok((await text('refresh', { refresh: { freq: '手动' }, scheduleActive: false })).includes('手动刷新'));
console.log('MyPages schedule/subscription cells passed (active, disabled, absent, legacy-email inactive, owner-default, unscheduled, manual).');
