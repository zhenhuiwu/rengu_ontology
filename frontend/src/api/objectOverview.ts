/** Existing object overview read endpoints; each permission/error stays explicit, never demo fallback. */
import { apiClient, currentApiContext, type PageResult, type QueryValue } from './http';
import { staticContextKey } from './static';
import type { QualityCheck } from './quality';
import type { Metric } from '@/mock/metrics';
import type { ActionTypeRow } from './action';
import type { RuleDto } from './dynamic';
export interface ObjectOverviewRuntime { metrics: Metric[] | null; actions: number | null; rules: number | null; checks: QualityCheck[] | null; errors: string[] }
export async function fetchObjectOverviewRuntime(id: string, name: string, apiName: string): Promise<ObjectOverviewRuntime | null> {
  const key = staticContextKey(), ctx = { ...currentApiContext() };
  async function pages<T>(module: 'static' | 'action' | 'dynamic' | 'data', path: string, query: Record<string, QueryValue> = {}) {
    const result: T[] = [], api = apiClient(module);
    if (!api.isReal()) throw new Error('相关模块尚未启用真实接口');
    for (let page = 1; ; page++) {
      const response = await api.get<PageResult<T>>(path, { readonlyParams: false, headers: { 'X-Demo-As': ctx.demoAs ?? '', 'X-Ontology-Id': ctx.ontologyId ?? '' }, query: { ...query, ver: ctx.ver, previewAs: ctx.previewAs, page, size: 200 } });
      if (key !== staticContextKey()) throw new Error('读取上下文已变化');
      result.push(...response.items);
      if (result.length >= response.total || !response.items.length) return result;
    }
  }
  const results = await Promise.allSettled([
    pages<Metric>('static', `/object-types/${encodeURIComponent(id)}/usage`, { kind: 'metric' }),
    pages<ActionTypeRow>('action', '/action-types').then((rows) => rows.filter((a) => a.targetKind === id || a.targetApi === apiName || a.target === name).length),
    ctx.ver ? Promise.resolve(null) : pages<RuleDto>('dynamic', '/rules').then((rows) => rows.filter((r) => r.objKind === id).length),
    pages<QualityCheck>('data', '/quality-checks', { typeKey: id }),
  ]);
  if (key !== staticContextKey()) return null;
  const value = <T>(index: number): T | null => results[index].status === 'fulfilled' ? results[index].value as T : null;
  return { metrics: value<Metric[]>(0), actions: value<number>(1), rules: value<number>(2), checks: value<QualityCheck[]>(3), errors: results.flatMap((r, i) => r.status === 'rejected' ? [`${['引用指标','动作类型','规则','数据质量'][i]}：${r.reason instanceof Error ? r.reason.message : String(r.reason)}`] : []) };
}
