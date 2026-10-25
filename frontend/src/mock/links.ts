/**
 * 关系类型（静态本体负责人）。对象类型页“关系”页签和关系类型页共用这一份数据。
 * 字段对齐《静态本体-后台表结构与接口定义》onto.link_type：api_name / display_name / from / to / cardinality /
 * from_property / to_property / via_object_type / description_nl。
 * 状态与 changes.ts 一致：CR-0005“所属大区”已发布（v2026.10.2）；CR-0006“店长”已驳回（不存在已发布的店长关系）；
 * CR-0012“持有”（会员 → 储值卡）在草稿里。其余关系随首次发布 v2026.03.2 上线（启用变更单之前）。
 */

export interface LinkObject { id: string; name: string; api: string; group: string; table: string; draft?: boolean }
/** 与对象类型页 TYPES 的 id / 名称 / API 名一致；储值卡只在 CR-0012 草稿里 */
export const LINK_OBJECTS: LinkObject[] = [
  { id: 'order', name: '订单', api: 'SalesOrder', group: '交易', table: 'ERP.T_SALE_ORDER' },
  { id: 'order_line', name: '订单行', api: 'SalesOrderLine', group: '交易', table: 'ERP.T_SALE_ORDER_LINE' },
  { id: 'refund', name: '退款单', api: 'Refund', group: '交易', table: 'ERP.T_REFUND' },
  { id: 'refund_line', name: '退款行', api: 'RefundLine', group: '交易', table: 'ERP.T_REFUND_LINE' },
  { id: 'member', name: '会员', api: 'Member', group: '客户', table: 'CRM.T_MEMBER' },
  { id: 'member_level', name: '会员等级', api: 'MemberLevel', group: '客户', table: 'CRM.T_MEMBER_LEVEL' },
  { id: 'stored_card', name: '储值卡', api: 'StoredValueCard', group: '客户', table: 'CRM.T_SVC_CARD', draft: true },
  { id: 'store', name: '门店', api: 'Store', group: '组织', table: 'ORG.T_STORE' },
  { id: 'region', name: '大区', api: 'Region', group: '组织', table: 'ORG.T_REGION' },
  { id: 'province', name: '省份', api: 'Province', group: '组织', table: 'ORG.T_PROVINCE' },
  { id: 'employee', name: '员工', api: 'Employee', group: '组织', table: 'HR_CORE.T_EMPLOYEE' },
  { id: 'product', name: '商品', api: 'Product', group: '商品', table: 'MDM.T_SKU' },
  { id: 'category', name: '品类', api: 'Category', group: '商品', table: 'MDM.T_CATEGORY' },
  { id: 'promotion', name: '促销活动', api: 'Promotion', group: '商品', table: 'PROMO.T_ACTIVITY' },
  { id: 'warehouse', name: '仓库', api: 'Warehouse', group: '供应链', table: 'WMS.T_WAREHOUSE' },
  { id: 'inventory', name: '库存快照', api: 'InventorySnapshot', group: '供应链', table: 'WMS.T_STOCK_SNAPSHOT' },
  { id: 'supplier', name: '供应商', api: 'Supplier', group: '供应链', table: 'NC.T_SUPPLIER' },
  { id: 'shipment', name: '出库单', api: 'Shipment', group: '供应链', table: 'TMS.T_SHIPMENT' },
];
export const objById = (id: string) => LINK_OBJECTS.find((o) => o.id === id);
export const objName = (id: string) => objById(id)?.name ?? id;

export type LinkStatus = '已发布' | '草稿' | '已驳回';
export type Cardinality = '1 : 1' | '1 : N' | 'N : 1' | 'N : M';
export const CARD_API: Record<Cardinality, string> = { '1 : 1': 'one_to_one', '1 : N': 'one_to_many', 'N : 1': 'many_to_one', 'N : M': 'many_to_many' };

export interface LinkQuality {
  /** 关联命中率：源端非空外键能在目标端找到记录的比例 */
  hit: number; orphans: number; nulls: number; checkedAt: string;
  /** 一对多 / 多对一方向上，一条源记录匹配到多条目标的条数（应为 0） */
  dup: number; note?: string;
  orphanSamples?: { key: string; count: number; reason: string }[];
}
export interface LinkUsage { type: '指标' | '认证问答' | '视图' | '规则' | '评测题'; name: string; note: string; hover?: { kind: string; id: string } }
export interface LinkHistory { ver: string; cr?: string; status: '生效中' | '已替代' | '草稿' | '已驳回'; by: string; at: string; change: string }
export interface LinkType {
  id: string; name: string; from: string; to: string; card: Cardinality;
  /** 新建草稿时可改；已发布关系 API 名即 id */
  api?: string;
  /** 与对象类型页“关联条件”列完全一致 */
  key: string; fromProp: string; toProp: string; via?: string;
  /** 物理 JOIN（源库列名） */
  join: string;
  reverse: string; nullable: boolean; desc: string; status: LinkStatus;
  /** 首次随哪个版本发布；未发布为空 */
  since?: string; cr?: string; owner: string;
  quality?: LinkQuality; usage: LinkUsage[]; history: LinkHistory[];
}

const BASE = (by: string, change = '首次发布'): LinkHistory => ({ ver: 'v2026.03.2', status: '生效中', by, at: '2026-03-18 10:06', change });
const Q = (hit: number, orphans: number, nulls = 0, more: Partial<LinkQuality> = {}): LinkQuality => ({ hit, orphans, nulls, dup: 0, checkedAt: '2026-10-25 06:00', ...more });
const M = (id: string, name: string, note: string): LinkUsage => ({ type: '指标', name, note, hover: { kind: 'metric', id } });
const R = (id: string, name: string, note: string): LinkUsage => ({ type: '规则', name: `${id} ${name}`, note, hover: { kind: 'rule', id } });

export const LINKS: LinkType[] = [
  // —— 订单（与对象类型页的 8 条完全一致）——
  { id: 'order_has_lines', name: '包含订单行', from: 'order', to: 'order_line', card: '1 : N', key: 'order_id = order_line.order_id', fromProp: 'order_id', toProp: 'order_id',
    join: 'T_SALE_ORDER.order_id = T_SALE_ORDER_LINE.order_id', reverse: '所属订单', nullable: false, status: '已发布', since: 'v2026.03.2', owner: '陈立军',
    desc: '一张订单包含一到多行商品明细；金额类指标在订单行上汇总。',
    quality: Q(100, 0),
    usage: [M('gmv', '销售额（不含税）', '基础对象为订单行，经本关系取订单状态过滤'), M('gross_margin', '毛利率', '经订单行汇总成本'), { type: '认证问答', name: '上月各大区销售额', note: '订单行 → 订单 → 门店 → 大区' }, { type: '视图', name: '门店经营月报', note: '2 个卡片' }, { type: '评测题', name: '销售额相关 9 题', note: '每次发布回放' }],
    history: [BASE('陈立军')] },
  { id: 'order_belongs_to_store', name: '下单门店', from: 'order', to: 'store', card: 'N : 1', key: 'store_id = store.store_id', fromProp: 'store_id', toProp: 'store_id',
    join: 'T_SALE_ORDER.store_id = T_STORE.store_id', reverse: '门店订单', nullable: false, status: '已发布', since: 'v2026.03.2', owner: '陈立军',
    desc: '顾客下单所在的门店；线上订单为下单时选择的门店。问“各门店订单数、销售额”默认按本关系。',
    quality: Q(100, 0),
    usage: [M('order_cnt', '有效订单数', '按门店、大区拆分时的默认路径'), M('gmv', '销售额（不含税）', '组织维度默认路径'), M('atv', '客单价（不含税）', '按门店拆分'), R('R-021', '门店销售额环比下降', '按下单门店汇总'), { type: '认证问答', name: '上个月各大区的有效订单数是多少？', note: '已认证' }, { type: '视图', name: '华东大区经营日报', note: '4 个卡片' }, { type: '评测题', name: '门店相关 12 题', note: '每次发布回放' }],
    history: [BASE('陈立军')] },
  { id: 'order_fulfilled_by_store', name: '履约门店', from: 'order', to: 'store', card: 'N : 1', key: 'fulfill_store_id = store.store_id', fromProp: 'fulfill_store_id', toProp: 'store_id',
    join: 'T_SALE_ORDER.fulfill_store_id = T_STORE.store_id', reverse: '履约订单', nullable: true, status: '已发布', since: 'v2026.03.2', owner: '周晓峰',
    desc: '实际出库发货的门店；到店自提时与下单门店相同。问数中需用户指明“履约门店”或“发货门店”。',
    quality: Q(98.73, 16284, 402115, {
      note: '孤儿记录集中在 6 家前置仓：门店主数据还没有“前置仓”业态（见 CR-0009 草稿）',
      orphanSamples: [
        { key: 'QZ0001', count: 4912, reason: '前置仓·上海闵行莘庄，门店主数据未建档' },
        { key: 'QZ0002', count: 3876, reason: '前置仓·上海浦东金桥，门店主数据未建档' },
        { key: 'QZ0004', count: 2731, reason: '前置仓·杭州西湖文三，门店主数据未建档' },
        { key: '（其余 3 个编码）', count: 4765, reason: '同上' },
      ] }),
    usage: [M('ship_ontime', '出库及时率', '按履约门店拆分（显式指定路径）'), { type: '认证问答', name: '9 月各履约门店的出库及时率', note: '已认证，问题里写明“履约门店”' }, { type: '视图', name: '履约时效看板', note: '1 个卡片' }],
    history: [BASE('周晓峰', '首次发布；问数路径设为需用户指明')] },
  { id: 'order_placed_by_member', name: '下单会员', from: 'order', to: 'member', card: 'N : 1', key: 'member_id = member.member_id', fromProp: 'member_id', toProp: 'member_id',
    join: 'T_SALE_ORDER.member_id = T_MEMBER.member_id', reverse: '会员订单', nullable: true, status: '已发布', since: 'v2026.03.2', owner: '刘婷',
    desc: '下单的会员；散客订单为空，统计时归入“散客”。',
    quality: Q(99.82, 2315, 318407, { note: '孤儿记录为已注销会员的历史订单，会员中心不保留注销会员', orphanSamples: [{ key: '（注销会员 1,906 个）', count: 2315, reason: '会员注销后主数据删除' }] }),
    usage: [M('gmv', '销售额（不含税）', '会员维度'), { type: '指标', name: '复购率', note: '按会员统计 90 天内订单数' }, { type: '指标', name: '复购客户销售额（不含税）', note: '会员过滤' }, { type: '认证问答', name: '上月复购客户销售额', note: '已认证' }, { type: '评测题', name: '复购相关 7 题', note: '每次发布回放' }],
    history: [BASE('刘婷')] },
  { id: 'order_ships_from_warehouse', name: '发货仓', from: 'order', to: 'warehouse', card: 'N : 1', key: 'warehouse_id = warehouse.wh_id', fromProp: 'warehouse_id', toProp: 'wh_id',
    join: 'T_SALE_ORDER.wh_code = T_WAREHOUSE.wh_code', reverse: '仓库订单', nullable: false, status: '已发布', since: 'v2026.03.2', owner: '孙浩然',
    desc: '下单时分配的发货仓。问“各仓订单数”默认按本关系；按实际出库仓统计需指明。',
    quality: Q(100, 0),
    usage: [M('ship_ontime', '出库及时率', '按仓库拆分的默认路径'), { type: '指标', name: '平均出库时长', note: '按仓库拆分' }, { type: '视图', name: '仓配日报', note: '3 个卡片' }],
    history: [BASE('孙浩然')] },
  { id: 'order_has_refunds', name: '退款单', from: 'order', to: 'refund', card: '1 : N', key: 'order_id = refund.order_id', fromProp: 'order_id', toProp: 'order_id',
    join: 'T_SALE_ORDER.order_id = T_REFUND.order_id', reverse: '原订单', nullable: false, status: '已发布', since: 'v2026.03.2', owner: '刘婷',
    desc: '一张订单可以发生多次退款。',
    quality: Q(99.97, 11, 0, { note: '11 张退款单的原订单在 2025 年归档库，未同步', orphanSamples: [{ key: '（2025 年归档订单）', count: 11, reason: '原订单在归档库' }] }),
    usage: [{ type: '指标', name: '退款率', note: '分子按退款单计数（CR-0010 审核中，改按退款完成时间）' }, { type: '认证问答', name: '上周退款率最高的门店', note: '已认证' }],
    history: [BASE('刘婷')] },
  { id: 'order_has_shipments', name: '出库单', from: 'order', to: 'shipment', card: '1 : N', key: 'order_id = shipment.order_id', fromProp: 'order_id', toProp: 'order_id',
    join: 'T_SALE_ORDER.order_id = T_SHIPMENT.order_id', reverse: '来源订单', nullable: false, status: '已发布', since: 'v2026.03.2', owner: '孙浩然',
    desc: '一张订单拆单时有多张出库单。',
    quality: Q(100, 0, 0, { dup: 0 }),
    usage: [M('ship_ontime', '出库及时率', '取首张出库单的出库时间'), R('R-015', '出库超 48 小时未签收', '经出库单取运单状态')],
    history: [BASE('孙浩然')] },
  { id: 'order_in_promotions', name: '参与活动', from: 'order', to: 'promotion', card: 'N : M', key: '经 order_promotion 中间表', fromProp: 'order_id', toProp: 'promo_id', via: 'order_promotion',
    join: 'T_SALE_ORDER.order_id = T_ORDER_PROMO.order_id AND T_ORDER_PROMO.promo_id = T_ACTIVITY.promo_id', reverse: '活动订单', nullable: true, status: '已发布', since: 'v2026.03.2', owner: '刘婷',
    desc: '一单可参加多个活动，金额不可加；按活动拆分销售额会重复计算，请用“活动让利额”。',
    quality: Q(99.41, 1208, 903518, { dup: 0, note: '1,208 条中间表记录指向已删除的草稿活动' }),
    usage: [{ type: '指标', name: '活动让利额', note: '按活动拆分' }, { type: '认证问答', name: '国庆活动带来多少订单', note: '已认证' }],
    history: [BASE('刘婷')] },

  // —— 交易明细 ——
  { id: 'order_line_product', name: '订单行商品', from: 'order_line', to: 'product', card: 'N : 1', key: 'sku = product.sku', fromProp: 'sku', toProp: 'sku',
    join: 'T_SALE_ORDER_LINE.sku_code = T_SKU.sku_code', reverse: '销售明细', nullable: false, status: '已发布', since: 'v2026.03.2', owner: '王敏',
    desc: '订单行售出的商品。', quality: Q(99.996, 207, 0, { note: '207 行为临时编码（门店手工录入的散称商品）' }),
    usage: [M('gmv', '销售额（不含税）', '商品维度'), M('gross_margin', '毛利率', '商品、品类维度'), R('R-007', '商品毛利率越线', '按商品汇总')],
    history: [BASE('王敏')] },
  { id: 'refund_has_lines', name: '包含退款行', from: 'refund', to: 'refund_line', card: '1 : N', key: 'refund_id = refund_line.refund_id', fromProp: 'refund_id', toProp: 'refund_id',
    join: 'T_REFUND.refund_id = T_REFUND_LINE.refund_id', reverse: '所属退款单', nullable: false, status: '已发布', since: 'v2026.03.2', owner: '刘婷',
    desc: '一张退款单包含一到多行退款明细。', quality: Q(100, 0),
    usage: [M('gmv', '销售额（不含税）', '扣减退款行不含税金额')], history: [BASE('刘婷')] },
  { id: 'refund_line_of_order_line', name: '原订单行', from: 'refund_line', to: 'order_line', card: 'N : 1', key: 'order_line_id = order_line.line_id', fromProp: 'order_line_id', toProp: 'line_id',
    join: 'T_REFUND_LINE.src_line_id = T_SALE_ORDER_LINE.line_id', reverse: '退款明细', nullable: false, status: '已发布', since: 'v2026.03.2', owner: '刘婷',
    desc: '退款行对应的原订单行，用于按商品扣减销售额。', quality: Q(99.95, 26, 0, { note: '26 行的原订单行在归档库' }),
    usage: [M('gmv', '销售额（不含税）', 'v3 起按退款行不含税金额扣减（CR-0007）'), M('gross_margin', '毛利率', '退货冲减成本')],
    history: [BASE('刘婷')] },

  // —— 客户 ——
  { id: 'member_has_level', name: '会员等级', from: 'member', to: 'member_level', card: 'N : 1', key: 'level_code = member_level.level_code', fromProp: 'level_code', toProp: 'level_code',
    join: 'T_MEMBER.level_code = T_MEMBER_LEVEL.level_code', reverse: '等级会员', nullable: false, status: '已发布', since: 'v2026.03.2', owner: '吴佳',
    desc: '会员当前所在等级。CR-0001 新增“黑金”已驳回，等级仍为 5 个。', quality: Q(100, 0),
    usage: [M('gmv', '销售额（不含税）', '会员等级维度'), { type: '指标', name: '新增会员数', note: '按等级拆分' }],
    history: [BASE('吴佳')] },
  { id: 'member_registered_at_store', name: '注册门店', from: 'member', to: 'store', card: 'N : 1', key: 'reg_store_id = store.store_id', fromProp: 'reg_store_id', toProp: 'store_id',
    join: 'T_MEMBER.reg_store_id = T_STORE.store_id', reverse: '注册会员', nullable: true, status: '已发布', since: 'v2026.03.2', owner: '吴佳',
    desc: '会员开卡注册的门店；线上注册为空。', quality: Q(99.63, 2964, 211840, { note: '2,964 个会员的注册门店已闭店' }),
    usage: [{ type: '指标', name: '新增会员数', note: '按门店拆分的默认路径' }, { type: '认证问答', name: '各门店本月新增会员数', note: '已认证' }],
    history: [BASE('吴佳')] },
  { id: 'member_holds_cards', name: '持有', from: 'member', to: 'stored_card', card: '1 : N', key: 'member_id = stored_card.member_id', fromProp: 'member_id', toProp: 'member_id',
    join: 'T_MEMBER.member_id = T_SVC_CARD.member_id', reverse: '持卡会员', nullable: true, status: '草稿', cr: 'CR-0012', owner: '王敏',
    desc: '会员名下的储值卡，一个会员可持有多张。储值余额改由储值卡汇总。',
    quality: Q(99.88, 412, 0, { checkedAt: '2026-10-25 15:02', note: '测试环境抽检：412 张卡的会员已注销（迁移方式待补，见 CR-0012 V15 警告）' }),
    usage: [{ type: '指标', name: '储值卡余额', note: '新增 v1（同一变更单）' }, { type: '指标', name: '会员储值余额', note: '改为经本关系汇总' }],
    history: [{ ver: '—', cr: 'CR-0012', status: '草稿', by: '王敏', at: '2026-10-25 15:02', change: '新增关系：会员 → 储值卡（1 : N）' }] },

  // —— 组织 ——
  { id: 'store_in_region', name: '所属大区', from: 'store', to: 'region', card: 'N : 1', key: 'region_code = region.region_code', fromProp: 'region_code', toProp: 'region_code',
    join: 'T_STORE.region_code = T_REGION.region_code', reverse: '大区门店', nullable: false, status: '已发布', since: 'v2026.10.2', cr: 'CR-0005', owner: '陈立军',
    desc: '门店按组织调整后的考核大区直接归属，不经过省份。问“各大区”默认按本关系。', quality: Q(100, 0, 0, { note: '486 家门店各属于一个大区（V10 通过）' }),
    usage: [M('gmv', '销售额（不含税）', '大区维度'), M('order_cnt', '有效订单数', '大区维度'), R('R-021', '门店销售额环比下降', '按大区汇总告警'), { type: '视图', name: '华东大区销售周报', note: '随 CR-0005 新增' }, { type: '认证问答', name: '上月各大区销售额', note: '已认证' }, { type: '评测题', name: '大区相关 6 题', note: 'v2026.10.2 发布前回放全部通过' }],
    history: [{ ver: 'v2026.10.2', cr: 'CR-0005', status: '生效中', by: '陈立军', at: '2026-10-17 20:45', change: '新增关系：门店 → 大区（N : 1），问数默认路径' }] },
  { id: 'store_in_province', name: '所在省份', from: 'store', to: 'province', card: 'N : 1', key: 'province_code = province.province_code', fromProp: 'province_code', toProp: 'province_code',
    join: 'T_STORE.province_code = T_PROVINCE.province_code', reverse: '省内门店', nullable: false, status: '已发布', since: 'v2026.03.2', owner: '陈立军',
    desc: '门店所在行政省份。', quality: Q(100, 0),
    usage: [M('gmv', '销售额（不含税）', '省份维度'), { type: '视图', name: '门店经营月报', note: '省份筛选' }],
    history: [BASE('陈立军')] },
  { id: 'province_in_region', name: '省份所属大区', from: 'province', to: 'region', card: 'N : 1', key: 'region_code = region.region_code', fromProp: 'region_code', toProp: 'region_code',
    join: 'T_PROVINCE.region_code = T_REGION.region_code', reverse: '下辖省份', nullable: false, status: '已发布', since: 'v2026.03.2', owner: '陈立军',
    desc: '省份的行政大区归属。组织调整后个别门店的考核大区与所在省份不同，按省份归属统计需指明。', quality: Q(100, 0),
    usage: [{ type: '认证问答', name: '按省份归属统计各大区门店数', note: '已认证，问题里写明“按省份”' }],
    history: [
      { ver: 'v2026.10.2', cr: 'CR-0005', status: '生效中', by: '陈立军', at: '2026-10-17 20:45', change: '关系定义不变；门店 → 大区 改走“所属大区”，本路径改为需用户指明' },
      { ...BASE('陈立军'), status: '已替代' },
    ] },
  { id: 'employee_works_at_store', name: '所属门店', from: 'employee', to: 'store', card: 'N : 1', key: 'store_id = store.store_id', fromProp: 'store_id', toProp: 'store_id',
    join: 'T_EMPLOYEE.store_id = T_STORE.store_id', reverse: '门店员工', nullable: true, status: '已发布', since: 'v2026.03.2', owner: '王敏',
    desc: '员工任职的门店；总部和仓库员工为空。员工为敏感对象，只有人事和门店运营可见。', quality: Q(99.71, 21, 1430, { note: '21 名员工的门店已闭店，人事未调岗' }),
    usage: [{ type: '视图', name: '门店人效看板', note: '仅人事、门店运营可见' }],
    history: [BASE('王敏')] },
  { id: 'store_manager', name: '店长', from: 'store', to: 'employee', card: 'N : 1', key: 'manager_emp_no = employee.emp_no', fromProp: 'manager_emp_no', toProp: 'emp_no',
    join: 'T_STORE_MGR.manager_emp_no = T_EMPLOYEE.emp_no', reverse: '管理门店', nullable: true, status: '已驳回', cr: 'CR-0006', owner: '孙浩然',
    desc: '门店的店长。CR-0006 已驳回：员工是敏感对象，需先在权限里定好店长信息的可见范围再提。',
    usage: [],
    history: [{ ver: '—', cr: 'CR-0006', status: '已驳回', by: '孙浩然', at: '2026-10-22 14:48', change: '新增关系：门店 → 员工（店长）；王敏驳回：请先定好店长信息的可见范围' }] },
  { id: 'store_replenished_from', name: '补货仓', from: 'store', to: 'warehouse', card: 'N : 1', key: 'replenish_wh_id = warehouse.wh_id', fromProp: 'replenish_wh_id', toProp: 'wh_id',
    join: 'T_STORE.replenish_wh_code = T_WAREHOUSE.wh_code', reverse: '配送门店', nullable: false, status: '已发布', since: 'v2026.03.2', owner: '孙浩然',
    desc: '给门店补货的配送仓。', quality: Q(100, 0),
    usage: [{ type: '指标', name: '缺货率', note: '按补货仓拆分' }, R('R-012', '可用库存低于安全线', '通知补货仓')],
    history: [BASE('孙浩然')] },

  // —— 商品 ——
  { id: 'product_in_category', name: '所属品类', from: 'product', to: 'category', card: 'N : 1', key: 'category_id = category.category_id', fromProp: 'category_id', toProp: 'category_id',
    join: 'T_SKU.category_code = T_CATEGORY.category_code', reverse: '品类商品', nullable: false, status: '已发布', since: 'v2026.03.2', owner: '王敏',
    desc: '商品挂在最末级品类（小类）上。', quality: Q(100, 0),
    usage: [M('gmv', '销售额（不含税）', '商品维度：大类 › 中类 › 小类'), M('gross_margin', '毛利率', '品类维度'), R('R-007', '商品毛利率越线', '按品类阈值')],
    history: [BASE('王敏')] },
  { id: 'category_parent', name: '上级品类', from: 'category', to: 'category', card: 'N : 1', key: 'parent_id = category.category_id', fromProp: 'parent_id', toProp: 'category_id',
    join: 'T_CATEGORY.parent_code = T_CATEGORY.category_code', reverse: '下级品类', nullable: true, status: '已发布', since: 'v2026.03.2', owner: '王敏',
    desc: '品类三级层级（大类 › 中类 › 小类）；大类的上级为空。', quality: Q(100, 0, 24, { note: '24 个大类无上级，符合预期' }),
    usage: [{ type: '视图', name: '品类经营分析', note: '层级下钻' }], history: [BASE('王敏')] },
  { id: 'product_main_supplier', name: '主供应商', from: 'product', to: 'supplier', card: 'N : 1', key: 'main_supplier_id = supplier.supplier_id', fromProp: 'main_supplier_id', toProp: 'supplier_id',
    join: 'T_SKU.main_vendor_code = T_SUPPLIER.supplier_code', reverse: '供货商品', nullable: true, status: '已发布', since: 'v2026.03.2', owner: '孙浩然',
    desc: '商品的主供应商。销售额不允许按供应商拆分（一个订单行对应多个供应商批次），请用“采购金额”。', quality: Q(97.86, 1031, 3620, { note: '1,031 个商品的主供应商已停用，采购未更新' }),
    usage: [{ type: '指标', name: '采购金额', note: '按供应商拆分' }], history: [BASE('孙浩然')] },
  { id: 'promotion_products', name: '活动商品', from: 'promotion', to: 'product', card: 'N : M', key: '经 promotion_sku 中间表', fromProp: 'promo_id', toProp: 'sku', via: 'promotion_sku',
    join: 'T_ACTIVITY.promo_id = T_PROMO_SKU.promo_id AND T_PROMO_SKU.sku_code = T_SKU.sku_code', reverse: '参与活动', nullable: false, status: '已发布', since: 'v2026.03.2', owner: '刘婷',
    desc: '活动适用的商品范围。', quality: Q(100, 0),
    usage: [{ type: '指标', name: '活动让利额', note: '按商品拆分' }], history: [BASE('刘婷')] },

  // —— 供应链 ——
  { id: 'inventory_of_product', name: '库存商品', from: 'inventory', to: 'product', card: 'N : 1', key: 'sku = product.sku', fromProp: 'sku', toProp: 'sku',
    join: 'T_STOCK_SNAPSHOT.sku_code = T_SKU.sku_code', reverse: '库存快照', nullable: false, status: '已发布', since: 'v2026.03.2', owner: '孙浩然',
    desc: '库存快照对应的商品。', quality: Q(100, 0),
    usage: [{ type: '指标', name: '缺货率', note: '按商品拆分' }, R('R-012', '可用库存低于安全线', '按商品判断')], history: [BASE('孙浩然')] },
  { id: 'inventory_in_warehouse', name: '所在仓库', from: 'inventory', to: 'warehouse', card: 'N : 1', key: 'warehouse_id = warehouse.wh_id', fromProp: 'warehouse_id', toProp: 'wh_id',
    join: 'T_STOCK_SNAPSHOT.wh_code = T_WAREHOUSE.wh_code', reverse: '仓库库存', nullable: false, status: '已发布', since: 'v2026.03.2', owner: '孙浩然',
    desc: '库存快照所在的仓库。', quality: Q(100, 0),
    usage: [{ type: '指标', name: '缺货率', note: '按仓库拆分' }, { type: '视图', name: '仓配日报', note: '库存卡片' }], history: [BASE('孙浩然')] },
  { id: 'shipment_from_warehouse', name: '出库仓', from: 'shipment', to: 'warehouse', card: 'N : 1', key: 'warehouse_id = warehouse.wh_id', fromProp: 'warehouse_id', toProp: 'wh_id',
    join: 'T_SHIPMENT.out_wh_code = T_WAREHOUSE.wh_code', reverse: '仓库出库单', nullable: false, status: '已发布', since: 'v2026.03.2', owner: '孙浩然',
    desc: '出库单实际出库的仓库；调拨时与订单的发货仓不同。', quality: Q(100, 0),
    usage: [{ type: '指标', name: '平均出库时长', note: '按实际出库仓拆分（显式指定路径）' }, { type: '认证问答', name: '各仓实际出库单量', note: '已认证，问题里写明“实际出库”' }],
    history: [BASE('孙浩然')] },
];
export const linkById = (id?: string | null) => LINKS.find((l) => l.id === id);

/**
 * 问数路径（V13）：同一对对象之间有多条路径时，必须且只能有一条默认路径，其余需用户在问题里指明。
 * links 按路径顺序列出经过的关系；hint 是用户需要说出的词。
 */
export interface QaPath { links: string[]; isDefault: boolean; hint?: string }
export interface PathExample { q: string; path: number; how: string }
export interface PathGroup { from: string; to: string; paths: QaPath[]; examples: PathExample[]; check: string }
export const PATH_GROUPS: PathGroup[] = [
  { from: 'order', to: 'store', check: 'V13 通过：3 条路径，默认“下单门店”',
    paths: [
      { links: ['order_belongs_to_store'], isDefault: true },
      { links: ['order_fulfilled_by_store'], isDefault: false, hint: '履约门店、发货门店' },
      { links: ['order_placed_by_member', 'member_registered_at_store'], isDefault: false, hint: '会员注册门店、开卡门店' },
    ],
    examples: [
      { q: '上个月各门店的有效订单数是多少？', path: 0, how: '没有指明路径，按默认路径“下单门店”' },
      { q: '上个月各门店下单和履约的订单数对比', path: -1, how: '同时出现“下单”和“履约”，先追问用户按哪条路径统计，不静默走默认路径' },
      { q: '9 月各履约门店的出库及时率', path: 1, how: '命中“履约门店”，走需指明的路径' },
      { q: '哪个门店发货的订单最多？', path: 1, how: '“发货门店”是“履约门店”的同义词' },
      { q: '按会员开卡门店统计本月订单数', path: 2, how: '命中“开卡门店”，经下单会员 → 注册门店；散客订单不计入' },
    ] },
  { from: 'store', to: 'region', check: 'V13 通过：2 条路径，默认“所属大区”（CR-0005）',
    paths: [
      { links: ['store_in_region'], isDefault: true },
      { links: ['store_in_province', 'province_in_region'], isDefault: false, hint: '按省份归属、行政大区' },
    ],
    examples: [
      { q: '上月各大区销售额', path: 0, how: '默认按门店的考核大区，不经过省份' },
      { q: '按省份归属统计各大区门店数', path: 1, how: '命中“按省份归属”，经所在省份 → 省份所属大区' },
    ] },
  { from: 'order', to: 'warehouse', check: 'V13 通过：2 条路径，默认“发货仓”',
    paths: [
      { links: ['order_ships_from_warehouse'], isDefault: true },
      { links: ['order_has_shipments', 'shipment_from_warehouse'], isDefault: false, hint: '实际出库仓' },
    ],
    examples: [
      { q: '各仓本周订单数', path: 0, how: '默认按下单时分配的发货仓' },
      { q: '各仓实际出库的订单数', path: 1, how: '命中“实际出库”，经出库单 → 出库仓；拆单的订单按出库单去重' },
    ] },
];
export const pathGroupsOf = (linkId: string) => PATH_GROUPS.filter((g) => g.paths.some((p) => p.links.includes(linkId)));
/** 关系在对象类型页“问数默认路径”列的取值 */
export const defaultLabel = (linkId: string) => {
  const g = PATH_GROUPS.find((x) => x.paths.some((p) => p.links.length === 1 && p.links[0] === linkId));
  if (!g) return '';
  return g.paths.find((p) => p.links[0] === linkId)!.isDefault ? '默认' : '需用户指明';
};
