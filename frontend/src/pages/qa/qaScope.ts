/** 工作台范围来自已发布组织层级维度的顶层取值；当前用户范围使用真实 singular row-scope 契约。 */
import { computed, ref } from 'vue';
import { loadMyRowScope, type MyRowScope } from '@/api/rowScope';
import { loadMeta, metaContextKey } from '@/pages/objects/objMeta';
import { fetchStaticPage, isStaticReal } from '@/api/static';
import { qaReal, getSessionOptions } from '@/api/qa';
import { DIM_ITEMS, type DimItem, type DimValue } from '@/mock/dimensions';
import { QA_SCOPE_OPTIONS, QA_CALIBER_OPTIONS } from '@/mock';

export interface Opt { value: string; label: string }
type ScopeDimension = Pick<DimItem, 'id' | 'kind' | 'group' | 'status' | 'levels' | 'object' | 'objectName'>;
const dim = ref<ScopeDimension | null>(null), tops = ref<DimValue[]>([]);
/** null仅表示已确认不限；空数组表示没有可用许可。 */
const rowScope = ref<string[] | null>(null);
const variants = ref<string[] | null>(null);
const fallbackScopes = ref<Opt[]>(qaReal() ? [] : QA_SCOPE_OPTIONS);
const fallbackCalibers = ref<Opt[]>(qaReal() ? [] : QA_CALIBER_OPTIONS);
let started: Promise<void> | null = null, startedKey = '', generation = 0;

function pickOrgDim(items: ScopeDimension[]) {
  return items.find(d => d.kind === '维度' && d.group === '组织' && d.status === '已发布' && d.levels.length > 1) ?? null;
}
function sourceValues(response: MyRowScope | null, objectName: string): string[] | null {
  // 后端objType是已发布对象显示名；不按行业、API名或无关对象猜范围。
  const matches = response?.items.filter(item => item.objType === objectName) ?? [];
  if (matches.length !== 1 || !matches[0].visible) return [];
  if (matches[0].unrestricted) return null;
  return [...new Set(matches[0].conditions.flatMap(condition => condition.values))];
}
export function ensureScopeOptions(real: boolean): Promise<void> {
  const key = JSON.stringify([metaContextKey(), real]);
  if (started && key === startedKey) return started;
  startedKey = key;
  const seq = ++generation, current = () => seq === generation && key === startedKey;
  dim.value = null; tops.value = []; rowScope.value = real ? [] : null; variants.value = null;
  fallbackScopes.value = real ? [] : QA_SCOPE_OPTIONS; fallbackCalibers.value = real ? [] : QA_CALIBER_OPTIONS;
  started = (async () => {
    if (!real) {
      const d = pickOrgDim(DIM_ITEMS); dim.value = d;
      tops.value = d ? DIM_ITEMS.find(item => item.id === d.id)!.values.filter(v => v.level === d.levels[0].name && v.status !== '停用') : [];
      return;
    }
    let selected: ScopeDimension | null = null;
    if (isStaticReal()) {
      try {
        for (let page = 1; ; page++) {
          const result = await fetchStaticPage<ScopeDimension>('/dimensions', { kind: '维度', group: '组织', page, size: 200 });
          if (!current()) return;
          selected = pickOrgDim(result.items);
          if (selected || !result.items.length || page * result.size >= result.total) break;
        }
        if (selected) {
          const values: DimValue[] = [];
          for (let page = 1; ; page++) {
            const result = await fetchStaticPage<DimValue>(`/dimensions/${encodeURIComponent(selected.id)}/values`, { level: selected.levels[0].name, status: '启用', page, size: 200 });
            if (!current()) return;
            values.push(...result.items);
            if (!result.items.length || values.length >= result.total) break;
          }
          dim.value = selected; tops.value = values;
        }
      } catch { /* 真实元数据不可用时保留空态。 */ }
    }
    if (!current()) return;
    const d = selected;
    const jobs: Promise<unknown>[] = [getSessionOptions().then(options => {
      if (current()) { fallbackScopes.value = options.scopes; fallbackCalibers.value = options.calibers; }
    }).catch(() => undefined)];
    if (d?.object) {
      jobs.push(loadMyRowScope().then(response => { if (current()) rowScope.value = sourceValues(response, d.objectName); }).catch(() => { if (current()) rowScope.value = []; }));
      jobs.push(loadMeta(d.object).then(meta => {
        if (!current() || !meta || meta.hasData === undefined) return;
        variants.value = [...new Set(meta.metrics.flatMap(metric => metric.variants.map(variant => variant.label).filter(Boolean)))];
      }).catch(() => undefined));
    }
    await Promise.all(jobs);
  })();
  return started;
}
function normalize(s: string | null | undefined): string {
  const text = (s ?? '').trim();
  const value = tops.value.find(v => v.name === text || v.code === text || v.synonyms.includes(text));
  return value?.name ?? text;
}
export function normScope(s: string | null | undefined): string {
  const value = normalize(s);
  return rowScope.value !== null && !rowScope.value.some(scope => normalize(scope) === value) ? '' : value;
}
export function useScopeCaliber() {
  const levelName = computed(() => dim.value?.levels[0].name ?? '范围');
  const scopeOpts = computed<Opt[]>(() => {
    const options = tops.value.length ? tops.value.map(v => ({ value: v.name, label: `范围：${v.name}` })) : fallbackScopes.value;
    return rowScope.value === null ? options : options.filter(option => rowScope.value!.some(value => normalize(value) === normalize(option.value)));
  });
  const caliberOpts = computed<Opt[]>(() => variants.value ? variants.value.map(value => ({ value, label: `口径：${value}` })) : fallbackCalibers.value);
  const showCaliber = computed(() => caliberOpts.value.length > 0);
  function defaultScope(previewScope: (objName: string) => string | null): string {
    const options = scopeOpts.value.map(option => option.value);
    const hit = (values: string[]) => values.map(normalize).find(value => options.includes(value));
    if (rowScope.value !== null) return hit(rowScope.value) ?? '';
    const preview = dim.value ? previewScope(dim.value.objectName) : null;
    const match = preview && /^(\S+) [=∈] (.+)$/.exec(preview.split('；或 ')[0]);
    return (match && hit(match[2].split('、'))) || options[0] || '';
  }
  return { levelName, scopeOpts, caliberOpts, showCaliber, defaultScope, normScope };
}
