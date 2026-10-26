/**
 * 工作台“范围”“口径”选项（backend/docs/backend/generic-object-explorer.md 工作台一节），不写死某个行业：
 * - 范围 = 组织层级维度（分组“组织”、已发布、层级 ≥ 2 的第一个维度 [推断]）最上层的启用取值；
 *   默认 = 当前用户的行级范围（本体小白的 GET /api/admin/v1/me/row-scopes?objectType=，还没上 master，形状 [推断]，失败就跳过）
 *   → 预览时被预览用户在该维度来源对象上的范围（“大区 = 华东”）→ 第一个选项。取值按名称或同义词匹配。
 *   静态是真接口时维度从静态拉（loadStatic 原地替换 DIM_ITEMS），否则读 mock。
 * - 口径 = 已发布指标的 variants 并集（静态 /metrics 上线该字段前：真接口用 W17 /session-options，演示用 mock 选项）；
 *   字段存在但全部为空 → 不显示口径选择。
 * 会话里存的 scope 是自由文本（≤60 字），旧会话里的“华东”按同义词归一到维度取值名。
 */
import { computed, ref } from 'vue';
import { apiClient, type PageResult } from '@/api/http';
import { loadStatic, isStaticReal } from '@/api/static';
import { getSessionOptions } from '@/api/qa';
import { DIM_ITEMS, type DimItem, type DimValue } from '@/mock/dimensions';
import { QA_SCOPE_OPTIONS, QA_CALIBER_OPTIONS } from '@/mock';

export interface Opt { value: string; label: string }
const adminApi = apiClient('admin');
const staticApi = apiClient('static');

const dim = ref<DimItem | null>(null);
const tops = ref<DimValue[]>([]);
const rowScope = ref<string[] | null>(null);
/** null = 还不知道（用兜底选项）；[] = 指标有 variants 字段但都为空 → 隐藏 */
const variants = ref<string[] | null>(null);
const fallbackScopes = ref<Opt[]>(QA_SCOPE_OPTIONS);
const fallbackCalibers = ref<Opt[]>(QA_CALIBER_OPTIONS);
let started: Promise<void> | null = null;

function pickOrgDim(items: DimItem[]): DimItem | null {
  return items.find((d) => d.kind === '维度' && d.group === '组织' && d.status === '已发布' && d.levels.length > 1) ?? null;
}
/** row-scopes 形状 [推断]：数组或 {items}；每项取 values[] / value，只认本维度 */
function parseRowScopes(r: unknown, dimId: string): string[] | null {
  const list = Array.isArray(r) ? r : (r as { items?: unknown[] })?.items;
  if (!Array.isArray(list)) return null;
  if (list.some((x: any) => x?.unrestricted || x?.scope === '全部')) return null;
  const vals = list.filter((x: any) => !x?.dimension || x.dimension === dimId)
    .flatMap((x: any) => (Array.isArray(x?.values) ? x.values : x?.value != null ? [x.value] : [])).map(String);
  return vals.length ? vals : null;
}

export function ensureScopeOptions(qaReal: boolean): Promise<void> {
  if (started) return started;
  started = (async () => {
    await loadStatic(['dimensions']).catch(() => undefined);
    const d = pickOrgDim(DIM_ITEMS);
    dim.value = d;
    tops.value = d ? d.values.filter((v) => v.level === d.levels[0].name && v.status !== '停用') : [];
    const jobs: Promise<unknown>[] = [];
    if (d && d.object && adminApi.isReal()) {
      jobs.push(adminApi.get<unknown>('/me/row-scopes', { query: { objectType: d.object } })
        .then((r) => { rowScope.value = parseRowScopes(r, d.id); }).catch(() => undefined));
    }
    if (isStaticReal()) {
      jobs.push(staticApi.get<PageResult<{ status?: string; variants?: (string | { name?: string; label?: string })[] }>>('/metrics', { query: { size: 200 } })
        .then((r) => {
          if (!r.items.some((m) => 'variants' in m)) return;
          const s = new Set<string>();
          r.items.filter((m) => !m.status || m.status === '已发布').forEach((m) => (m.variants ?? []).forEach((v) => {
            const n = typeof v === 'string' ? v : v.label ?? v.name; if (n) s.add(n);
          }));
          variants.value = [...s];
        }).catch(() => undefined));
    }
    if (qaReal) {
      jobs.push(getSessionOptions().then((o) => { fallbackScopes.value = o.scopes; fallbackCalibers.value = o.calibers; }).catch(() => undefined));
    }
    await Promise.all(jobs);
  })();
  return started;
}

/** 名称或同义词匹配到维度取值；匹配不到返回原文 */
export function normScope(s: string | null | undefined): string {
  const t = (s ?? '').trim();
  if (!t) return t;
  const v = tops.value.find((x) => x.name === t || x.code === t || x.synonyms.includes(t));
  return v ? v.name : t;
}
export function useScopeCaliber() {
  /** 维度最上层的名称（“大区”），用于“从这里开始按 X = …”提示 */
  const levelName = computed(() => dim.value?.levels[0].name ?? '范围');
  const scopeOpts = computed<Opt[]>(() => (tops.value.length
    ? tops.value.map((v) => ({ value: v.name, label: `范围：${v.name}` }))
    : fallbackScopes.value));
  const caliberOpts = computed<Opt[]>(() => (variants.value
    ? variants.value.map((v) => ({ value: v, label: `口径：${v}` }))
    : fallbackCalibers.value));
  const showCaliber = computed(() => caliberOpts.value.length > 0);
  /** 默认范围：行级范围 → 预览范围（“层级 = 值”）→ 第一个选项 */
  function defaultScope(previewScope: (objName: string) => string | null): string {
    const opts = scopeOpts.value.map((o) => o.value);
    const hit = (xs: string[]) => xs.map(normScope).find((x) => opts.includes(x));
    const rs = rowScope.value && hit(rowScope.value);
    if (rs) return rs;
    const d = dim.value;
    const pvs = d ? previewScope(d.objectName) : null;
    const m = pvs && /^(\S+) [=∈] (.+)$/.exec(pvs.split('；或 ')[0]);
    const ps = m && hit(m[2].split('、'));
    if (ps) return ps;
    return opts[0] ?? '';
  }
  return { levelName, scopeOpts, caliberOpts, showCaliber, defaultScope, normScope };
}
