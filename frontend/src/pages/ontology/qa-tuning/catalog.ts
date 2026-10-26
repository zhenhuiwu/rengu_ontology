/** T2 的请求上下文目录；真实模式缺记录时不借用演示名称或版本。 */
import { computed, ref } from 'vue';
import { qaReal, type QaPlanOptions } from '@/api/qa';
import { T_METRICS, type SemPlan } from '@/mock/qa-tuning';

const remote = ref<QaPlanOptions['metrics']>([]);
export const setPlanMetrics = (rows: QaPlanOptions['metrics']) => { remote.value = rows; };
export const metricOptions = computed(() => qaReal() ? remote.value : Object.values(T_METRICS));
export function metricOf(key: string) {
  if (!qaReal()) return T_METRICS[key] ?? { key, name: `${key}（指标不可用）`, version: '', drawerId: undefined, cr: undefined };
  const m = remote.value.find((x) => x.key === key || x.code === key);
  return m ? { ...m, drawerId: m.code } : { key, name: `${key}（指标不可用）`, version: '', drawerId: undefined, cr: undefined };
}
export const metricLabel = (key: string) => { const m = metricOf(key); return `${m.name} ${m.version}`.trim(); };
export function planText(plan?: SemPlan | null): string {
  if (!plan) return '';
  return [metricLabel(plan.metric), plan.dims?.length ? `按 ${plan.dims.join('、')}` : '汇总', plan.time, plan.filter, plan.sort].filter(Boolean).join(' · ');
}
