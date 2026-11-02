import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import ts from 'typescript';

const source = await readFile(new URL('../src/components/graphEdges.ts', import.meta.url), 'utf8');
const { outputText } = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.ESNext } });
const { graphEdgeRoutes, relationshipRoutes, graphLabelPlacement, positionGraphLabels, fitGraphContent } = await import(`data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`);
const layoutSource = await readFile(new URL('../src/components/graphLayout.ts', import.meta.url), 'utf8');
const layoutJs = ts.transpileModule(layoutSource, { compilerOptions: { target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.ESNext } }).outputText
  .replace(/(['"])echarts\/lib\/chart\/graph\/forceHelper.js\1/, JSON.stringify(new URL('../node_modules/echarts/lib/chart/graph/forceHelper.js', import.meta.url).href));
const { layoutGraph } = await import(`data:text/javascript;base64,${Buffer.from(layoutJs).toString('base64')}`);
const nodes = [
  { id: 'a', x: 0, y: 100, width: 100, height: 40 },
  { id: 'b', x: 240, y: 100, width: 100, height: 40 },
  { id: 'c', x: 480, y: 100, width: 100, height: 40 },
];
const routes = graphEdgeRoutes(nodes, [
  { id: 'forward', from: 'a', to: 'b' },
  { id: 'return', from: 'b', to: 'a' },
  { id: 'loop', from: 'b', to: 'b' },
  { id: 'skip', from: 'a', to: 'c' },
]);
assert.equal(routes.get('forward').vertices.length, 0);
assert.equal(routes.get('forward').source.anchor.args.dy, 0);
assert.equal(routes.get('return').source.anchor.name, 'top');
assert.equal(routes.get('loop').vertices.length, 2);
assert.notEqual(routes.get('loop').source.anchor.args.dx, routes.get('loop').target.anchor.args.dx);
assert.ok(routes.get('skip').vertices.every((p) => p.y > 140));
assert.ok([...routes.values()].every((r) => r.connector.name === 'normal' && Number.isFinite(r.labelPosition.distance)));
const parallel = graphEdgeRoutes([nodes[0], { ...nodes[1], x: 0, y: 300 }], [
  { id: 'one', from: 'a', to: 'b', text: '下单门店' }, { id: 'two', from: 'a', to: 'b', text: '履约门店' },
]);
assert.notEqual(parallel.get('one').source.anchor.args.dx, parallel.get('two').source.anchor.args.dx);
assert.ok(parallel.get('one').labelPosition.offset.x < 0 && parallel.get('two').labelPosition.offset.x > 0);
let updates = 0;
const edge = { label: { position: { distance: 0.5 } }, getLabelAt() { return this.label; },
  getProp: () => ({ offset: { x: 0, y: -12 } }), setLabelAt(_index, label) { this.label = label; updates++; } };
const view = { isEdgeView: () => true, sourcePoint: { x: 0, y: 0 }, targetPoint: { x: 200, y: 100 },
  routePoints: [{ x: 100, y: 0 }, { x: 100, y: 100 }], container: { querySelector: () => ({ getBBox: () => ({ width: 60, height: 18 }) }) } };
const graph = { getNodes: () => [], getEdges: () => [edge], findViewByCell: () => view };
positionGraphLabels(graph); positionGraphLabels(graph);
assert.equal(updates, 1);
assert.equal(edge.label.position.distance, 1 / 6);
// Realistic cyclic, parallel and self-linked topology; no fixed position table.
const objects = ['order', 'member', 'refund', 'refundLine', 'orderLine', 'product', 'category', 'supplier', 'level', 'promotion', 'shipment', 'stock', 'warehouse', 'province', 'employee', 'area', 'store'].map((id) => ({ id, name: id }));
const relations = [['order', 'member'], ['order', 'refund'], ['refund', 'refundLine'], ['refundLine', 'orderLine'],
  ['order', 'orderLine'], ['orderLine', 'product'], ['product', 'category'], ['category', 'category'], ['product', 'supplier'],
  ['member', 'level'], ['member', 'promotion'], ['promotion', 'product'], ['order', 'shipment'], ['shipment', 'warehouse'],
  ['stock', 'warehouse'], ['stock', 'product'], ['store', 'warehouse'], ['warehouse', 'store'], ['store', 'area'],
  ['area', 'province'], ['store', 'employee'], ['employee', 'store'], ['order', 'store'], ['member', 'store'],
  ['order', 'store'], ['warehouse', 'area']].map(([from, to], i) => ({ id: `relation-${i}`, from, to, name: '关系名称', text: '关系名称' }));
const boxes = [...layoutGraph(objects, relations)].map(([id, box]) => ({ id, ...box }));
assert.deepEqual([...layoutGraph(objects, relations)], [...layoutGraph([...objects].reverse(), [...relations].reverse())]);
assert.ok(boxes.every((a, i) => boxes.slice(i + 1).every((b) => Math.hypot(a.x - b.x, a.y - b.y) > 143)));
const relationship = relationshipRoutes(boxes, relations);
assert.equal(relationship.size, relations.length);
assert.ok([...relationship.values()].every((route) => route.router.name === 'normal' && route.labelPosition.options.ensureLegibility));
assert.notDeepEqual(relationship.get('relation-22').vertices, relationship.get('relation-24').vertices);
const moved = relationshipRoutes(boxes.map((box) => box.id === 'order' ? { ...box, x: box.x + 100 } : box), relations);
assert.notDeepEqual(relationship.get('relation-22').vertices, moved.get('relation-22').vertices);
const overlap = (a, b) => a.x < b.x + b.width && a.x + a.width > b.x && a.y < b.y + b.height && a.y + a.height > b.y;
assert.ok(boxes.every((a, i) => boxes.slice(i + 1).every((b) => !overlap(a, b))));
const retailRoutes = graphEdgeRoutes(boxes, relations);
const endpoints = new Set();
const labels = [];
for (const route of retailRoutes.values()) {
  const points = ['source', 'target'].map((end) => {
    const terminal = route[end], node = boxes.find((n) => n.id === terminal.cell), side = terminal.anchor.name;
    const offset = terminal.anchor.args.dx ?? terminal.anchor.args.dy ?? 0;
    const key = `${node.id}:${side}:${offset}`;
    assert.ok(!endpoints.has(key), `shared terminal ${key}`); endpoints.add(key);
    return side === 'top' || side === 'bottom' ? { x: node.x + node.width / 2 + offset, y: side === 'top' ? node.y : node.y + node.height }
      : { x: side === 'left' ? node.x : node.x + node.width, y: node.y + node.height / 2 + offset };
  });
  // Manhattan routes are evaluated by X6 in the browser, not by this pure geometry check.
  if (route.router.name !== 'normal') continue;
  const path = [points[0], ...route.vertices, points[1]];
  assert.ok(path.slice(1).every((p, i) => p.x === path[i].x || p.y === path[i].y), 'non-orthogonal segment');
  const placement = graphLabelPlacement(path, 64, 22, [...boxes, ...labels]);
  assert.ok(placement && [...boxes, ...labels].every((box) => !overlap(placement.box, box)), 'label collision');
  labels.push(placement.box);
}
assert.ok(labels.length > 5);
// The longest horizontal segment is blocked: a free shorter segment must be used.
const placement = graphLabelPlacement([{ x: 0, y: 0 }, { x: 200, y: 0 }, { x: 200, y: 100 }, { x: 280, y: 100 }], 60, 18,
  [{ x: -40, y: -80, width: 280, height: 140 }]);
assert.ok(placement.box.y > 60);
let fitOptions;
let includedLabels = 0;
fitGraphContent({ ...graph, getContentArea: (options) => { fitOptions = options; return { union() { includedLabels++; return this; } }; }, zoomToRect() {} });
assert.equal(fitOptions.useCellGeometry, false);
assert.equal(includedLabels, 1);
console.log('Graph edge tracks: OK');
