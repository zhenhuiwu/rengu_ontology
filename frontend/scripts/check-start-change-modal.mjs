// Real Vue prop resolution: omitted Boolean must close, explicit false must preserve async flow.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import ts from 'typescript';
import * as vue from 'vue';
import { parse, compileScript } from '@vue/compiler-sfc';

const source = readFileSync(new URL('../src/pages/ontology/parts/StartChangeModal.vue', import.meta.url), 'utf8')
  .replace('</script>', '\ndefineExpose({ok,title});\n</script>');
const { descriptor } = parse(source);
const script = compileScript(descriptor, { id: 'real-modal' }).content;
const js = ts.transpileModule(script, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
const exports = {};
const imports = {
  vue, './useDraftView': { allDrafts: () => [], loadDraftState: async () => {} },
  '@/api/http': { currentApiContext: () => ({ ontologyId: '2' }) },
};
runInNewContext(js, { exports, require: name => imports[name] });
const component = { ...exports.default, render: () => null };
const renderer = vue.createRenderer({
  createComment: () => ({}), insert() {}, remove() {}, parentNode: () => null, nextSibling: () => null,
  createText: () => ({}), setText() {}, setElementText() {}, createElement: () => ({}), patchProp() {},
});
for (const [extra, shouldClose] of [[{}, true], [{ closeOnOk: false }, false]]) {
  const emitted = [], selections = [];
  const app = renderer.createApp(component, { open: true, targetName: '当前定义', ...extra,
    'onUpdate:open': value => emitted.push(value), onOk: opts => selections.push(opts) });
  const form = app.mount({});
  form.title = '实际变更'; await vue.nextTick(); form.ok(); await vue.nextTick();
  assert.equal(selections.length, 1, 'one real submit intent');
  assert.equal(emitted.includes(false), shouldClose, 'Vue default and explicit false must stay distinct');
  app.unmount();
}
console.log('StartChangeModal real Vue default-close / explicit-false: passed; no HTTP/DB');
