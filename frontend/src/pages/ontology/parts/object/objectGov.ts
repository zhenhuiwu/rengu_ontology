/**
 * 对象类型页“使用与治理”几个页签共用的取数：哪些指标、变更单涉及这个对象类型。
 * 指标的基础对象从公式文字里认（订单行、退款行……），维度按中文名映射到对象类型 id；不改 metrics.ts。
 */
import { METRICS, type Metric } from '@/mock/metrics';
import { CHANGE_REQUESTS, crDetail, type ChangeRequest } from '@/mock/changes';
import { qObjType } from '@/mock/quality';
import { draftOf, allDrafts } from '../useDraftView';

export const objLabel = (id: string) => qObjType(id)?.name ?? id;

/** 指标维度 → 对象类型 id（“日期”不是对象类型；“渠道”是订单上的属性） */
const DIM_OBJ: Record<string, string> = { 门店: 'store', 区域: 'region', 大区: 'region', 渠道: 'order', 品类: 'category', 仓库: 'warehouse', 商品: 'product', 会员: 'member' };
/** 公式里出现的对象类型中文名 → id；长名在前，避免“订单行”被认成“订单” */
const FORMULA_OBJ: [string, string][] = [['订单行', 'order_line'], ['退款行', 'refund_line'], ['退款单', 'refund'], ['退款', 'refund'], ['订单', 'order'], ['会员', 'member'], ['商品', 'product']];

export interface MetricUse { m: Metric; base: string[]; via: string[] }
function baseOf(m: Metric): string[] {
  let f = m.formula;
  const out: string[] = [];
  for (const [name, id] of FORMULA_OBJ) if (f.includes(name)) { out.push(id); f = f.split(name).join(''); }
  // 派生指标（毛利率、客单价、出库及时率）公式里没有对象名，按订单行 / 订单算
  if (!out.length) out.push(m.formula.includes('订单数') ? 'order' : 'order_line');
  return [...new Set(out)];
}
/** 基础对象或维度涉及这个对象类型的指标 */
export function metricsOf(objectId: string): MetricUse[] {
  return METRICS.map((m) => ({ m, base: baseOf(m), via: m.dims.filter((d) => DIM_OBJ[d] === objectId) }))
    .filter((u) => u.base.includes(objectId) || u.via.length);
}

export interface CrUse { cr: ChangeRequest; parts: string[] }
/** 涉及这个对象类型的变更单（touches / diffs），按时间倒序；本会话新建的草稿也算 */
export function crsOf(objectId: string): CrUse[] {
  const name = objLabel(objectId);
  const out: CrUse[] = [];
  for (const cr of CHANGE_REQUESTS) {
    const d = crDetail(cr.id);
    const parts = new Set<string>();
    for (const t of d.touches) {
      if (t === `object:${objectId}`) parts.add('对象类型');
      else if (t.startsWith(`property:${objectId}.`)) parts.add('属性');
      else if (t.startsWith(`relation:${objectId}.`)) parts.add('关系');
    }
    for (const x of d.diffs) if (x.target === name || x.target.startsWith(`${name}.`)) parts.add(x.type);
    if (draftOf('object', objectId)?.id === cr.id) parts.add('对象类型');
    if (parts.size) out.push({ cr, parts: [...parts] });
  }
  // 本会话发起的变更单不在 CHANGE_REQUESTS 里
  const mine = draftOf('object', objectId);
  if (mine && !out.some((u) => u.cr.id === mine.id)) {
    const d = allDrafts().find((x) => x.id === mine.id)!;
    out.unshift({ cr: { id: d.id, title: d.title, owner: d.owner, reviewer: '--', status: '草稿', level: '一般', changes: d.changes, updatedAt: d.updatedAt, reason: '' }, parts: ['对象类型'] });
  }
  return out.sort((a, b) => (a.cr.updatedAt < b.cr.updatedAt ? 1 : -1));
}
