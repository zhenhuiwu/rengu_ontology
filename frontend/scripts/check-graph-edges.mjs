import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import ts from 'typescript';

const source = await readFile(new URL('../src/components/graphEdges.ts', import.meta.url), 'utf8');
const { outputText } = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.ESNext } });
const { graphEdgeRoutes, positionGraphLabels } = await import(`data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`);
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
console.log('Graph edge tracks: OK');
