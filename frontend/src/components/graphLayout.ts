// ECharts already supplies the bounded force simulation used by its graph series.
// @ts-expect-error ECharts ships this helper's declaration only under types/src.
import { forceLayout } from 'echarts/lib/chart/graph/forceHelper.js';

export interface LayoutNode { id: string; name: string }
export interface LayoutEdge { from: string; to: string; name?: string }
export interface LayoutBox { x: number; y: number; width: number; height: number }

/** Stable starting positions, then a finite simulation; nothing runs while the graph is idle. */
export function layoutGraph(nodes: LayoutNode[], edges: LayoutEdge[]): Map<string, LayoutBox> {
  const ordered = [...nodes].sort((a, b) => a.id.localeCompare(b.id));
  const particles = ordered.map((node, i) => {
    const angle = i * Math.PI * (3 - Math.sqrt(5));
    const radius = Math.sqrt(i + 1) * 88;
    return { id: node.id, p: [Math.cos(angle) * radius, Math.sin(angle) * radius], w: 1, rep: 300 };
  });
  const byId = new Map(particles.map((node) => [node.id, node]));
  const links = edges.filter((edge) => edge.from !== edge.to && byId.has(edge.from) && byId.has(edge.to))
    .sort((a, b) => `${a.from}:${a.to}:${a.name ?? ''}`.localeCompare(`${b.from}:${b.to}:${b.name ?? ''}`))
    .map((edge) => ({ n1: byId.get(edge.from)!, n2: byId.get(edge.to)!, d: Math.max(192, (edge.name?.length ?? 0) * 14 + 120) }));
  const simulation = forceLayout(particles, links, { rect: { x: -500, y: -360, width: 1000, height: 720 }, gravity: 0.035, friction: 0.6 });
  for (let step = 0; step < 520; step++) simulation.step();
  // Keep circle captions and the space around them apart after force convergence.
  for (let pass = 0; pass < 40; pass++) for (let i = 0; i < particles.length; i++) for (let j = i + 1; j < particles.length; j++) {
    const a = particles[i].p, b = particles[j].p;
    const dx = b[0] - a[0], dy = b[1] - a[1], distance = Math.hypot(dx, dy) || 1;
    if (distance >= 184) continue;
    const push = (184 - distance) / 2;
    a[0] -= dx / distance * push; a[1] -= dy / distance * push;
    b[0] += dx / distance * push; b[1] += dy / distance * push;
  }
  const width = Math.max(0, ...particles.map((node) => node.p[0])) - Math.min(0, ...particles.map((node) => node.p[0]));
  const height = Math.max(0, ...particles.map((node) => node.p[1])) - Math.min(0, ...particles.map((node) => node.p[1]));
  return new Map(particles.map((node) => [node.id, { x: (height > width ? node.p[1] : node.p[0]) - 36,
    y: (height > width ? -node.p[0] : node.p[1]) - 36, width: 72, height: 72 }]));
}

/** Use real domains, or the object type identifier when no domain is available. */
export function relationshipColor(group: string, dark: boolean, groups: string[] = []) {
  const palette = dark ? ['#3e596a', '#55506e', '#3a5b55', '#635440', '#42567b']
    : ['#d6e7ee', '#e4def0', '#d8e9e1', '#eee4d2', '#dce5f3'];
  if (!group) return dark ? '#3b4a60' : '#e2e7ec';
  const hash = [...group].reduce((value, ch) => (value * 31 + ch.charCodeAt(0)) >>> 0, 0);
  const index = groups.indexOf(group);
  return palette[(index < 0 ? hash : index) % palette.length];
}
