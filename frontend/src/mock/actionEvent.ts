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
import type { DraftCR } from './releases';

const pad = (n: number, w = 2) => String(n).padStart(w, '0');
const fmtTime = (t: number) => {
  const d = new Date(t);
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
};
const NOW = new Date('2026-10-25T15:42:18+08:00').getTime();
const hashOf = (s: string) => [...s].reduce((h, c) => (h * 31 + c.charCodeAt(0)) >>> 0, 7);

/* ───────────────────────── 动作类型 ───────────────────────── */

export type PubStatus = '已发布' | '变更中' | '草稿';
/** 执行结果：成功灰、失败红、结果未知橙（带问号，文案“结果未知 · 待对账”） */
export type RunStatus = '成功' | '失败' | '结果未知' | '待审批' | '已驳回';

export interface ActionType {
  id: string; name: string; api: string; group: string; status: PubStatus; version: string;
  target: string; targetApi: string; targetKind?: 'store' | 'product' | 'order' | 'region';
  owner: string; desc: string; runners: string; approval: string;
  system: string; endpoint: string; idemKey: string; timeout: string; retry: string; idempotent: boolean;
  usedBy: string[]; // 规则 ID
}

export const ACTION_GROUPS = ['全部', '营销', '组织', '交易', '供应链', '定价', '告警'];

export const ACTION_TYPES: ActionType[] = [
  { id: 'issue_recall_coupon', name: '发放复购召回优惠券', api: 'issueRecallCoupon', group: '营销', status: '已发布', version: 'v3', target: '会员', targetApi: 'Member', owner: '吴佳',
    desc: '向 60 天未复购的会员发放定向召回券，券由 CRM 营销中心生成并通过小程序推送。同一会员同一活动只发一次。',
    runners: '会员运营组、门店店长（仅本店会员）', approval: '单次 ≤ 500 张免审；超过 500 张需营销经理审批', system: 'CRM 营销中心', endpoint: 'POST /crm/v2/coupons/issue',
    idemKey: 'issueRecallCoupon:{会员ID}:{活动ID}', timeout: '8 秒', retry: '幂等接口，失败自动重试 3 次（2s / 8s / 30s）', idempotent: true, usedBy: ['R-021'] },
  { id: 'assign_region_manager', name: '指派大区经理跟进', api: 'assignRegionManager', group: '组织', status: '已发布', version: 'v2', target: '大区', targetApi: 'Region', targetKind: 'region', owner: '陈立军',
    desc: '把大区级经营异常指派给大区经理跟进，生成跟进任务并在企业微信提醒。', runners: '经营分析组、区域运营总监', approval: '免审', system: '本体（无外部写回）', endpoint: '—',
    idemKey: 'assignRegionManager:{大区ID}:{告警ID}', timeout: '3 秒', retry: '本体内部编辑，失败直接报错，不重试', idempotent: true, usedBy: ['R-021'] },
  { id: 'adjust_region_budget', name: '调整大区预算', api: 'adjustRegionBudget', group: '组织', status: '变更中', version: 'v2', target: '大区', targetApi: 'Region', targetKind: 'region', owner: '刘婷',
    desc: '调整大区当月营销费用预算，写回财务 FMS 预算台账。', runners: '财务 BP、区域运营总监', approval: '调整幅度 ≤ 5% 由财务 BP 审批；> 5% 需财务总监二级审批', system: '财务 FMS', endpoint: 'POST /fms/budget/adjust',
    idemKey: 'adjustRegionBudget:{大区ID}:{预算月份}:{申请单号}', timeout: '10 秒', retry: '非幂等接口，不自动重试', idempotent: false, usedBy: [] },
  { id: 'correct_order_status', name: '订单状态修正', api: 'correctOrderStatus', group: '交易', status: '已发布', version: 'v4', target: '订单', targetApi: 'SalesOrder', targetKind: 'order', owner: '孙浩然',
    desc: '订单出现非法状态迁移时，按生命周期 v4 把订单改回合法状态，并写回 ERP 订单中心。', runners: '订单运营组', approval: '免审（仅允许改为生命周期内的合法状态）', system: 'ERP 订单中心', endpoint: 'PUT /erp/orders/{id}/status',
    idemKey: 'correctOrderStatus:{订单号}:{目标状态}', timeout: '5 秒', retry: '幂等接口，失败自动重试 3 次', idempotent: true, usedBy: ['SM-ORD'] },
  { id: 'request_replenish', name: '补货申请', api: 'requestReplenishment', group: '供应链', status: '已发布', version: 'v3', target: '商品', targetApi: 'Product', targetKind: 'product', owner: '周晓峰',
    desc: '可用库存低于安全线时生成补货申请，推送 WMS 生成调拨 / 采购建议。', runners: '门店店长、供应链计划员', approval: '补货金额 ≤ 2 万元免审；超过需区域供应链经理审批', system: 'WMS 仓储', endpoint: 'POST /wms/replenish/requests',
    idemKey: 'requestReplenishment:{门店ID}:{SKU}:{日期}', timeout: '8 秒', retry: '幂等接口，失败自动重试 3 次', idempotent: true, usedBy: ['R-012'] },
  { id: 'adjust_store_price', name: '门店调价', api: 'adjustStorePrice', group: '定价', status: '已发布', version: 'v2', target: '商品', targetApi: 'Product', targetKind: 'product', owner: '王敏',
    desc: '调整单店单品的售价，审批通过后下发 POS 价格中心，次日 0 点生效。', runners: '品类经理', approval: '降价幅度 > 10% 或低于成本价需定价委员会审批', system: 'POS 价格中心', endpoint: 'POST /pos/price/changes',
    idemKey: 'adjustStorePrice:{门店ID}:{SKU}:{生效日期}', timeout: '8 秒', retry: '幂等接口，失败自动重试 2 次', idempotent: true, usedBy: [] },
  { id: 'start_price_approval', name: '发起调价审批', api: 'startPriceApproval', group: '定价', status: '已发布', version: 'v3', target: '商品', targetApi: 'Product', targetKind: 'product', owner: '王敏',
    desc: '毛利率越线时发起调价审批流程，审批通过后自动执行“门店调价”。', runners: '品类经理、规则自动执行', approval: '定价委员会审批', system: 'OA 审批', endpoint: 'POST /oa/flows/price-approval',
    idemKey: 'startPriceApproval:{SKU}:{门店ID}:{日期}', timeout: '6 秒', retry: '幂等接口，失败自动重试 3 次', idempotent: true, usedBy: ['R-007'] },
  { id: 'close_alert', name: '关闭告警', api: 'closeAlert', group: '告警', status: '已发布', version: 'v1', target: '告警', targetApi: 'Alert', owner: '陈立军',
    desc: '填写处理结论后关闭告警，告警进入“已解决”或“已忽略”。', runners: '告警处理人', approval: '免审', system: '本体（无外部写回）', endpoint: '—',
    idemKey: 'closeAlert:{告警ID}', timeout: '3 秒', retry: '本体内部编辑，失败直接报错，不重试', idempotent: true, usedBy: ['R-007', 'R-012', 'R-015', 'R-021', 'SM-ORD'] },
  { id: 'urge_logistics', name: '发起物流催派', api: 'urgeLogistics', group: '交易', status: '已发布', version: 'v1', target: '订单', targetApi: 'SalesOrder', targetKind: 'order', owner: '陈立军',
    desc: '出库超时未签收的订单，向承运商 TMS 发起催派工单。', runners: '订单运营组、规则自动执行', approval: '免审', system: 'TMS 运输', endpoint: 'POST /tms/tickets/urge',
    idemKey: 'urgeLogistics:{订单号}:{运单号}', timeout: '8 秒', retry: '幂等接口，失败自动重试 3 次', idempotent: true, usedBy: ['R-015'] },
  { id: 'claim_alert', name: '认领告警', api: 'claimAlert', group: '告警', status: '已发布', version: 'v1', target: '告警', targetApi: 'Alert', owner: '陈立军',
    desc: '把告警认领给自己，告警进入“处理中”。', runners: '告警所属规则的订阅人', approval: '免审', system: '本体（无外部写回）', endpoint: '—',
    idemKey: 'claimAlert:{告警ID}', timeout: '3 秒', retry: '本体内部编辑，失败直接报错，不重试', idempotent: true, usedBy: ['R-007', 'R-012', 'R-015', 'R-021', 'SM-ORD'] },
  { id: 'reassign_alert', name: '转派告警', api: 'reassignAlert', group: '告警', status: '已发布', version: 'v1', target: '告警', targetApi: 'Alert', owner: '陈立军',
    desc: '把告警转给其他处理人，并通知新处理人。', runners: '当前处理人、告警管理员', approval: '免审', system: '本体（无外部写回）', endpoint: '—',
    idemKey: 'reassignAlert:{告警ID}:{新处理人}', timeout: '3 秒', retry: '本体内部编辑，失败直接报错，不重试', idempotent: true, usedBy: [] },
  { id: 'reissue_shipment', name: '补发出库单', api: 'reissueShipment', group: '交易', status: '已发布', version: 'v2', target: '订单', targetApi: 'SalesOrder', targetKind: 'order', owner: '周晓峰',
    desc: '丢件或破损时按原订单补发，WMS 生成新出库单。', runners: '客服主管、订单运营组', approval: '补发金额 ≤ 300 元免审', system: 'WMS 仓储', endpoint: 'POST /wms/outbound/reissue',
    idemKey: 'reissueShipment:{订单号}:{补发原因}', timeout: '8 秒', retry: '非幂等接口，不自动重试', idempotent: false, usedBy: [] },
  { id: 'approve_refund', name: '退款审核', api: 'approveRefund', group: '交易', status: '已发布', version: 'v2', target: '订单', targetApi: 'SalesOrder', targetKind: 'order', owner: '孙浩然',
    desc: '审核售后退款申请，通过后 ERP 生成退款单并原路退回。', runners: '客服主管', approval: '退款 ≤ 200 元免审；超过需门店店长确认', system: 'ERP 订单中心', endpoint: 'POST /erp/refunds/approve',
    idemKey: 'approveRefund:{退款申请号}', timeout: '6 秒', retry: '幂等接口，失败自动重试 3 次', idempotent: true, usedBy: [] },
  { id: 'change_member_level', name: '调整会员等级', api: 'changeMemberLevel', group: '营销', status: '草稿', version: 'v1', target: '会员', targetApi: 'Member', owner: '吴佳',
    desc: '人工调整会员等级（补偿、投诉处理），写回 CRM 会员中心。', runners: '会员运营组', approval: '升 2 级及以上需会员运营经理审批', system: 'CRM 会员中心', endpoint: 'PUT /crm/v2/members/{id}/level',
    idemKey: 'changeMemberLevel:{会员ID}:{目标等级}', timeout: '6 秒', retry: '幂等接口，失败自动重试 2 次', idempotent: true, usedBy: [] },
  { id: 'change_store_status', name: '变更门店状态', api: 'changeStoreStatus', group: '组织', status: '已发布', version: 'v1', target: '门店', targetApi: 'Store', targetKind: 'store', owner: '陈立军',
    desc: '门店营业中 / 装修中 / 已闭店之间切换，写回 ERP 组织主数据。', runners: '区域运营总监', approval: '闭店需运营副总裁审批', system: 'ERP 主数据', endpoint: 'PUT /erp/org/stores/{id}/status',
    idemKey: 'changeStoreStatus:{门店ID}:{目标状态}:{生效日期}', timeout: '6 秒', retry: '幂等接口，失败自动重试 2 次', idempotent: true, usedBy: [] },
  { id: 'set_safety_stock', name: '调整安全库存', api: 'setSafetyStock', group: '供应链', status: '草稿', version: 'v1', target: '商品', targetApi: 'Product', targetKind: 'product', owner: '周晓峰',
    desc: '按门店调整单品安全库存，影响“可用库存低于安全线”规则的阈值。', runners: '供应链计划员', approval: '免审', system: 'WMS 仓储', endpoint: 'PUT /wms/safety-stock',
    idemKey: 'setSafetyStock:{门店ID}:{SKU}', timeout: '6 秒', retry: '幂等接口，失败自动重试 2 次', idempotent: true, usedBy: ['R-012'] },
];
export const actionTypeById = (id: string) => ACTION_TYPES.find((a) => a.id === id);

/** 变更中的动作类型对应的变更单（本页本地草稿，不写入 releases.ts） */
export const ACTION_DRAFTS: Record<string, DraftCR> = {
  adjust_region_budget: { id: 'CR-0010', title: '调整大区预算：超 5% 增加二级审批', owner: '刘婷', updatedAt: '2026-10-25 11:26', changes: 3 },
};

/* 参数 */
export interface ActionParam { name: string; api: string; type: string; required: boolean; def: string; rule: string }
const P = (name: string, api: string, type: string, required: boolean, def = '—', rule = '—'): ActionParam => ({ name, api, type, required, def, rule });

const PARAMS: Record<string, ActionParam[]> = {
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
export function writebackOf(a: ActionType): { edits: OntologyEdit[]; effects: SideEffect[] } {
  const internal = a.endpoint === '—';
  const edits: OntologyEdit[] = a.id === 'issue_recall_coupon'
    ? [
        { op: '新建', obj: '优惠券发放记录', field: '会员、活动、券号（CRM 返回）、面额、有效期' },
        { op: '修改', obj: '会员', field: '最近召回时间、召回次数 + 1' },
        { op: '关联', obj: '会员 ↔ 营销活动', field: '参与记录' },
      ]
    : [{ op: '修改', obj: a.target, field: '状态、最近操作人、最近操作时间' }, { op: '新建', obj: '操作记录', field: '动作、参数快照、执行人' }];
  const effects: SideEffect[] = [
    { kind: '事件', target: 'ActionExecuted 动作执行完成', when: '写回成功后', note: '供规则与自动化订阅' },
    { kind: '通知', target: a.id === 'issue_recall_coupon' ? '企业微信 · 会员常去门店店长' : '企业微信 · 执行人', when: '写回成功后', note: '失败不影响动作结果' },
  ];
  if (!internal) effects.push({ kind: '事件', target: 'ActionOutcomeUnknown 动作结果未知', when: '写回超时或无响应', note: '进入对账队列，每 15 分钟按幂等键查询一次' });
  return { edits, effects };
}

/* 执行记录 */
export interface ActionRun {
  id: string; typeId: string; at: string; by: string; source: string;
  objKind?: 'store' | 'product' | 'order' | 'region'; objId: string; objLabel: string;
  summary: string; status: RunStatus; ms: number; code: string; ruleId?: string; alertId?: string;
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
function summaryFor(a: ActionType, r: () => number) {
  switch (a.id) {
    case 'issue_recall_coupon': return `满 99 减 ${[10, 15, 20][Math.floor(r() * 3)]} · 14 天 · 小程序`;
    case 'request_replenish': return `补货 ${Math.floor(24 + r() * 180)} 件 · 仓库调拨`;
    case 'adjust_store_price': return `售价 → ${(9 + r() * 60).toFixed(2)} 元 · 次日生效`;
    case 'start_price_approval': return `建议降价 ${(3 + r() * 9).toFixed(1)}%`;
    case 'correct_order_status': return `→ ${['已出库', '已签收', '拣货中'][Math.floor(r() * 3)]} · 系统同步乱序`;
    case 'urge_logistics': return `催派 · 出库 ${(48 + r() * 20).toFixed(1)} 小时`;
    case 'adjust_region_budget': return `会员召回预算 ${r() < 0.5 ? '+' : '−'}${(1 + r() * 8).toFixed(1)}%`;
    case 'approve_refund': return `退款 ${(20 + r() * 400).toFixed(2)} 元`;
    default: return '—';
  }
}
const RUNS: ActionRun[] = [];
const RUN_BY_ID = new Map<string, ActionRun>();
ACTION_TYPES.forEach((a, ai) => {
  if (a.status === '草稿') return;
  const r = seeded(9100 + ai * 37);
  const n = 46 + Math.floor(r() * 90);
  let t = NOW - Math.floor(r() * 1800) * 1000;
  const internal = a.endpoint === '—';
  for (let i = 0; i < n; i++) {
    t -= Math.floor(600 + r() * 9000) * 1000;
    const x = r();
    let status: RunStatus = '成功';
    if (x < 0.06) status = '失败';
    else if (x < 0.11 && !internal) status = '结果未知';
    else if (x < 0.15 && a.approval !== '免审') status = '待审批';
    else if (x < 0.17 && a.approval !== '免审') status = '已驳回';
    if (i === 1 && !internal) status = '结果未知';
    if (i === 3) status = '失败';
    const rule = a.usedBy.length && r() < 0.55 ? a.usedBy[Math.floor(r() * a.usedBy.length)] : undefined;
    const alert = rule ? ALERTS.find((al) => al.ruleId === rule) : undefined;
    const d = new Date(t);
    const id = `AR-${d.getFullYear()}${pad(d.getMonth() + 1)}${pad(d.getDate())}-${pad(ai, 2)}${pad(n - i, 3)}`;
    const run: ActionRun = {
      id, typeId: a.id, at: fmtTime(t), by: rule ? `规则 ${rule}` : PEOPLE[Math.floor(r() * PEOPLE.length)],
      source: rule ? '规则自动执行' : r() < 0.3 ? '告警处理' : '手动',
      ...targetFor(a, r), summary: summaryFor(a, r), status,
      ms: status === '结果未知' ? parseInt(a.timeout, 10) * 1000 : Math.floor(180 + r() * 2400),
      code: status === '成功' ? (internal ? '本体已提交' : 'HTTP 200') : status === '失败' ? (internal ? '提交条件未通过' : r() < 0.5 ? 'HTTP 409 业务校验失败' : 'HTTP 500') : status === '结果未知' ? '超时无响应' : status === '待审批' ? '审批中' : '审批驳回',
      ruleId: rule, alertId: alert?.id,
    };
    RUNS.push(run); RUN_BY_ID.set(id, run);
  }
});
export const runsOf = (typeId: string) => RUNS.filter((r) => r.typeId === typeId);
export const runById = (id: string) => RUN_BY_ID.get(id);
/** 近 30 天统计（按执行记录汇总） */
export function runStats(typeId: string) {
  const rows = runsOf(typeId);
  const c = (s: RunStatus) => rows.filter((r) => r.status === s).length;
  const ok = c('成功');
  const done = rows.length - c('待审批');
  return { total: rows.length, ok, failed: c('失败'), unknown: c('结果未知'), pending: c('待审批'), rate: done ? ok / done : 0,
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
  { id: 'order_status_changed', name: '订单状态变更', api: 'OrderStatusChanged', source: '数据同步', sourceRef: 'ERP 订单中心 · CDC 增量同步', object: '订单', objectApi: 'SalesOrder', objKind: 'order', status: '已发布', version: 'v3', owner: '孙浩然',
    desc: '订单的状态字段发生变化时产生，一次迁移一条事件。', dedupKey: '订单号 + 新状态 + 源系统更新时间', dedupWindow: '24 小时', retention: '180 天', delivery: '至少一次', ordering: '同一订单内按发生时间有序', weekly: 48210,
    specific: [F('订单号', 'orderId', '字符串', true, '订单业务主键', 'SO20261025xxxx'), F('原状态', 'fromStatus', '枚举', true, '迁移前状态', '拣货中'), F('新状态', 'toStatus', '枚举', true, '迁移后状态', '已出库'), F('门店', 'storeId', '字符串', true, '下单门店', 'SH0031'), F('渠道', 'channel', '枚举', true, '下单渠道', '美团闪购'), F('操作来源', 'operator', '字符串', false, '源系统操作人或接口', 'wms-sync')] },
  { id: 'inventory_changed', name: '库存变动', api: 'InventoryChanged', source: '数据同步', sourceRef: 'WMS 仓储 · 每 5 分钟增量同步', object: '商品', objectApi: 'Product', objKind: 'product', status: '已发布', version: 'v2', owner: '周晓峰',
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
    specific: [F('执行记录', 'runId', '字符串', true, '执行记录 ID', 'AR-20261025-00135'), F('动作类型', 'actionType', '字符串', true, '动作 API 名', 'issueRecallCoupon'), F('目标对象', 'targetId', '字符串', true, '动作目标对象 ID', '1011846'), F('执行人', 'executor', '字符串', true, '人或规则', '规则 R-012'), F('写回结果', 'writeback', '字符串', false, '外部系统返回码', 'HTTP 200')] },
  { id: 'action_outcome_unknown', name: '动作结果未知', api: 'ActionOutcomeUnknown', source: '动作', sourceRef: '外部写回超时或无响应时', object: '动作执行', objectApi: 'ActionRun', status: '已发布', version: 'v1', owner: '陈立军',
    desc: '写回外部系统超时、无响应时产生，进入对账队列，结果确认后再产生“动作执行完成”或失败通知。', dedupKey: '执行记录 ID', dedupWindow: '24 小时', retention: '365 天', delivery: '至少一次', ordering: '不保证', weekly: 37,
    specific: [F('执行记录', 'runId', '字符串', true, '执行记录 ID', 'AR-20261025-00134'), F('动作类型', 'actionType', '字符串', true, '动作 API 名', 'requestReplenishment'), F('外部系统', 'system', '字符串', true, '写回目标', 'WMS 仓储'), F('幂等键', 'idemKey', '字符串', true, '对账查询用', 'requestReplenishment:SH0031:1011846:2026-10-25'), F('等待时长', 'waitedMs', '整数', true, '毫秒', '8000')] },
  { id: 'coupon_redeemed', name: '优惠券核销', api: 'CouponRedeemed', source: '外部系统', sourceRef: 'CRM 营销中心 · Webhook 推送', object: '会员', objectApi: 'Member', status: '已发布', version: 'v1', owner: '吴佳',
    desc: '会员在任一渠道核销优惠券时，由 CRM 实时推送。', dedupKey: '券号', dedupWindow: '永久（不重复）', retention: '365 天', delivery: '至少一次', ordering: '不保证', weekly: 8640,
    specific: [F('券号', 'couponNo', '字符串', true, 'CRM 券号', 'CP2610250012'), F('会员', 'memberId', '字符串', true, '会员 ID', 'M31852207'), F('订单号', 'orderId', '字符串', true, '核销订单', 'SO20261025xxxx'), F('核销门店', 'storeId', '字符串', true, '门店编码', 'SH0047'), F('优惠金额', 'discount', '金额', true, '元', '15.00')] },
  { id: 'refund_created', name: '退款单创建', api: 'RefundCreated', source: '数据同步', sourceRef: 'ERP 订单中心 · CDC 增量同步', object: '订单', objectApi: 'SalesOrder', objKind: 'order', status: '已发布', version: 'v1', owner: '孙浩然',
    desc: 'ERP 生成退款单时产生。', dedupKey: '退款单号', dedupWindow: '24 小时', retention: '180 天', delivery: '至少一次', ordering: '同一订单内有序', weekly: 1420,
    specific: [F('退款单号', 'refundId', '字符串', true, 'ERP 退款单号', 'RF2610250031'), F('订单号', 'orderId', '字符串', true, '原订单', 'SO20261025xxxx'), F('退款金额', 'amount', '金额', true, '元', '54.90'), F('退款原因', 'reason', '枚举', true, '售后原因', '商品破损')] },
  { id: 'store_price_changed', name: '门店价格变更', api: 'StorePriceChanged', source: '外部系统', sourceRef: 'POS 价格中心 · 每日 0 点批量推送', object: '商品', objectApi: 'Product', objKind: 'product', status: '变更中', version: 'v2', owner: '王敏',
    desc: 'POS 价格中心下发的新价生效时产生，一店一品一条。', dedupKey: '门店 + SKU + 生效日期', dedupWindow: '24 小时', retention: '365 天', delivery: '至少一次', ordering: '不保证', weekly: 2260,
    specific: [F('SKU', 'sku', '字符串', true, '商品编码', '1004512'), F('门店', 'storeId', '字符串', true, '门店编码', 'SH0031'), F('原价', 'oldPrice', '金额', true, '元', '69.90'), F('新价', 'newPrice', '金额', true, '元', '64.90'), F('生效日期', 'effectiveDate', '日期', true, '新价生效日', '2026-10-25')] },
  { id: 'waybill_signed', name: '运单签收', api: 'WaybillSigned', source: '外部系统', sourceRef: 'TMS 运输 · 承运商回调', object: '订单', objectApi: 'SalesOrder', objKind: 'order', status: '已发布', version: 'v1', owner: '周晓峰',
    desc: '承运商回传签收时产生。', dedupKey: '运单号', dedupWindow: '永久（不重复）', retention: '180 天', delivery: '至少一次', ordering: '不保证', weekly: 31240,
    specific: [F('运单号', 'waybill', '字符串', true, '承运商运单号', 'SF1428xxxxxx'), F('订单号', 'orderId', '字符串', true, '订单业务主键', 'SO20261025xxxx'), F('签收时间', 'signedAt', '日期时间', true, '承运商回传', '2026-10-25 14:08:51'), F('签收人', 'signer', '字符串', false, '脱敏', '王*')] },
  { id: 'alert_closed', name: '告警关闭', api: 'AlertClosed', source: '动作', sourceRef: '动作“关闭告警”执行成功后', object: '告警', objectApi: 'Alert', status: '已发布', version: 'v1', owner: '陈立军',
    desc: '告警被关闭（已解决 / 已忽略）时产生。', dedupKey: '告警 ID', dedupWindow: '永久（不重复）', retention: '365 天', delivery: '至少一次', ordering: '不保证', weekly: 318,
    specific: [F('告警', 'alertId', '字符串', true, '告警 ID', 'AL-20261024-0112'), F('结论', 'resolution', '枚举', true, '已解决 / 已忽略', '已解决'), F('处理人', 'closedBy', '字符串', true, '关闭人', '陈立军'), F('说明', 'note', '文本', false, '处理说明', '承运商已补派')] },
];
export const eventTypeById = (id: string) => EVENT_TYPES.find((e) => e.id === id);
export const EVENT_DRAFTS: Record<string, DraftCR> = {
  store_price_changed: { id: 'CR-0008', title: '门店价格变更：增加促销价字段', owner: '王敏', updatedAt: '2026-10-24 17:52', changes: 2 },
};

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
  if (rule?.action) list.push({ kind: '自动化', name: `自动执行“${rule.action}”`, filter: '告警级别 ∈ {高, 中}', then: `执行动作“${rule.action}”`, status: '启用', weekly: Math.round(e.weekly * 0.6) });
  list.push({ kind: '通知', name: '告警推送', filter: '全部', then: '企业微信通知规则订阅人', status: '启用', weekly: e.weekly });
  if (e.id === 'gross_margin_breach') list.push({ kind: '规则', name: '门店销售额环比下降', ruleId: 'R-021', filter: '同门店当周 ≥ 3 条', then: '作为辅助信号参与周度复核', status: '启用', weekly: 6 });
  list.push({ kind: '外部订阅', name: '经营分析数据集', filter: '全部', then: '投递 Kafka topic ontology.alerts', status: e.source === '规则' ? '启用' : '异常', weekly: e.weekly });
  return list;
};
export function subscribersOf(e: EventType): Subscriber[] {
  if (SUBS[e.id]) return SUBS[e.id];
  if (e.source === '规则') return SUBS_RULE(e);
  if (e.id === 'action_outcome_unknown') return [
    { kind: '自动化', name: '写回对账任务', filter: '全部', then: '每 15 分钟按幂等键查询外部系统，确认后补发结果', status: '启用', weekly: e.weekly },
    { kind: '通知', name: '结果未知提醒', filter: '等待时长 ≥ 30 分钟', then: '企业微信通知动作执行人', status: '启用', weekly: 9 },
  ];
  return [
    { kind: '自动化', name: `${e.name}后更新${e.object}`, filter: '全部', then: `回写${e.object}的相关字段`, status: '启用', weekly: e.weekly },
    { kind: '外部订阅', name: '数据仓库 ODS', filter: '全部', then: `投递 Kafka topic ontology.${e.api}`, status: '启用', weekly: e.weekly },
  ];
}

/* 近期事件 */
export type EventOutcome = '已投递' | '已去重' | '已生成告警' | '投递失败' | '部分投递';
export interface EventRecord {
  id: string; typeId: string; at: string; objKind?: 'store' | 'product' | 'order'; objId: string; objLabel: string;
  summary: string; outcome: EventOutcome; subscribers: number; dedupKey: string; latencyMs: number;
}
function eventObj(e: EventType, r: () => number): Pick<EventRecord, 'objKind' | 'objId' | 'objLabel'> {
  if (e.objKind === 'order') { const o = ORDERS[Math.floor(r() * 600)]; return { objKind: 'order', objId: o.id, objLabel: o.id }; }
  if (e.objKind === 'product') { const p = PRODUCTS[Math.floor(r() * PRODUCTS.length)]; return { objKind: 'product', objId: p.sku, objLabel: p.name }; }
  if (e.objKind === 'store') { const s = STORES[Math.floor(r() * STORES.length)]; return { objKind: 'store', objId: s.id, objLabel: s.name }; }
  if (e.object === '告警') { const al = ALERTS[Math.floor(r() * ALERTS.length)]; return { objId: al.id, objLabel: al.id }; }
  if (e.object === '动作执行') { const run = RUNS[Math.floor(r() * RUNS.length)]; return { objId: run.id, objLabel: run.id }; }
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
