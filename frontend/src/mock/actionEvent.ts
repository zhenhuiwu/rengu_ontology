/**
 * 动作类型 / 事件类型（本体 · 构建）示例数据。
 * 页面：pages/ontology/ActionTypes.vue、EventTypes.vue；抽屉解析：components/actionEventResolvers.ts。
 * 引用的门店 / 商品 / 订单 / 规则 ID 全部取自现有 mock，保证 ObjRef 都能解析。
 */
import { seeded } from './format';
import { STORES } from './stores';
import { PRODUCTS } from './products';
import { ORDERS } from './orders';
import { RULES, ALERTS } from './dynamic';
import { EXT_SYSTEMS, extSystemById } from './extsys';

const pad = (n: number, w = 2) => String(n).padStart(w, '0');
const fmtTime = (t: number) => {
  const d = new Date(t);
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
};
const NOW = new Date('2026-10-25T15:42:18+08:00').getTime();
const hashOf = (s: string) => [...s].reduce((h, c) => (h * 31 + c.charCodeAt(0)) >>> 0, 7);

/* ───────────────────────── 动作类型 ───────────────────────── */

export type PubStatus = '已发布' | '变更中' | '草稿';
/**
 * 执行状态与动作中心（动态本体 mock/actions.ts 的 ActionStatus）一致：
 * 待确认 →（确认提交）审批中 → 执行中 → 成功 / 失败 / 结果未知 →（对账）已对账；待确认可撤回为已撤回。
 * 颜色：成功 / 已对账 / 已撤回 / 审批中 / 执行中灰，待确认橙，失败红，结果未知橙色问号“结果未知 · 待对账”。
 */
export type RunStatus = '待确认' | '审批中' | '执行中' | '成功' | '失败' | '结果未知' | '已撤回' | '已对账';

/** 写回目标只引用 外部系统（mock/extsys.ts EXT_SYSTEMS）的 id；系统名、超时、重试、幂等都从那里取，这里不重复维护 */
interface ActionTypeDef {
  id: string; name: string; api: string; group: string; status: PubStatus; version: string;
  target: string; targetApi: string; targetKind?: 'store' | 'product' | 'order' | 'region';
  owner: string; desc: string; runners: string; approval: string;
  sysId: string | null; endpoint: string; idemKey: string;
  usedBy: string[]; // 规则 ID
}
export interface ActionType extends ActionTypeDef {
  system: string; timeout: string; retry: string; idempotent: boolean;
}

export const ACTION_GROUPS = ['全部', '营销', '组织', '交易', '供应链', '定价', '告警'];

/** 规则 → 命中后生成的动作（与 dynamic.ts RULES[].action 语义一致；R-021 门店销售额环比下降只生成告警，不绑定动作） */
export const RULE_ACTION: Record<string, string> = {
  'R-007': 'start_price_approval', 'R-012': 'request_replenish', 'R-015': 'urge_logistics', 'SM-ORD': 'correct_order_status',
};
/** 规则生成的动作：每条规则固定一位确认人 */
const RULE_CONFIRMER: Record<string, string> = {
  'R-007': '王敏', 'R-012': '周晓峰', 'R-015': '陈立军', 'SM-ORD': '孙浩然',
};
const RULE_RUNNER = (rule: string) => `规则 ${rule}（生成后待确认）`;
const ALERT_RULES = ['R-007', 'R-012', 'R-015', 'R-021', 'SM-ORD'];

const DEFS: ActionTypeDef[] = [
  { id: 'issue_recall_coupon', name: '发放复购召回优惠券', api: 'issueRecallCoupon', group: '营销', status: '已发布', version: 'v3', target: '会员', targetApi: 'Member', owner: '吴佳',
    desc: '向 60 天未复购的会员发放定向召回券，券由 CRM 生成并通过小程序推送。同一会员同一活动只发一次。只发券，不打标签（打标签用“同步会员标签”）。',
    runners: '会员运营组、门店店长（仅本店会员）', approval: '单次 ≤ 500 张免审；超过 500 张需营销经理审批', sysId: 'EXT-CRM', endpoint: 'POST /coupon/grant',
    idemKey: 'issueRecallCoupon:{会员ID}:{活动ID}', usedBy: [] },
  { id: 'sync_member_tags', name: '同步会员标签', api: 'syncMemberTags', group: '营销', status: '已发布', version: 'v1', target: '门店', targetApi: 'Store', targetKind: 'store', owner: '何静',
    desc: '把门店会员分群结果（如“高价值复购”“沉睡 60 天”）推送到 CRM 会员中心打标签，供小程序会员页和导购企业微信按标签触达。只打标签，不发券。',
    runners: '会员运营组', approval: '覆盖会员 ≤ 5,000 人免审；超过需会员运营 何静 审批', sysId: 'EXT-CRM', endpoint: 'POST /member/tags/sync',
    idemKey: 'syncMemberTags:{门店ID}:{标签ID}:{分群快照日期}', usedBy: [] },
  { id: 'assign_region_manager', name: '指派大区经理跟进', api: 'assignRegionManager', group: '组织', status: '已发布', version: 'v2', target: '大区', targetApi: 'Region', targetKind: 'region', owner: '陈立军',
    desc: '把大区级经营异常指派给大区经理跟进，生成跟进任务并在企业微信提醒。', runners: '经营分析组、区域运营总监', approval: '免审', sysId: null, endpoint: '—',
    idemKey: 'assignRegionManager:{大区ID}:{告警ID}', usedBy: [] },
  { id: 'adjust_region_budget', name: '调整大区预算', api: 'adjustRegionBudget', group: '组织', status: '变更中', version: 'v2', target: '大区', targetApi: 'Region', targetKind: 'region', owner: '刘婷',
    desc: '调整大区当月营销费用预算，写回用友 ERP 预算台账。', runners: '财务 BP、区域运营总监', approval: '调整幅度 ≤ 5% 由财务 BP 审批；> 5% 需财务总监二级审批', sysId: 'EXT-ERP', endpoint: 'POST /budget/adjust',
    idemKey: 'adjustRegionBudget:{大区ID}:{预算月份}:{申请单号}', usedBy: [] },
  { id: 'correct_order_status', name: '订单状态修正', api: 'correctOrderStatus', group: '交易', status: '已发布', version: 'v4', target: '订单', targetApi: 'SalesOrder', targetKind: 'order', owner: '孙浩然',
    desc: '订单出现非法状态迁移时，按生命周期 v4 把订单改回合法状态，并写回订单 OMS。', runners: `订单运营组、${RULE_RUNNER('SM-ORD')}`, approval: '免审（仅允许改为生命周期内的合法状态）', sysId: 'EXT-OMS', endpoint: 'PUT /orders/status',
    idemKey: 'correctOrderStatus:{订单号}:{目标状态}', usedBy: ['SM-ORD'] },
  { id: 'request_replenish', name: '补货申请', api: 'requestReplenishment', group: '供应链', status: '已发布', version: 'v3', target: '商品', targetApi: 'Product', targetKind: 'product', owner: '周晓峰',
    desc: '可用库存低于安全线时生成补货申请，写入用友 ERP 请购单，由 ERP 生成调拨 / 采购单。', runners: `门店店长、供应链计划员、${RULE_RUNNER('R-012')}`, approval: '补货金额 ≤ 2 万元免审；超过需区域供应链经理审批', sysId: 'EXT-ERP', endpoint: 'POST /praybill/save',
    idemKey: 'requestReplenishment:{门店ID}:{SKU}:{日期}', usedBy: ['R-012'] },
  { id: 'adjust_store_price', name: '门店调价', api: 'adjustStorePrice', group: '定价', status: '已发布', version: 'v2', target: '商品', targetApi: 'Product', targetKind: 'product', owner: '王敏',
    desc: '调整单店单品的售价，审批通过后写入用友 ERP 价格档案并下发门店 POS，次日 0 点生效。', runners: '品类经理', approval: '降价幅度 > 10% 或低于成本价需定价委员会审批', sysId: 'EXT-ERP', endpoint: 'POST /price/adjust',
    idemKey: 'adjustStorePrice:{门店ID}:{SKU}:{生效日期}', usedBy: [] },
  { id: 'start_price_approval', name: '发起调价审批', api: 'startPriceApproval', group: '定价', status: '已发布', version: 'v3', target: '商品', targetApi: 'Product', targetKind: 'product', owner: '王敏',
    desc: '毛利率越线时在泛微 OA 发起调价审批流程，审批通过后由品类经理执行“门店调价”。', runners: `品类经理、${RULE_RUNNER('R-007')}`, approval: '定价委员会审批（泛微 OA 流程）', sysId: 'EXT-OA', endpoint: 'POST /request/create',
    idemKey: 'startPriceApproval:{SKU}:{门店ID}:{日期}', usedBy: ['R-007'] },
  { id: 'close_alert', name: '关闭告警', api: 'closeAlert', group: '告警', status: '已发布', version: 'v1', target: '告警', targetApi: 'Alert', owner: '陈立军',
    desc: '选择关闭结果并填写原因后关闭告警，告警进入“已解决”或“已忽略”；与告警处理页的关闭表单一致。', runners: '告警处理人（只能关闭本人处理中的告警）', approval: '免审', sysId: null, endpoint: '—',
    idemKey: 'closeAlert:{告警ID}', usedBy: ALERT_RULES },
  { id: 'urge_logistics', name: '发起物流催派', api: 'urgeLogistics', group: '交易', status: '已发布', version: 'v1', target: '订单', targetApi: 'SalesOrder', targetKind: 'order', owner: '陈立军',
    desc: '出库超时未签收的订单，通过订单 OMS 向承运商提交催派工单。', runners: `订单运营组、${RULE_RUNNER('R-015')}`, approval: '免审', sysId: 'EXT-OMS', endpoint: 'POST /logistics/urge',
    idemKey: 'urgeLogistics:{订单号}:{运单号}', usedBy: ['R-015'] },
  { id: 'claim_alert', name: '认领告警', api: 'claimAlert', group: '告警', status: '已发布', version: 'v1', target: '告警', targetApi: 'Alert', owner: '陈立军',
    desc: '把告警认领给自己，告警进入“处理中”。', runners: '告警所属规则的订阅人', approval: '免审', sysId: null, endpoint: '—',
    idemKey: 'claimAlert:{告警ID}', usedBy: ALERT_RULES },
  { id: 'reassign_alert', name: '转派告警', api: 'reassignAlert', group: '告警', status: '已发布', version: 'v1', target: '告警', targetApi: 'Alert', owner: '陈立军',
    desc: '把告警转给其他处理人，并通知新处理人。', runners: '当前处理人、告警管理员', approval: '免审', sysId: null, endpoint: '—',
    idemKey: 'reassignAlert:{告警ID}:{新处理人}', usedBy: [] },
  { id: 'reissue_shipment', name: '补发出库单', api: 'reissueShipment', group: '交易', status: '已发布', version: 'v2', target: '订单', targetApi: 'SalesOrder', targetKind: 'order', owner: '周晓峰',
    desc: '丢件或破损时按原订单补发，WMS 生成新出库单。', runners: '客服主管、订单运营组', approval: '补发金额 ≤ 300 元免审', sysId: 'EXT-WMS', endpoint: 'POST /outbound/create',
    idemKey: 'reissueShipment:{订单号}:{补发原因}', usedBy: [] },
  { id: 'approve_refund', name: '退款审核', api: 'approveRefund', group: '交易', status: '已发布', version: 'v2', target: '订单', targetApi: 'SalesOrder', targetKind: 'order', owner: '孙浩然',
    desc: '审核售后退款申请，通过后用友 ERP 生成退款单并原路退回。', runners: '客服主管', approval: '退款 ≤ 200 元免审；超过需门店店长确认', sysId: 'EXT-ERP', endpoint: 'POST /refund/approve',
    idemKey: 'approveRefund:{退款申请号}', usedBy: [] },
  { id: 'change_member_level', name: '调整会员等级', api: 'changeMemberLevel', group: '营销', status: '草稿', version: 'v1', target: '会员', targetApi: 'Member', owner: '吴佳',
    desc: '人工调整会员等级（补偿、投诉处理），写回 CRM 会员中心。', runners: '会员运营组', approval: '升 2 级及以上需会员运营经理审批', sysId: 'EXT-CRM', endpoint: 'PUT /member/level',
    idemKey: 'changeMemberLevel:{会员ID}:{目标等级}', usedBy: [] },
  { id: 'change_store_status', name: '变更门店状态', api: 'changeStoreStatus', group: '组织', status: '已发布', version: 'v1', target: '门店', targetApi: 'Store', targetKind: 'store', owner: '陈立军',
    desc: '门店营业中 / 装修中 / 已闭店之间切换，写回用友 ERP 组织主数据。', runners: '区域运营总监', approval: '闭店需运营副总裁审批', sysId: 'EXT-ERP', endpoint: 'PUT /org/stores/status',
    idemKey: 'changeStoreStatus:{门店ID}:{目标状态}:{生效日期}', usedBy: [] },
  { id: 'set_safety_stock', name: '调整安全库存', api: 'setSafetyStock', group: '供应链', status: '草稿', version: 'v1', target: '商品', targetApi: 'Product', targetKind: 'product', owner: '周晓峰',
    desc: '按门店调整单品安全库存，影响“可用库存低于安全线”规则的阈值。', runners: '供应链计划员', approval: '免审', sysId: 'EXT-WMS', endpoint: 'PUT /safety-stock',
    idemKey: 'setSafetyStock:{门店ID}:{SKU}', usedBy: [] },
];
const INTERNAL = { system: '本体（无外部写回）', timeout: '3 秒', retry: '本体内部编辑，失败直接报错，不重试', idempotent: true };
export const withSystem = (d: ActionTypeDef): ActionType => {
  const s = extSystemById(d.sysId);
  return { ...d, ...(s ? { system: s.name, timeout: `${s.timeoutMs / 1000} 秒`, retry: s.retry, idempotent: s.idempotent } : INTERNAL) };
};
export const ACTION_TYPES: ActionType[] = DEFS.map(withSystem);
export const actionTypeById = (id: string) => ACTION_TYPES.find((a) => a.id === id);
/** 写回目标可选的外部系统（草稿里编辑写回用）：EXT_SYSTEMS 里生产环境、支持写回的系统 */
export const writebackSystems = () => EXT_SYSTEMS.filter((s) => s.env === '生产' && s.status !== '停用');
export const extSystemOf = (a: ActionType) => extSystemById(a.sysId);
/** 规则生成的动作：发起人 = 规则，确认人 = 具体的人 */
export const ruleConfirmer = (rule: string) => RULE_CONFIRMER[rule] ?? '';
const RULE_ACTION_REV: Record<string, string> = Object.fromEntries(Object.entries(RULE_ACTION).map(([k, v]) => [v, k]));

/* 参数 */
export interface ActionParam { name: string; api: string; type: string; required: boolean; def: string; rule: string }
const P = (name: string, api: string, type: string, required: boolean, def = '—', rule = '—'): ActionParam => ({ name, api, type, required, def, rule });

const PARAMS: Record<string, ActionParam[]> = {
  sync_member_tags: [
    P('门店', 'store', '对象引用 · 门店', true, '当前选中门店', '门店状态 = 营业中'),
    P('会员标签', 'tag', '枚举', true, '高价值复购', '取自 CRM 标签库（状态 = 启用）'),
    P('分群规则', 'segment', '对象引用 · 会员分群', true, '近 90 天复购 ≥ 3 次', '分群结果非空'),
    P('分群快照日期', 'snapshotDate', '日期', true, '昨天', '不晚于今天'),
    P('预计覆盖会员', 'memberCount', '整数', false, '按分群快照计算', '只读，提交时重新计算'),
    P('同步方式', 'mode', '枚举', true, '增量', '增量 / 全量覆盖'),
    P('标签有效期（天）', 'ttlDays', '整数', true, '30', '1 ≤ 天数 ≤ 180'),
    P('同步渠道', 'channels', '枚举列表', false, '小程序会员页、企业微信导购', '—'),
    P('关联告警', 'alert', '对象引用 · 告警', false, '—', '从告警处理发起时自动带入'),
    P('备注', 'remark', '文本', false, '—', '≤ 200 字'),
  ],
  close_alert: [
    P('告警', 'alert', '对象引用 · 告警', true, '当前告警', '告警状态 = 处理中'),
    P('关闭结果', 'closeAs', '枚举', true, '已解决', '已解决 / 已忽略'),
    P('原因', 'reason', '文本', true, '—', '必填，≤ 200 字；写入告警处理记录“关闭为{关闭结果}：{原因}”'),
  ],
  issue_recall_coupon: [
    P('会员', 'member', '对象引用 · 会员', true, '当前选中会员', '会员状态 = 正常'),
    P('召回活动', 'campaign', '对象引用 · 营销活动', true, '—', '活动状态 = 进行中'),
    P('券模板', 'couponTemplate', '枚举', true, '满 99 减 15', '取自 CRM 券模板（已启用）'),
    P('面额（元）', 'faceValue', '金额', true, '15.00', '5.00 ≤ 面额 ≤ 50.00'),
    P('使用门槛（元）', 'threshold', '金额', true, '99.00', '门槛 ≥ 面额 × 3'),
    P('有效天数', 'validDays', '整数', true, '14', '1 ≤ 天数 ≤ 30'),
    P('适用门店', 'stores', '对象引用列表 · 门店', false, '会员常去门店', '最多 20 家'),
    P('适用品类', 'categories', '枚举列表', false, '全品类', '不含烟酒、奶粉'),
    P('发放渠道', 'channel', '枚举', true, '小程序', '小程序 / 短信 / 企业微信'),
    P('推送文案', 'pushText', '文本', false, '系统模板', '≤ 60 字，不含外链'),
    P('发放时间', 'sendAt', '日期时间', false, '立即', '不早于当前时间，不晚于活动结束'),
    P('预算科目', 'budgetItem', '枚举', true, '会员召回', '取自大区当月预算科目'),
    P('召回原因', 'reason', '枚举', true, '60 天未复购', '60 天未复购 / 投诉补偿 / 其他'),
    P('备注', 'remark', '文本', false, '—', '≤ 200 字'),
    P('关联告警', 'alert', '对象引用 · 告警', false, '—', '来自规则时自动带入'),
  ],
  request_replenish: [
    P('商品', 'product', '对象引用 · 商品', true, '当前选中商品', '商品状态 = 在售'),
    P('门店', 'store', '对象引用 · 门店', true, '告警所属门店', '门店状态 = 营业中'),
    P('补货数量', 'qty', '整数', true, '安全库存 × 1.5 − 可用库存', '> 0，且 ≤ 门店最大陈列量'),
    P('补货方式', 'mode', '枚举', true, '仓库调拨', '仓库调拨 / 供应商直送'),
    P('期望到货', 'eta', '日期', false, '次日', '不早于明天'),
    P('关联告警', 'alert', '对象引用 · 告警', false, '—', '来自规则时自动带入'),
    P('备注', 'remark', '文本', false, '—', '≤ 200 字'),
  ],
  correct_order_status: [
    P('订单', 'order', '对象引用 · 订单', true, '当前选中订单', '—'),
    P('目标状态', 'targetStatus', '枚举', true, '上一个合法状态', '必须是生命周期 v4 中的合法迁移'),
    P('修正原因', 'reason', '枚举', true, '系统同步乱序', '系统同步乱序 / 人工误操作 / 其他'),
    P('关联告警', 'alert', '对象引用 · 告警', false, '—', '来自规则时自动带入'),
    P('说明', 'remark', '文本', false, '—', '原因选“其他”时必填'),
  ],
};
const GENERIC_PARAMS = (a: ActionType): ActionParam[] => [
  P(a.target, a.targetApi.charAt(0).toLowerCase() + a.targetApi.slice(1), `对象引用 · ${a.target}`, true, `当前选中${a.target}`, '—'),
  P('原因', 'reason', '枚举', true, '—', '取自原因字典'),
  P('生效时间', 'effectiveAt', '日期时间', false, '立即', '不早于当前时间'),
  P('关联告警', 'alert', '对象引用 · 告警', false, '—', '来自规则时自动带入'),
  P('备注', 'remark', '文本', false, '—', '≤ 200 字'),
];
export const paramsOf = (a: ActionType) => PARAMS[a.id] ?? GENERIC_PARAMS(a);

/* 提交条件 */
export interface SubmitCond { kind: '当前用户' | '参数' | '上下文对象'; name: string; expr: string; fail: string }
const CONDS: Record<string, SubmitCond[]> = {
  sync_member_tags: [
    { kind: '当前用户', name: '执行人角色', expr: '当前用户.角色 = 会员运营', fail: '只有会员运营组可以同步会员标签' },
    { kind: '参数', name: '标签有效期', expr: '1 ≤ 参数.标签有效期 ≤ 180', fail: '标签有效期须在 1–180 天之间' },
    { kind: '上下文对象', name: '门店状态', expr: '门店.状态 = 营业中', fail: '门店不在营业中，不能同步标签' },
    { kind: '上下文对象', name: '标签已启用', expr: 'CRM 标签(参数.会员标签).状态 = 启用', fail: '标签已停用（CRM-509），请换用启用中的标签' },
    { kind: '上下文对象', name: '分群非空', expr: '会员分群(参数.分群规则, 参数.分群快照日期).人数 > 0', fail: '会员分群为空，未生成任务（CRM-422）' },
    { kind: '上下文对象', name: '不重复同步', expr: '不存在 标签同步记录 where 门店 = 参数.门店 且 标签 = 参数.会员标签 且 快照日期 = 参数.分群快照日期', fail: '该门店同一标签同一快照日已同步过' },
  ],
  close_alert: [
    { kind: '当前用户', name: '本人处理中', expr: '告警.处理人 = 当前用户', fail: '只能关闭本人处理中的告警，请先认领' },
    { kind: '上下文对象', name: '告警状态', expr: '告警.状态 = 处理中', fail: '告警不在处理中，不能关闭' },
    { kind: '参数', name: '关闭结果', expr: '参数.关闭结果 ∈ {已解决, 已忽略}', fail: '请选择关闭结果' },
    { kind: '参数', name: '原因必填', expr: '参数.原因 ≠ 空', fail: '请填写关闭原因' },
  ],
  issue_recall_coupon: [
    { kind: '当前用户', name: '执行人角色', expr: '当前用户.角色 ∈ {会员运营, 门店店长}', fail: '只有会员运营组和门店店长可以发券' },
    { kind: '当前用户', name: '门店范围', expr: '当前用户.角色 = 门店店长 ⇒ 会员.常去门店 ∈ 当前用户.管辖门店', fail: '店长只能给本店会员发券' },
    { kind: '参数', name: '面额与门槛', expr: '参数.使用门槛 ≥ 参数.面额 × 3', fail: '使用门槛至少是面额的 3 倍' },
    { kind: '参数', name: '有效期', expr: '1 ≤ 参数.有效天数 ≤ 30', fail: '有效天数须在 1–30 天之间' },
    { kind: '上下文对象', name: '未复购天数', expr: '会员.最近购买日期 ≤ 今天 − 60 天', fail: '该会员近 60 天有购买，不属于召回对象' },
    { kind: '上下文对象', name: '不重复发放', expr: '不存在 优惠券发放记录 where 会员 = 参数.会员 且 活动 = 参数.召回活动', fail: '该会员已领取过本活动的召回券' },
    { kind: '上下文对象', name: '预算余额', expr: '大区预算.剩余(参数.预算科目) ≥ 参数.面额', fail: '本大区“会员召回”预算不足' },
  ],
};
const GENERIC_CONDS = (a: ActionType): SubmitCond[] => [
  { kind: '当前用户', name: '执行人角色', expr: `当前用户.角色 ∈ {${a.runners.split('、').map((s) => s.replace(/（.*）/, '')).join(', ')}}`, fail: `你没有执行“${a.name}”的权限` },
  { kind: '参数', name: '原因必填', expr: '参数.原因 ≠ 空', fail: '请选择原因' },
  { kind: '上下文对象', name: `${a.target}状态`, expr: `${a.target}.状态 ∉ {已停用, 已闭店}`, fail: `${a.target}已停用，不能执行该动作` },
];
export const condsOf = (a: ActionType) => CONDS[a.id] ?? GENERIC_CONDS(a);

/* 写回与副作用 */
export interface OntologyEdit { op: '修改' | '新建' | '关联'; obj: string; field: string }
export interface SideEffect { kind: '通知' | '事件' | 'Webhook'; target: string; when: string; note: string }
/** 结果未知时的处理（与外部系统页一致：超时不重试） */
export const UNKNOWN_FOLLOWUP = '系统按幂等键自动查询，查不到再人工对账';
export function writebackOf(a: ActionType): { edits: OntologyEdit[]; effects: SideEffect[] } {
  const internal = !a.sysId;
  const edits: OntologyEdit[] = a.id === 'issue_recall_coupon'
    ? [
        { op: '新建', obj: '优惠券发放记录', field: '会员、活动、券号（CRM 返回）、面额、有效期' },
        { op: '修改', obj: '会员', field: '最近召回时间、召回次数 + 1' },
        { op: '关联', obj: '会员 ↔ 营销活动', field: '参与记录' },
      ]
    : a.id === 'sync_member_tags'
      ? [
          { op: '新建', obj: '标签同步记录', field: '门店、标签、分群快照日期、覆盖人数、CRM 任务号' },
          { op: '修改', obj: '门店', field: '最近标签同步时间' },
        ]
      : a.id === 'close_alert'
        ? [
            { op: '修改', obj: '告警', field: '状态（已解决 / 已忽略）、关闭人、关闭时间' },
            { op: '新建', obj: '告警处理记录', field: '“关闭为{关闭结果}：{原因}”' },
          ]
        : [{ op: '修改', obj: a.target, field: '状态、最近操作人、最近操作时间' }, { op: '新建', obj: '操作记录', field: '动作、参数快照、发起人、确认人' }];
  const effects: SideEffect[] = [
    { kind: '事件', target: a.id === 'close_alert' ? 'AlertClosed 告警关闭' : 'ActionExecuted 动作执行完成', when: '写回成功后', note: '供规则与自动化订阅' },
    { kind: '通知', target: a.id === 'issue_recall_coupon' ? '企业微信 · 会员常去门店店长' : a.id === 'sync_member_tags' ? '企业微信 · 门店导购' : '企业微信 · 发起人与确认人', when: '写回成功后', note: '失败不影响动作结果' },
  ];
  if (!internal) effects.push({ kind: '事件', target: 'ActionOutcomeUnknown 动作结果未知', when: '外部系统超时或无响应（不重试）', note: UNKNOWN_FOLLOWUP });
  return { edits, effects };
}

/* 执行记录（编号 ACT-YYYYMMDD-NNNN，与动作中心 mock/actions.ts 同一格式） */
export type RunSource = '规则' | '告警处理' | '问数' | '手动';
export interface ActionRun {
  id: string; typeId: string; at: string;
  /** 发起人：规则生成时为“规则 R-xxx”，否则为人 */
  initiator: string;
  /** 确认人：待确认时为指定确认人；手动发起时与发起人相同 */
  confirmer: string;
  approver?: string;
  source: RunSource;
  objKind?: 'store' | 'product' | 'order' | 'region'; objId: string; objLabel: string;
  summary: string; status: RunStatus; ms: number; code: string;
  /** 外部系统明确返回失败后的自动重试次数（超时不重试） */
  retries: number;
  ruleId?: string; alertId?: string; idemKey: string; extRef?: string; reconcile?: string;
}
const PEOPLE = ['王敏', '陈立军', '刘婷', '周晓峰', '吴佳', '孙浩然', '赵蕾', '何静'];
function targetFor(a: ActionType, r: () => number): Pick<ActionRun, 'objKind' | 'objId' | 'objLabel'> {
  if (a.targetKind === 'order') { const o = ORDERS[Math.floor(r() * 400)]; return { objKind: 'order', objId: o.id, objLabel: o.id }; }
  if (a.targetKind === 'product') { const p = PRODUCTS[Math.floor(r() * PRODUCTS.length)]; return { objKind: 'product', objId: p.sku, objLabel: `${p.name}` }; }
  if (a.targetKind === 'store') { const s = STORES[Math.floor(r() * STORES.length)]; return { objKind: 'store', objId: s.id, objLabel: s.name }; }
  if (a.targetKind === 'region') return { objKind: 'region', objId: 'REG-EC', objLabel: '华东大区' };
  if (a.target === '告警') { const al = ALERTS[Math.floor(r() * ALERTS.length)]; return { objId: al.id, objLabel: al.id }; }
  const m = `M${String(31000000 + Math.floor(r() * 899999)).slice(0, 8)}`;
  return { objId: m, objLabel: `会员 ${m}` };
}
const TAGS = ['高价值复购', '沉睡 60 天', '新客首单', '母婴偏好'];
function summaryFor(a: ActionType, r: () => number) {
  switch (a.id) {
    case 'issue_recall_coupon': return `满 99 减 ${[10, 15, 20][Math.floor(r() * 3)]} · 14 天 · 小程序`;
    case 'sync_member_tags': return `标签“${TAGS[Math.floor(r() * TAGS.length)]}” · 覆盖 ${Math.floor(300 + r() * 4200)} 人 · 增量`;
    case 'request_replenish': return `补货 ${Math.floor(24 + r() * 180)} 件 · 仓库调拨`;
    case 'adjust_store_price': return `售价 → ${(9 + r() * 60).toFixed(2)} 元 · 次日生效`;
    case 'start_price_approval': return `建议降价 ${(3 + r() * 9).toFixed(1)}%`;
    case 'correct_order_status': return `→ ${['已出库', '已签收', '拣货中'][Math.floor(r() * 3)]} · 系统同步乱序`;
    case 'urge_logistics': return `催派 · 出库 ${(48 + r() * 20).toFixed(1)} 小时`;
    case 'adjust_region_budget': return `会员召回预算 ${r() < 0.5 ? '+' : '−'}${(1 + r() * 8).toFixed(1)}%`;
    case 'approve_refund': return `退款 ${(20 + r() * 400).toFixed(2)} 元`;
    case 'close_alert': return r() < 0.75 ? '关闭为已解决：已处理完毕' : '关闭为已忽略：误报（促销期波动）';
    default: return '—';
  }
}
const FAIL_CODES: Record<string, string[]> = {
  'EXT-CRM': ['CRM-509 标签“高价值复购”已停用', 'CRM-422 会员分群为空，未生成任务', 'HTTP 409 券库存不足'],
  'EXT-ERP': ['HTTP 409 请购单校验失败：物料未启用', 'HTTP 422 预算科目已关账'],
  'EXT-OMS': ['HTTP 409 订单已关闭，不能修改', 'HTTP 422 目标状态不是合法迁移'],
  'EXT-OA': ['HTTP 400 流程模板已停用', 'HTTP 409 同一 SKU 已有审批中流程'],
  'EXT-WMS': ['HTTP 409 出库单已存在', 'HTTP 422 库位不足'],
};
const RUNS: ActionRun[] = [];
const RUN_BY_ID = new Map<string, ActionRun>();
ACTION_TYPES.forEach((a, ai) => {
  if (a.status === '草稿') return;
  const r = seeded(9100 + ai * 37);
  const n = 46 + Math.floor(r() * 90);
  let t = NOW - Math.floor(r() * 1800) * 1000;
  const sys = extSystemById(a.sysId);
  const internal = !sys;
  const needsApproval = a.approval !== '免审' && !a.approval.startsWith('免审');
  const ruleOf = RULE_ACTION_REV[a.id];
  for (let i = 0; i < n; i++) {
    t -= Math.floor(600 + r() * 9000) * 1000;
    const rule = ruleOf && (i === 0 || i === 2 || r() < 0.5) ? ruleOf : undefined;
    const alert = rule ? ALERTS.filter((al) => al.ruleId === rule)[Math.floor(r() * 4)] ?? ALERTS.find((al) => al.ruleId === rule) : undefined;
    const x = r();
    let status: RunStatus = '成功';
    if (x < 0.06) status = '失败';
    else if (x < 0.1 && !internal) status = '结果未知';
    else if (x < 0.13 && !internal) status = '已对账';
    else if (x < 0.16 && needsApproval) status = '审批中';
    else if (x < 0.18 && rule) status = '已撤回';
    if (i === 1 && !internal) status = '结果未知';
    if (i === 3) status = '失败';
    if (i === 5 && !internal) status = '已对账';
    if (i === 4 && needsApproval) status = '审批中';
    if (i === 0 && !internal && !rule) status = '执行中';
    if (i === 0 && rule) status = '待确认';
    if (i === 2 && rule) status = '待确认';
    const person = PEOPLE[Math.floor(r() * PEOPLE.length)];
    r(); // 保持随机序列不变（原先抽确认人）
    const confirmer = rule ? ruleConfirmer(rule) : person;
    const explicitFail = status === '失败' && !internal;
    const firstFailCode = sys ? (FAIL_CODES[sys.id] ?? ['HTTP 500'])[Math.floor(r() * (FAIL_CODES[sys.id]?.length ?? 1))] : '提交条件未通过';
    const retries = explicitFail && sys!.idempotent ? sys!.maxRetry : status === '成功' && !internal && r() < 0.12 ? 1 : 0;
    const objT = alert && alert.objKind === a.targetKind ? { objKind: a.targetKind, objId: alert.objId, objLabel: alert.objLabel } : targetFor(a, r);
    const run: ActionRun = {
      id: '', typeId: a.id, at: fmtTime(t),
      initiator: rule ? `规则 ${rule}` : person, confirmer,
      approver: needsApproval ? a.approval.match(/需(.+?)(审批|确认)/)?.[1] : undefined,
      source: rule ? '规则' : r() < 0.25 ? '告警处理' : r() < 0.2 ? '问数' : '手动',
      ...objT, summary: summaryFor(a, r), status,
      ms: status === '结果未知' || status === '已对账' ? (sys?.timeoutMs ?? 3000) : status === '待确认' || status === '已撤回' ? 0 : Math.floor(180 + r() * 2400),
      code: status === '成功' ? (internal ? '本体已提交' : retries ? '第 2 次成功（首次 HTTP 503 明确失败）' : 'HTTP 200')
        : status === '失败' ? firstFailCode
          : status === '结果未知' ? `超时 ${(sys?.timeoutMs ?? 3000) / 1000} 秒未响应 · 未重试`
            : status === '已对账' ? '对账确认：外部已处理'
              : status === '待确认' ? `待 ${confirmer} 确认`
                : status === '已撤回' ? `${confirmer} 撤回` : status === '审批中' ? '审批中' : '已调用，等待返回',
      retries, ruleId: rule, alertId: alert?.id ?? (rule ? undefined : r() < 0.2 ? ALERTS[Math.floor(r() * ALERTS.length)].id : undefined),
      idemKey: '',
      extRef: status === '成功' || status === '已对账' ? (sys ? `${sys.category}-${Math.floor(100000 + r() * 899999)}` : undefined) : undefined,
      reconcile: status === '已对账' ? `按幂等键查到外部记录，确认已处理（${person} 复核）` : undefined,
    };
    RUNS.push(run);
  }
});
// 统一编号：全部执行记录按时间倒序，ACT-日期-流水号（流水号从 2000 起，与动作中心 0413–0532 不冲突）
RUNS.sort((x, y) => (x.at < y.at ? 1 : -1));
const usedKeys = new Set<string>();
RUNS.forEach((run, i) => {
  run.id = `ACT-${run.at.slice(0, 10).replace(/-/g, '')}-${pad(2000 + RUNS.length - i, 4)}`;
  const k = run.alertId ? `act-${run.alertId.toLowerCase()}` : '';
  run.idemKey = k && !usedKeys.has(k) ? k : `act-${run.id.slice(4).toLowerCase()}`;
  usedKeys.add(run.idemKey);
  RUN_BY_ID.set(run.id, run);
});
export const runsOf = (typeId: string) => RUNS.filter((r) => r.typeId === typeId);
export const runById = (id: string) => RUN_BY_ID.get(id);
/** 近 30 天统计（按执行记录汇总） */
export function runStats(typeId: string) {
  const rows = runsOf(typeId);
  const c = (s: RunStatus) => rows.filter((r) => r.status === s).length;
  const ok = c('成功') + c('已对账');
  const pending = c('待确认') + c('审批中') + c('执行中');
  const done = rows.length - pending - c('已撤回');
  return { total: rows.length, ok, failed: c('失败'), unknown: c('结果未知'), pending, confirm: c('待确认'), rate: done ? ok / done : 0,
    avgMs: rows.length ? Math.round(rows.reduce((s, r) => s + r.ms, 0) / rows.length) : 0 };
}
/** 近 14 天每日执行量（成功 / 失败 / 结果未知） */
export function runDaily(typeId: string) {
  const r = seeded(hashOf(typeId));
  return Array.from({ length: 14 }, (_, i) => {
    const d = new Date(NOW - (13 - i) * 86400000);
    const ok = Math.floor(4 + r() * 18);
    return { date: `${pad(d.getMonth() + 1)}-${pad(d.getDate())}`, ok, failed: r() < 0.4 ? Math.floor(1 + r() * 2) : 0, unknown: r() < 0.3 ? 1 : 0 };
  });
}

/* 版本历史（动作 / 事件共用结构） */
export interface TypeVersion { ver: string; release: string; status: '生效中' | '已替代' | '草稿'; by: string; at: string; change: string }
export function versionsOf(version: string, owner: string, changes: string[]): TypeVersion[] {
  const n = parseInt(version.slice(1), 10);
  const releases = ['v2026.10.3', 'v2026.09.2', 'v2026.08.1', 'v2026.06.1', 'v2026.03.2'];
  const dates = ['2026-10-24 21:17', '2026-09-16 20:05', '2026-08-05 19:42', '2026-06-11 18:30', '2026-03-18 10:06'];
  return Array.from({ length: n }, (_, i) => ({
    ver: `v${n - i}`, release: releases[i] ?? 'v2026.01.1', status: i === 0 ? '生效中' : '已替代', by: owner, at: dates[i] ?? '2026-01-08 10:00',
    change: i === n - 1 ? '首次发布' : changes[i % changes.length],
  }));
}
export const ACTION_VERSION_NOTES = ['提交条件增加预算余额校验', '写回改为幂等键调用，超时进入对账', '新增参数“适用门店”', '审批阈值由 300 张调整为 500 张'];

/* ───────────────────────── 事件类型 ───────────────────────── */

export type EventSource = '数据同步' | '规则' | '动作' | '外部系统';
export interface EventType {
  id: string; name: string; api: string; source: EventSource; sourceRef: string; ruleId?: string;
  object: string; objectApi: string; objKind?: 'store' | 'product' | 'order';
  status: PubStatus; version: string; owner: string; desc: string;
  dedupKey: string; dedupWindow: string; retention: string; delivery: string; ordering: string;
  weekly: number; specific: PayloadField[];
}
export interface PayloadField { name: string; api: string; type: string; required: boolean; desc: string; sample: string }
const F = (name: string, api: string, type: string, required: boolean, desc: string, sample: string): PayloadField => ({ name, api, type, required, desc, sample });

export const EVENT_SOURCES = ['全部', '数据同步', '规则', '动作', '外部系统'];

export const EVENT_TYPES: EventType[] = [
  { id: 'order_status_changed', name: '订单状态变更', api: 'OrderStatusChanged', source: '数据同步', sourceRef: '订单 OMS · CDC 增量同步', object: '订单', objectApi: 'SalesOrder', objKind: 'order', status: '已发布', version: 'v3', owner: '孙浩然',
    desc: '订单的状态字段发生变化时产生，一次迁移一条事件。', dedupKey: '订单号 + 新状态 + 源系统更新时间', dedupWindow: '24 小时', retention: '180 天', delivery: '至少一次', ordering: '同一订单内按发生时间有序', weekly: 48210,
    specific: [F('订单号', 'orderId', '字符串', true, '订单业务主键', 'SO20261025xxxx'), F('原状态', 'fromStatus', '枚举', true, '迁移前状态', '拣货中'), F('新状态', 'toStatus', '枚举', true, '迁移后状态', '已出库'), F('门店', 'storeId', '字符串', true, '下单门店', 'SH0031'), F('渠道', 'channel', '枚举', true, '下单渠道', '美团闪购'), F('操作来源', 'operator', '字符串', false, '源系统操作人或接口', 'wms-sync')] },
  { id: 'inventory_changed', name: '库存变动', api: 'InventoryChanged', source: '数据同步', sourceRef: '仓储 WMS · 每 5 分钟增量同步', object: '商品', objectApi: 'Product', objKind: 'product', status: '已发布', version: 'v2', owner: '周晓峰',
    desc: '门店可用库存变化超过 1 个销售单位时产生。', dedupKey: '门店 + SKU + 快照时间', dedupWindow: '1 小时', retention: '90 天', delivery: '至少一次', ordering: '同一门店同一 SKU 有序', weekly: 126480,
    specific: [F('SKU', 'sku', '字符串', true, '商品编码', '1011846'), F('门店', 'storeId', '字符串', true, '库存所在门店', 'SH0031'), F('变动前', 'before', '整数', true, '变动前可用库存', '58'), F('变动后', 'after', '整数', true, '变动后可用库存', '37'), F('变动原因', 'reason', '枚举', true, '销售 / 调拨 / 盘点 / 报损', '销售')] },
  { id: 'stock_below_safety', name: '库存低于安全线', api: 'StockBelowSafety', source: '规则', sourceRef: '规则 R-012 可用库存低于安全线', ruleId: 'R-012', object: '商品', objectApi: 'Product', objKind: 'product', status: '已发布', version: 'v2', owner: '周晓峰',
    desc: '规则 R-012 命中时产生，携带当前库存与安全库存。', dedupKey: '规则 + 门店 + SKU', dedupWindow: '24 小时（与规则冷却一致）', retention: '365 天', delivery: '至少一次', ordering: '不保证', weekly: 386,
    specific: [F('SKU', 'sku', '字符串', true, '商品编码', '1011846'), F('门店', 'storeId', '字符串', true, '门店编码', 'SH0031'), F('可用库存', 'available', '整数', true, '命中时可用库存', '37'), F('安全库存', 'safety', '整数', true, '门店安全库存', '120'), F('告警', 'alertId', '字符串', true, '生成的告警 ID', 'AL-20261025-0139')] },
  { id: 'store_sales_drop', name: '门店销售额环比下降', api: 'StoreSalesDropped', source: '规则', sourceRef: '规则 R-021 门店销售额环比下降', ruleId: 'R-021', object: '门店', objectApi: 'Store', objKind: 'store', status: '已发布', version: 'v2', owner: '陈立军',
    desc: '每周一按销售额（不含税）周环比计算，下降超过 15% 的门店各产生一条。', dedupKey: '规则 + 门店 + 周', dedupWindow: '7 天', retention: '365 天', delivery: '至少一次', ordering: '不保证', weekly: 9,
    specific: [F('门店', 'storeId', '字符串', true, '门店编码', 'HZ0058'), F('本周销售额', 'current', '金额', true, '不含税', '182,406.30'), F('上周销售额', 'previous', '金额', true, '不含税', '232,671.88'), F('环比', 'delta', '百分比', true, '周环比', '−21.6%'), F('告警', 'alertId', '字符串', true, '生成的告警 ID', 'AL-20261020-0074')] },
  { id: 'gross_margin_breach', name: '毛利率越线', api: 'GrossMarginBreached', source: '规则', sourceRef: '规则 R-007 商品毛利率越线', ruleId: 'R-007', object: '商品', objectApi: 'Product', objKind: 'product', status: '已发布', version: 'v3', owner: '刘婷',
    desc: '商品毛利率从 ≥ 18% 跌破 18% 时产生（只在越线那一次产生）。', dedupKey: '规则 + 商品 + 门店 + 日期', dedupWindow: '7 天', retention: '365 天', delivery: '至少一次', ordering: '不保证', weekly: 54,
    specific: [F('SKU', 'sku', '字符串', true, '商品编码', '1004512'), F('门店', 'storeId', '字符串', false, '为空表示全渠道', 'SH0102'), F('毛利率', 'margin', '百分比', true, '当前毛利率', '16.42%'), F('阈值', 'threshold', '百分比', true, '规则阈值', '18.00%'), F('告警', 'alertId', '字符串', true, '生成的告警 ID', 'AL-20261025-0127')] },
  { id: 'shipment_overdue', name: '出库超时', api: 'ShipmentOverdue', source: '规则', sourceRef: '规则 R-015 出库超 48 小时未签收', ruleId: 'R-015', object: '订单', objectApi: 'SalesOrder', objKind: 'order', status: '已发布', version: 'v1', owner: '陈立军',
    desc: '已出库超过 48 小时仍未签收的订单，每单产生一次。', dedupKey: '规则 + 订单', dedupWindow: '永久（不重复）', retention: '365 天', delivery: '至少一次', ordering: '不保证', weekly: 212,
    specific: [F('订单号', 'orderId', '字符串', true, '订单业务主键', 'SO20261025xxxx'), F('运单号', 'waybill', '字符串', true, '承运商运单号', 'SF1428xxxxxx'), F('出库时长', 'hours', '小数', true, '小时', '53.4'), F('承运商', 'carrier', '枚举', true, '承运商', '顺丰同城')] },
  { id: 'member_churn_risk', name: '会员流失预警', api: 'MemberChurnRisk', source: '外部系统', sourceRef: 'CRM 会员中心 · 流失模型每日推送', object: '会员', objectApi: 'Member', status: '草稿', version: 'v1', owner: '吴佳',
    desc: 'CRM 流失模型评分 ≥ 0.7 的会员，每天推送一次。', dedupKey: '会员 ID + 评分日期', dedupWindow: '24 小时', retention: '90 天', delivery: '至少一次', ordering: '不保证', weekly: 3120,
    specific: [F('会员', 'memberId', '字符串', true, '会员 ID', 'M31852207'), F('流失评分', 'score', '小数', true, '0–1', '0.82'), F('最近购买', 'lastPurchase', '日期', true, '最近一次购买日期', '2026-08-19'), F('常去门店', 'storeId', '字符串', false, '近 90 天消费最多的门店', 'SH0047')] },
  { id: 'illegal_transition', name: '订单状态非法迁移', api: 'IllegalOrderTransition', source: '规则', sourceRef: '状态机 SM-ORD 订单生命周期 v4', ruleId: 'SM-ORD', object: '订单', objectApi: 'SalesOrder', objKind: 'order', status: '已发布', version: 'v4', owner: '孙浩然',
    desc: '订单状态变更不在生命周期 v4 允许的迁移里时产生。', dedupKey: '事件 ID', dedupWindow: '24 小时', retention: '365 天', delivery: '至少一次', ordering: '同一订单内有序', weekly: 31,
    specific: [F('订单号', 'orderId', '字符串', true, '订单业务主键', 'SO20261025xxxx'), F('原状态', 'fromStatus', '枚举', true, '迁移前状态', '已签收'), F('新状态', 'toStatus', '枚举', true, '迁移后状态', '待支付'), F('原始事件', 'causeEventId', '字符串', true, '触发的订单状态变更事件', 'EV-…'), F('告警', 'alertId', '字符串', true, '生成的告警 ID', 'AL-20261025-0143')] },
  { id: 'action_executed', name: '动作执行完成', api: 'ActionExecuted', source: '动作', sourceRef: '所有已发布动作类型 · 写回成功后', object: '动作执行', objectApi: 'ActionRun', status: '已发布', version: 'v2', owner: '陈立军',
    desc: '任一动作写回成功（含本体编辑提交）后产生。', dedupKey: '执行记录 ID', dedupWindow: '24 小时', retention: '365 天', delivery: '至少一次', ordering: '同一目标对象有序', weekly: 1864,
    specific: [F('执行记录', 'runId', '字符串', true, '执行记录 ID', 'ACT-20261025-2135'), F('动作类型', 'actionType', '字符串', true, '动作 API 名', 'requestReplenishment'), F('目标对象', 'targetId', '字符串', true, '动作目标对象 ID', '1011846'), F('发起人', 'initiator', '字符串', true, '人或规则', '规则 R-012'), F('确认人', 'confirmer', '字符串', true, '确认提交的人', '周晓峰'), F('写回结果', 'writeback', '字符串', false, '外部系统返回码', 'HTTP 200')] },
  { id: 'action_outcome_unknown', name: '动作结果未知', api: 'ActionOutcomeUnknown', source: '动作', sourceRef: '外部写回超时或无响应时', object: '动作执行', objectApi: 'ActionRun', status: '已发布', version: 'v1', owner: '陈立军',
    desc: '写回外部系统超时、无响应时产生（不自动重试）；系统按幂等键自动查询，查不到再人工对账，结果确认后再产生“动作执行完成”或失败通知。', dedupKey: '执行记录 ID', dedupWindow: '24 小时', retention: '365 天', delivery: '至少一次', ordering: '不保证', weekly: 37,
    specific: [F('执行记录', 'runId', '字符串', true, '执行记录 ID', 'ACT-20261025-2134'), F('动作类型', 'actionType', '字符串', true, '动作 API 名', 'requestReplenishment'), F('外部系统', 'system', '字符串', true, '写回目标（外部系统 ID）', 'EXT-ERP 用友 ERP'), F('幂等键', 'idemKey', '字符串', true, '自动查询与对账用', 'act-al-20261025-0139'), F('等待时长', 'waitedMs', '整数', true, '毫秒（超时即记为结果未知，不重试）', '10000')] },
  { id: 'coupon_redeemed', name: '优惠券核销', api: 'CouponRedeemed', source: '外部系统', sourceRef: 'CRM 营销中心 · Webhook 推送', object: '会员', objectApi: 'Member', status: '已发布', version: 'v1', owner: '吴佳',
    desc: '会员在任一渠道核销优惠券时，由 CRM 实时推送。', dedupKey: '券号', dedupWindow: '永久（不重复）', retention: '365 天', delivery: '至少一次', ordering: '不保证', weekly: 8640,
    specific: [F('券号', 'couponNo', '字符串', true, 'CRM 券号', 'CP2610250012'), F('会员', 'memberId', '字符串', true, '会员 ID', 'M31852207'), F('订单号', 'orderId', '字符串', true, '核销订单', 'SO20261025xxxx'), F('核销门店', 'storeId', '字符串', true, '门店编码', 'SH0047'), F('优惠金额', 'discount', '金额', true, '元', '15.00')] },
  { id: 'refund_created', name: '退款单创建', api: 'RefundCreated', source: '数据同步', sourceRef: '用友 ERP · CDC 增量同步', object: '订单', objectApi: 'SalesOrder', objKind: 'order', status: '已发布', version: 'v1', owner: '孙浩然',
    desc: 'ERP 生成退款单时产生。', dedupKey: '退款单号', dedupWindow: '24 小时', retention: '180 天', delivery: '至少一次', ordering: '同一订单内有序', weekly: 1420,
    specific: [F('退款单号', 'refundId', '字符串', true, 'ERP 退款单号', 'RF2610250031'), F('订单号', 'orderId', '字符串', true, '原订单', 'SO20261025xxxx'), F('退款金额', 'amount', '金额', true, '元', '54.90'), F('退款原因', 'reason', '枚举', true, '售后原因', '商品破损')] },
  { id: 'store_price_changed', name: '门店价格变更', api: 'StorePriceChanged', source: '外部系统', sourceRef: 'POS 价格中心 · 每日 0 点批量推送', object: '商品', objectApi: 'Product', objKind: 'product', status: '变更中', version: 'v2', owner: '王敏',
    desc: 'POS 价格中心下发的新价生效时产生，一店一品一条。', dedupKey: '门店 + SKU + 生效日期', dedupWindow: '24 小时', retention: '365 天', delivery: '至少一次', ordering: '不保证', weekly: 2260,
    specific: [F('SKU', 'sku', '字符串', true, '商品编码', '1004512'), F('门店', 'storeId', '字符串', true, '门店编码', 'SH0031'), F('原价', 'oldPrice', '金额', true, '元', '69.90'), F('新价', 'newPrice', '金额', true, '元', '64.90'), F('生效日期', 'effectiveDate', '日期', true, '新价生效日', '2026-10-25')] },
  { id: 'waybill_signed', name: '运单签收', api: 'WaybillSigned', source: '外部系统', sourceRef: '顺丰速运 · 承运商回调', object: '订单', objectApi: 'SalesOrder', objKind: 'order', status: '已发布', version: 'v1', owner: '周晓峰',
    desc: '承运商回传签收时产生。', dedupKey: '运单号', dedupWindow: '永久（不重复）', retention: '180 天', delivery: '至少一次', ordering: '不保证', weekly: 31240,
    specific: [F('运单号', 'waybill', '字符串', true, '承运商运单号', 'SF1428xxxxxx'), F('订单号', 'orderId', '字符串', true, '订单业务主键', 'SO20261025xxxx'), F('签收时间', 'signedAt', '日期时间', true, '承运商回传', '2026-10-25 14:08:51'), F('签收人', 'signer', '字符串', false, '脱敏', '王*')] },
  { id: 'alert_closed', name: '告警关闭', api: 'AlertClosed', source: '动作', sourceRef: '动作“关闭告警”执行成功后', object: '告警', objectApi: 'Alert', status: '已发布', version: 'v1', owner: '陈立军',
    desc: '告警被关闭（已解决 / 已忽略）时产生。', dedupKey: '告警 ID', dedupWindow: '永久（不重复）', retention: '365 天', delivery: '至少一次', ordering: '不保证', weekly: 318,
    specific: [F('告警', 'alertId', '字符串', true, '告警 ID', 'AL-20261024-0112'), F('结论', 'resolution', '枚举', true, '已解决 / 已忽略', '已解决'), F('处理人', 'closedBy', '字符串', true, '关闭人', '陈立军'), F('说明', 'note', '文本', false, '处理说明', '承运商已补派')] },
];
export const eventTypeById = (id: string) => EVENT_TYPES.find((e) => e.id === id);

/** 所有事件共有的信封字段 + 事件自身字段 */
const ENVELOPE: PayloadField[] = [
  F('事件 ID', 'eventId', '字符串', true, '全局唯一', 'EV-20261025-0001234'),
  F('事件类型', 'eventType', '字符串', true, '事件类型 API 名', 'OrderStatusChanged'),
  F('发生时间', 'occurredAt', '日期时间', true, '业务发生时间（源系统）', '2026-10-25 15:40:58'),
  F('接收时间', 'ingestedAt', '日期时间', true, '平台接收时间', '2026-10-25 15:41:02'),
  F('来源', 'source', '字符串', true, '源系统 / 规则 / 动作', 'erp-cdc'),
  F('对象类型', 'objectType', '字符串', true, '本体对象类型', 'SalesOrder'),
  F('对象 ID', 'objectId', '字符串', true, '本体对象主键', 'SO20261025xxxx'),
  F('本体版本', 'ontologyVersion', '字符串', true, '产生事件时的本体发布版本', 'v2026.10.3'),
  F('去重键', 'dedupKey', '字符串', true, '按事件类型规则计算', '—'),
  F('追踪 ID', 'traceId', '字符串', false, '跨系统链路追踪', '7f3a…c21e'),
];
export const payloadOf = (e: EventType) => [...e.specific, ...ENVELOPE];

/* 订阅方 */
export interface Subscriber { kind: '规则' | '自动化' | '通知' | '外部订阅'; name: string; ruleId?: string; filter: string; then: string; status: '启用' | '停用' | '异常'; weekly: number }
const SUBS: Record<string, Subscriber[]> = {
  order_status_changed: [
    { kind: '规则', name: '订单状态非法迁移', ruleId: 'SM-ORD', filter: '全部', then: '校验生命周期 v4，非法则产生“订单状态非法迁移”', status: '启用', weekly: 48210 },
    { kind: '自动化', name: '已出库订单启动签收计时', filter: '新状态 = 已出库', then: '写入出库时间，供 R-015 计算', status: '启用', weekly: 17630 },
    { kind: '外部订阅', name: '数据仓库 ODS 订单流水', filter: '全部', then: '投递 Kafka topic ontology.order.status', status: '启用', weekly: 48210 },
    { kind: '通知', name: '大额订单取消提醒', filter: '新状态 = 已取消 且 金额 ≥ 2,000', then: '企业微信通知门店店长', status: '停用', weekly: 0 },
  ],
  inventory_changed: [
    { kind: '规则', name: '可用库存低于安全线', ruleId: 'R-012', filter: '变动后 < 变动前', then: '计算可用库存 < 安全库存', status: '启用', weekly: 61240 },
    { kind: '外部订阅', name: '补货模型特征', filter: '全部', then: '投递 Kafka topic ontology.inventory', status: '启用', weekly: 126480 },
  ],
};
const SUBS_RULE = (e: EventType): Subscriber[] => {
  const rule = e.ruleId ? RULES[e.ruleId] : undefined;
  const list: Subscriber[] = [];
  const act = e.ruleId && RULE_ACTION[e.ruleId] ? actionTypeById(RULE_ACTION[e.ruleId]) : undefined;
  if (act) list.push({ kind: '自动化', name: `生成动作“${act.name}”`, filter: '告警级别 ∈ {高, 中}', then: `生成“${act.name}”，待 ${ruleConfirmer(e.ruleId!)} 确认后提交`, status: '启用', weekly: Math.round(e.weekly * 0.6) });
  list.push({ kind: '通知', name: '告警推送', filter: '全部', then: '企业微信通知规则订阅人', status: '启用', weekly: e.weekly });
  if (e.id === 'gross_margin_breach') list.push({ kind: '规则', name: '门店销售额环比下降', ruleId: 'R-021', filter: '同门店当周 ≥ 3 条', then: '作为辅助信号参与周度复核', status: '启用', weekly: 6 });
  list.push({ kind: '外部订阅', name: '经营分析数据集', filter: '全部', then: '投递 Kafka topic ontology.alerts', status: e.source === '规则' ? '启用' : '异常', weekly: e.weekly });
  return list;
};
export function subscribersOf(e: EventType): Subscriber[] {
  if (SUBS[e.id]) return SUBS[e.id];
  if (e.source === '规则') return SUBS_RULE(e);
  if (e.id === 'action_outcome_unknown') return [
    { kind: '自动化', name: '写回对账任务', filter: '全部', then: '系统按幂等键自动查询，查不到再人工对账；确认后补发结果', status: '启用', weekly: e.weekly },
    { kind: '通知', name: '结果未知提醒', filter: '等待时长 ≥ 30 分钟', then: '企业微信通知发起人与确认人', status: '启用', weekly: 9 },
  ];
  return [
    { kind: '自动化', name: `${e.name}后更新${e.object}`, filter: '全部', then: `回写${e.object}的相关字段`, status: '启用', weekly: e.weekly },
    { kind: '外部订阅', name: '数据仓库 ODS', filter: '全部', then: `投递 Kafka topic ontology.${e.api}`, status: '启用', weekly: e.weekly },
  ];
}

/* 近期事件 */
export type EventOutcome = '已投递' | '已去重' | '已生成告警' | '投递失败' | '部分投递';
export interface EventRecord {
  id: string; typeId: string; at: string; objKind?: 'store' | 'product' | 'order' | 'action'; objId: string; objLabel: string;
  summary: string; outcome: EventOutcome; subscribers: number; dedupKey: string; latencyMs: number;
}
function eventObj(e: EventType, r: () => number): Pick<EventRecord, 'objKind' | 'objId' | 'objLabel'> {
  if (e.objKind === 'order') { const o = ORDERS[Math.floor(r() * 600)]; return { objKind: 'order', objId: o.id, objLabel: o.id }; }
  if (e.objKind === 'product') { const p = PRODUCTS[Math.floor(r() * PRODUCTS.length)]; return { objKind: 'product', objId: p.sku, objLabel: p.name }; }
  if (e.objKind === 'store') { const s = STORES[Math.floor(r() * STORES.length)]; return { objKind: 'store', objId: s.id, objLabel: s.name }; }
  if (e.object === '告警') { const al = ALERTS[Math.floor(r() * ALERTS.length)]; return { objId: al.id, objLabel: al.id }; }
  if (e.object === '动作执行') {
    // 动作执行完成只引用已成功 / 已对账的执行记录，动作结果未知只引用结果未知的
    const pool = RUNS.filter((x) => (e.id === 'action_executed' ? x.status === '成功' || x.status === '已对账' : x.status === '结果未知'));
    const run = (pool.length ? pool : RUNS)[Math.floor(r() * (pool.length || RUNS.length))];
    return { objKind: 'action', objId: run.id, objLabel: run.id };
  }
  const m = `M${String(31000000 + Math.floor(r() * 899999)).slice(0, 8)}`;
  return { objId: m, objLabel: `会员 ${m}` };
}
function eventSummary(e: EventType, r: () => number) {
  const st = ['待出库', '拣货中', '已出库', '已签收'];
  switch (e.id) {
    case 'order_status_changed': { const i = Math.floor(r() * 3); return `${st[i]} → ${st[i + 1]}`; }
    case 'inventory_changed': { const a = Math.floor(30 + r() * 200); return `可用 ${a} → ${a - Math.floor(1 + r() * 12)} · 销售`; }
    case 'stock_below_safety': return `可用 ${Math.floor(10 + r() * 60)} · 安全 ${[60, 120, 200][Math.floor(r() * 3)]}`;
    case 'store_sales_drop': return `周环比 −${(15 + r() * 9).toFixed(1)}%`;
    case 'gross_margin_breach': return `毛利率 ${(15 + r() * 3).toFixed(2)}% · 阈值 18.00%`;
    case 'shipment_overdue': return `出库 ${(48 + r() * 12).toFixed(1)} 小时 · 顺丰同城`;
    case 'illegal_transition': return ['已签收 → 待支付', '已取消 → 已出库', '已出库 → 待支付'][Math.floor(r() * 3)];
    case 'refund_created': return `退款 ${(10 + r() * 300).toFixed(2)} 元`;
    case 'store_price_changed': return `售价 ${(20 + r() * 50).toFixed(2)} → 新价次日生效`;
    case 'coupon_redeemed': return `满 99 减 ${[10, 15, 20][Math.floor(r() * 3)]}`;
    case 'waybill_signed': return '已签收 · 本人';
    case 'alert_closed': return r() < 0.8 ? '已解决' : '已忽略';
    case 'member_churn_risk': return `流失评分 ${(0.7 + r() * 0.28).toFixed(2)}`;
    default: return '写回成功';
  }
}
const EVENTS: EventRecord[] = [];
const EVENT_BY_ID = new Map<string, EventRecord>();
EVENT_TYPES.forEach((e, ei) => {
  if (e.status === '草稿') return;
  const r = seeded(7300 + ei * 53);
  const n = 64 + Math.floor(r() * 120);
  const gap = Math.max(20, Math.floor((7 * 86400) / Math.min(e.weekly, n * 1.2)));
  let t = NOW - Math.floor(r() * 300) * 1000;
  for (let i = 0; i < n; i++) {
    t -= Math.floor(gap * (0.4 + r() * 1.2)) * 1000;
    const x = r();
    let outcome: EventOutcome = e.source === '规则' ? '已生成告警' : '已投递';
    if (x < 0.04) outcome = '投递失败';
    else if (x < 0.08) outcome = '部分投递';
    else if (x < 0.16) outcome = '已去重';
    if (i === 2) outcome = '投递失败';
    const d = new Date(t);
    const id = `EV-${d.getFullYear()}${pad(d.getMonth() + 1)}${pad(d.getDate())}-${pad(ei, 2)}${pad(n - i, 5)}`;
    const obj = eventObj(e, r);
    const rec: EventRecord = { id, typeId: e.id, at: fmtTime(t), ...obj, summary: eventSummary(e, r), outcome,
      subscribers: outcome === '已去重' ? 0 : subscribersOf(e).filter((s) => s.status !== '停用').length, dedupKey: `${e.api}:${obj.objId}`, latencyMs: Math.floor(40 + r() * 900) };
    EVENTS.push(rec); EVENT_BY_ID.set(id, rec);
  }
});
export const eventsOf = (typeId: string) => EVENTS.filter((e) => e.typeId === typeId);
export const eventById = (id: string) => EVENT_BY_ID.get(id);
export function eventDaily(typeId: string, weekly: number) {
  const r = seeded(hashOf(typeId) + 11);
  return Array.from({ length: 14 }, (_, i) => {
    const d = new Date(NOW - (13 - i) * 86400000);
    return { date: `${pad(d.getMonth() + 1)}-${pad(d.getDate())}`, value: Math.round((weekly / 7) * (0.7 + r() * 0.6)) };
  });
}
export const EVENT_VERSION_NOTES = ['载荷增加“操作来源”字段', '去重键加入源系统更新时间，避免乱序重复', '保留期由 90 天延长到 180 天'];

/** 动作 / 事件类型引用到的规则 */
export const ruleById = (id: string) => RULES[id];
