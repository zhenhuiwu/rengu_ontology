/**
 * 动作中心示例数据（动态本体）：约 120 条动作记录，固定种子。
 * 对象全部取自 ORDERS / PRODUCTS / STORES，来源告警取自 ALERTS（同一套 ID，ObjRef 都能解析）。
 * 状态流转：待确认 →（确认提交）审批中 → 执行中 → 成功 / 失败 / 结果未知 →（对账）已对账；待确认可撤回为已撤回。
 */
import { seeded } from './format';
import { ORDERS } from './orders';
import { PRODUCTS } from './products';
import { STORES } from './stores';
import { ALERTS, RULES, type Alert } from './dynamic';
import { extSystemById } from './extsys';
import { ACTION_TYPES } from './actionEvent';

export type ActionName = '发起调价审批' | '补货申请' | '发起物流催派' | '订单状态修正' | '同步会员标签' | '调整大区预算' | '发放复购召回优惠券';
export type ActionStatus = '待确认' | '审批中' | '执行中' | '成功' | '失败' | '结果未知' | '已撤回' | '已对账';
/** 外部系统 id，取自 外部系统 页（mock/extsys.ts 的 EXT_SYSTEMS） */
export type SysId = 'EXT-OA' | 'EXT-ERP' | 'EXT-OMS' | 'EXT-CRM';
/** 作用对象类型，与 动作类型 页（mock/actionEvent.ts）的 target 一致：门店 / 商品 / 订单 / 大区 / 会员 / 会员分群 / 告警 */
export type ObjKind = 'store' | 'product' | 'order' | 'region' | 'member' | 'segment' | 'alert';
export const OBJ_KIND_LABEL: Record<ObjKind, string> = { store: '门店', product: '商品', order: '订单', region: '大区', member: '会员', segment: '会员分群', alert: '告警' };
const KIND_BY_TARGET: Record<string, ObjKind> = Object.fromEntries(Object.entries(OBJ_KIND_LABEL).map(([k, v]) => [v, k as ObjKind]));
/**
 * 动作类型 → 作用对象类型，按动作类型目录（名称一字不差）：
 * 大区 = 发放复购召回优惠券 / 指派大区经理跟进 / 调整大区预算；会员分群 = 同步会员标签；会员 = 调整会员等级；
 * 订单 = 订单状态修正 / 发起物流催派 / 补发出库单 / 退款审核；商品 = 补货申请 / 门店调价 / 发起调价审批 / 调整安全库存；
 * 门店 = 变更门店状态；告警 = 关闭告警 / 认领告警 / 转派告警。
 */
export const ACTION_OBJ_KIND: Record<string, ObjKind> = Object.fromEntries(ACTION_TYPES.map((t) => [t.name, KIND_BY_TARGET[t.target]]));
const catalog = (name: string) => ACTION_TYPES.find((t) => t.name === name)!;
/** 有对象详情（ObjRef 悬停卡 / 抽屉）的类型；会员、会员分群没有对象详情，显示为纯文本 */
export const hasObjRef = (k: ObjKind) => k === 'store' || k === 'product' || k === 'order' || k === 'region';
/** 会员分群（同步会员标签的作用对象） */
export const SEGMENTS = [
  { id: 'SEG-0012', name: '高价值复购会员' }, { id: 'SEG-0018', name: '60 天未复购会员' }, { id: 'SEG-0021', name: '母婴品类常购会员' },
  { id: 'SEG-0026', name: '生鲜高频会员' }, { id: 'SEG-0031', name: '双 11 预约会员' }, { id: 'SEG-0035', name: '新客首单 30 天内' },
];
/** 规则自动暂存动作的确认人（每条规则固定，与 规则与自动化 页的“确认人”一致） */
export const RULE_CONFIRMER: Record<string, string> = { 'R-007': '王敏', 'R-012': '周晓峰', 'R-015': '陈立军', 'SM-ORD': '孙浩然', 'R-027': '吴佳' };
/** 问数会话（问数工作台的会话编号） */
export const QA_SESSIONS = [
  { id: 'S0001', title: '华东大区复购客户销售额' }, { id: 'S0002', title: '本周门店客流 Top 10' }, { id: 'S0003', title: '华东各重点门店销售额' },
];

/**
 * 来源：规则 = 规则命中后自动暂存（发起人记为“规则 R-xxx”，确认人按规则固定）；告警 = 处理告警时人工发起；
 * 问数 = 问数会话建议（session 为会话编号 S0001…）；手动 = 动作中心直接发起。
 */
export interface ActionSource { type: '规则' | '告警' | '问数' | '手动'; alertId?: string; ruleId?: string; ruleVersion?: string; confirmer?: string; session?: string; sessionTitle?: string }
export interface ActionLog { time: string; text: string; desc?: string; status?: 'done' | 'warn' | 'error' | 'unknown' }
export interface ActionRecord {
  id: string; action: ActionName; typeVersion: string; source: ActionSource;
  objKind: ObjKind; objId: string; objLabel: string;
  initiator: string; approver: string; idemKey: string; status: ActionStatus;
  submittedAt: string; sysId: SysId; system: string; extRef: string | null;
  params: string;              // 动作参数摘要
  failReason?: string;         // 失败：外部系统返回的原因
  unknownReason?: string;      // 结果未知：超时 / 无响应的说明
  reconcile?: string;          // 已对账：对账结论
  retryOf?: string; retriedAs?: string;
  log: ActionLog[];
}

/**
 * 动作名称 / 版本以 动作类型 页（mock/actionEvent.ts）为准：补货申请 v3、发起物流催派 v1、订单状态修正 v4；
 * 发起调价审批 v3（泛微 OA）、调整大区预算 v2、发放复购召回优惠券 v3、同步会员标签 v1。外部系统一律引用 EXT_SYSTEMS 的 id，显示名取外部系统页的名称。
 * 重试口径同外部系统页：只有外部明确返回失败才按系统配置自动重试；超时 / 无响应 = 结果未知 · 待对账，不自动重试。
 */
const LOCAL: Record<ActionName, { approver: string; call: string }> = {
  发起调价审批: { approver: '品类经理 王敏', call: '发起调价审批流程' },
  补货申请: { approver: '品类经理 王敏', call: '保存请购单（补货申请）' },
  发起物流催派: { approver: '区域运营 陈立军', call: '提交催派工单' },
  订单状态修正: { approver: '区域运营 陈立军', call: '回写订单状态' },
  同步会员标签: { approver: '会员运营 何静', call: '推送会员标签任务' },
  调整大区预算: { approver: '财务总监 赵磊', call: '提交预算调整单' },
  发放复购召回优惠券: { approver: '营销经理 吴佳', call: '发放会员券' },
};
/** 外部系统 / 对象类型 / 版本取自动作类型目录；审批人与调用说明是动作中心本地的示例 */
export const ACTION_META = Object.fromEntries((Object.keys(LOCAL) as ActionName[]).map((n) => {
  const t = catalog(n);
  return [n, { sysId: t.sysId as SysId, kind: ACTION_OBJ_KIND[n], version: t.version, ...LOCAL[n] }];
})) as Record<ActionName, { sysId: SysId; kind: ObjKind; approver: string; version: string; call: string }>;
export const sysName = (id: SysId) => extSystemById(id)?.name ?? id;
/** 结果未知：系统每 15 分钟按幂等键查询外部结果，3 次仍无法确认时等人工对账 */
export const AUTO_QUERY_TEXT = '按幂等键自动查询 3 次（每 15 分钟），仍无法确认';
export const ACTION_NAMES = Object.keys(ACTION_META) as ActionName[];
/** 批量示例数据随机取的动作（大区级动作只有下面单独写的两条） */
const SAMPLE_NAMES: ActionName[] = ['发起调价审批', '补货申请', '发起物流催派', '订单状态修正', '同步会员标签'];
/** 动作中心用到的外部系统（筛选项），id 与名称都来自 EXT_SYSTEMS */
export const ACTION_SYSTEMS = [...new Set(ACTION_NAMES.map((n) => ACTION_META[n].sysId))].map((id) => ({ id, name: sysName(id) }));
const PEOPLE = ['王敏', '陈立军', '刘婷', '孙浩然', '何静', '赵蕾', '吴佳'];
const FAILS: Record<SysId, string[]> = {
  'EXT-OA': ['OA-4012 商品处于促销锁价期，不能发起调价流程', 'OA-4103 调价幅度超过品类上限 15.0%'],
  'EXT-ERP': ['NC-2071 供货仓可用库存不足，无法生成请购单', 'NC-1150 门店当日请购已截单（16:00）'],
  'EXT-OMS': ['OMS-3418 订单状态版本冲突，请刷新后重试', 'OMS-3301 运单已签收，催派无效'],
  'EXT-CRM': ['CRM-509 标签“高价值复购”已停用', 'CRM-422 会员分群为空，未生成任务'],
};
const pad = (n: number, w = 2) => String(n).padStart(w, '0');
const addMin = (t: string, m: number) => {
  const d = new Date(new Date(t.replace(' ', 'T') + '+08:00').getTime() + m * 60000 + 8 * 3600000);
  return d.toISOString().slice(0, 19).replace('T', ' ');
};
const extRefOf = (sys: SysId, t: string, n: number) =>
  `${{ 'EXT-OA': 'OA', 'EXT-ERP': 'PR', 'EXT-OMS': 'TK', 'EXT-CRM': 'TG' }[sys]}${t.slice(0, 10).replace(/-/g, '')}${pad(n, 5)}`;

function paramsOf(a: ActionName, label: string, r: () => number) {
  switch (a) {
    case '发起调价审批': return `建议售价下调 ${(2 + r() * 6).toFixed(1)}%（${label}）`;
    case '补货申请': return `补货 ${60 + Math.floor(r() * 180)} 件，供货仓 上海嘉定常温仓`;
    case '发起物流催派': return '催派承运商：顺丰速运，要求 24 小时内签收';
    case '订单状态修正': return '按订单生命周期 v4 改回上一个合法状态';
    case '调整大区预算': return '11 月会员召回预算 +4.5%（¥186.0 万元 → ¥194.4 万元）';
    case '发放复购召回优惠券': return '60 天未复购会员 3,182 人，满 99 减 15 券，活动 HD-RECALL-0829';
    default: return `标签“高价值复购”写入分群“${label}”，覆盖会员 ${1200 + Math.floor(r() * 3800)} 人`;
  }
}

/** 外部明确失败时的自动重试次数（外部系统页配置） */
const retries = (x: ActionRecord) => extSystemById(x.sysId)?.maxRetry ?? 0;

/** 按状态生成执行记录（触发 → 动作类型/发起人 → 确认/审批 → 外部调用 → 结果） */
export function buildLog(x: ActionRecord): ActionLog[] {
  const m = ACTION_META[x.action];
  const t = x.submittedAt;
  const log: ActionLog[] = [];
  const owner = x.source.confirmer ?? x.initiator;   // 确认 / 撤回 / 对账的人：规则来源用规则固定的确认人
  if (x.source.type === '规则') log.push({ time: addMin(t, -6), text: `规则 ${x.source.ruleId} ${x.source.ruleVersion} 命中${x.source.alertId ? `（告警 ${x.source.alertId}）` : ''}，自动暂存动作`, desc: '系统' });
  else if (x.source.type === '告警') log.push({ time: addMin(t, -6), text: `告警 ${x.source.alertId} 命中规则 ${x.source.ruleId} ${x.source.ruleVersion}`, desc: '系统' });
  else if (x.source.type === '问数') log.push({ time: addMin(t, -3), text: `问数会话 ${x.source.session}「${x.source.sessionTitle}」建议`, desc: x.initiator });
  else log.push({ time: addMin(t, -1), text: '手动发起', desc: x.initiator });
  log.push({ time: t, text: `生成动作「${x.action}」· 动作类型 ${m.version}`, desc: `发起人 ${x.initiator}${x.source.confirmer ? ` · 确认人 ${x.source.confirmer}` : ''} · 幂等键 ${x.idemKey}` });
  if (x.status === '待确认') { log.push({ time: t, text: '等待确认提交', desc: `确认人 ${owner}，确认后进入 ${x.approver} 审批`, status: 'warn' }); return log; }
  if (x.status === '已撤回') { log.push({ time: addMin(t, 12), text: '撤回：对象情况已变化，不再执行', desc: owner }); return log; }
  log.push({ time: addMin(t, 2), text: '确认提交', desc: `确认人 ${owner}` });
  if (x.status === '审批中') { log.push({ time: addMin(t, 2), text: '等待审批', desc: `审批人 ${x.approver}`, status: 'warn' }); return log; }
  log.push({ time: addMin(t, 21), text: '审批通过', desc: `审批人 ${x.approver}` });
  log.push({ time: addMin(t, 22), text: `调用 ${x.system}：${m.call}`, desc: `幂等键 ${x.idemKey}` });
  if (x.status === '执行中') { log.push({ time: addMin(t, 22), text: `等待 ${x.system} 返回`, desc: '超过 30 分钟未返回将标记为结果未知', status: 'warn' }); return log; }
  if (x.status === '成功') log.push({ time: addMin(t, 23), text: `${x.system} 返回成功，外部单号 ${x.extRef}`, desc: '系统' });
  if (x.status === '失败') log.push({ time: addMin(t, 23), text: `${x.system} 明确返回失败${retries(x) ? `，已自动重试 ${retries(x)} 次仍失败` : ''}`, desc: x.failReason, status: 'error' });
  if (x.status === '结果未知' || x.status === '已对账') {
    // 超时时长取外部系统登记的 timeoutMs（会员 CRM 5 秒、用友 ERP 10 秒……），超时不重试
    const sec = (extSystemById(m.sysId)?.timeoutMs ?? 5000) / 1000;
    log.push({ time: addMin(t, 22), text: `${x.system} ${sec} 秒未返回结果，标记为结果未知（不重试写入）`, desc: x.unknownReason ? `系统 · ${x.unknownReason}` : '系统', status: 'warn' });
    log.push({ time: addMin(t, 67), text: AUTO_QUERY_TEXT, desc: `系统 · 幂等键 ${x.idemKey}`, status: 'unknown' });
    if (x.status === '结果未知') log.push({ time: addMin(t, 67), text: '转人工对账', desc: `对账人 ${owner} · 在 ${x.system} 按幂等键核对后确认结果`, status: 'warn' });
  }
  if (x.status === '已对账') log.push({ time: addMin(t, 140), text: `对账：${x.reconcile}`, desc: `${owner} · 已在 ${x.system} 核对外部单号 ${x.extRef}` });
  return log;
}

function build(): ActionRecord[] {
  const r = seeded(20261025 + 77);
  const list: ActionRecord[] = [];
  const mk = (p: Omit<ActionRecord, 'id' | 'typeVersion' | 'idemKey' | 'system' | 'sysId' | 'approver' | 'params' | 'log' | 'extRef'> & { extRef?: string | null }) => {
    const m = ACTION_META[p.action];
    // 结果未知 / 已对账 的执行记录要往后排到 +140 分钟，今天的记录提交时间不晚于 14:00，避免时间线出现“未来”时间
    if ((p.status === '结果未知' || p.status === '已对账') && p.submittedAt > '2026-10-25 14:00:00') p.submittedAt = addMin(p.submittedAt, -180);
    const rec: ActionRecord = {
      ...p, id: '', idemKey: '', typeVersion: m.version, sysId: m.sysId, system: sysName(m.sysId), approver: m.approver,
      extRef: p.extRef !== undefined ? p.extRef : (['成功', '失败', '结果未知', '已对账'].includes(p.status) ? extRefOf(m.sysId, p.submittedAt, Math.floor(r() * 99999)) : null),
      params: paramsOf(p.action, p.objLabel, r), log: [],
    };
    if (p.status === '失败' && !rec.failReason) rec.failReason = FAILS[m.sysId][Math.floor(r() * 2)];
    if (p.status === '已对账' && !rec.reconcile) rec.reconcile = r() < 0.7 ? '外部已成功' : '外部未执行';
    list.push(rec);
    return rec;
  };
  const fromAlert = (a: Alert, status: ActionStatus, submittedAt: string, extra: Partial<ActionRecord> = {}) => {
    const rule = RULES[a.ruleId];
    // 规则命中后自动暂存：发起人记为规则本身，确认人按规则固定（RULE_CONFIRMER）
    return mk({ action: rule.action as ActionName, source: { type: '规则', alertId: a.id, ruleId: rule.id, ruleVersion: rule.version, confirmer: RULE_CONFIRMER[rule.id] },
      objKind: a.objKind, objId: a.objId, objLabel: a.objLabel, initiator: `规则 ${rule.id}`, status, submittedAt, ...extra });
  };
  const al = (id: string) => ALERTS.find((a) => a.id === id)!;

  // —— 今天和昨天、与告警处理页对得上的几条 ——
  fromAlert(al('AL-20261025-0127'), '待确认', '2026-10-25 16:12:40');
  fromAlert(al('AL-20261025-0136'), '执行中', '2026-10-25 15:20:16');
  fromAlert(al('AL-20261024-0118'), '失败', '2026-10-24 20:03:52', { failReason: 'NC-2071 供货仓可用库存不足，无法生成请购单' });
  fromAlert(al('AL-20261024-0105'), '结果未知', '2026-10-24 11:30:05');
  fromAlert(al('AL-20261024-0112'), '成功', '2026-10-24 16:42:10');
  fromAlert(al('AL-20261023-0097'), '成功', '2026-10-23 21:40:33');
  fromAlert(al('AL-20261019-0068'), '成功', '2026-10-19 09:12:47');
  fromAlert(al('AL-20261018-0061'), '成功', '2026-10-18 10:36:21');

  // —— 其余：问数建议、手动发起，和历史告警（已解决且规则带动作）关联的成功记录 ——
  // 状态按固定数量分配（近期多在途），保证 待确认、结果未知与对账 两个页签都超过一屏
  const shuffle = <T,>(arr: T[]) => { for (let i = arr.length - 1; i > 0; i--) { const j = Math.floor(r() * (i + 1)); [arr[i], arr[j]] = [arr[j], arr[i]]; } return arr; };
  const fill = (spec: [ActionStatus, number][]) => spec.flatMap(([st, n]) => Array<ActionStatus>(n).fill(st));
  const recent = shuffle(fill([['待确认', 23], ['审批中', 6], ['执行中', 3], ['结果未知', 12], ['失败', 6]]));
  const older = shuffle(fill([['结果未知', 6], ['已对账', 11], ['已撤回', 5], ['失败', 4], ['成功', 54]]));
  const histAlerts = ALERTS.filter((a) => a.status === '已解决' && RULES[a.ruleId].action && a.triggeredAt < '2026-10-18');
  for (let i = 0; i < 130; i++) {
    const day = 25 - Math.floor(i / 10);
    const time = `2026-10-${pad(Math.max(day, 4))} ${pad(8 + Math.floor(r() * (day >= 25 ? 8 : 12)))}:${pad(Math.floor(r() * 60))}:${pad(Math.floor(r() * 60))}`;
    const status: ActionStatus = (i < recent.length ? recent[i] : older.shift()) ?? '成功';
    const who = PEOPLE[Math.floor(r() * PEOPLE.length)];
    const pickSrc = r();
    if (pickSrc < 0.4 && histAlerts.length && i >= recent.length && status === '成功') {
      const a = histAlerts.splice(Math.floor(r() * histAlerts.length), 1)[0];
      fromAlert(a, '成功', addMin(a.triggeredAt, 25 + Math.floor(r() * 90)));
      continue;
    }
    const action = SAMPLE_NAMES[Math.floor(r() * SAMPLE_NAMES.length)];
    const kind = ACTION_META[action].kind;
    let objId: string; let objLabel: string;
    if (kind === 'order') { const o = ORDERS[Math.floor(r() * 400)]; objId = o.id; objLabel = o.id; }
    else if (kind === 'product') { const p = PRODUCTS[Math.floor(r() * PRODUCTS.length)]; objId = p.sku; objLabel = `${p.name} ${p.spec}`; }
    else if (kind === 'segment') { const g = SEGMENTS[Math.floor(r() * SEGMENTS.length)]; objId = g.id; objLabel = g.name; }
    else { const s = STORES[Math.floor(r() * STORES.length)]; objId = s.id; objLabel = s.name; }
    const qs = pickSrc < 0.75 ? QA_SESSIONS[Math.floor(r() * QA_SESSIONS.length)] : null;
    const source: ActionSource = qs ? { type: '问数', session: qs.id, sessionTitle: qs.title } : { type: '手动' };
    mk({ action, source, objKind: kind, objId, objLabel, initiator: who, status, submittedAt: time });
  }
  // —— R-012 库存低于安全线暂存的补货申请：确认人是周晓峰，别人（如王敏）只能看 ——
  fromAlert(al('AL-20261025-0139'), '待确认', '2026-10-25 14:23:05').id = 'ACT-20261025-0552';
  // —— 大区级：问数建议调整预算（审批中）；R-027 大区复购下降暂存的召回券（CRM 超时，结果未知，不重试）——
  mk({ action: '调整大区预算', source: { type: '问数', session: 'S0001', sessionTitle: QA_SESSIONS[0].title }, objKind: 'region', objId: 'REG-EC', objLabel: '华东大区',
    initiator: '王敏', status: '审批中', submittedAt: '2026-10-25 15:36:00' }).id = 'ACT-20261025-0551';
  mk({ action: '发放复购召回优惠券', source: { type: '规则', ruleId: 'R-027', ruleVersion: 'v1', confirmer: RULE_CONFIRMER['R-027'] }, objKind: 'region', objId: 'REG-EC', objLabel: '华东大区',
    initiator: '规则 R-027', status: '结果未知', submittedAt: '2026-08-29 22:10:00', unknownReason: 'POST /coupon/grant 无响应' }).id = 'ACT-20260829-0412';

  list.sort((a, b) => (a.submittedAt < b.submittedAt ? 1 : -1));
  // 编号按提交时间倒序连续编排；告警来源的幂等键与告警处理页一致：act-<告警编号>
  // 上面三条是后补的，编号固定（0552 / 0551 / 0412），不挤占原有编号
  let seq = 412 + list.filter((x) => !x.id).length;
  for (const x of list) {
    if (!x.id) x.id = `ACT-${x.submittedAt.slice(0, 10).replace(/-/g, '')}-${pad(seq--, 4)}`;
    x.idemKey = x.source.alertId ? `act-${x.source.alertId.toLowerCase()}` : `act-${x.id.slice(4).toLowerCase()}`;
    x.log = buildLog(x);
  }
  return list;
}

export const ACTIONS: ActionRecord[] = build();
