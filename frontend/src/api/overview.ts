/**
 * 本体总览（OntologyOverview.vue）的对象关系缩略图：GET /ontology/graph（开关键 static，前缀 ontology；后端 app/modules/static service.graph）。
 * 返回 `{nodes: LinkObject[], edges: {from, to, linkId, status}[]}`，`?ver=` 由 http.ts 自动带上。
 * 这里只做形状转换，页面照旧按 LINK_OBJECTS / LINKS 的字段画图；mock 模式不发请求（调用方先判断 isOverviewReal）。
 */
import { apiClient, type PageResult } from './http';
import { LINK_OBJECTS, LINKS, type LinkObject } from '@/mock/links';
import { ACTION_TYPES, EVENT_TYPES, runsOf, eventsOf } from '@/mock/actionEvent';
import { METRICS } from '@/mock/metrics';
import { DIM_ITEMS } from '@/mock/dimensions';
import { CHANGE_REQUESTS, CURRENT_USER } from '@/mock/changes';
import { QUALITY_CHECKS, DATA_CUTOFF } from '@/mock/quality';

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

/* ── 演示模式入口（真接口模式不调用）：总览页只经这里读写 mock，形状与真接口一致 ── */
/** 演示数据截至时间（真接口为 /ontology/overview 的 dataAsOf） */
export const DEMO_DATA_AS_OF = DATA_CUTOFF;
const failedChecks = () => QUALITY_CHECKS.filter((c) => c.result === '失败');
/** 有数据质量失败的对象类型（演示模式画红框） */
export const demoFailedTypes = (): Set<string> => new Set(failedChecks().map((c) => c.typeKey));

/**
 * 演示版资源卡片，形状同 OverviewSummary.cards。
 * histVer：查看历史版本时维度只算当时已有的；dimInDraft：维度是否在某张草稿单里（useDraftView.draftOf）。
 */
export function demoCards(histVer: string | null, dimInDraft: (id: string) => unknown): OverviewCard[] {
  const runs = ACTION_TYPES.flatMap((a) => runsOf(a.id));
  const events = EVENT_TYPES.flatMap((e) => eventsOf(e.id));
  const dims = DIM_ITEMS.filter((d) => d.status !== '已驳回' && (!histVer || d.since <= histVer));
  return [
    { key: 'obj', title: '对象类型', unit: '个', count: LINK_OBJECTS.filter((o) => !o.draft).length, health: [
      { n: LINK_OBJECTS.filter((o) => o.draft).length, text: '个草稿', tone: 'normal' },
      { n: demoFailedTypes().size, text: '个有质量检查失败', tone: 'failed' },
    ] },
    { key: 'link', title: '关系类型', unit: '条', count: LINKS.filter((l) => l.status === '已发布').length, health: [
      { n: LINKS.filter((l) => l.status === '草稿').length, text: '条草稿', tone: 'normal' },
      { n: LINKS.filter((l) => (l.quality?.orphans ?? 0) > 0).length, text: '条有孤儿记录', tone: 'normal' },
    ] },
    { key: 'action', title: '动作类型', unit: '个', count: ACTION_TYPES.length, health: [
      { n: ACTION_TYPES.filter((a) => a.status !== '已发布').length, text: '个变更中 / 草稿', tone: 'normal' },
      { n: runs.filter((r) => r.status === '结果未知').length, text: '次结果未知', tone: 'attention' },
      { n: runs.filter((r) => r.status === '失败').length, text: '次执行失败', tone: 'failed' },
    ] },
    { key: 'event', title: '事件类型', unit: '个', count: EVENT_TYPES.length, health: [
      { n: EVENT_TYPES.filter((e) => e.status !== '已发布').length, text: '个变更中 / 草稿', tone: 'normal' },
      { n: events.filter((e) => e.outcome === '投递失败').length, text: '次投递失败', tone: 'failed' },
    ] },
    { key: 'metric', title: '指标', unit: '个', count: METRICS.filter((m) => m.status === '已发布').length, health: [
      { n: METRICS.filter((m) => m.status === '草稿').length, text: '个草稿', tone: 'normal' },
      { n: METRICS.filter((m) => m.status === '已下线').length, text: '个已下线', tone: 'normal' },
    ] },
    { key: 'dim', title: '维度与字典', unit: '个', count: dims.length, health: [
      { n: dims.filter((d) => d.kind === '维度').length, text: '个维度', tone: 'normal' },
      { n: dims.filter((d) => d.kind === '码表').length, text: '个码表', tone: 'normal' },
      { n: dims.filter((d) => d.status === '草稿' || dimInDraft(d.id)).length, text: '个变更中', tone: 'normal' },
    ] },
  ];
}

const CR_STATUS: Record<string, string> = { 草稿: '草稿', 审核中: '审核中', 已通过: '已通过 · 待发布' };
/** 演示版待处理（未发布变更单 + 失败的质量检查），形状同 /ontology/todos 的 items */
export function demoTodos(): OverviewTodo[] {
  const crs = CHANGE_REQUESTS.filter((c) => c.status in CR_STATUS).map((c): OverviewTodo => ({
    id: c.id, kind: '变更', title: c.title, who: c.status === '审核中' ? `审核人 ${c.reviewer}` : c.owner,
    status: CR_STATUS[c.status], tone: c.status === '审核中' ? 'attention' : 'normal', time: c.updatedAt,
  }));
  const checks = failedChecks().map((c): OverviewTodo => ({
    id: c.id, kind: '数据质量', title: `${c.typeName} · ${c.kind}：${c.rule}`, who: `实际 ${c.actual}`,
    status: '失败', tone: 'failed', time: c.failSince ?? c.lastRun,
  }));
  return [...crs, ...checks];
}

/** 演示版：某个发布版本对应的变更单号（最近发布点进变更与发布时选中） */
export const demoCrOfRelease = (ver: string): string | undefined => CHANGE_REQUESTS.find((c) => c.release === ver)?.id;

/** 演示版：从总览发起变更，登记到变更与发布列表最前面（id 由 useDraftView.start 生成） */
export function demoAddChange(id: string, title: string, owner?: string): void {
  const at = new Date();
  const pad = (n: number) => String(n).padStart(2, '0');
  CHANGE_REQUESTS.unshift({
    id, title, owner: owner || CURRENT_USER, reviewer: '待指定', status: '草稿', level: '修订', changes: 0,
    updatedAt: `${at.getFullYear()}-${pad(at.getMonth() + 1)}-${pad(at.getDate())} ${pad(at.getHours())}:${pad(at.getMinutes())}`, reason: '从本体总览发起，待补充',
  });
}

