/**
 * 对象类型的属性清单（已发布版），按对象类型 id 分开。
 * 主键、外键、源表与 src/mock/links.ts（key / fromProp / toProp / table）一致，主键与 src/mock/quality.ts 的 pk 对应
 * （quality 里写的是源字段名，比如商品主键属性 sku 的源字段是 sku_code）。
 * 草稿差异不在这里写死：对象类型页按草稿单号从 src/mock/changes.ts 的 diffs 里取（见 draftPropDiff）。
 */
import { crDetail } from './changes';

export interface ObjProp { api: string; name: string; type: string; group: string; pk?: boolean; title?: boolean; dim?: boolean; sensitive?: boolean; enumName?: string; source: string; desc: string }

type Row = [api: string, name: string, type: string, group: string, flags: string, source: string, desc: string, enumName?: string];
const mk = (rows: Row[]): ObjProp[] => rows.map(([api, name, type, group, flags, source, desc, enumName]) => ({
  api, name, type, group, source, desc, enumName,
  pk: flags.includes('pk') || undefined, title: flags.includes('title') || undefined,
  dim: flags.includes('dim') || undefined, sensitive: flags.includes('sens') || undefined,
}));
const SYS: Row[] = [
  ['source_updated_at', '源数据更新时间', 'timestamp', '其他', '', 'update_time', '冲突策略按此列判断最新值'],
  ['etl_batch', '同步批次', 'string', '其他', '', 'etl_batch_id', '系统字段'],
];

export const OBJECT_PROPS: Record<string, ObjProp[]> = {
  order: mk([
    ['order_id', '订单号', 'string', '标识', 'pk', 'order_id', '业务主键，全局唯一'],
    ['order_no_display', '展示单号', 'string', '标识', 'title', 'order_no', '小票和客服使用的单号'],
    ['channel', '渠道', 'enum', '交易', 'dim', 'channel_code', '门店、小程序、外卖平台、社区团购', '销售渠道'],
    ['status', '订单状态', 'enum', '交易', 'dim', 'status', '待支付、待出库、拣货中、已出库、已签收、已取消', '订单状态'],
    ['store_id', '门店', 'string', '交易', 'dim', 'store_id', '外键 → 门店（store_id）'],
    ['fulfill_store_id', '履约门店', 'string', '交易', 'dim', 'fulfill_store_id', '外键 → 门店（store_id）；实际出库发货的门店，自提单为空'],
    ['member_id', '会员', 'string', '交易', '', 'member_id', '外键 → 会员；散客为空'],
    ['warehouse_id', '发货仓', 'string', '交易', 'dim', 'wh_code', '外键 → 仓库'],
    ['created_at', '下单时间', 'timestamp', '时间', 'dim', 'create_time', ''],
    ['paid_at', '支付时间', 'timestamp', '时间', '', 'pay_time', ''],
    ['shipped_at', '出库时间', 'timestamp', '时间', 'dim', 'out_time', '财务口径的时间字段'],
    ['signed_at', '签收时间', 'timestamp', '时间', '', 'sign_time', ''],
    ['cancelled_at', '取消时间', 'timestamp', '时间', '', 'cancel_time', ''],
    ['amount_gross', '应付金额（含税）', 'decimal', '金额', '', 'total_amt', '单位：元'],
    ['amount_net', '应付金额（不含税）', 'decimal', '金额', '', 'total_amt_notax', '单位：元'],
    ['discount_amount', '优惠金额', 'decimal', '金额', '', 'discount_amt', '单位：元'],
    ['paid_amount', '实付金额', 'decimal', '金额', '', 'pay_amt', '单位：元'],
    ['freight_amount', '运费', 'decimal', '金额', '', 'freight_amt', '单位：元'],
    ['tax_amount', '税额', 'decimal', '金额', '', 'tax_amt', '单位：元'],
    ['pay_method', '支付方式', 'enum', '交易', 'dim', 'pay_type', '', '支付方式'],
    ['line_count', '商品行数', 'integer', '交易', '', 'item_cnt', ''],
    ['is_test', '是否测试单', 'boolean', '交易', '', 'test_flag', '默认过滤掉'],
    ['receiver_name', '收货人', 'string', '收货', 'sens', 'recv_name', '脱敏显示'],
    ['receiver_phone', '收货电话', 'string', '收货', 'sens', 'recv_mobile', '脱敏显示'],
    ['receiver_address', '收货地址', 'string', '收货', 'sens', 'recv_addr', '脱敏显示'],
    ['receiver_city', '收货城市', 'string', '收货', 'dim', 'recv_city', ''],
    ['delivery_type', '配送方式', 'enum', '收货', 'dim', 'deliver_type', '自提、同城配送、快递', '配送方式'],
    ['promotion_ids', '参与活动', 'json', '交易', '', 'promo_list', '数组；一单可参与多个活动'],
    ['coupon_code', '优惠券码', 'string', '交易', '', 'coupon_no', ''],
    ['remark', '买家备注', 'string', '其他', '', 'buyer_memo', ''],
    ...SYS,
  ]),
  order_line: mk([
    ['line_id', '订单行号', 'string', '标识', 'pk', 'line_id', '业务主键'],
    ['order_id', '订单', 'string', '标识', '', 'order_id', '外键 → 订单'],
    ['sku', '商品', 'string', '交易', 'dim,title', 'sku_code', '外键 → 商品（sku）'],
    ['qty', '数量', 'decimal', '交易', '', 'qty', '称重商品按千克'],
    ['unit_price', '单价（含税）', 'decimal', '金额', '', 'price', '单位：元'],
    ['amount_ex_tax', '行金额（不含税）', 'decimal', '金额', '', 'line_amt_notax', '销售额（不含税）的取数字段'],
    ['discount_amount', '行优惠', 'decimal', '金额', '', 'line_discount', '单位：元'],
    ['cost_amount', '行成本', 'decimal', '金额', '', 'line_cost', '移动加权平均成本'],
    ['tax_rate', '税率', 'decimal', '金额', '', 'tax_rate', ''],
    ['is_gift', '是否赠品', 'boolean', '交易', '', 'gift_flag', ''],
    ['promo_id', '促销活动', 'string', '交易', '', 'promo_id', '外键 → 促销活动'],
    ['created_at', '下单时间', 'timestamp', '时间', 'dim', 'create_time', '冗余自订单'],
    ...SYS,
  ]),
  refund: mk([
    ['refund_id', '退款单号', 'string', '标识', 'pk,title', 'refund_id', '业务主键'],
    ['order_id', '原订单', 'string', '标识', '', 'order_id', '外键 → 订单'],
    ['refund_type', '退款类型', 'enum', '退款', 'dim', 'refund_type', '仅退款、退货退款', '退款类型'],
    ['refund_reason', '退款原因', 'enum', '退款', 'dim', 'reason_code', '', '退款原因'],
    ['status', '退款状态', 'enum', '退款', 'dim', 'status', '申请中、已同意、已拒绝、已完成', '退款状态'],
    ['applied_at', '申请时间', 'timestamp', '时间', 'dim', 'apply_time', ''],
    ['refund_done_at', '退款完成时间', 'timestamp', '时间', 'dim', 'done_time', '退款率口径的时间字段（CR-0010 审核中）'],
    ['refund_amount', '退款金额（含税）', 'decimal', '金额', '', 'refund_amt', '单位：元'],
    ['refund_ex_tax', '退款金额（不含税）', 'decimal', '金额', '', 'refund_amt_notax', '单位：元'],
    ['channel', '渠道', 'enum', '退款', 'dim', 'channel_code', '同原订单', '销售渠道'],
    ...SYS,
  ]),
  refund_line: mk([
    ['refund_line_id', '退款行号', 'string', '标识', 'pk', 'line_id', '业务主键'],
    ['refund_id', '退款单', 'string', '标识', '', 'refund_id', '外键 → 退款单'],
    ['order_line_id', '原订单行', 'string', '标识', '', 'order_line_id', '外键 → 订单行'],
    ['sku', '商品', 'string', '退款', 'dim,title', 'sku_code', '外键 → 商品'],
    ['qty', '退货数量', 'decimal', '退款', '', 'qty', ''],
    ['refund_ex_tax', '退款金额（不含税）', 'decimal', '金额', '', 'refund_amt_notax', '单位：元'],
    ['restock', '是否回库', 'boolean', '退款', '', 'restock_flag', ''],
    ...SYS,
  ]),
  member: mk([
    ['member_id', '会员号', 'string', '标识', 'pk', 'member_id', '业务主键，会员中心生成'],
    ['nickname', '昵称', 'string', '标识', 'title', 'nick_name', ''],
    ['mobile', '手机号', 'string', '联系方式', 'sens', 'mobile', '脱敏显示；空值率阈值 2%'],
    ['real_name', '姓名', 'string', '联系方式', 'sens', 'real_name', '脱敏显示'],
    ['gender', '性别', 'enum', '画像', 'dim', 'gender', '', '性别'],
    ['birthday', '生日', 'date', '画像', 'sens', 'birthday', '只用于生日券，不进问数'],
    ['level_code', '会员等级', 'string', '会员', 'dim', 'level_code', '外键 → 会员等级'],
    ['reg_store_id', '注册门店', 'string', '会员', 'dim', 'reg_store_id', '外键 → 门店（store_id）；线上注册为空'],
    ['reg_channel', '注册渠道', 'enum', '会员', 'dim', 'reg_channel', '门店、小程序、外卖平台', '销售渠道'],
    ['registered_at', '注册时间', 'timestamp', '时间', 'dim', 'reg_time', ''],
    ['last_order_at', '最近下单时间', 'timestamp', '时间', '', 'last_order_time', '复购客户判断用'],
    ['points', '积分余额', 'integer', '资产', '', 'points', ''],
    ['stored_balance', '储值余额', 'decimal', '资产', '', 'svc_balance', '会员表上的余额字段'],
    ['tags', '会员标签', 'json', '画像', '', 'tag_list', 'CRM 同步，数组'],
    ['status', '会员状态', 'enum', '会员', 'dim', 'status', '正常、冻结、注销', '会员状态'],
    ...SYS,
  ]),
  member_level: mk([
    ['level_code', '等级编码', 'string', '标识', 'pk', 'level_code', '业务主键'],
    ['level_name', '等级名称', 'string', '标识', 'title,dim', 'level_name', '普卡、银卡、金卡、钻石、至尊'],
    ['min_points', '起始积分', 'integer', '规则', '', 'min_points', ''],
    ['discount_rate', '会员折扣', 'decimal', '规则', '', 'discount', ''],
    ['sort_order', '排序', 'integer', '其他', '', 'sort_no', ''],
    ['source_updated_at', '源数据更新时间', 'timestamp', '其他', '', 'update_time', ''],
  ]),
  store: mk([
    ['store_id', '门店编码', 'string', '标识', 'pk', 'store_id', '业务主键；订单、会员、员工都按 store_id 关联'],
    ['store_name', '门店名称', 'string', '标识', 'title', 'store_name', ''],
    ['short_name', '门店简称', 'string', '标识', '', 'short_name', '看板展示用'],
    ['format', '业态', 'enum', '组织', 'dim', 'format_code', '直营店、加盟店、社区店、mini 店', '门店业态'],
    ['region_code', '所属大区', 'string', '组织', 'dim', 'region_code', '外键 → 大区（region_code），CR-0005 起门店直接挂大区'],
    ['province_code', '所在省份', 'string', '组织', 'dim', 'province_code', '外键 → 省份'],
    ['city', '城市', 'string', '地址', 'dim', 'city_name', ''],
    ['address', '门店地址', 'string', '地址', '', 'address', ''],
    ['longitude', '经度', 'decimal', '地址', '', 'lng', ''],
    ['latitude', '纬度', 'decimal', '地址', '', 'lat', ''],
    ['replenish_wh_id', '补货仓', 'string', '供应链', 'dim', 'replenish_wh_code', '外键 → 仓库'],
    ['area_sqm', '营业面积', 'decimal', '经营', '', 'area', '单位：平方米'],
    ['opened_at', '开业日期', 'date', '经营', 'dim', 'open_date', '同店口径按开业满 12 个月'],
    ['status', '门店状态', 'enum', '经营', 'dim', 'status', '筹备、营业、停业、闭店', '门店状态'],
    ['phone', '门店电话', 'string', '地址', '', 'tel', ''],
    ['business_hours', '营业时间', 'string', '经营', '', 'biz_hours', ''],
    ...SYS,
  ]),
  region: mk([
    ['region_code', '大区编码', 'string', '标识', 'pk', 'region_code', '业务主键'],
    ['region_name', '大区名称', 'string', '标识', 'title,dim', 'region_name', '华东、华南、华北、华中、西南、西北'],
    ['short_name', '简称', 'string', '标识', '', 'short_name', ''],
    ['manager_emp_no', '大区经理', 'string', '组织', '', 'manager_emp_no', '员工工号；店长信息权限未定前不建关系'],
    ['hq_city', '总部城市', 'string', '组织', '', 'hq_city', ''],
    ['store_count', '门店数', 'integer', '经营', '', 'store_cnt', '每日由门店主数据汇总'],
    ['province_count', '下辖省份数', 'integer', '经营', '', 'province_cnt', ''],
    ['budget_annual', '年度预算', 'decimal', '经营', '', 'budget_amt', '单位：万元；“调整大区预算”动作改的就是它'],
    ['status', '状态', 'enum', '组织', 'dim', 'status', '启用、停用', '启用状态'],
    ['established_at', '设立日期', 'date', '组织', '', 'setup_date', ''],
    ['sort_order', '排序', 'integer', '其他', '', 'sort_no', ''],
    ...SYS,
  ]),
  province: mk([
    ['province_code', '省份编码', 'string', '标识', 'pk', 'province_code', '国标行政区划码前两位'],
    ['province_name', '省份名称', 'string', '标识', 'title,dim', 'province_name', ''],
    ['region_code', '所属大区', 'string', '组织', 'dim', 'region_code', '外键 → 大区'],
    ['store_count', '门店数', 'integer', '经营', '', 'store_cnt', ''],
    ['source_updated_at', '源数据更新时间', 'timestamp', '其他', '', 'update_time', ''],
  ]),
  product: mk([
    ['sku', 'SKU 编码', 'string', '标识', 'pk', 'sku_code', '业务主键'],
    ['sku_name', '商品名称', 'string', '标识', 'title', 'sku_name', ''],
    ['barcode', '条码', 'string', '标识', '', 'barcode', 'EAN-13'],
    ['category_id', '所属品类', 'string', '分类', 'dim', 'category_code', '外键 → 品类'],
    ['brand', '品牌', 'string', '分类', 'dim', 'brand_name', ''],
    ['spec', '规格', 'string', '分类', '', 'spec', ''],
    ['unit', '销售单位', 'enum', '分类', '', 'unit', '件、盒、千克', '计量单位'],
    ['is_weighed', '是否称重', 'boolean', '分类', '', 'weigh_flag', ''],
    ['main_supplier_id', '主供应商', 'string', '供应链', '', 'main_vendor_code', '外键 → 供应商'],
    ['retail_price', '零售价（含税）', 'decimal', '价格', '', 'retail_price', '单位：元；“门店调价”动作改门店价'],
    ['cost_price', '进价（不含税）', 'decimal', '价格', '', 'cost_price', '单位：元'],
    ['tax_rate', '税率', 'decimal', '价格', '', 'tax_rate', ''],
    ['shelf_life_days', '保质期（天）', 'integer', '供应链', '', 'shelf_life', 'CR-0003 新增'],
    ['safety_stock', '安全库存', 'decimal', '供应链', '', 'safety_qty', '“调整安全库存”动作改的就是它'],
    ['status', '商品状态', 'enum', '分类', 'dim', 'status', '新品、在售、停售、淘汰', '商品状态'],
    ['launched_at', '上市日期', 'date', '分类', '', 'launch_date', ''],
    ...SYS,
  ]),
  category: mk([
    ['category_id', '品类编码', 'string', '标识', 'pk', 'category_code', '业务主键'],
    ['category_name', '品类名称', 'string', '标识', 'title,dim', 'category_name', ''],
    ['parent_id', '上级品类', 'string', '层级', 'dim', 'parent_code', '外键 → 品类；一级品类为空'],
    ['level', '层级', 'integer', '层级', 'dim', 'cat_level', '1–3 级'],
    ['buyer', '采购负责人', 'string', '组织', '', 'buyer_name', ''],
    ['sort_order', '排序', 'integer', '其他', '', 'sort_no', ''],
    ['source_updated_at', '源数据更新时间', 'timestamp', '其他', '', 'update_time', ''],
  ]),
  promotion: mk([
    ['promo_id', '活动编号', 'string', '标识', 'pk', 'promo_id', '业务主键'],
    ['promo_name', '活动名称', 'string', '标识', 'title', 'promo_name', ''],
    ['promo_type', '活动类型', 'enum', '活动', 'dim', 'promo_type', '满减、折扣、买赠、券', '活动类型'],
    ['funded_by', '出资方', 'enum', '活动', 'dim', 'fund_side', '自营、品牌、平台', '出资方'],
    ['start_at', '开始时间', 'timestamp', '时间', 'dim', 'begin_time', ''],
    ['end_at', '结束时间', 'timestamp', '时间', '', 'end_time', ''],
    ['budget', '活动预算', 'decimal', '金额', '', 'budget_amt', '单位：元'],
    ['status', '活动状态', 'enum', '活动', 'dim', 'status', '', '活动状态'],
    ...SYS,
  ]),
  warehouse: mk([
    ['wh_id', '仓库编码', 'string', '标识', 'pk', 'wh_code', '业务主键'],
    ['wh_name', '仓库名称', 'string', '标识', 'title,dim', 'wh_name', ''],
    ['wh_type', '仓库类型', 'enum', '仓储', 'dim', 'wh_type', '常温、冷链', '仓库类型'],
    ['city', '城市', 'string', '仓储', 'dim', 'city_name', ''],
    ['capacity', '库容', 'decimal', '仓储', '', 'capacity', '单位：托'],
    ['source_updated_at', '源数据更新时间', 'timestamp', '其他', '', 'update_time', ''],
  ]),
  inventory: mk([
    ['snapshot_key', '快照键', 'string', '标识', 'pk', 'wh_code + sku_code', '仓库 + 商品 + 快照时间'],
    ['warehouse_id', '仓库', 'string', '库存', 'dim', 'wh_code', '外键 → 仓库'],
    ['sku', '商品', 'string', '库存', 'dim,title', 'sku_code', '外键 → 商品'],
    ['on_hand', '在库量', 'decimal', '库存', '', 'onhand_qty', ''],
    ['available', '可用量', 'decimal', '库存', '', 'avail_qty', ''],
    ['locked', '锁定量', 'decimal', '库存', '', 'lock_qty', ''],
    ['snapshot_at', '快照时间', 'timestamp', '时间', 'dim', 'snap_time', '每 30 分钟'],
    ...SYS,
  ]),
  supplier: mk([
    ['supplier_id', '供应商编码', 'string', '标识', 'pk', 'supplier_code', '业务主键'],
    ['supplier_name', '供应商名称', 'string', '标识', 'title', 'supplier_name', ''],
    ['credit_code', '统一社会信用代码', 'string', '资质', '', 'uscc', ''],
    ['contact', '联系人', 'string', '联系方式', 'sens', 'contact_name', '脱敏显示'],
    ['contact_phone', '联系电话', 'string', '联系方式', 'sens', 'contact_tel', '脱敏显示'],
    ['settle_type', '结算方式', 'enum', '结算', 'dim', 'settle_type', '月结、票到 30 天、票到 60 天', '结算方式'],
    ['status', '合作状态', 'enum', '资质', 'dim', 'status', '', '合作状态'],
    ...SYS,
  ]),
  purchase: mk([
    ['po_no', '采购单号', 'string', '标识', 'pk,title', 'po_no', '业务主键'],
    ['supplier_id', '供应商', 'string', '采购', 'dim', 'supplier_code', '外键 → 供应商'],
    ['warehouse_id', '收货仓', 'string', '采购', 'dim', 'wh_code', '外键 → 仓库'],
    ['status', '采购状态', 'enum', '采购', 'dim', 'status', '', '采购状态'],
    ['ordered_at', '下单时间', 'timestamp', '时间', 'dim', 'po_time', ''],
    ['amount', '采购金额（不含税）', 'decimal', '金额', '', 'po_amt_notax', '单位：元'],
    ...SYS,
  ]),
  shipment: mk([
    ['shipment_no', '出库单号', 'string', '标识', 'pk,title', 'shipment_no', '业务主键'],
    ['order_id', '订单', 'string', '标识', '', 'order_id', '外键 → 订单'],
    ['warehouse_id', '出库仓', 'string', '物流', 'dim', 'out_wh_code', '外键 → 仓库'],
    ['waybill_no', '运单号', 'string', '物流', '', 'waybill_no', '自提单为空'],
    ['carrier', '承运商', 'enum', '物流', 'dim', 'carrier_code', '', '承运商'],
    ['shipped_at', '出库时间', 'timestamp', '时间', 'dim', 'out_time', ''],
    ['delivered_at', '送达时间', 'timestamp', '时间', '', 'sign_time', ''],
    ...SYS,
  ]),
  employee: mk([
    ['emp_no', '工号', 'string', '标识', 'pk', 'emp_no', '业务主键'],
    ['emp_name', '姓名', 'string', '标识', 'title,sens', 'emp_name', '敏感对象，按权限可见'],
    ['store_id', '所属门店', 'string', '组织', 'dim', 'store_id', '外键 → 门店（store_id）；总部员工为空'],
    ['position', '岗位', 'enum', '组织', 'dim', 'post_code', '', '岗位'],
    ['mobile', '手机号', 'string', '联系方式', 'sens', 'mobile', '脱敏显示'],
    ['hired_at', '入职日期', 'date', '组织', '', 'hire_date', ''],
    ['status', '在职状态', 'enum', '组织', 'dim', 'status', '', '在职状态'],
    ...SYS,
  ]),
};

/** 草稿里某个属性的改动（取自 changes.ts 的 diffs 行） */
export interface PropDiff { kind: '新增' | '修改' | '删除'; field: string; before: string; after: string; row: string }

/**
 * 某张草稿单对这个对象类型的属性改了什么：
 * - type = 属性，target = 对象名，field 末尾是 API 名（如“储值余额 stored_balance”）
 * - type = 枚举值，target = “对象名.属性名”（如“门店.业态”）
 * 返回按属性 API 名索引的改动；other 是同一张单里不落在属性上的改动数（对象类型、关系、映射、指标等）。
 */
export function draftPropDiff(crId: string, objName: string, props: ObjProp[]) {
  const map: Record<string, PropDiff> = {};
  let other = 0;
  for (const d of crDetail(crId).diffs) {
    let p: ObjProp | undefined;
    if (d.type === '属性' && d.target === objName) p = props.find((x) => d.field.split(' ').pop() === x.api);
    else if (d.type === '枚举值' && d.target.startsWith(`${objName}.`)) p = props.find((x) => x.name === d.target.slice(objName.length + 1));
    if (p) map[p.api] = { kind: d.type === '枚举值' ? '修改' : d.kind, field: d.type === '枚举值' ? `取值${d.kind}` : d.field, before: d.before, after: d.after, row: d.key };
    else other += 1;
  }
  return { map, other };
}

/**
 * 对象类型概览用的元信息（按对象类型 id）。负责人、数据源取 quality.ts 的 Q_OBJECT_TYPES，
 * 这里只补业务域、描述、示例问题、看板卡片数、近 30 天问数次数、源表未使用字段数等页面上要显示的值。
 * 没写到的对象类型按分组给默认值（见 objectMeta）。
 */
export interface ObjQ { q: string; cert: boolean; query?: string }
export interface ObjMeta {
  domain: string; desc: string; conflict: string; lifecycle: string; sync: string;
  qs: ObjQ[]; cards: number; qa30: number; unused: number;
}
const REL = 'release: v2026.10.3';
const META: Record<string, Partial<ObjMeta>> = {
  order: {
    domain: '零售交易', sync: '每小时增量，按源数据更新时间', cards: 14, qa30: 2318, unused: 6,
    desc: '顾客在门店、小程序、外卖平台、社区团购下的一笔交易。一单可以有多行商品，按出库时间计入销售。测试单默认不参与统计。',
    qs: [
      { q: '上个月各大区的有效订单数是多少？', cert: true, query: `metric: order_cnt\nbreakdown: [组织.大区]\ntime: 出库时间 = 上月\n${REL}` },
      { q: '华东大区 9 月小程序渠道的订单，出库及时率是多少？', cert: true, query: `metric: ship_ontime\nfilters: [大区 = 华东, 渠道 = 小程序]\ntime: 出库时间 = 2026-09\n${REL}` },
      { q: '本周取消的订单里，哪些门店最多？', cert: false },
    ],
  },
  order_line: {
    domain: '零售交易', cards: 9, qa30: 1206, unused: 4,
    desc: '订单里的一行商品，销售额（不含税）和毛利都从这里取数。称重商品数量按千克记录。',
    qs: [
      { q: '上周销售额（不含税）前 10 的商品是哪些？', cert: true, query: `metric: gmv\nbreakdown: [商品]\ntime: 下单时间 = 上周\nlimit: 10\n${REL}` },
      { q: '赠品行占全部订单行的比例是多少？', cert: false },
    ],
  },
  refund: {
    domain: '售后', cards: 5, qa30: 486, unused: 3,
    desc: '顾客对一笔订单发起的退款或退货退款。退款率口径正在改为按退款完成时间统计（CR-0010 审核中）。',
    qs: [
      { q: '上个月各渠道的退款金额（不含税）是多少？', cert: true, query: `metric: refund_amount\nbreakdown: [渠道]\ntime: 退款完成时间 = 上月\n${REL}` },
      { q: '退款原因里“商品质量”占多少？', cert: false },
    ],
  },
  member: {
    domain: '会员运营', conflict: '源系统优先（会员中心为准）', sync: '每 2 小时增量，按源数据更新时间', cards: 11, qa30: 1532, unused: 8,
    desc: '在会员中心注册过的顾客。手机号、姓名、生日是敏感字段，问数里默认脱敏；储值卡正在拆成独立对象（CR-0012 草稿）。',
    qs: [
      { q: '9 月新注册会员里，30 天内复购的比例是多少？', cert: true, query: `metric: repeat_rate\nfilters: [注册时间 = 2026-09]\nwindow: 30d\n${REL}` },
      { q: '华东各门店的金卡会员有多少？', cert: true, query: `metric: member_cnt\nbreakdown: [注册门店]\nfilters: [大区 = 华东, 会员等级 = 金卡]\n${REL}` },
      { q: '冻结的会员主要来自哪些注册渠道？', cert: false },
    ],
  },
  store: {
    domain: '组织与门店', conflict: '源系统优先（门店主数据为准）', sync: '每日 02:00 全量', cards: 18, qa30: 3104, unused: 5,
    desc: '直营、加盟、社区店和 mini 店。订单、会员、员工都按门店编码关联；CR-0005 起门店直接挂在大区下。新开的前置仓正在作为业态补充（CR-0009 草稿）。',
    qs: [
      { q: '华东大区上周销售额（不含税）最低的 5 家门店是哪些？', cert: true, query: `metric: gmv\nbreakdown: [门店]\nfilters: [大区 = 华东]\ntime: 出库时间 = 上周\norder: asc\nlimit: 5\n${REL}` },
      { q: '开业满 12 个月的门店，同店销售增长是多少？', cert: true, query: `metric: gmv\nfilters: [开业日期 ≤ 今天 − 12 个月]\ncompare: 去年同期\n${REL}` },
      { q: '社区店的平均营业面积是多少？', cert: false },
    ],
  },
  region: {
    domain: '组织与门店', conflict: '源系统优先（门店主数据为准）', sync: '每日 02:00 全量', cards: 7, qa30: 942, unused: 2,
    desc: '集团六个经营大区。门店直接挂在大区下，大区经理按大区看经营数据；“调整大区预算”动作改的是年度预算。',
    qs: [
      { q: '本月各大区的销售额（不含税）和毛利率分别是多少？', cert: true, query: `metric: [gmv, gross_margin]\nbreakdown: [大区]\ntime: 出库时间 = 本月\n${REL}` },
      { q: '哪个大区的门店数增长最快？', cert: false },
    ],
  },
  product: {
    domain: '商品', sync: '每小时增量，按源数据更新时间', cards: 12, qa30: 1877, unused: 9,
    desc: '在售和停售的 SKU。品类按四级挂在品类树上；保质期属性自 v2026.09.4 起提供，临期预警规则依赖它。',
    qs: [
      { q: '生鲜品类上周毛利率低于 18% 的商品有哪些？', cert: true, query: `metric: gross_margin\nbreakdown: [商品]\nfilters: [品类 = 生鲜]\nhaving: 毛利率 < 18%\n${REL}` },
      { q: '保质期不足 7 天的在库商品有多少？', cert: false },
    ],
  },
};
const GROUP_DOMAIN: Record<string, string> = { 交易: '零售交易', 客户: '会员运营', 组织: '组织与门店', 商品: '商品', 供应链: '供应链' };

/** 某个对象类型的概览元信息；name / group 用于没写到的对象类型生成默认值 */
export function objectMeta(id: string, name: string, group: string): ObjMeta {
  const m = META[id] ?? {};
  const n = [...id].reduce((s, c) => s + c.charCodeAt(0), 0);
  return {
    domain: m.domain ?? GROUP_DOMAIN[group] ?? group,
    desc: m.desc ?? `${name}的主数据，按源系统同步到本体，供问数、看板和对象详情使用。`,
    conflict: m.conflict ?? '用户编辑优先',
    lifecycle: m.lifecycle ?? '正式',
    sync: m.sync ?? '每日 02:00 全量',
    qs: m.qs ?? [{ q: `各大区的${name}数量分别是多少？`, cert: false }],
    cards: m.cards ?? n % 6,
    qa30: m.qa30 ?? (n * 7) % 400,
    unused: m.unused ?? (n % 5) + 1,
  };
}

/** 数据源 → 数据库类型（数据源名与 quality.ts 的 Q_OBJECT_TYPES.source 一致） */
const SOURCE_DB: Record<string, string> = {
  'POS 交易库（华东）': 'Oracle 19c', 会员中心: 'MySQL 8.0', 门店主数据: '达梦 DM8 · Oracle 兼容模式', 商品主数据: '达梦 DM8 · Oracle 兼容模式',
  促销引擎: 'MySQL 8.0', '供应链 WMS 库': 'SQL Server 2019', '用友 NC 采购': 'Oracle 19c', 物流运单: 'MySQL 8.0', 'HR 人事库': '达梦 DM8 · Oracle 兼容模式',
};
export const dbOf = (source?: string) => (source && SOURCE_DB[source]) || '--';

/** 订单（order）字段映射页签里手工写全的行（原在 pages/ontology/ObjectTypes.vue 页内），按属性名覆盖自动生成的映射 */
export const ORDER_MAP_ROWS = [
  { prop: '订单号', col: 'order_id', srcType: 'VARCHAR2(32)', transform: '' },
  { prop: '渠道', col: 'channel_code', srcType: 'CHAR(2)', transform: '字典映射：01→门店，02→小程序，03→外卖平台，04→社区团购' },
  { prop: '订单状态', col: 'status', srcType: 'NUMBER(2)', transform: '字典映射：10→待支付 … 50→已出库' },
  { prop: '出库时间', col: 'out_time', srcType: 'DATE', transform: '按 Asia/Shanghai 解释' },
  { prop: '应付金额（不含税）', col: 'total_amt_notax', srcType: 'NUMBER(14,2)', transform: '' },
  { prop: '收货电话', col: 'recv_mobile', srcType: 'VARCHAR2(20)', transform: '敏感字段：中间 4 位脱敏' },
  { prop: '是否测试单', col: 'test_flag', srcType: 'CHAR(1)', transform: "'Y' → 是，其余 → 否" },
];
