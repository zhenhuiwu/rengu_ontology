import type { EdgeProperties, Graph, LabelPositionObject } from '@antv/x6';

interface NodeBox { id: string; x: number; y: number; width: number; height: number }
interface Link { id: string; from: string; to: string; text?: string }
type Point = { x: number; y: number };
type Side = 'top' | 'right' | 'bottom' | 'left';

/** Shared straight / orthogonal tracks; X6 handles obstacles outside the simple tracks. */
export function graphEdgeRoutes(nodes: NodeBox[], edges: Link[], mainLabelOffset = -12) {
  const boxes = new Map(nodes.map((n) => [n.id, n]));
  const routes = new Map<string, EdgeProperties & { labelPosition: LabelPositionObject }>();
  const center = (n: NodeBox) => ({ x: n.x + n.width / 2, y: n.y + n.height / 2 });
  const terminal = (n: NodeBox, side: Side, offset = 0) => ({
    cell: n.id, anchor: { name: side, args: { [side === 'top' || side === 'bottom' ? 'dx' : 'dy']: offset } },
    connectionPoint: { name: 'anchor' },
  });
  const point = (n: NodeBox, side: Side, offset = 0): Point => {
    const c = center(n);
    return side === 'top' || side === 'bottom'
      ? { x: c.x + offset, y: side === 'top' ? n.y : n.y + n.height }
      : { x: side === 'left' ? n.x : n.x + n.width, y: c.y + offset };
  };
  let returnLane = 0;
  for (const e of edges) {
    const a = boxes.get(e.from), b = boxes.get(e.to);
    if (!a || !b) continue;
    const ca = center(a), cb = center(b);
    const siblings = edges.filter((l) => (l.from === e.from && l.to === e.to) || (l.from === e.to && l.to === e.from));
    const index = siblings.indexOf(e);
    const parallel = siblings.filter((l) => l.from === e.from);
    const span = Math.abs(ca.y - cb.y) < 1 ? Math.min(a.height, b.height) : Math.min(a.width, b.width);
    const offset = (parallel.indexOf(e) - (parallel.length - 1) / 2) * Math.min(48, span / parallel.length);
    let sourceSide: Side, targetSide: Side;
    let sourceOffset = 0, targetOffset = 0;
    let vertices: Point[] = [];
    let labelOffset = -12;
    if (e.from === e.to) {
      sourceSide = targetSide = 'top';
      sourceOffset = -a.width * 0.28; targetOffset = a.width * 0.28;
      const y = a.y - 112 - index * 32;
      vertices = [{ x: ca.x + sourceOffset, y }, { x: ca.x + targetOffset, y }];
    } else if (Math.abs(ca.y - cb.y) < 1) {
      const between = nodes.some((n) => n !== a && n !== b && Math.abs(center(n).y - ca.y) < n.height
        && center(n).x > Math.min(ca.x, cb.x) && center(n).x < Math.max(ca.x, cb.x));
      if (between || (siblings.length > 1 && ca.x > cb.x)) {
        sourceSide = targetSide = ca.x > cb.x ? 'top' : 'bottom';
        const y = sourceSide === 'top' ? Math.min(a.y, b.y) - 64 - returnLane++ * 28 : Math.max(a.y + a.height, b.y + b.height) + 36 + returnLane++ * 28;
        vertices = [{ x: ca.x, y }, { x: cb.x, y }];
      } else {
        sourceSide = ca.x < cb.x ? 'right' : 'left'; targetSide = ca.x < cb.x ? 'left' : 'right';
        sourceOffset = targetOffset = offset;
        labelOffset = mainLabelOffset;
      }
    } else if (Math.abs(ca.x - cb.x) < 1) {
      sourceSide = ca.y < cb.y ? 'bottom' : 'top'; targetSide = ca.y < cb.y ? 'top' : 'bottom';
      sourceOffset = targetOffset = offset;
    } else {
      const vertical = Math.abs(cb.y - ca.y) > Math.abs(cb.x - ca.x) * 0.45;
      if (vertical) {
        sourceSide = ca.y < cb.y ? 'bottom' : 'top'; targetSide = ca.y < cb.y ? 'top' : 'bottom';
        const incoming = edges.filter((l) => l.to === e.to && l.from !== e.to && boxes.has(l.from)
          && Math.sign(center(boxes.get(l.from)!).y - cb.y) === Math.sign(ca.y - cb.y));
        const lane = incoming.indexOf(e);
        targetOffset = (lane - (incoming.length - 1) / 2) * Math.min(40, b.width / (incoming.length + 1));
        const startY = point(a, sourceSide).y, endY = point(b, targetSide).y;
        const y = startY + (endY - startY) * (lane + 1) / (incoming.length + 1);
        vertices = [{ x: ca.x, y }, { x: cb.x + targetOffset, y }];
      } else {
        sourceSide = ca.x < cb.x ? 'right' : 'left'; targetSide = ca.x < cb.x ? 'left' : 'right';
        const x = (point(a, sourceSide).x + point(b, targetSide).x) / 2 + offset;
        vertices = [{ x, y: ca.y }, { x, y: cb.y }];
      }
    }
    const path = [point(a, sourceSide, sourceOffset), ...vertices, point(b, targetSide, targetOffset)];
    const blocked = path.slice(1).some((p, i) => nodes.some((n) => n !== a && n !== b
      && Math.max(p.x, path[i].x) > n.x - 8 && Math.min(p.x, path[i].x) < n.x + n.width + 8
      && Math.max(p.y, path[i].y) > n.y - 8 && Math.min(p.y, path[i].y) < n.y + n.height + 8));
    if (Math.abs(ca.y - cb.y) > 1 && siblings.length > parallel.length) labelOffset = ca.x < cb.x ? -16 : 16;
    const labelX = vertices.length || parallel.length === 1 ? 0 : Math.sign(offset) * 32;
    if (!vertices.length && ca.x === cb.x && parallel.length > 1) labelOffset += (parallel.indexOf(e) - (parallel.length - 1) / 2) * 24;
    routes.set(e.id, {
      source: terminal(a, sourceSide, sourceOffset), target: terminal(b, targetSide, targetOffset),
      vertices: blocked ? [] : vertices,
      router: blocked ? { name: 'manhattan', args: { padding: 16, step: 8, startDirections: [sourceSide], endDirections: [targetSide] } } : { name: 'normal' },
      connector: { name: 'normal' },
      labelPosition: { distance: 0.5, offset: { x: labelX, y: labelOffset } },
    });
  }
  return routes;
}

/** Place labels on the rendered route, including any detour made by Manhattan. */
export function positionGraphLabels(graph: Graph) {
  const nodes = graph.getNodes().map((node) => node.getBBox());
  const occupied: { x: number; y: number; width: number; height: number }[] = [];
  for (const edge of graph.getEdges()) {
    const view = graph.findViewByCell(edge);
    const label = edge.getLabelAt(0);
    const base = edge.getProp<LabelPositionObject>('labelPosition');
    if (!view?.isEdgeView() || !label || !base || !view.sourcePoint || !view.targetPoint) continue;
    const path = [view.sourcePoint, ...view.routePoints, view.targetPoint];
    const lengths = path.slice(1).map((p, i) => Math.abs(p.x - path[i].x) + Math.abs(p.y - path[i].y));
    const horizontal = lengths.map((length, i) => ({ length: path[i].y === path[i + 1].y ? length : 0, i })).sort((a, b) => b.length - a.length)[0];
    const segment = horizontal?.length ? horizontal.i : lengths.indexOf(Math.max(...lengths));
    if (segment < 0) continue;
    const total = lengths.reduce((sum, length) => sum + length, 0);
    const distance = (lengths.slice(0, segment).reduce((sum, length) => sum + length, 0) + lengths[segment] / 2) / (total || 1);
    const size = view.container.querySelector<SVGGElement>('[data-index="0"]')?.getBBox();
    const width = size?.width ?? 60, height = size?.height ?? 18;
    const offset = typeof base.offset === 'object' ? base.offset : { x: 0, y: -12 };
    const x = (path[segment].x + path[segment + 1].x) / 2 + (offset.x ?? 0) - width / 2;
    const y = (path[segment].y + path[segment + 1].y) / 2 + (offset.y ?? -12) - height / 2;
    const candidates = [0, -20, 20].map((shift) => ({ shift, overlaps: [...nodes, ...occupied].filter((box) =>
      x + width > box.x && x < box.x + box.width && y + shift + height > box.y && y + shift < box.y + box.height).length }));
    candidates.sort((a, b) => a.overlaps - b.overlaps);
    const shift = candidates[0].shift;
    occupied.push({ x, y: y + shift, width, height });
    const position = { distance, offset: { x: offset.x ?? 0, y: (offset.y ?? -12) + shift } };
    if (JSON.stringify(label.position) !== JSON.stringify(position)) edge.setLabelAt(0, { ...label, position });
  }
}
