/**
 * 本体总览（OntologyOverview.vue）的对象关系缩略图：GET /ontology/graph（开关键 static，前缀 ontology；后端 app/modules/static service.graph）。
 * 返回 `{nodes: LinkObject[], edges: {from, to, linkId, status}[]}`，`?ver=` 由 http.ts 自动带上。
 * 这里只做形状转换，页面照旧按 LINK_OBJECTS / LINKS 的字段画图；mock 模式不发请求（调用方先判断 isOverviewReal）。
 */
import { apiClient } from './http';
import { LINK_OBJECTS, LINKS, type LinkObject } from '@/mock/links';

const api = apiClient('static');
export const isOverviewReal = (): boolean => api.isReal();

/** 页面画图用的边（mock LINKS 的 from / to / status 三个字段） */
export interface GraphEdge { from: string; to: string; status: string }
export interface OverviewGraph { nodes: LinkObject[]; edges: GraphEdge[] }

interface GraphDto {
  nodes: { id: string; name: string; api: string; group: string; table?: string | null }[];
  edges: { from: string; to: string; linkId: string; status?: string | null }[];
}

/** mock 版：LINK_OBJECTS + LINKS（页面原来的数据源，mock 模式 / 后台没起来时用） */
export const mockGraph = (): OverviewGraph => ({
  nodes: [...LINK_OBJECTS],
  edges: LINKS.map((l) => ({ from: l.from, to: l.to, status: l.status })),
});

/**
 * 真接口版。接口 nodes 只有已发布对象类型：草稿对象（如 CR-0012 的储值卡）只在变更单里，
 * 变更单仍是 mock，先保留 mock 的草稿节点和连到草稿节点的 mock 边（同 static.ts loadObjects）[推断]。
 * 接口 status 为空时按「已发布」画实线 [推断]。
 */
export async function fetchGraph(): Promise<OverviewGraph> {
  const d = await api.get<GraphDto>('/ontology/graph');
  const ids = new Set(d.nodes.map((n) => n.id));
  const drafts = LINK_OBJECTS.filter((o) => o.draft && !ids.has(o.id));
  const draftIds = new Set(drafts.map((o) => o.id));
  return {
    nodes: [...d.nodes.map((n): LinkObject => ({ id: n.id, name: n.name, api: n.api, group: n.group, table: n.table ?? '--' })), ...drafts],
    edges: [
      ...d.edges.map((e): GraphEdge => ({ from: e.from, to: e.to, status: e.status ?? '已发布' })),
      ...LINKS.filter((l) => draftIds.has(l.from) || draftIds.has(l.to)).map((l) => ({ from: l.from, to: l.to, status: l.status })),
    ],
  };
}
