/** 指标口径页的示例数据（原在 pages/ontology/Metrics.vue 页内）。列表含 15 个指标；定义、维度、版本、引用只给“销售额（不含税）”写全，所有指标共用。 */
import { RULES } from './dynamic';

export interface MetricListItem {
  id: string; name: string; api: string; group: string; unit: string; version: string;
  status: '已发布' | '草稿' | '已下线'; owner: string;
  /** 已提交审核、尚未发布的变更单（不是草稿，不能在这里编辑） */
  review?: { cr: string; note: string };
}
export const METRIC_LIST: MetricListItem[] = [
  { id: 'gmv', name: '销售额（不含税）', api: 'sales_amount_net', group: '销售', unit: '元', version: 'v3', status: '已发布', owner: '陈立军' },
  { id: 'gmv_tax', name: '销售额（含税）', api: 'sales_amount_gross', group: '销售', unit: '元', version: 'v2', status: '已发布', owner: '陈立军' },
  { id: 'repeat_sales', name: '复购客户销售额（不含税）', api: 'repeat_customer_sales_net', group: '销售', unit: '元', version: 'v3', status: '已发布', owner: '刘婷' },
  { id: 'repeat_sales_tax', name: '复购客户销售额（含税）', api: 'repeat_customer_sales_gross', group: '销售', unit: '元', version: 'v1', status: '已发布', owner: '刘婷' },
  { id: 'order_cnt', name: '有效订单数', api: 'valid_order_count', group: '销售', unit: '单', version: 'v1', status: '已发布', owner: '陈立军' },
  { id: 'atv', name: '客单价（不含税）', api: 'avg_ticket_value', group: '销售', unit: '元', version: 'v1', status: '草稿', owner: '刘婷' },
  { id: 'refund_rate', name: '退款率', api: 'refund_rate', group: '销售', unit: '%', version: 'v2', status: '已发布', owner: '刘婷', review: { cr: 'CR-0010', note: '审核中：v2 → v3，改按退款完成时间统计' } },
  { id: 'gross_margin', name: '毛利率', api: 'gross_margin_rate', group: '利润', unit: '%', version: 'v2', status: '已发布', owner: '刘婷' },
  { id: 'gross_profit', name: '毛利额', api: 'gross_profit', group: '利润', unit: '元', version: 'v2', status: '已发布', owner: '刘婷' },
  { id: 'ship_ontime', name: '出库及时率', api: 'ship_ontime_rate', group: '履约', unit: '%', version: 'v1', status: '已发布', owner: '周晓峰' },
  { id: 'ship_avg', name: '平均出库时长', api: 'avg_ship_minutes', group: '履约', unit: '分钟', version: 'v1', status: '已发布', owner: '周晓峰' },
  { id: 'stockout', name: '缺货率', api: 'stockout_rate', group: '库存', unit: '%', version: 'v1', status: '已发布', owner: '周晓峰' },
  { id: 'turnover', name: '库存周转天数', api: 'inventory_turnover_days', group: '库存', unit: '天', version: 'v1', status: '已下线', owner: '周晓峰' },
  { id: 'new_member', name: '新增会员数', api: 'new_member_count', group: '会员', unit: '人', version: 'v1', status: '已发布', owner: '吴佳' },
  { id: 'repurchase', name: '复购率', api: 'repurchase_rate', group: '会员', unit: '%', version: 'v2', status: '已发布', owner: '吴佳' },
];

// 定义（示例只给“销售额（不含税）”写全）
export const METRIC_DEF = [
  { k: '基础对象', v: '订单行（SalesOrderLine）' },
  { k: '时间字段', v: '订单行.出库时间' },
  { k: '聚合方式', v: '求和（可加）' },
  { k: '默认过滤', v: '订单.状态 = 已出库；门店.是否测试店 = 否' },
  { k: '同义词', v: '营业额、销售收入、流水、净销售额' },
  { k: '同名默认', v: '是：用户只说“销售额”时按本指标回答（含税版本不设同名默认）' },
  { k: '负责人', v: '陈立军 · 财务数据组' },
  { k: '生效版本', v: 'v3 · 随本体发布 v2026.10.3 生效' },
  { k: '数据截至', v: '2026-10-24 23:00' },
];

export const METRIC_DIMS = [
  { dim: '时间', hier: '年 › 季 › 月 › 周 › 日', obj: '订单行.出库时间', def: '月', note: '' },
  { dim: '组织', hier: '大区 › 门店', obj: '门店.所属大区', def: '大区', note: '门店直属考核大区（CR-0005），按出库日的归属计算' },
  { dim: '商品', hier: '大类 › 中类 › 小类 › SKU', obj: '商品', def: '大类', note: '' },
  { dim: '渠道', hier: '渠道', obj: '订单.渠道', def: '', note: '字典 9 项，启用 6 个' },
  { dim: '会员', hier: '会员等级 › 会员', obj: '会员', def: '', note: '非会员订单归入“散客”' },
  { dim: '仓库', hier: '仓库', obj: '订单行.发货仓', def: '', note: '' },
  { dim: '省份', hier: '省份 › 城市', obj: '门店.所在省份', def: '', note: '行政区划，经“所在省份”关系；与考核大区不同时需指明' },
];

export const METRIC_VERSIONS = [
  { ver: 'v3', release: 'v2026.10.3', status: '生效中', by: '陈立军', at: '2026-10-24 21:17', change: '退款改为按退款行不含税金额扣减；默认过滤增加“非测试门店”' },
  { ver: 'v2', release: 'v2026.08.1', status: '已替代', by: '陈立军', at: '2026-08-05 19:42', change: '时间字段由下单时间改为出库时间，与财务月结一致' },
  { ver: 'v1', release: 'v2026.03.2', status: '已替代', by: '刘婷', at: '2026-03-18 10:06', change: '首次发布' },
];
export const METRIC_REFS = [
  { type: '指标', name: '客单价', note: '分子', to: { path: '/build/ontology/metrics', query: { metric: 'atv' } } },
  { type: '指标', name: '毛利率', note: '分母', to: { path: '/build/ontology/metrics', query: { metric: 'gross_margin' } } },
  { type: '指标', name: '复购客户销售额', note: '基础口径，加会员过滤', to: { path: '/build/ontology/metrics', query: { metric: 'repeat_sales' } } },
  ...Object.values(RULES).filter((r) => r.metric === 'gmv').map((r) => ({ type: '规则', name: `${r.id} ${r.name}`, note: `条件：${r.condition}；口径升版后需回放试跑`, to: { path: '/build/ontology/rules', query: { rule: r.id } } })),
  { type: '看板', name: '华东大区经营日报', note: '4 个卡片' },
  { type: '看板', name: '门店经营月报', note: '2 个卡片' },
  { type: '认证问答', name: '上月各大区销售额', note: '已认证' },
];
