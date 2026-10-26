import { apiClient } from './http';
import { definitionContext } from './definitionForms';
import { fetchCR, staticContextKey } from './static';
import type { DefinitionProperty } from './definitionForms';
export interface ImportObject { table: string; id: string; name: string; api: string; group: string; datasource: string; pk: string; desc: string; owner?: string; props: DefinitionProperty[] }
export interface ImportPreview { datasourceId: string; schema: string; tables: { name: string; comment: string; pk: string[]; columns: { name: string; type: string; nullable: boolean; comment: string }[]; fks: { columns: string[]; refTable: string; refColumns: string[] }[] }[]; proposal: { objectTypes: ImportObject[]; linkTypes: { id: string; name: string; from: string; to: string; [key: string]: unknown }[] }; warnings: string[]; fingerprint: string; generator: string }
const api = apiClient('data');
const check = (key: string) => { if (key !== staticContextKey()) throw new Error('身份或版本已切换，请重新预览'); };
export async function previewObjectImport(id: string, schema: string, tables: string[]) {
  const ctx = definitionContext();
  const result = await api.post<ImportPreview>(`/datasources/${encodeURIComponent(id)}/import-preview`, { schema, tables }, ctx.options);
  check(ctx.key); return result;
}
export async function commitObjectImport(preview: ImportPreview, tables: string[], objects: ImportObject[], includeLinks: boolean, opts: { join?: string; title?: string; desc?: string }, linkIds: string[] = []) {
  const ctx = definitionContext();
  const header = opts.join ? await fetchCR(opts.join, ctx.options) : undefined;
  check(ctx.key);
  const result = await api.post<{ crId: string; lockVersion: number; targets: { kind: string; id: string; isNew: boolean; name: string }[] }>(`/datasources/${encodeURIComponent(preview.datasourceId)}/import-to-cr`, {
    schema: preview.schema, tables, fingerprint: preview.fingerprint,
    objectTypes: objects.map(({ table, id, name, api, owner }) => ({ table, id, name, api, owner })), includeLinks, linkIds,
    title: opts.title, reason: opts.desc, crId: opts.join, lockVersion: header?.lockVersion,
  }, ctx.options);
  check(ctx.key); return result;
}

export interface DatasourceMapping { objectId: string; name: string; schema: string; table: string }
export interface MetadataDiff { objectId: string; datasourceId: string; schema: string; table: string; added: { name: string; type: string; nullable: boolean; comment: string | null }[]; deleted: string[] }
export async function recollectObjectMetadata(id: string) {
  const ctx = definitionContext();
  if (!api.isReal()) throw new Error('重新采集元数据需要启用真实数据接口');
  const result = await api.post<MetadataDiff>(`/object-types/${encodeURIComponent(id)}/metadata-diff`, {}, ctx.options);
  check(ctx.key); return result;
}
export async function fetchDatasourceMappings(id: string) {
  const ctx = definitionContext();
  const result = await api.get<DatasourceMapping[]>(`/datasources/${encodeURIComponent(id)}/mappings`, ctx.options);
  check(ctx.key); return result;
}
export async function mappingObjectChoices() {
  const ctx = definitionContext(), client = apiClient('static'), rows: { id: string; name: string; api: string }[] = [];
  if (!client.isReal()) throw new Error('对象映射需要启用真实静态接口');
  for (let page = 1; ; page++) {
    const result = await client.get<import('./http').PageResult<{ id: string; name: string; api: string }>>('/object-types', { ...ctx.options, query: { ...ctx.options.query, page, size: 200 } });
    check(ctx.key); rows.push(...result.items);
    if (rows.length >= result.total || !result.items.length) return rows;
  }
}
export async function commitTableMapping(id: string, schema: string, table: string, objectId: string, opts: { join?: string; title?: string; desc?: string }) {
  const ctx = definitionContext();
  const head = opts.join ? await fetchCR(opts.join, ctx.options) : undefined;
  check(ctx.key);
  const result = await api.put<{ crId: string; lockVersion: number }>(`/datasources/${encodeURIComponent(id)}/tables/${encodeURIComponent(schema)}/${encodeURIComponent(table)}/mapping`, { objectId, crId: opts.join, lockVersion: head?.lockVersion, title: opts.title, reason: opts.desc }, ctx.options);
  check(ctx.key); return result;
}
