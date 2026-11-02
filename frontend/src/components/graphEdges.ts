import type { EdgeProperties, Graph, LabelPositionObject } from '@antv/x6';

interface NodeBox { id: string; x: number; y: number; width: number; height: number }
interface Link { id: string; from: string; to: string; text?: string }
type Point = { x: number; y: number };
type Side = 'top' | 'right' | 'bottom' | 'left';
type Route = EdgeProperties & { labelPosition: LabelPositionObject };

/** Shared straight / orthogonal tracks; X6 handles obstacles outside the simple tracks. */
export function graphEdgeRoutes(nodes: NodeBox[], edges: Link[], mainLabelOffset = -12) {
  const boxes = new Map(nodes.map((n) => [n.id, n]));
  const routes = new Map<string, Route>();
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
    if (Math.abs(ca.y - cb.y) > 1 && siblings.length > parallel.length) labelOffset = ca.x < cb.x ? -16 : 16;
    const labelX = vertices.length || parallel.length === 1 ? 0 : Math.sign(offset) * 32;
    if (!vertices.length && ca.x === cb.x && parallel.length > 1) labelOffset += (parallel.indexOf(e) - (parallel.length - 1) / 2) * 24;
    routes.set(e.id, {
      source: terminal(a, sourceSide, sourceOffset), target: terminal(b, targetSide, targetOffset),
      vertices,
      router: { name: 'normal' },
      connector: { name: 'normal' },
      labelPosition: { distance: 0.5, offset: { x: labelX, y: labelOffset } },
    });
  }
  // Allocate every terminal on a node side, including edges to different neighbours.
  const ports = new Map<string, { route: Route; end: 'source' | 'target'; node: NodeBox; side: Side; order: number }[]>();
  for (const route of routes.values()) for (const end of ['source', 'target'] as const) {
    const terminal = route[end] as { cell: string; anchor: { name: Side } };
    const other = route[end === 'source' ? 'target' : 'source'] as { cell: string };
    const node = boxes.get(terminal.cell)!, neighbour = center(boxes.get(other.cell)!);
    const side = terminal.anchor.name, key = `${node.id}:${side}`;
    const group = ports.get(key) ?? [];
    group.push({ route, end, node, side, order: side === 'top' || side === 'bottom' ? neighbour.x : neighbour.y });
    ports.set(key, group);
  }
  for (const group of ports.values()) {
    group.sort((a, b) => a.order - b.order);
    group.forEach(({ route, end, node, side }, i) => {
      const span = (side === 'top' || side === 'bottom' ? node.width : node.height) - 16;
      route[end] = terminal(node, side, (i - (group.length - 1) / 2) * Math.min(24, span / group.length));
    });
  }
  const channels = new Map<string, number>();
  for (const route of routes.values()) {
    const source = route.source as { cell: string; anchor: { name: Side; args: { dx?: number; dy?: number } } };
    const target = route.target as typeof source;
    const a = boxes.get(source.cell)!, b = boxes.get(target.cell)!;
    const start = point(a, source.anchor.name, source.anchor.args.dx ?? source.anchor.args.dy ?? 0);
    const end = point(b, target.anchor.name, target.anchor.args.dx ?? target.anchor.args.dy ?? 0);
    let vertices = (route.vertices ?? []) as Point[];
    if (vertices.length === 2) {
      const horizontal = vertices[0].y === vertices[1].y;
      const middle = horizontal ? vertices[0].y : vertices[0].x;
      const key = `${horizontal ? 'y' : 'x'}:${Math.round(middle / 8)}`;
      const lane = channels.get(key) ?? 0;
      channels.set(key, lane + 1);
      const track = middle + (lane % 2 ? -1 : 1) * Math.ceil(lane / 2) * 8;
      vertices = horizontal ? [{ x: start.x, y: track }, { x: end.x, y: track }]
        : [{ x: track, y: start.y }, { x: track, y: end.y }];
    } else if (Math.abs(start.x - end.x) > 0.01 && Math.abs(start.y - end.y) > 0.01) {
      const horizontal = source.anchor.name === 'left' || source.anchor.name === 'right';
      const middle = horizontal ? (start.x + end.x) / 2 : (start.y + end.y) / 2;
      const key = `${horizontal}:${Math.round(middle / 16)}`;
      const lane = channels.get(key) ?? 0;
      channels.set(key, lane + 1);
      const track = middle + (lane % 2 ? -1 : 1) * Math.ceil(lane / 2) * 8;
      vertices = horizontal ? [{ x: track, y: start.y }, { x: track, y: end.y }]
        : [{ x: start.x, y: track }, { x: end.x, y: track }];
    }
    const path = [start, ...vertices, end];
    const blocked = path.slice(1).some((p, i) => nodes.some((n) => n !== a && n !== b
      && Math.max(p.x, path[i].x) > n.x - 8 && Math.min(p.x, path[i].x) < n.x + n.width + 8
      && Math.max(p.y, path[i].y) > n.y - 8 && Math.min(p.y, path[i].y) < n.y + n.height + 8));
    route.vertices = blocked ? vertices.filter((p) => !nodes.some((n) => p.x > n.x - 16 && p.x < n.x + n.width + 16 && p.y > n.y - 16 && p.y < n.y + n.height + 16)) : vertices;
    route.router = blocked ? { name: 'manhattan', args: { padding: 16, step: 8, maxLoopCount: 10000, perpendicular: false,
      startDirections: [source.anchor.name], endDirections: [target.anchor.name] } } : { name: 'normal' };
  }
  return routes;
}

/** Object relationships use short direct links; only parallel links and self-loops bend. */
export function relationshipRoutes(nodes: NodeBox[], edges: Link[]) {
  const boxes = new Map(nodes.map((node) => [node.id, node]));
  const routes = new Map<string, Route>();
  for (const edge of edges) {
    const a = boxes.get(edge.from), b = boxes.get(edge.to);
    if (!a || !b) continue;
    const ca = { x: a.x + a.width / 2, y: a.y + a.height / 2 };
    const cb = { x: b.x + b.width / 2, y: b.y + b.height / 2 };
    const siblings = edges.filter((other) => [other.from, other.to].sort().join(':') === [edge.from, edge.to].sort().join(':'));
    const lane = siblings.indexOf(edge) - (siblings.length - 1) / 2;
    const length = Math.hypot(cb.x - ca.x, cb.y - ca.y) || 1;
    // A canonical direction keeps reciprocal edges on distinct sides of the line.
    const sign = edge.from < edge.to ? 1 : -1;
    const vertices = edge.from === edge.to
      ? [{ x: ca.x - 52, y: ca.y - 94 }, { x: ca.x + 52, y: ca.y - 94 }]
      : lane ? [{ x: (ca.x + cb.x) / 2 - (cb.y - ca.y) / length * lane * 36 * sign,
        y: (ca.y + cb.y) / 2 + (cb.x - ca.x) / length * lane * 36 * sign }] : [];
    routes.set(edge.id, {
      source: { cell: edge.from, anchor: edge.from === edge.to ? { name: 'topLeft' } : { name: 'center' }, connectionPoint: { name: 'boundary' } },
      target: { cell: edge.to, anchor: edge.from === edge.to ? { name: 'topRight' } : { name: 'center' }, connectionPoint: { name: 'boundary' } },
      vertices, router: { name: 'normal' }, connector: { name: vertices.length ? 'smooth' : 'normal' },
      labelPosition: { distance: 0.5, offset: 0, options: { keepGradient: true, ensureLegibility: true } },
    });
  }
  return routes;
}

type Box = { x: number; y: number; width: number; height: number };
const overlaps = (a: Box, b: Box) => a.x + a.width + 4 > b.x && a.x - 4 < b.x + b.width
  && a.y + a.height + 4 > b.y && a.y - 4 < b.y + b.height;

/** Search all route segments before moving a label further away from its line. */
export function graphLabelPlacement(path: Point[], width: number, height: number, obstacles: Box[], preferred = -12) {
  const lengths = path.slice(1).map((p, i) => Math.abs(p.x - path[i].x) + Math.abs(p.y - path[i].y));
  const total = lengths.reduce((sum, length) => sum + length, 0) || 1;
  const segments = lengths.map((length, i) => ({ length, i, horizontal: Math.abs(path[i].y - path[i + 1].y) < 0.01 }))
    .filter(({ length }) => length > 0).sort((a, b) => Number(b.horizontal) - Number(a.horizontal) || b.length - a.length);
  for (const detached of [false, true]) for (const { length, i, horizontal } of segments) {
    const ratios = [0.5, 0.25, 0.75];
    const extent = horizontal ? width : height;
    for (let offset = extent / 2 + 8; offset < length - extent / 2 - 8; offset += 24) ratios.push(offset / length);
    const normal = horizontal ? height / 2 + 6 : width / 2 + 6;
    const shifts = detached
      // shortcut: very dense graphs can need remote labels; use a dedicated graph explorer when this becomes frequent.
      ? [Math.min(...obstacles.map((b) => horizontal ? b.y : b.x), 0) - normal - (horizontal ? path[i].y : path[i].x) - 8,
        Math.max(...obstacles.map((b) => horizontal ? b.y + b.height : b.x + b.width), 0) + normal - (horizontal ? path[i].y : path[i].x) + 8]
      : horizontal ? [preferred, -normal, normal, -normal - 24, normal + 24] : [-normal, normal, -normal - 24, normal + 24];
    for (const shift of shifts) for (const ratio of ratios) {
      const x = path[i].x + (path[i + 1].x - path[i].x) * ratio;
      const y = path[i].y + (path[i + 1].y - path[i].y) * ratio;
      const offset = horizontal ? { x: 0, y: shift } : { x: shift, y: 0 };
      const box = { x: x + offset.x - width / 2, y: y + offset.y - height / 2, width, height };
      if (obstacles.some((obstacle) => overlaps(box, obstacle))) continue;
      const distance = (lengths.slice(0, i).reduce((sum, n) => sum + n, 0) + length * ratio) / total;
      return { box, position: { distance, offset } };
    }
  }
}

/** Place labels on the rendered route, including any detour made by Manhattan. */
export function positionGraphLabels(graph: Graph) {
  const nodes = graph.getNodes().map((node) => node.getBBox());
  const occupied: Box[] = [];
  for (const edge of graph.getEdges()) {
    const view = graph.findViewByCell(edge);
    const label = edge.getLabelAt(0);
    const base = edge.getProp<LabelPositionObject>('labelPosition');
    if (!view?.isEdgeView() || !label || !base || !view.sourcePoint || !view.targetPoint) continue;
    const path = [view.sourcePoint, ...view.routePoints, view.targetPoint];
    const size = view.container.querySelector<SVGGElement>('[data-index="0"]')?.getBBox();
    const width = (size?.width ?? 60) + 8, height = (size?.height ?? 18) + 4;
    if (edge.getProp('relationship') === true) {
      let placement: { box: Box; position: LabelPositionObject } | undefined;
      for (const shift of [0, -18, 18, -36, 36, -60, 60, -84, 84]) {
        for (const distance of [0.5, 0.4, 0.6, 0.3, 0.7, 0.2, 0.8]) {
          const point = view.getPointAtRatio(distance), tangent = view.getTangentAtRatio(distance);
          if (!point || !tangent) continue;
          const dx = tangent.end.x - tangent.start.x, dy = tangent.end.y - tangent.start.y;
          const length = Math.hypot(dx, dy) || 1, cos = dx / length, sin = dy / length;
          const w = Math.abs(cos) * width + Math.abs(sin) * height;
          const h = Math.abs(sin) * width + Math.abs(cos) * height;
          const x = point.x - sin * shift, y = point.y + cos * shift;
          const box = { x: x - w / 2, y: y - h / 2, width: w, height: h };
          const hitsNode = nodes.some((node) => {
            const dx = node.x + node.width / 2 - x, dy = node.y + node.height / 2 - y;
            const localX = Math.abs(dx * cos + dy * sin), localY = Math.abs(-dx * sin + dy * cos);
            return Math.hypot(Math.max(0, localX - width / 2), Math.max(0, localY - height / 2)) < node.width / 2 + 6;
          });
          if (hitsNode || occupied.some((obstacle) => overlaps(box, obstacle))) continue;
          placement = { box, position: { distance, offset: shift, options: { keepGradient: true, ensureLegibility: true } } };
          break;
        }
        if (placement) break;
      }
      if (placement) {
        occupied.push(placement.box);
        if (JSON.stringify(label.position) !== JSON.stringify(placement.position)) edge.setLabelAt(0, { ...label, position: placement.position });
      }
      continue;
    }
    const offset = typeof base.offset === 'object' ? base.offset : { x: 0, y: -12 };
    const placement = graphLabelPlacement(path, width, height, [...nodes, ...occupied], offset.y ?? -12);
    if (!placement) continue;
    const { box, position } = placement;
    occupied.push(box);
    if (JSON.stringify(label.position) !== JSON.stringify(position)) edge.setLabelAt(0, { ...label, position });
  }
  return occupied;
}

export function fitGraphContent(graph: Graph, padding = 32, maxScale = 1) {
  const labels = positionGraphLabels(graph);
  let area = graph.getContentArea({ useCellGeometry: false });
  // Labels render asynchronously; include their new positions before the DOM catches up.
  for (const label of labels) area = area.union(label);
  const relationship = graph.getEdges().some((edge) => edge.getProp('relationship') === true);
  graph.zoomToRect(area, { padding, maxScale: relationship ? 1 : maxScale, minScale: relationship ? 0.86 : undefined });
}
