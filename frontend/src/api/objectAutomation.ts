/** 对象自动化只读汇总：完整轻目录与后台聚合，命中记录独立分页；固定请求上下文。 */
import { apiClient, currentApiContext, type ApiContext, type PageResult, type QueryValue } from './http';
import { dynamicApi, type RuleDto } from './dynamic';
import { actionApi, actionContextKey, loadSnapshot, loadEventSubscribers, actionTypes, eventTypes, subscribersOf, type EventStats } from './action';
import type { ActionType, EventType } from '@/mock/actionEvent';
import type { Hit } from '@/mock/rules';

const staticApi = apiClient('static');
export const isAutomationReal = () => staticApi.isReal() || dynamicApi.isReal() || actionApi.isReal();
const fixed = (ctx: ApiContext, query: Record<string, QueryValue> = {}) => ({ headers: { 'X-Demo-As': ctx.demoAs ?? '', 'X-Ontology-Id': ctx.ontologyId ?? '' }, readonlyParams: false, query: { ...query, ver: ctx.ver ?? undefined, previewAs: ctx.previewAs ?? undefined } });
async function pages<T>(path: string, ctx: ApiContext, query: Record<string, QueryValue> = {}): Promise<T[]> {
  const items: T[] = [];
  for (let page = 1; ; page++) {
    const r = await dynamicApi.get<PageResult<T>>(path, fixed(ctx, { ...query, page, size: 200 }));
    items.push(...r.items);
    if (items.length >= r.total || !r.items.length) return items;
  }
}
export interface AutomationData { objectName: string; rules: RuleDto[]; events: (EventType & { subscriptionCount: number })[]; hitCounts: Record<string, number>; alertCounts: Record<string, number>; pending: Record<string, number>; actionNames: Record<string, string>; eventNames: Record<string, string> }
export async function fetchObjectAutomation(objectId: string): Promise<AutomationData | null> {
  if (!dynamicApi.isReal() || !actionApi.isReal() || !staticApi.isReal()) throw new Error('自动化汇总需要启用静态、动态及动作事件真实接口');
  const ctx = { ...currentApiContext() }; const key = actionContextKey(ctx);
  if (ctx.ver) throw new Error('历史规则配置尚未接入，当前页不提供历史自动化汇总');
  const [allRules, object] = await Promise.all([
    pages<RuleDto>('/rules', ctx, { objKind: objectId }),
    staticApi.get<{ id: string; name: string; api: string }>(`/object-types/${encodeURIComponent(objectId)}`, fixed(ctx)),
    loadSnapshot(),
  ]);
  if (key !== actionContextKey()) return null;
  const rules = allRules.filter((r) => r.objKind === objectId);
  const allEvents = eventTypes();
  const selectedEvents = allEvents.filter((e) => e.objKind === objectId || e.objectApi === object.api || e.object === objectId || e.object === object.name);
  await Promise.all(selectedEvents.map(e => loadEventSubscribers(e.id)));
  if (key !== actionContextKey()) return null;
  const actionNames = Object.fromEntries(actionTypes().map((a) => [a.id, a.name]));
  const eventNames = Object.fromEntries(allEvents.map((e) => [e.id, e.name]));
  const subscriptions = Object.fromEntries(selectedEvents.map((e) => [e.id, subscribersOf(e).length]));
  const [stats, events] = await Promise.all([
    fetchBehaviorStats(objectId),
    Promise.all(selectedEvents.map(async (e) => {
      const stats = await actionApi.get<EventStats>(`/event-types/${encodeURIComponent(e.id)}/stats`, fixed(ctx));
      return { ...e, weekly: stats.weekly, subscriptionCount: subscriptions[e.id] };
    })),
  ]);
  if (key !== actionContextKey()) return null;
  return { objectName: object.name, rules, events, hitCounts: Object.fromEntries(Object.entries(stats.rules).map(([id, r]) => [id, r.hits7d])), alertCounts: Object.fromEntries(Object.entries(stats.rules).map(([id, r]) => [id, r.alerts7d])), pending: Object.fromEntries(Object.entries(stats.rules).map(([id, r]) => [id, r.pending])), actionNames, eventNames };
}

export interface BehaviorStats {
  rules: Record<string, { hits7d: number; alerts7d: number; pending: number }>;
  centerStatusCounts: Record<string, number>;
  actions: ActionType[];
  actions30d: Record<string, { total: number; rate: number; failed: number; unknown: number }>;
}
export function fetchBehaviorStats(objectId: string) {
  if (!dynamicApi.isReal()) throw new Error('行为统计需要启用真实动态接口');
  const ctx = { ...currentApiContext() };
  return dynamicApi.get<BehaviorStats>(`/object-types/${encodeURIComponent(objectId)}/behavior/stats`, fixed(ctx));
}
export function fetchAutomationHits(objectId: string, ruleId: string, page: number, size: number) {
  if (!dynamicApi.isReal()) throw new Error('自动化命中需要启用真实动态接口');
  const ctx = { ...currentApiContext() };
  return dynamicApi.get<PageResult<Hit & { ruleName?: string }>>(`/object-types/${encodeURIComponent(objectId)}/automation/hits`, fixed(ctx, { ruleId: ruleId === '全部' ? undefined : ruleId, page, size }));
}
