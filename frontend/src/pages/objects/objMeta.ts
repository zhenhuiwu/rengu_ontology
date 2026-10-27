/**
 * 对象元数据适配层（backend/docs/backend/generic-object-explorer.md）：对象搜索 / 对象详情 / 工作台只认这里的形状，
 * 不在页面里写死某个行业的对象、字段、筛选项。
 *
 * 真模式类型列表、元数据和属性只读静态 API，错误直接抛出；显式 mock 模式才读本地定义。
 * - 类型列表：静态 GET /object-types（已发布、未隐藏，完整分页）
 * - 元数据：静态 GET /object-types/{id}/meta + /properties（完整分页）
 * - 分面：静态 GET /object-types/{id}/facets → 调用方兜底（真接口用 qa 搜索结果里的过渡分面，演示用 objRows 本地算）
 * - 关系读已加载的静态定义；真模式未接入动作候选返回 []，建议问句和指标仅取后台返回。
 *
 * 默认标志 [推断]，与后台 explore_api.flags 一致：主键和标题可搜索；enum / boolean / 时间和非主键的维度字符串可筛选；
 * 数值和时间可排序。listed / visibility / format / unit 静态还没有，默认规则见 metaFromProps。
 */
import { apiClient, currentApiContext, isBackendDown, type PageResult } from '@/api/http';
import { loadStatic } from '@/api/static';
import { LINK_OBJECTS, LINKS, type Cardinality } from '@/mock/links';
import { OBJECT_PROPS, objectMeta, type ObjProp } from '@/mock/objectProps';
import { ACTION_TYPES } from '@/mock/actionEvent';
import { METRICS } from '@/mock/metrics';
import { fmtInt, fmtMoney } from '@/mock/format';

const staticApi = apiClient('static');
const objectApi = apiClient('qa');
const realMetadata = () => objectApi.isReal() || staticApi.isReal();

export type Visibility = 'prominent' | 'normal' | 'hidden';
export interface PropMeta {
  key: string; name: string; dataType: string; group: string; desc: string;
  searchable: boolean; filterable: boolean; sortable: boolean; listed: boolean; order: number; visibility: Visibility;
  /** money（两位小数千分位）/ int / number / date / datetime / text */
  format: string; unit: string; pk: boolean; title: boolean; sensitive: boolean;
}
export interface TypeBrief { id: string; name: string; group: string }
export interface MetricVariantMeta { code: string; label: string; default?: boolean }
export interface MetricMeta { id: string; name: string; unit: string | null; variants: MetricVariantMeta[] }
export interface TypeMeta extends TypeBrief {
  titleProp: string; pkProp: string; properties: PropMeta[]; sampleQuestions: string[];
  /** 相关指标（静态 meta.metrics；mock 兜底按指标维度推 [推断]）；variants 给工作台口径 */
  metrics: MetricMeta[]; kwPlaceholder?: string; hasData?: boolean;
}
export type FacetMeta =
  | { key: string; kind: 'enum'; values: { value: string; count: number }[] }
  | { key: string; kind: 'range'; min: string | number | null; max: string | number | null };
export type FilterVal = string[] | { min?: string | number | null; max?: string | number | null };
export type Filters = Record<string, FilterVal>;

const NUMERIC = new Set(['decimal', 'integer', 'int']);
const TIME = new Set(['date', 'timestamp']);
export const isNumeric = (p: PropMeta) => NUMERIC.has(p.dataType);
export const isRange = (p: PropMeta) => NUMERIC.has(p.dataType) || TIME.has(p.dataType);
/** 列表里默认最多几列（标题列之外）[推断] */
const MAX_LISTED = 7;

/** ObjProp（静态 /properties 或 mock）→ PropMeta，默认标志 [推断] */
export function metaFromProps(props: ObjProp[]): PropMeta[] {
  const out = props.map((p, i): PropMeta => {
    const dt = p.type;
    const unit = /单位[:：]\s*([^\s，。；,;）)]+)/.exec(p.desc ?? '')?.[1] ?? '';
    const hidden = p.group === '其他' && !p.pk && !p.title;
    return {
      key: p.api, name: p.name, dataType: dt, group: p.group || '其他', desc: p.desc ?? '',
      searchable: !!(p.pk || p.title),
      filterable: dt === 'enum' || dt === 'boolean' || TIME.has(dt) || (dt === 'string' && !!p.dim && !p.pk),
      sortable: NUMERIC.has(dt) || TIME.has(dt),
      listed: false, order: i, visibility: p.pk || p.title ? 'prominent' : hidden ? 'hidden' : 'normal',
      format: dt === 'decimal' ? (unit === '元' ? 'money' : 'number') : dt === 'integer' ? 'int' : dt === 'date' ? 'date' : dt === 'timestamp' ? 'datetime' : 'text',
      unit, pk: !!p.pk, title: !!p.title, sensitive: !!p.sensitive,
    };
  });
  // 默认列 [推断]：主键 + 最多 3 个枚举/布尔 + 2 个数值 + 1 个时间，余下用维度字符串补到 MAX_LISTED
  const cand = out.filter((p) => p.visibility !== 'hidden' && !p.sensitive && !p.title && p.dataType !== 'json');
  const quota: [(p: PropMeta) => boolean, number][] = [
    [(p) => p.pk, 1], [(p) => p.dataType === 'enum' || p.dataType === 'boolean', 3], [isNumeric, 2],
    [(p) => TIME.has(p.dataType), 1], [(p) => p.filterable, MAX_LISTED],
  ];
  const picked = new Set<PropMeta>();
  quota.forEach(([ok, n]) => cand.filter((p) => ok(p) && !picked.has(p)).slice(0, n).forEach((p) => { if (picked.size < MAX_LISTED) picked.add(p); }));
  picked.forEach((p) => { p.listed = true; });
  return out;
}

function withTitle(t: TypeBrief, props: PropMeta[], sample: string[], extra: Partial<TypeMeta> = {}): TypeMeta {
  const pk = extra.pkProp || props.find((p) => p.pk)?.key || props[0]?.key || 'id';
  const title = extra.titleProp || props.find((p) => p.title)?.key || pk;
  return { ...extra, metrics: extra.metrics ?? (realMetadata() ? [] : mockMetrics(t)), ...t, properties: props, titleProp: title, pkProp: pk, sampleQuestions: sample };
}
/** mock 兜底：维度里包含本类型的已发布指标 [推断]，没有口径变体 */
const mockMetrics = (t: TypeBrief): MetricMeta[] =>
  METRICS.filter((m) => m.status === '已发布' && m.dims.includes(t.name)).map((m) => ({ id: m.id, name: m.name, unit: m.unit, variants: [] }));

const mockTypes = (): TypeBrief[] => LINK_OBJECTS.filter((o) => !o.draft).map(({ id, name, group }) => ({ id, name, group }));
const sampleOf = (t: TypeBrief) => objectMeta(t.id, t.name, t.group).qs.map((q) => q.q);

export function metaContextKey(): string {
  const { ontologyId, demoAs, previewAs, ver } = currentApiContext();
  return JSON.stringify([staticApi.isReal(), objectApi.isReal(), ontologyId, demoAs, previewAs, ver]);
}
function contextOptions() {
  const { ontologyId, demoAs, previewAs, ver } = currentApiContext();
  const headers: Record<string, string> = { 'X-Demo-As': demoAs ?? '', 'X-Ontology-Id': ontologyId ?? '' };
  return { readonlyParams: false, query: { previewAs, ver }, headers };
}
const typesCache = new Map<string, Promise<TypeBrief[]>>();
/** 已发布、未隐藏的对象类型（按分组顺序保持接口给的顺序）。静态 hidden 标志还没有，[推断] 全部视为不隐藏 */
export function listTypes(): Promise<TypeBrief[]> {
  const key = metaContextKey();
  const opts = contextOptions();
  if (!typesCache.has(key)) {
    typesCache.set(key, (async () => {
      if (!realMetadata()) return mockTypes();
      const api = objectApi.isReal() ? objectApi : staticApi;
      const rows: (TypeBrief & { status?: string; hidden?: boolean })[] = [];
      for (let page = 1; ; page++) {
        const r = await api.get<PageResult<TypeBrief & { status?: string; hidden?: boolean }>>('/object-types', { ...opts, query: { ...opts.query, page, size: 200 } });
        rows.push(...r.items);
        if (!r.items.length || typeof r.total !== 'number' || rows.length >= r.total) break;
      }
      return rows.filter((t) => (!t.status || t.status === '已发布') && !t.hidden).map(({ id, name, group }) => ({ id, name, group }));
    })().catch((e) => { typesCache.delete(key); throw e; }));
  }
  return typesCache.get(key)!;
}
export const groupTypes = (ts: TypeBrief[]) => {
  const g = new Map<string, TypeBrief[]>();
  ts.forEach((t) => g.set(t.group, [...(g.get(t.group) ?? []), t]));
  return [...g].map(([label, items]) => ({ label, options: items.map((t) => ({ value: t.id, label: t.name })) }));
};

const metaCache = new Map<string, Promise<TypeMeta | null>>();
/** 某类型的元数据；类型不存在返回 null */
export function loadMeta(id: string, withLinks = true): Promise<TypeMeta | null> {
  const key = JSON.stringify([metaContextKey(), id, withLinks]);
  const opts = contextOptions();
  if (!metaCache.has(key)) {
    metaCache.set(key, (async () => {
      const t = (await listTypes()).find((x) => x.id === id);
      if (!t) return null;
      // 真实关系加载失败同样阻止展示，避免把旧缓存当成当前定义。
      if (withLinks && !objectApi.isReal()) await loadStatic(['links']);
      if (realMetadata()) {
        type RawMeta = { titleProp: string | null; pkProp?: string | null; kwPlaceholder?: string; hasData?: boolean; properties: (Partial<PropMeta> & { key: string })[]; sampleQuestions?: (string | null)[]; metrics?: MetricMeta[] };
        const api = objectApi.isReal() ? objectApi : staticApi;
        const [m, sp] = await Promise.all([api.get<RawMeta>(`/object-types/${encodeURIComponent(id)}/meta`, opts), objectApi.isReal() ? Promise.resolve([] as ObjProp[]) : staticProps(id, opts)]);
        const base = new Map(metaFromProps(sp).map((p) => [p.key, p]));
        const props = m.properties.map((p, i) => {
          const b = base.get(p.key) ?? metaFromProps([{ api: p.key, name: p.name ?? p.key, type: p.dataType ?? 'string', group: '其他', source: '', desc: '' }])[0];
          const q = Object.fromEntries(Object.entries(p).filter(([, v]) => v !== null && v !== undefined)) as Partial<PropMeta>;
          return { ...b, order: i, ...q, pk: b.pk || p.key === m.pkProp, title: b.title || p.key === m.titleProp } as PropMeta;
        });
        const qs = (m.sampleQuestions ?? []).filter((x): x is string => !!x);
        return withTitle(t, props.sort((a, b) => a.order - b.order), qs, {
          titleProp: m.titleProp ?? undefined, pkProp: m.pkProp ?? undefined, kwPlaceholder: m.kwPlaceholder, hasData: m.hasData,
          metrics: (m.metrics ?? []).map((x) => ({ ...x, variants: x.variants ?? [] })),
        });
      }
      return withTitle(t, metaFromProps(OBJECT_PROPS[id] ?? []), sampleOf(t));
    })().catch((e) => { metaCache.delete(key); throw e; }));
  }
  return metaCache.get(key)!;
}
/** 完整属性表补充分组/说明/敏感标志；失败不能用默认标记掩盖。 */
async function staticProps(id: string, opts: ReturnType<typeof contextOptions>): Promise<ObjProp[]> {
  const rows: ObjProp[] = [];
  for (let page = 1; ; page++) {
    const r = await staticApi.get<PageResult<ObjProp>>(`/object-types/${encodeURIComponent(id)}/properties`, { ...opts, query: { ...opts.query, page, size: 200 } });
    rows.push(...r.items);
    if (!r.items.length || typeof r.total !== 'number' || rows.length >= r.total) return rows;
  }
}

/** 静态 /facets（按其他已选筛选联动，不带关键词）；仅后台不可达时返回 null，业务错误交给页面提示 */
export async function loadStaticFacets(id: string, filters: Filters): Promise<FacetMeta[] | null> {
  if (!staticApi.isReal()) return null;
  try {
    const r = await staticApi.get<(FacetMeta & { hasData?: boolean; order?: number })[]>(`/object-types/${encodeURIComponent(id)}/facets`, { query: Object.keys(filters).length ? { filters: JSON.stringify(filters) } : {} });
    return r.filter((f) => f.hasData !== false).sort((a, b) => (a.order ?? 0) - (b.order ?? 0));
  } catch (e) { if (isBackendDown(e)) return null; throw e; }
}

/** 从本类型出发的关系（已发布）：label 用本侧看到的名字（正向 name / 反向 reverse） */
export interface LinkMeta { id: string; label: string; other: string; thisProp: string; otherProp: string; many: boolean }
export function linksOf(id: string): LinkMeta[] {
  const many = (c: Cardinality, fwd: boolean) => (fwd ? c === '1 : N' || c === 'N : M' : c === 'N : 1' || c === 'N : M');
  return LINKS.filter((l) => l.status === '已发布' && !l.via && (l.from === id || l.to === id)).map((l) => l.from === id
    ? { id: l.id, label: l.name, other: l.to, thisProp: l.fromProp, otherProp: l.toProp, many: many(l.card, true) }
    : { id: l.id, label: l.reverse, other: l.from, thisProp: l.toProp, otherProp: l.fromProp, many: many(l.card, false) });
}
/** 作用对象是本类型的已发布动作（target_kind / target 名称匹配） */
export function actionsOf(t: TypeBrief) {
  if (staticApi.isReal()) return [];
  return ACTION_TYPES.filter((a) => a.status === '已发布' && (a.targetKind === t.id || a.target === t.name))
    .map((a) => ({ id: a.id, name: a.name, desc: a.desc, approval: a.approval }));
}
/** 相关指标（只给名称、单位和口径变体，不编造数值） */
export const metricsOf = (t: TypeMeta): MetricMeta[] => t.metrics;

/** 按属性格式显示值 */
export function fmtVal(p: PropMeta | undefined, v: unknown): string {
  if (v === null || v === undefined || v === '') return '--';
  if (typeof v === 'boolean') return v ? '是' : '否';
  if (!p) return String(v);
  if (p.format === 'money' && typeof v === 'number') return fmtMoney(v);
  if (p.format === 'int' && typeof v === 'number') return fmtInt(v);
  if (p.format === 'date') return String(v).slice(0, 10);
  return String(v);
}
/** 列 / 字段标题：带单位（元 / ㎡ …） */
export const propLabel = (p: PropMeta) => (p.unit && !p.name.includes(p.unit) ? `${p.name}（${p.unit}）` : p.name);
