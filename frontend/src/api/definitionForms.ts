import { apiClient, currentApiContext, type PageResult } from './http';
import { crSave, crStart, fetchCR, fetchCRItems, staticContextKey, fetchMetricGroups, type CRItemOut, type DraftChange } from './static';
export type DefinitionKind = 'object' | 'metric' | 'dimension';
export interface DefinitionValues { id?: string; name: string; api: string; group: string; owner: string; [key: string]: unknown }
export interface DefinitionOption { id: string; name: string; api?: string; status?: string; version?: string; [key: string]: unknown }
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
  if (kind === 'metric') fields.dims = dto.dimNames ?? [];
  return { fields, properties: dto.props ?? [], items };
}
export async function saveDefinition(kind: DefinitionKind, values: DefinitionValues, properties: DefinitionProperty[], original: DefinitionValues | undefined, originalProperties: DefinitionProperty[], opts: { join?: string; title?: string; desc?: string }, resume?: string, previousProperties: DefinitionProperty[] = originalProperties) {
  const ctx = definitionContext(), id = original?.id ?? values.api;
  const target = { kind, id: String(id), name: values.name, isNew: !original };
  const started = resume ? { join: resume } : await crStart(target, opts);
  check(ctx);
  const crId = started.join;
  if (!crId) throw new Error('未取得数据库变更单号');
  // Expose the CR id on failure so retry resumes this draft instead of creating another target.
  try {
    const keys = kind === 'object' ? ['name','api','group','domain','owner','desc','conflict','source','table','pk','sync'] : kind === 'metric' ? ['name','api','group','unit','owner','version','formula','dims','variants'] : ['name','api','kind','group','owner','object','attr','column','valueSrc','sync','desc','levels'];
    const changes = keys.filter((key) => !original || key !== 'api').map((key) => ({ key, before: original?.[key], after: values[key] ?? '', type: kind === 'object' ? '对象类型' : kind === 'metric' ? '指标' : '维度', field: key, op: original ? 'update' as const : 'add' as const }));
    await crSave(crId, target, 'def', changes); check(ctx);
    if (kind === 'object') {
      const byApi = new Map(originalProperties.map((p) => [p.api, p]));
      const rows: DraftChange[] = properties.map((p) => ({ key: p.api, before: byApi.get(p.api), after: p, op: byApi.has(p.api) ? 'update' as const : 'add' as const, type: '属性', field: p.name }));
      rows.push(...previousProperties.filter((p) => !properties.some((v) => v.api === p.api)).map((p) => ({ key: p.api, before: p, after: undefined, op: 'delete' as const, type: '属性', field: p.name })));
      await crSave(crId, target, 'properties', rows); check(ctx);
    }
    return { id: target.id, crId };
  } catch (error) { throw Object.assign(error instanceof Error ? error : new Error(String(error)), { crId }); }
}
