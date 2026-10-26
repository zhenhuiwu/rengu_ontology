/** 共享悬浮卡/详情抽屉的真实只读数据；未接入项不补造指标、时间线或写操作。 */
import { effectScope, reactive, watch } from 'vue';
import { apiClient, currentApiContext } from '@/api/http';
import { loadMeta, fmtVal, propLabel } from '@/pages/objects/objMeta';
import { getRow } from '@/pages/objects/objRows';
import type { ObjView, ObjField } from './objectRegistry';

const staticApi = apiClient('static');
const qaApi = apiClient('qa');
const dynamicApi = apiClient('dynamic');
const businessKinds = new Set(['region', 'store', 'product', 'order']);
const cache = reactive<Record<string, ObjView>>({});
const pending = new Set<string>();
const contextKey = () => JSON.stringify(currentApiContext());
let generation = 0;
let observing = false;

function observeContext() {
  if (observing) return;
  observing = true;
  // 首次实际解析时身份 provider 已注册；独立作用域与全站共享缓存同寿命。
  effectScope(true).run(() => watch(contextKey, () => {
    generation += 1;
    for (const key of Object.keys(cache)) delete cache[key];
    pending.clear();
  }, { flush: 'sync' }));
}

function state(kind: string, id: string, text: string, failed = false): ObjView {
  return { kind, id, title: id, code: id, kindLabel: kind,
    status: { text: failed ? '加载失败' : '加载中', color: failed ? 'error' : 'processing' },
    brief: [{ label: '状态', value: text }], fields: [{ label: '状态', value: text }] };
}
function fieldsOf(row: Record<string, unknown>, labels: Record<string, string>): ObjField[] {
  return Object.entries(labels).filter(([key]) => row[key] !== null && row[key] !== undefined && row[key] !== '')
    .map(([key, label]) => ({ label, value: String(row[key]) }));
}
async function read(kind: string, id: string, current: () => boolean): Promise<ObjView | null> {
  if (businessKinds.has(kind)) {
    if (!staticApi.isReal() || !qaApi.isReal()) throw new Error('对象元数据与数据服务尚未接入');
    const meta = await loadMeta(kind);
    if (!current()) return null;
    if (!meta) throw new Error('对象类型不存在');
    const result = await getRow(meta, id, true, () => null);
    if (result.denied) throw new Error('无权查看此对象');
    if (!result.bound) throw new Error('此对象类型尚未接入数据');
    if (!result.row) throw new Error('对象不存在');
    const shown = meta.properties.filter((p) => p.visibility !== 'hidden');
    const titleProp = meta.properties.find((p) => p.key === meta.titleProp);
    const fields = shown.map((p) => ({ label: propLabel(p), value: p.sensitive ? '已脱敏' : fmtVal(p, result.row!.values[p.key]) }));
    return { kind, id, title: titleProp && (titleProp.sensitive || titleProp.visibility === 'hidden') ? id : result.row.title, code: id, kindLabel: meta.name, fields,
      brief: [...shown.filter((p) => p.visibility === 'prominent'), ...shown.filter((p) => p.visibility !== 'prominent')]
        .slice(0, 4).map((p) => ({ label: propLabel(p), value: p.sensitive ? '已脱敏' : fmtVal(p, result.row!.values[p.key]) })) };
  }
  const path = kind === 'metric' ? '/metrics' : kind === 'object-type' ? '/object-types' : '/rules';
  const row = await (kind === 'rule' ? dynamicApi : staticApi).get<Record<string, unknown>>(`${path}/${encodeURIComponent(id)}`);
  let fields: ObjField[];
  if (kind === 'metric') {
    fields = fieldsOf(row, { api: 'API 名称', formula: '口径', unit: '单位', owner: '负责人', version: '版本', updatedAt: '数据更新' });
    if (Array.isArray(row.def)) fields.push(...row.def.filter((r) => r && typeof r.k === 'string' && typeof r.v === 'string').map((r) => ({ label: r.k, value: r.v })));
  }
  else if (kind === 'object-type') fields = fieldsOf(row, { api: 'API 名称', group: '分组', status: '状态', source: '数据源', owner: '负责人' });
  else {
    fields = fieldsOf(row, { version: '版本', owner: '负责人', objKind: '作用对象', expr: '条件', dedupe: '去重' });
    if (row.trigger && typeof row.trigger === 'object') fields.push(...fieldsOf(row.trigger as Record<string, unknown>, { kind: '触发方式', text: '执行计划' }));
  }
  const status = row.displayStatus ?? row.status;
  return { kind, id, title: String(row.name ?? id), code: String(row.api ?? id), kindLabel: kind === 'metric' ? '指标' : kind === 'object-type' ? '对象类型' : '规则',
    fields, brief: fields.slice(0, 4), ...(status ? { status: { text: String(status), color: 'default' as const } } : {}) };
}

export const usesRealObject = (kind: string) => businessKinds.has(kind) ? (staticApi.isReal() || qaApi.isReal())
  : kind === 'metric' || kind === 'object-type' ? staticApi.isReal() : kind === 'rule' ? dynamicApi.isReal() : false;

/** undefined = 此 kind 对应模块显式处于 mock；真实失败始终返回错误状态，禁止调用旧 resolver。 */
export function resolveRealObject(kind: string, id: string): ObjView | undefined {
  if (!usesRealObject(kind)) return undefined;
  observeContext();
  const key = JSON.stringify([contextKey(), kind, id]);
  if (cache[key]) return cache[key];
  if (!pending.has(key)) {
    pending.add(key);
    const started = generation;
    const current = () => started === generation && key === JSON.stringify([contextKey(), kind, id]);
    read(kind, id, current).then((view) => { if (current() && view) cache[key] = view; }, (error) => {
      if (current()) cache[key] = state(kind, id, error instanceof Error ? error.message : '对象加载失败', true);
    }).finally(() => { if (started === generation) pending.delete(key); });
  }
  return state(kind, id, '正在读取数据库对象');
}

