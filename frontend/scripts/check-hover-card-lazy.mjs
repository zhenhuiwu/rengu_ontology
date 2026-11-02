import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { parse, compileScript } from '@vue/compiler-sfc';
import { renderToString } from '@vue/server-renderer';
import ts from 'typescript';
import * as vue from 'vue';

// Render the actual SFC, including its content slot, with a counted object resolver.
const require = createRequire(import.meta.url);
const { descriptor } = parse(readFileSync(new URL('../src/components/HoverCard.vue', import.meta.url), 'utf8'));
const compiled = compileScript(descriptor, { id: 'hover-regression', inlineTemplate: true });
const js = ts.transpileModule(compiled.content, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
let calls = 0, real = true, shouldOpen = false;
const module = { exports: {} };
const resolve = () => { calls++; return { title: 'Preview', code: '1', kindLabel: 'Order', brief: [] }; };
new Function('require', 'exports', 'module', js)((id) => id === './objectRegistry'
  ? { resolveObject: resolve } : id === './realObjectResolver'
    ? { usesRealObject: () => real } : require(id), module.exports, module);
const Popover = {
  props: ['open'], emits: ['update:open'],
  setup(_props, { slots, emit }) {
    if (shouldOpen) emit('update:open', true);
    return () => vue.h('div', [slots.default?.(), slots.content?.()]);
  },
};
async function render() {
  calls = 0;
  const app = vue.createSSRApp(module.exports.default, { kind: 'order', id: '1' });
  app.component('a-popover', Popover);
  app.component('a-tag', { render: () => null });
  await renderToString(app);
  return calls;
}
assert.equal(await render(), 0, 'Closed real preview must never resolve an object');
shouldOpen = true;
assert.equal(await render(), 1, 'Opening a real preview must resolve the object');
real = false; shouldOpen = false;
assert.equal(await render(), 1, 'Explicit mock preview must keep resolving local data');
console.log('PASS: real closed=0, real opened=1, mock closed=1 object resolves.');
