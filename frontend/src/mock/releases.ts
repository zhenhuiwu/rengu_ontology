/** 本体发布记录、变更草稿（CR）、站内通知。 */
import { ORDERS } from './orders';

export interface Release { ver: string; publishedAt: string; by: string; note: string }
/** 第一条是当前版本，和 app store 的 ontologyVersion 一致 */
export const RELEASES: Release[] = [
  { ver: 'v2026.10.3', publishedAt: '2026-10-24 21:17', by: '王敏', note: '销售额改为不含税口径；新增 gmv_tax' },
  { ver: 'v2026.10.2', publishedAt: '2026-10-17 20:45', by: '陈立军', note: '门店新增“大区”关系；告警规则 R-021' },
  { ver: 'v2026.10.1', publishedAt: '2026-10-09 22:03', by: '王敏', note: '订单状态“已发货”统一为“已出库”' },
  { ver: 'v2026.09.4', publishedAt: '2026-09-28 21:30', by: '刘婷', note: '商品新增保质期属性' },
  { ver: 'v2026.09.3', publishedAt: '2026-09-19 19:12', by: '孙浩然', note: '复购客户定义改为 90 天内 ≥2 单' },
];
export const releaseByVer = (v: string) => RELEASES.find((r) => r.ver === v);

/** 变更草稿（CR）：ontology/parts/useDraftView 在用 */
export interface DraftCR { id: string; title: string; owner: string; updatedAt: string; changes: number }
export const DRAFTS: DraftCR[] = [
  { id: 'CR-0014', title: '调整大区预算：超 5% 增加二级审批', owner: '刘婷', updatedAt: '2026-10-25 11:26', changes: 3 },
  { id: 'CR-0013', title: '门店价格变更：增加促销价字段', owner: '王敏', updatedAt: '2026-10-24 17:52', changes: 2 },
  { id: 'CR-0012', title: '会员对象拆分“储值卡”', owner: '王敏', updatedAt: '2026-10-25 15:02', changes: 7 },
  { id: 'CR-0011', title: '毛利率口径扣除促销补贴', owner: '何静', updatedAt: '2026-10-24 18:40', changes: 6 },
  { id: 'CR-0009', title: '门店新增“业态”枚举值“前置仓”', owner: '陈立军', updatedAt: '2026-10-21 10:26', changes: 1 },
];

export type TargetKind = 'object' | 'metric' | 'action' | 'event' | 'rule' | 'link' | 'dimension' | 'knowledge';
/** 每张草稿改了哪些对象 / 指标 / 动作类型 / 事件类型 / 关系 / 业务知识（示例数据；ontology/parts/useDraftView 复制一份再用） */
export const DRAFT_TARGETS: Record<string, Partial<Record<TargetKind, string[]>>> = {
  'CR-0014': { action: ['adjust_region_budget'] },
  'CR-0013': { event: ['store_price_changed'] },
  'CR-0012': { object: ['member'], link: ['member_holds_cards'] },
  'CR-0011': { metric: ['gross_margin'], rule: ['R-007'], knowledge: ['K-018', 'K-024'] },
  'CR-0009': { object: ['store'], dimension: ['format'] },
};

export interface Notice { id: string; kind: string; objId: string; title: string; desc: string; time: string; read: boolean }
export const NOTICES: Notice[] = [
  { id: 'N-0107', kind: 'region', objId: 'REG-EC', title: '华东大区进入预警', desc: '复购客户销售额环比 -8.4%', time: '15:41', read: false },
  { id: 'N-0106', kind: 'order', objId: ORDERS[3].id, title: '订单状态非法迁移', desc: '已签收 → 待支付', time: '15:41', read: false },
  { id: 'N-0105', kind: 'product', objId: '1011846', title: '库存低于安全线', desc: '伊利纯牛奶 250ml×16盒 可用 37 提', time: '14:22', read: false },
  { id: 'N-0104', kind: 'metric', objId: 'gmv', title: '指标口径已变更', desc: '销售额改为不含税（v2026.10.3）', time: '昨天 21:17', read: false },
  { id: 'N-0103', kind: 'store', objId: 'HZ0058', title: '门店销售下降', desc: '杭州滨江星光大道店 环比 −21.6%', time: '10-20 09:00', read: false },
  { id: 'N-0102', kind: 'order', objId: ORDERS[8].id, title: '出库超时', desc: '出库 53.4 小时，阈值 48 小时', time: '10-20 08:10', read: false },
  { id: 'N-0101', kind: 'store', objId: 'NJ0076', title: '告警已解决', desc: '南京江宁百家湖店 客流恢复', time: '10-19 17:32', read: false },
];
