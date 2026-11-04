// Exercise installed X6 scheduler/queue and actual Lineage/Overview drawing with in-memory views.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import ts from 'typescript';
import { parse, compileScript, compileTemplate } from '@vue/compiler-sfc';

function evaluate(source, deps = {}, globals = {}) {
  const exports = {};
  const js = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020, experimentalDecorators: true } }).outputText;
  runInNewContext(js, { exports, ...globals, require: key => { assert.ok(key in deps, key); return deps[key]; } });
  return exports;
}
const x6 = file => readFileSync(new URL(`../node_modules/@antv/x6/src/renderer/${file}.ts`, import.meta.url), 'utf8');
let frame = 0;
const frames = new Map();
const window = { requestAnimationFrame: fn => { frames.set(++frame, fn); return frame; }, cancelAnimationFrame: id => frames.delete(id) };
const queueModule = evaluate(x6('queueJob'), {}, { window });
const { Scheduler } = evaluate(x6('scheduler'), {
  '../common': { Disposable: class {}, disposable: () => () => {} },
  '../constants': { FLAG_INSERT: 1, FLAG_REMOVE: 2 },
  '../view': {}, './queueJob': queueModule,
});
function fixture() {
  let cells = [];
  const mounted = new Set();
  const scheduler = Object.create(Scheduler.prototype);
  const model = { getCells: () => cells, getConnectedEdges: () => [] };
  scheduler.graph = { model, options: { async: true }, trigger() {} };
  scheduler.views = {}; scheduler.willRemoveViews = {}; scheduler.queue = new queueModule.JobQueue();
  scheduler.removeZPivots = () => {};
  scheduler.createCellView = cell => ({ cell, getBootstrapFlag: () => 1, hasAction: () => false, remove() { mounted.delete(this); } });
  scheduler.renderViewInArea = view => { mounted.add(view); scheduler.views[view.cell.id].state = 1; };
  const makeCells = data => [...data.nodes.map(n => ({ ...n, isNode: () => true, isEdge: () => false })), ...data.edges.map(e => ({ ...e, isNode: () => false, isEdge: () => true }))];
  const graph = {
    fromJSON(data) { const previous = cells; cells = makeCells(data); scheduler.onModelReseted({ options: {}, previous }); },
    oldReplace(data) { scheduler.removeViews(cells); cells = makeCells(data); scheduler.renderViews(cells); },
    clearCells() { scheduler.removeViews(cells); cells = []; },
    getNodes: () => cells.filter(cell => cell.isNode()),
  };
  return { graph, mounted, scheduler, flush: () => scheduler.queue.flushJobsSync() };
}
const source = readFileSync(new URL('../src/pages/ontology/Lineage.vue', import.meta.url), 'utf8');
const { descriptor, errors } = parse(source);
assert.equal(errors.length, 0);
compileScript(descriptor, { id: 'lineage-reset' });
assert.equal(compileTemplate({ source: descriptor.template.content, filename: 'Lineage.vue', id: 'lineage-reset' }).errors.length, 0);
const tree = ts.createSourceFile('Lineage.ts', descriptor.scriptSetup.content, ts.ScriptTarget.Latest, true);
const draw = tree.statements.find(node => ts.isFunctionDeclaration(node) && node.name.text === 'draw').getText(tree);
const metadata = count => ({ nodes: Array.from({ length: count }, (_, i) => ({ id: `n${i}`, name: `Node ${i}`, code: `table.column${i}`, kind: i ? 'property' : 'source' })), edges: Array.from({ length: count - 1 }, (_, i) => ({ id: `e${i}`, from: `n${i}`, to: `n${i + 1}`, name: 'reads' })) });
const before = fixture();
before.graph.fromJSON(metadata(15)); before.flush();
before.graph.oldReplace(metadata(11)); before.flush();
assert.ok(before.mounted.size > 21, 'Installed async scheduler must reproduce stale same-ID views');

const after = fixture();
const visible = { value: metadata(15) }, relationships = { value: visible.value.edges };
let highlights = 0, fits = 0;
const page = evaluate(`${draw}\nexport { draw };`, {}, {
  graph: after.graph, visible, relationships, groups: { value: [] }, kinds: {}, chrome: { value: 'navy' }, GRAY: {},
  layoutGraph: nodes => new Map(nodes.map((n, i) => [n.id, { x: i * 80, y: 0, width: 72, height: 72 }])),
  relationshipRoutes: (nodes, edges) => new Map(edges.map(e => [e.id, { source: e.from, target: e.to, labelPosition: 0.5 }])),
  relationshipColor: () => 'blue', highlight: () => highlights++, fit: () => fits++,
});
function verify(count, target = after) {
  assert.equal([...target.mounted].filter(v => v.cell.isNode()).length, count);
  assert.equal([...target.mounted].filter(v => v.cell.isEdge()).length, count - 1);
  assert.equal(new Set([...target.mounted].map(v => v.cell.id)).size, count * 2 - 1);
  assert.equal(Object.keys(target.scheduler.willRemoveViews).length, 0);
}
for (const count of [15, 11, 15, 5]) {
  visible.value = metadata(count); relationships.value = visible.value.edges;
  page.draw(); after.flush(); verify(count);
}
// Rapid focus changes before the renderer flushes must cancel all obsolete pending views too.
for (const count of [15, 11, 5]) {
  visible.value = metadata(count); relationships.value = visible.value.edges; page.draw();
}
after.flush(); verify(5);
assert.equal(highlights, 7); assert.equal(fits, 7);
const node = after.scheduler.views.n0.view.cell;
assert.equal(node.label, 'column0\ntable');
assert.equal(node.attrs.label.fontSize, 14);
assert.equal(after.scheduler.views.e0.view.cell.attrs.line.targetMarker.name, 'classic');

const overviewSource = readFileSync(new URL('../src/pages/ontology/OntologyOverview.vue', import.meta.url), 'utf8');
const overviewSfc = parse(overviewSource);
assert.equal(overviewSfc.errors.length, 0);
compileScript(overviewSfc.descriptor, { id: 'overview-reset' });
assert.equal(compileTemplate({ source: overviewSfc.descriptor.template.content, filename: 'OntologyOverview.vue', id: 'overview-reset' }).errors.length, 0);
const overviewTree = ts.createSourceFile('Overview.ts', overviewSfc.descriptor.scriptSetup.content, ts.ScriptTarget.Latest, true);
const addCells = overviewTree.statements.find(n => ts.isFunctionDeclaration(n) && n.name.text === 'addCells').getText(overviewTree);
const themeWatch = overviewTree.statements.find(n => ts.isExpressionStatement(n) && n.getText(overviewTree).startsWith('watch(chrome,')).getText(overviewTree);
// The single remaining clear is the intentional loading boundary, before async reads.
assert.equal((overviewSource.match(/\.clearCells\(/g) ?? []).length, 1);
const overview = fixture(), graphData = { value: metadata(15) }, chrome = { value: 'navy' };
let onTheme, overviewFocus = 0;
const overviewPage = evaluate(`${addCells}\n${themeWatch}\nexport { addCells };`, {}, {
  graphData, graphRef: overview.graph, live: { value: true }, failedByType: new Set(), graphGroups: { value: [] }, chrome, GRAY: {}, STATUS: {},
  layoutGraph: nodes => new Map(nodes.map((n, i) => [n.id, { x: i * 80, y: 0, width: 72, height: 72 }])),
  relationshipRoutes: (nodes, edges) => new Map(edges.map(e => [e.id, { source: e.from, target: e.to, labelPosition: 0.5 }])),
  relationshipColor: (group, navy) => navy ? 'navy-fill' : 'light-fill', focusGraph: () => overviewFocus++,
  requestAnimationFrame: window.requestAnimationFrame, fitGraphContent() {}, watch: (source, callback) => { assert.equal(source, chrome); onTheme = callback; },
});
overviewPage.addCells(overview.graph); overview.flush(); verify(15, overview);
for (const theme of ['light', 'navy', 'light']) { chrome.value = theme; onTheme(); }
overview.flush(); verify(15, overview);
assert.equal(overview.scheduler.views.n0.view.cell.attrs.body.fill, 'light-fill');
assert.equal(overview.scheduler.views.n0.view.cell.x, 0);
assert.equal(overview.scheduler.views.n1.view.cell.x, 80);
assert.equal(overview.scheduler.views.e0.view.cell.attrs.line.targetMarker.name, 'classic');
assert.equal(overviewFocus, 4);
overview.graph.clearCells();
graphData.value = metadata(11); overviewPage.addCells(overview.graph); overview.flush(); verify(11, overview);
console.log('Lineage/Overview atomic reset PASS: installed X6 old-job collision reproduced; focus/theme/rapid replacement contain one view per cell; both SFCs compiled. No browser or HTTP.');
