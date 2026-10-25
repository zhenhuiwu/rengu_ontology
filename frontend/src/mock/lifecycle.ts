/**
 * 对象类型 · 行为（动态本体）：生命周期状态机示例数据。
 * 订单生命周期 v4 与规则 SM-ORD（订单状态非法迁移）、动作“订单状态修正”同一口径：
 *   不在下面迁移表里的状态变化 = 非法迁移，由 SM-ORD 产生告警并暂存“订单状态修正”。
 * 有草稿单的对象（门店 CR-0009、会员 CR-0012）在草稿视图里可以改状态机，改动见 LcDraft，页面叠加显示并标“新增 / 修改 / 删除”。
 * 版本号是整数（v1、v2…），随本体发布生效。
 */
import { reactive } from 'vue';
import type { ObjKind } from './actions';
import { hitsOf } from './rules';
import { ORDERS } from './orders';
import { STORES } from './stores';
import { PRODUCTS } from './products';
import { seeded } from './format';

export type LcStateKind = '初始' | '中间' | '终态';
export type LcTrigger = '事件' | '动作' | '系统' | '人工';
export interface LcState { key: string; name: string; kind: LcStateKind; desc: string; count: number; x: number; y: number; sla?: string }
export interface LcTransition {
  id: string; from: string; to: string; trigger: LcTrigger;
  /** 触发来源：事件类型名 / 动作类型名 / 系统任务 / 人工操作 */
  by: string; eventId?: string; actionId?: string;
  guard: string; effect: string; weekly: number;
}
export interface LcVersion {
  ver: number; status: '生效中' | '已替代'; by: string; at: string; change: string;
  /** 随哪次本体发布生效（mock/releases.ts 的版本号；更早的发布不在列表里时不写） */
  release?: string;
  /** 回到上一版需要撤掉的改动：查看更早的本体历史版本时用 */
  undo?: { removeTransitions?: string[]; rename?: Record<string, string> };
}
export interface Lifecycle {
  objectId: string; objectName: string; field: string; version: number; owner: string; updatedAt: string;
  /** 校验非法迁移的规则 */
  ruleId?: string; desc: string;
  states: LcState[]; transitions: LcTransition[]; versions: LcVersion[];
}

/** 对象类型页的类型 id → 动作 / 规则用的对象类型（没有的不出现：这些类型没有动作和规则） */
export const OBJ_TYPE_KIND: Record<string, ObjKind> = { order: 'order', store: 'store', product: 'product', region: 'region', member: 'member' };
export const OBJ_TYPE_NAME: Record<string, string> = {
  order: '订单', order_line: '订单行', refund: '退款单', refund_line: '退款行', member: '会员', member_level: '会员等级', store: '门店', region: '大区',
  province: '省份', product: '商品', category: '品类', promotion: '促销活动', warehouse: '仓库', inventory: '库存快照', supplier: '供应商',
  purchase: '采购单', shipment: '出库单', employee: '员工',
};

const S = (key: string, kind: LcStateKind, desc: string, count: number, x: number, y: number, sla?: string): LcState => ({ key, name: key, kind, desc, count, x, y, sla });
const T = (id: string, from: string, to: string, trigger: LcTrigger, by: string, guard: string, effect: string, weekly: number, ref: { eventId?: string; actionId?: string } = {}): LcTransition =>
  ({ id, from, to, trigger, by, guard, effect, weekly, ...ref });

export const LIFECYCLES: Lifecycle[] = [
  {
    objectId: 'order', objectName: '订单', field: '订单状态（status）', version: 4, owner: '孙浩然', updatedAt: '2026-10-09 22:03', ruleId: 'SM-ORD',
    desc: '订单从下单到签收或取消的全部合法状态变化。数据同步收到的状态变更逐条按本表校验，不在表里的变化记为非法迁移。',
    states: [
      S('待支付', '初始', '顾客已下单、尚未支付。小程序、外卖平台订单从这里开始；门店收银单直接进入“待出库”。', 1826, 40, 150, '30 分钟内支付，超时自动取消'),
      S('待出库', '中间', '已支付，等待仓库或门店生成拣货任务。', 6412, 300, 150, '2 小时内生成拣货任务'),
      S('拣货中', '中间', '已生成拣货任务，正在拣货打包。', 2937, 560, 150, '4 小时内出库'),
      S('已出库', '中间', '已出库交给承运商或骑手；按出库时间计入销售。', 18204, 820, 150, '48 小时内签收（规则 R-015）'),
      S('已签收', '终态', '顾客已签收。之后的退货走“退款单”，订单状态不再变化。', 1214883, 1080, 150),
      S('已取消', '终态', '支付超时、顾客取消或拒收退回。不计入销售。', 42140, 560, 330),
    ],
    transitions: [
      T('T1', '待支付', '待出库', '事件', '订单状态变更（支付成功）', '实付金额 = 应付金额（含税）− 优惠金额', '写入支付时间', 18640, { eventId: 'order_status_changed' }),
      T('T2', '待支付', '已取消', '系统', '支付超时自动取消（每 5 分钟）', '下单时长 > 30 分钟且未支付', '写入取消时间；释放占用库存', 2210),
      T('T3', '待出库', '拣货中', '事件', '订单状态变更（WMS 生成拣货任务）', '发货仓 ≠ 空', '—', 18322, { eventId: 'order_status_changed' }),
      T('T4', '待出库', '已取消', '人工', '顾客或客服取消', '尚未生成拣货任务', '写入取消时间；原路退款', 316),
      T('T5', '拣货中', '已出库', '事件', '订单状态变更（出库单完成）', '出库单状态 = 已完成', '写入出库时间；计入销售（财务口径）', 18160, { eventId: 'order_status_changed' }),
      T('T6', '拣货中', '待出库', '动作', '订单状态修正', '拣货任务已作废', '回到待出库，重新生成拣货任务', 14, { actionId: 'correct_order_status' }),
      T('T7', '已出库', '已签收', '事件', '运单签收', '运单状态 = 已签收', '写入签收时间', 17904, { eventId: 'waybill_signed' }),
      T('T8', '已出库', '已取消', '人工', '拒收退回', '运单状态 = 拒收 且 退回入库完成', '写入取消时间；冲减销售', 61),
      T('T9', '待出库', '待出库', '动作', '补发出库单', '原出库单已作废', '状态不变，生成新出库单', 22, { actionId: 'reissue_shipment' }),
    ],
    versions: [
      { ver: 4, status: '生效中', by: '王敏', at: '2026-10-09 22:03', change: '状态“已发货”统一为“已出库”；新增“拣货中 → 待出库”（只能经动作“订单状态修正”回退）', release: 'v2026.10.1',
        undo: { removeTransitions: ['T6'], rename: { 已出库: '已发货' } } },
      { ver: 3, status: '已替代', by: '孙浩然', at: '2026-06-03 16:20', change: '新增“已出库 → 已取消”（拒收退回）' },
      { ver: 2, status: '已替代', by: '陈立军', at: '2026-02-11 09:15', change: '“待出库”和“已出库”之间增加“拣货中”' },
      { ver: 1, status: '已替代', by: '陈立军', at: '2025-11-20 14:03', change: '初版：待支付、待出库、已出库、已签收、已取消' },
    ],
  },
  {
    objectId: 'store', objectName: '门店', field: '营业状态（biz_status）', version: 2, owner: '陈立军', updatedAt: '2026-07-02 11:30',
    desc: '门店从筹备到闭店的营业状态。暂停营业期间不参与销售目标考核，规则 R-021 / R-017 不对其计算。',
    states: [
      S('筹备中', '初始', '已签约、尚未开业。', 12, 40, 160),
      S('营业中', '中间', '正常营业。', 451, 330, 160),
      S('暂停营业', '中间', '因装修、疫情、设备故障等临时停业。', 9, 620, 40, '最长 90 天，超期需区域经理复核'),
      S('已闭店', '终态', '已撤店，历史数据保留。', 14, 900, 160),
    ],
    transitions: [
      T('T1', '筹备中', '营业中', '人工', '开业确认（区域运营）', '证照齐全 且 POS 已启用', '写入开业日期；纳入销售目标', 1),
      T('T2', '营业中', '暂停营业', '动作', '变更门店状态', '填写停业原因与预计恢复日期', '暂停考核；外卖平台同步下线', 2, { actionId: 'change_store_status' }),
      T('T3', '暂停营业', '营业中', '动作', '变更门店状态', '—', '恢复考核；外卖平台同步上线', 2, { actionId: 'change_store_status' }),
      T('T4', '营业中', '已闭店', '人工', '闭店审批（泛微 OA）', '库存清零 且 未完结订单 = 0', '写入闭店日期', 0),
      T('T5', '暂停营业', '已闭店', '人工', '闭店审批（泛微 OA）', '库存清零 且 未完结订单 = 0', '写入闭店日期', 0),
    ],
    versions: [
      { ver: 2, status: '生效中', by: '陈立军', at: '2026-07-02 11:30', change: '“暂停营业”改由动作“变更门店状态”触发，并同步外卖平台' },
      { ver: 1, status: '已替代', by: '陈立军', at: '2025-11-20 14:03', change: '初版' },
    ],
  },
  {
    objectId: 'product', objectName: '商品', field: '商品状态（sku_status）', version: 3, owner: '王敏', updatedAt: '2026-08-12 15:05',
    desc: '商品从新品引入到淘汰的状态。停售商品不参与补货（R-012）和调价（R-007）。',
    states: [
      S('新品', '初始', '已建档、试销 30 天内。', 1206, 40, 160, '试销 30 天内转正或淘汰'),
      S('在售', '中间', '正常销售、补货。', 41388, 330, 160),
      S('停售', '中间', '暂停销售（质量问题、供应中断）。', 3904, 620, 40),
      S('淘汰', '终态', '不再经营，清完库存后下架。', 1715, 900, 160),
    ],
    transitions: [
      T('T1', '新品', '在售', '人工', '品类经理转正', '试销期动销率 ≥ 60%', '纳入补货与调价规则', 84),
      T('T2', '新品', '淘汰', '人工', '品类经理淘汰', '试销期结束', '停止补货', 31),
      T('T3', '在售', '停售', '事件', '库存变动（供应中断）', '供应商停供 或 质检不合格', '暂停补货申请', 46, { eventId: 'inventory_changed' }),
      T('T4', '停售', '在售', '人工', '品类经理恢复销售', '供应恢复', '恢复补货', 39),
      T('T5', '在售', '淘汰', '人工', '品类经理淘汰', '近 90 天动销率 < 10%', '停止补货；进入清仓', 22),
      T('T6', '停售', '淘汰', '人工', '品类经理淘汰', '—', '停止补货；进入清仓', 7),
    ],
    versions: [
      { ver: 3, status: '生效中', by: '王敏', at: '2026-08-12 15:05', change: '“停售”可恢复为“在售”' },
      { ver: 2, status: '已替代', by: '王敏', at: '2026-03-09 10:00', change: '新增“新品”试销状态' },
      { ver: 1, status: '已替代', by: '刘婷', at: '2025-11-20 14:03', change: '初版' },
    ],
  },
  {
    objectId: 'member', objectName: '会员', field: '会员生命周期（lifecycle）', version: 2, owner: '吴佳', updatedAt: '2026-05-21 09:40',
    desc: '按最近一次购买时间计算的会员状态，每日 02:00 批量更新。沉睡、流失会员是复购召回（R-027）的对象。',
    states: [
      S('新客', '初始', '首单 30 天内。', 58210, 40, 150),
      S('活跃', '中间', '近 60 天有购买。', 412906, 330, 150),
      S('沉睡', '中间', '60–180 天未购买。', 236551, 620, 150),
      S('流失', '中间', '超过 180 天未购买。', 155360, 900, 150),
    ],
    transitions: [
      T('T1', '新客', '活跃', '系统', '每日批量计算', '首单后 30 天内再次购买', '—', 6120),
      T('T2', '新客', '沉睡', '系统', '每日批量计算', '首单后 60 天未再购买', '进入复购召回分群', 3310),
      T('T3', '活跃', '沉睡', '系统', '每日批量计算', '最近购买距今 > 60 天', '进入复购召回分群', 9480),
      T('T4', '沉睡', '活跃', '事件', '订单状态变更（已签收）', '—', '退出召回分群', 7215, { eventId: 'order_status_changed' }),
      T('T5', '沉睡', '流失', '系统', '每日批量计算', '最近购买距今 > 180 天', '—', 4102),
      T('T6', '流失', '活跃', '事件', '订单状态变更（已签收）', '—', '记为召回成功', 830, { eventId: 'order_status_changed' }),
    ],
    versions: [
      { ver: 2, status: '生效中', by: '吴佳', at: '2026-05-21 09:40', change: '沉睡阈值由 90 天改为 60 天' },
      { ver: 1, status: '已替代', by: '吴佳', at: '2025-11-20 14:03', change: '初版' },
    ],
  },
  {
    objectId: 'promotion', objectName: '促销活动', field: '活动状态（status）', version: 1, owner: '吴佳', updatedAt: '2026-01-15 17:22',
    desc: '促销活动从创建、审批到结束的状态。进行中的活动锁定参与商品的价格（发起调价审批会被 OA 拒绝）。',
    states: [
      S('草稿', '初始', '营销创建，尚未提交。', 64, 40, 150),
      S('待审批', '中间', '已提交泛微 OA 审批。', 23, 280, 150),
      S('已排期', '中间', '审批通过，等待开始时间。', 41, 520, 150),
      S('进行中', '中间', '活动生效，参与商品锁价。', 118, 760, 150),
      S('已结束', '终态', '到达结束时间或提前结束。', 2391, 1000, 150),
      S('已作废', '终态', '审批驳回或撤销。', 34, 520, 320),
    ],
    transitions: [
      T('T1', '草稿', '待审批', '人工', '提交审批', '预算科目已选', '发起 OA 流程', 52),
      T('T2', '待审批', '已排期', '事件', 'OA 审批通过', '—', '—', 47),
      T('T3', '待审批', '已作废', '事件', 'OA 审批驳回', '—', '—', 5),
      T('T4', '已排期', '进行中', '系统', '到达开始时间', '—', '锁定参与商品价格', 46),
      T('T5', '进行中', '已结束', '系统', '到达结束时间', '—', '解除锁价', 44),
      T('T6', '已排期', '已作废', '人工', '营销撤销', '尚未开始', '—', 2),
    ],
    versions: [{ ver: 1, status: '生效中', by: '吴佳', at: '2026-01-15 17:22', change: '初版' }],
  },
  {
    objectId: 'refund', objectName: '退款单', field: '退款状态（refund_status）', version: 2, owner: '孙浩然', updatedAt: '2026-04-08 13:10',
    desc: '退款单从申请到完成的状态。大额退款（≥ 500 元）由规则 R-009 通知复核。',
    states: [
      S('待审核', '初始', '顾客或客服发起退款申请。', 412, 40, 150, '24 小时内审核'),
      S('已同意', '中间', '审核通过，等待原路退回。', 188, 300, 150),
      S('已退款', '终态', '资金已原路退回。', 37960, 560, 150),
      S('已驳回', '终态', '审核不通过。', 354, 300, 320),
    ],
    transitions: [
      T('T1', '待审核', '已同意', '动作', '退款审核', '退款金额 ≤ 实付金额', '通知支付渠道退款', 2120, { actionId: 'approve_refund' }),
      T('T2', '待审核', '已驳回', '动作', '退款审核', '填写驳回原因', '短信通知顾客', 96, { actionId: 'approve_refund' }),
      T('T3', '已同意', '已退款', '事件', '退款单创建（支付渠道回执）', '回执金额 = 退款金额', '冲减销售', 2104, { eventId: 'refund_created' }),
    ],
    versions: [
      { ver: 2, status: '生效中', by: '孙浩然', at: '2026-04-08 13:10', change: '审核改为动作“退款审核”' },
      { ver: 1, status: '已替代', by: '孙浩然', at: '2025-11-20 14:03', change: '初版' },
    ],
  },
];
export const lifecycleOf = (objectId: string) => LIFECYCLES.find((l) => l.objectId === objectId);

/**
 * 草稿里的生命周期改动（本会话内，挂在对象所在的变更单上：门店 CR-0009、会员 CR-0012 或新发起的单）。
 * 现有两张草稿单都没改生命周期，所以初始为空；页面在草稿视图里新增 / 修改 / 删除时写这里。
 */
export interface LcDraft { addStates: LcState[]; addTransitions: LcTransition[]; editTransitions: Record<string, Partial<LcTransition>>; removeTransitions: string[] }
export const emptyLcDraft = (): LcDraft => ({ addStates: [], addTransitions: [], editTransitions: {}, removeTransitions: [] });
/** 本会话的草稿改动，键 = 变更单号:对象类型 id（切页签、切对象后仍保留） */
export const LC_DRAFT_EDITS = reactive<Record<string, LcDraft>>({});

/** 迁移的显示名：“待支付 → 待出库” */
export const trName = (t: Pick<LcTransition, 'from' | 'to'>) => `${t.from} → ${t.to}`;

/** 近 7 天非法迁移：取校验规则（SM-ORD）近 7 天的命中，按“原状态 → 新状态”汇总；与规则页命中记录同一来源 */
export interface LcIllegal { from: string; to: string; count: number; alertId: string | null; lastAt: string }
export const SINCE_7D = '2026-10-19';
export function illegalOf(objectId: string): LcIllegal[] {
  const lc = lifecycleOf(objectId);
  if (!lc?.ruleId) return [];
  const map = new Map<string, LcIllegal>();
  for (const h of hitsOf(lc.ruleId).filter((x) => x.at >= SINCE_7D)) {
    const [from, to] = h.value.split(' → ');
    const k = h.value;
    const cur = map.get(k) ?? { from, to, count: 0, alertId: null, lastAt: h.at };
    cur.count += 1;
    if (!cur.alertId && h.alertId) cur.alertId = h.alertId;
    if (h.at > cur.lastAt) cur.lastAt = h.at;
    map.set(k, cur);
  }
  return [...map.values()].sort((a, b) => b.count - a.count);
}

/* ───────── 近期状态变化记录（数据同步收到的逐条变化） ───────── */
export interface LcChange {
  id: string; at: string; objId: string; objLabel: string; from: string; to: string;
  /** 命中的迁移；null = 非法迁移 */
  trId: string | null; by: string; alertId: string | null;
}
const pad = (n: number) => String(n).padStart(2, '0');
const CHANGE_PREFIX: Record<string, string> = { order: 'ORD', store: 'STO', product: 'SKU', member: 'MEM', promotion: 'PRM', refund: 'RFD' };
const CHANGE_CACHE = new Map<string, LcChange[]>();
export function changesOf(objectId: string): LcChange[] {
  if (CHANGE_CACHE.has(objectId)) return CHANGE_CACHE.get(objectId)!;
  const lc = lifecycleOf(objectId);
  if (!lc) return [];
  const r = seeded(objectId.split('').reduce((h, c) => h * 31 + c.charCodeAt(0), 11) % 100000);
  const legal = lc.transitions.filter((t) => t.from !== t.to);
  const out: LcChange[] = [];
  const pick = <T,>(arr: T[]) => arr[Math.floor(r() * arr.length)];
  if (objectId === 'order') {
    // 订单：按订单当前状态推出最近一次合法迁移
    for (const o of ORDERS.slice(0, 64)) {
      const t = legal.find((x) => x.to === o.status);
      if (!t) continue;
      const at = new Date(new Date(o.createdAt.replace(' ', 'T') + '+08:00').getTime() + Math.floor(60 + r() * 3000) * 1000);
      const atText = `${at.getFullYear()}-${pad(at.getMonth() + 1)}-${pad(at.getDate())} ${pad(at.getHours())}:${pad(at.getMinutes())}:${pad(at.getSeconds())}`;
      if (atText > '2026-10-25 15:42:18') continue;
      out.push({ id: '', at: atText, objId: o.id, objLabel: o.id, from: t.from, to: t.to, trId: t.id, by: t.by, alertId: null });
    }
    // 非法迁移：SM-ORD 近 7 天命中
    for (const h of hitsOf('SM-ORD').filter((x) => x.at >= SINCE_7D)) {
      const [from, to] = h.value.split(' → ');
      out.push({ id: '', at: h.at, objId: h.objId, objLabel: h.objLabel, from, to, trId: null, by: '数据同步（OMS 订单表）', alertId: h.alertId });
    }
  } else {
    const ids: [string, string][] = objectId === 'store' ? STORES.map((s) => [s.id, s.name])
      : objectId === 'product' ? PRODUCTS.map((p) => [p.sku, `${p.name} ${p.spec}`])
      : Array.from({ length: 40 }, (_, i) => {
        const id = objectId === 'member' ? `M${String(31800000 + Math.floor(r() * 99999)).padStart(8, '0')}` : objectId === 'promotion' ? `PROMO-2026-${String(100 + i * 3).padStart(4, '0')}` : `RF2026102${Math.floor(r() * 5)}${String(Math.floor(r() * 99999)).padStart(5, '0')}`;
        return [id, id];
      });
    const n = objectId === 'store' ? 10 : 42;
    for (let i = 0; i < n; i++) {
      const t = pick(legal.filter((x) => x.weekly > 0).length ? legal.filter((x) => x.weekly > 0) : legal);
      const [id, label] = pick(ids);
      const day = 25 - Math.floor((i / n) * 14);
      const hour = objectId === 'member' && t.trigger === '系统' ? 2 : day === 25 ? 2 + Math.floor(r() * 13) : Math.floor(r() * 24);
      const at = `2026-10-${pad(day)} ${pad(hour)}:${pad(Math.floor(r() * 60))}:${pad(Math.floor(r() * 60))}`;
      out.push({ id: '', at, objId: id, objLabel: label, from: t.from, to: t.to, trId: t.id, by: t.by, alertId: null });
    }
  }
  out.sort((a, b) => b.at.localeCompare(a.at)).forEach((c, i) => { c.id = `SC-${CHANGE_PREFIX[objectId] ?? 'OBJ'}-${String(out.length - i).padStart(4, '0')}`; });
  CHANGE_CACHE.set(objectId, out);
  return out;
}

/**
 * 某次本体发布时生效的生命周期（查看历史版本 ?ver= 时用）：撤掉该发布之后的版本改动。
 * at = 发布时间；null 表示当前版本。
 */
export function lifecycleAsOf(objectId: string, at: string | null): Lifecycle | undefined {
  const lc = lifecycleOf(objectId);
  if (!lc || !at) return lc;
  const newer = lc.versions.filter((v) => v.at > at);
  if (!newer.length) return lc;
  let states = lc.states.map((s) => ({ ...s }));
  let transitions = lc.transitions.map((t) => ({ ...t }));
  for (const v of newer) {
    const rm = v.undo?.removeTransitions ?? [];
    transitions = transitions.filter((t) => !rm.includes(t.id));
    const rn = v.undo?.rename ?? {};
    states = states.map((s) => (rn[s.key] ? { ...s, key: rn[s.key], name: rn[s.key] } : s));
    transitions = transitions.map((t) => ({ ...t, from: rn[t.from] ?? t.from, to: rn[t.to] ?? t.to }));
  }
  const eff = lc.versions.find((v) => v.at <= at);
  return { ...lc, version: eff?.ver ?? lc.version, updatedAt: eff?.at ?? lc.updatedAt, states, transitions,
    versions: lc.versions.filter((v) => v.at <= at).map((v) => ({ ...v, status: v.ver === eff?.ver ? '生效中' : '已替代' })) };
}
