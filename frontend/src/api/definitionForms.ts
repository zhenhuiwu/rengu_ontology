import { apiClient, currentApiContext, type PageResult } from './http';
import { applyModelChange, fetchCR, fetchCRItems, staticContextKey, fetchMetricGroups, type CRItemIn, type CRItemOut, type DraftChange } from './static';
export type DefinitionKind = 'object' | 'metric' | 'dimension';
export interface DefinitionValues { id?: string; name: string; api: string; group: string; owner: string; [key: string]: unknown }
export interface MetricDefaultRange { kind: 'rolling_days'; days: number }
export interface DefinitionOption { id: string; name: string; api?: string; status?: string; version?: string; object?: string; [key: string]: unknown }
export interface DefinitionProperty { api: string; name: string; type: string; group: string; source: string; desc: string; pk?: boolean; title?: boolean; dim?: boolean; sensitive?: boolean; enumName?: string; visibility?: 'prominent' | 'normal' | 'hidden'; searchable?: boolean; filterable?: boolean; sortable?: boolean; listed?: boolean; order?: number; listWidth?: number | null; format?: string | null; unit?: string | null }
const api = apiClient('static');
const paths = { object: 'object-types', metric: 'metrics', dimension: 'dimensions' };
export function definitionContext() {
  const ctx = currentApiContext();
  return { key: staticContextKey(), options: { readonlyParams: false, query: { ver: ctx.ver, previewAs: ctx.previewAs }, headers: { 'X-Demo-As': ctx.demoAs ?? '', 'X-Ontology-Id': ctx.ontologyId ?? '' } } };
}
const check = (ctx: ReturnType<typeof definitionContext>) => { if (ctx.key !== staticContextKey()) throw new Error('身份或版本已切换，请重新打开表单'); };
async function all(ctx: ReturnType<typeof definitionContext>, path: string) {
  const rows: DefinitionOption[] = [];
  for (let page = 1; ; page++) {
    const result = await api.get<PageResult<DefinitionOption>>(path, { ...ctx.options, query: { ...ctx.options.query, page, size: 200 } });
    check(ctx); rows.push(...result.items);
    if (rows.length >= result.total || !result.items.length) return rows;
  }
}
export async function definitionChoices(kind: DefinitionKind) {
  const ctx = definitionContext();
  const [owners, objects, metrics, dimensions, metricGroups] = await Promise.all([
    api.get<DefinitionOption[]>('/build/owners', { ...ctx.options, query: { ...ctx.options.query, kind } }),
    kind !== 'metric' ? all(ctx, '/object-types') : [],
    kind === 'metric' ? all(ctx, '/metrics') : [],
    kind !== 'object' ? all(ctx, '/dimensions') : [],
    kind === 'metric' ? fetchMetricGroups() : [],
  ]);
  check(ctx); return { owners, objects, metrics, dimensions, metricGroups };
}
export async function definitionProperties(id: string) {
  const ctx = definitionContext();
  const dto = await api.get<{ props: DefinitionProperty[] }>(`/object-types/${encodeURIComponent(id)}`, ctx.options);
  check(ctx); return dto.props;
}
export async function definitionDetail(kind: DefinitionKind, id: string, crId?: string) {
  const ctx = definitionContext();
  const dto = await api.get<DefinitionValues & { brief?: object; meta?: object; props?: DefinitionProperty[] }>(`/${paths[kind]}/${encodeURIComponent(id)}`, { ...ctx.options, query: { ...ctx.options.query, full: true } });
  check(ctx);
  let items: CRItemOut[] = [];
  if (crId) {
    await fetchCR(crId, ctx.options); check(ctx);
    items = await fetchCRItems(crId, { kind, targetId: id }, ctx.options); check(ctx);
  }
  const fields = { ...dto, ...dto.brief, ...dto.meta } as DefinitionValues;
  // The API's dimension table rows are display rows; the definition is the explicit dimNames array.
  if (kind === 'metric') {
    fields.dims = dto.dimNames ?? [];
    // 执行口径：后端 execDef 只含已保存的键，表单空串表示“未设置”。
    const exec = (dto.execDef ?? {}) as { timeField?: string; defaultFilter?: string; defaultRange?: MetricDefaultRange | null };
    fields.timeField = exec.timeField ?? ''; fields.defaultFilter = exec.defaultFilter ?? '';
    fields.defaultRange = exec.defaultRange ?? null;
  }
  return { fields, properties: dto.props ?? [], items };
}
export const METRIC_EXEC_KEYS = { timeField: '时间字段', defaultFilter: '默认过滤' } as const;
/** 时间字段/默认过滤：后端只收非空文本，清空走 delete，未改不发（crSave 会丢掉前后相同的 update）。 */
export function execChanges(values: DefinitionValues, original?: DefinitionValues, resumed = false): DraftChange[] {
  const changes = (Object.keys(METRIC_EXEC_KEYS) as (keyof typeof METRIC_EXEC_KEYS)[]).flatMap((key): DraftChange[] => {
    const after = String(values[key] ?? '').trim(), before = String(original?.[key] ?? '').trim();
    const base = { key, type: '指标', field: METRIC_EXEC_KEYS[key] };
    // 新指标：清空时只在续写草稿里发 delete（crSave 会直接移除之前的 add 行）。
    if (!original) return after ? [{ ...base, after, op: 'add' as const }] : resumed ? [{ ...base, after: undefined, op: 'delete' as const }] : [];
    if (!after && before) return [{ ...base, before, after: undefined, op: 'delete' as const }];
    return [{ ...base, before, after: after || before, op: 'update' as const }];
  });
  const after = values.defaultRange ?? null, before = original?.defaultRange ?? null;
  if (JSON.stringify(after) !== JSON.stringify(before) || (!original && resumed)) {
    changes.push({ key: 'defaultRange', type: '指标', field: '默认时间范围', before,
      after: after ?? undefined, op: after == null ? 'delete' : original ? 'update' : 'add' });
  }
  return changes;
}
export async function saveDefinition(kind: DefinitionKind, values: DefinitionValues, properties: DefinitionProperty[], original: DefinitionValues | undefined, originalProperties: DefinitionProperty[], opts: { join?: string; title?: string; desc?: string }, resume?: string, previousProperties: DefinitionProperty[] = originalProperties, lockVersion?: number) {
  const ctx = definitionContext(), id = original?.id ?? values.api;
  const target = { kind, id: String(id), name: values.name, isNew: !original };
  // 保存即生效：已存在的历史草稿由后端接管（不再要求先处理草稿才能编辑）
  if (lockVersion === undefined) throw new Error('版本信息尚未加载，请重新打开表单');
    const keys = kind === 'object' ? ['name','api','group','domain','owner','desc','conflict','source','table','pk','sync'] : kind === 'metric' ? ['name','api','group','unit','owner','version','formula','dims','variants'] : ['name','api','kind','group','owner','object','attr','column','valueSrc','sync','desc','levels'];
    const changes: DraftChange[] = keys.filter((key) => !original || key !== 'api').map((key) => ({ key, before: original?.[key], after: values[key] ?? '', type: kind === 'object' ? '对象类型' : kind === 'metric' ? '指标' : '维度', field: key, op: original ? 'update' as const : 'add' as const }));
    if (kind === 'metric') changes.push(...execChanges(values, original, !!resume));
    const sections: { section: string; rows: CRItemIn[] }[] = [{ section: 'def', rows: changes.filter(c => c.op !== 'update' || JSON.stringify(c.before ?? null) !== JSON.stringify(c.after ?? null)).map(c => ({ ...c, op: c.op ?? (original ? 'update' : 'add') })) }];
    if (kind === 'object') {
      const byApi = new Map(originalProperties.map((p) => [p.api, p]));
      const rows: DraftChange[] = properties.map((p) => ({ key: p.api, before: byApi.get(p.api), after: p, op: byApi.has(p.api) ? 'update' as const : 'add' as const, type: '属性', field: p.name }));
      rows.push(...previousProperties.filter((p) => !properties.some((v) => v.api === p.api)).map((p) => ({ key: p.api, before: p, after: undefined, op: 'delete' as const, type: '属性', field: p.name })));
      sections.push({ section: 'properties', rows: rows.filter(c => c.op !== 'update' || JSON.stringify(c.before ?? null) !== JSON.stringify(c.after ?? null)).map(c => ({ ...c, op: c.op ?? 'update' })) });
    }
    const result = await applyModelChange(target, sections, lockVersion); check(ctx);
    return { id: result.targetId, crId: '', published: true };
}
