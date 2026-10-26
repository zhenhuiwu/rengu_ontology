/**
 * 本体总览（OntologyOverview.vue）的对象关系缩略图：GET /ontology/graph（开关键 static，前缀 ontology；后端 app/modules/static service.graph）。
 * 返回 `{nodes: LinkObject[], edges: {from, to, linkId, status}[]}`，`?ver=` 由 http.ts 自动带上。
 * 这里只做形状转换，页面照旧按 LINK_OBJECTS / LINKS 的字段画图；mock 模式不发请求（调用方先判断 isOverviewReal）。
 */
import { apiClient, type PageResult } from './http';
import { LINK_OBJECTS, LINKS, type LinkObject } from '@/mock/links';

const api = apiClient('static');
export const isOverviewReal = (): boolean => api.isReal();
export interface OverviewHealth { n: number; text: string; tone: 'normal' | 'attention' | 'failed' }
export interface OverviewCard { key: string; title: string; count: number; unit: string; health: OverviewHealth[] }
export interface OverviewSummary { cards: OverviewCard[]; qualityConfigured: boolean; dataAsOf: string }
export interface OverviewTodo { id: string; kind: '变更' | '数据质量'; title: string; who: string; status: string; tone: OverviewHealth['tone']; time: string }
export const fetchOverview = () => api.get<OverviewSummary>('/ontology/overview');
export const fetchOverviewTodos = (kind: string, page: number, size: number) => api.get<PageResult<OverviewTodo> & { qualityConfigured: boolean }>('/ontology/todos', { query: { kind, page, size } });
export const fetchOverviewReleases = () => api.get<{ items: { ver: string; publishedAt: string; by: string; note: string }[] }>('/releases');

/** 页面画图用的关系标识、名称及方向。 */
export interface GraphEdge { id: string; name: string; from: string; to: string; status: string }
export interface OverviewGraph { nodes: LinkObject[]; edges: GraphEdge[] }

interface GraphDto {
  nodes: { id: string; name: string; api: string; group: string; table?: string | null }[];
  edges: { from: string; to: string; linkId: string; name: string; status?: string | null }[];
}

/** mock 版：LINK_OBJECTS + LINKS（页面原来的数据源，mock 模式 / 后台没起来时用） */
export const mockGraph = (): OverviewGraph => ({
  nodes: [...LINK_OBJECTS],
  edges: LINKS.map((l) => ({ id: l.id, name: l.name, from: l.from, to: l.to, status: l.status })),
});

/**
 * 真接口版只显示后台返回的定义，不混入演示草稿节点或边。
 * 接口 status 为空时按「已发布」画实线 [推断]。
 */
export async function fetchGraph(): Promise<OverviewGraph> {
  const d = await api.get<GraphDto>('/ontology/graph');
  return {
    nodes: d.nodes.map((n): LinkObject => ({ id: n.id, name: n.name, api: n.api, group: n.group, table: n.table ?? '--' })),
    edges: d.edges.map((e): GraphEdge => ({ id: e.linkId, name: e.name, from: e.from, to: e.to, status: e.status ?? '已发布' })),
  };
}
