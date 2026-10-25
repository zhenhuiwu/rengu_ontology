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

export type ActionName = '调价审批' | '补货申请' | '发起物流催派' | '订单状态修正' | '同步会员标签';
export type ActionStatus = '待确认' | '审批中' | '执行中' | '成功' | '失败' | '结果未知' | '已撤回' | '已对账';
/** 外部系统 id，取自 外部系统 页（mock/extsys.ts 的 EXT_SYSTEMS） */
export type SysId = 'EXT-OA' | 'EXT-ERP' | 'EXT-OMS' | 'EXT-CRM';
export type ObjKind = 'store' | 'product' | 'order';

export interface ActionSource { type: '告警' | '问数' | '手动'; alertId?: string; ruleId?: string; ruleVersion?: string; session?: string }
export interface ActionLog { time: string; text: string; desc?: string; status?: 'done' | 'warn' | 'error' | 'unknown' }
export interface ActionRecord {
  id: string; action: ActionName; typeVersion: string; source: ActionSource;
  objKind: ObjKind; objId: string; objLabel: string;
  initiator: string; approver: string; idemKey: string; status: ActionStatus;
  submittedAt: string; sysId: SysId; system: string; extRef: string | null;
  params: string;              // 动作参数摘要
  failReason?: string;         // 失败：外部系统返回的原因
  reconcile?: string;          // 已对账：对账结论
  retryOf?: string; retriedAs?: string;
  log: ActionLog[];
}

/**
 * 动作名称 / 版本以 动作类型 页（mock/actionEvent.ts）为准：补货申请 v3、发起物流催派 v1、订单状态修正 v4；
 * 调价审批 v3 用 外部系统 页（泛微 OA）的动作名。外部系统一律引用 EXT_SYSTEMS 的 id，显示名取外部系统页的名称。
 * 重试口径同外部系统页：只有外部明确返回失败才按系统配置自动重试；超时 / 无响应 = 结果未知 · 待对账，不自动重试。
 */
export const ACTION_META: Record<ActionName, { sysId: SysId; kind: ObjKind; approver: string; version: string; call: string }> = {
  调价审批: { sysId: 'EXT-OA', kind: 'product', approver: '品类经理 王敏', version: 'v3', call: '发起调价审批流程' },
  补货申请: { sysId: 'EXT-ERP', kind: 'product', approver: '品类经理 王敏', version: 'v3', call: '保存请购单（补货申请）' },
  发起物流催派: { sysId: 'EXT-OMS', kind: 'order', approver: '区域运营 陈立军', version: 'v1', call: '提交催派工单' },
  订单状态修正: { sysId: 'EXT-OMS', kind: 'order', approver: '区域运营 陈立军', version: 'v4', call: '回写订单状态' },
  同步会员标签: { sysId: 'EXT-CRM', kind: 'store', approver: '会员运营 何静', version: 'v1', call: '推送会员标签任务' },
};
export const sysName = (id: SysId) => extSystemById(id)?.name ?? id;
/** 结果未知：系统每 15 分钟按幂等键查询外部结果，3 次仍无法确认时等人工对账 */
export const AUTO_QUERY_TEXT = '按幂等键自动查询 3 次（每 15 分钟），仍无法确认';
export const ACTION_NAMES = Object.keys(ACTION_META) as ActionName[];
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
    case '调价审批': return `建议售价下调 ${(2 + r() * 6).toFixed(1)}%（${label}）`;
    case '补货申请': return `补货 ${60 + Math.floor(r() * 180)} 件，供货仓 上海嘉定常温仓`;
    case '发起物流催派': return '催派承运商：顺丰速运，要求 24 小时内签收';
    case '订单状态修正': return '按订单生命周期 v4 改回上一个合法状态';
    default: return `标签“高价值复购”，覆盖会员 ${1200 + Math.floor(r() * 3800)} 人`;
  }
}

/** 外部明确失败时的自动重试次数（外部系统页配置） */
const retries = (x: ActionRecord) => extSystemById(x.sysId)?.maxRetry ?? 0;

/** 按状态生成执行记录（触发 → 动作类型/发起人 → 确认/审批 → 外部调用 → 结果） */
export function buildLog(x: ActionRecord): ActionLog[] {
  const m = ACTION_META[x.action];
  const t = x.submittedAt;
  const log: ActionLog[] = [];
  if (x.source.type === '告警') log.push({ time: addMin(t, -6), text: `告警 ${x.source.alertId} 命中规则 ${x.source.ruleId} ${x.source.ruleVersion}`, desc: '系统' });
  else if (x.source.type === '问数') log.push({ time: addMin(t, -3), text: `问数会话建议：${x.source.session}`, desc: x.initiator });
  else log.push({ time: addMin(t, -1), text: '手动发起', desc: x.initiator });
  log.push({ time: t, text: `生成动作「${x.action}」· 动作类型 ${m.version}`, desc: `发起人 ${x.initiator} · 幂等键 ${x.idemKey}` });
  if (x.status === '待确认') { log.push({ time: t, text: '等待确认提交', desc: `确认后进入 ${x.approver} 审批`, status: 'warn' }); return log; }
  if (x.status === '已撤回') { log.push({ time: addMin(t, 12), text: '撤回：对象情况已变化，不再执行', desc: x.initiator }); return log; }
  log.push({ time: addMin(t, 2), text: '确认提交', desc: `确认人 ${x.initiator}` });
  if (x.status === '审批中') { log.push({ time: addMin(t, 2), text: '等待审批', desc: `审批人 ${x.approver}`, status: 'warn' }); return log; }
  log.push({ time: addMin(t, 21), text: '审批通过', desc: `审批人 ${x.approver}` });
  log.push({ time: addMin(t, 22), text: `调用 ${x.system}：${m.call}`, desc: `幂等键 ${x.idemKey}` });
  if (x.status === '执行中') { log.push({ time: addMin(t, 22), text: `等待 ${x.system} 返回`, desc: '超过 30 分钟未返回将标记为结果未知', status: 'warn' }); return log; }
  if (x.status === '成功') log.push({ time: addMin(t, 23), text: `${x.system} 返回成功，外部单号 ${x.extRef}`, desc: '系统' });
  if (x.status === '失败') log.push({ time: addMin(t, 23), text: `${x.system} 明确返回失败${retries(x) ? `，已自动重试 ${retries(x)} 次仍失败` : ''}`, desc: x.failReason, status: 'error' });
  if (x.status === '结果未知' || x.status === '已对账') {
    log.push({ time: addMin(t, 52), text: `${x.system} 30 分钟未返回结果，标记为结果未知（不自动重试写入）`, desc: '系统', status: 'warn' });
    log.push({ time: addMin(t, 97), text: AUTO_QUERY_TEXT, desc: `系统 · 幂等键 ${x.idemKey}`, status: 'unknown' });
  }
  if (x.status === '已对账') log.push({ time: addMin(t, 140), text: `对账：${x.reconcile}`, desc: `${x.initiator} · 已在 ${x.system} 核对外部单号 ${x.extRef}` });
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
  const fromAlert = (a: Alert, status: ActionStatus, submittedAt: string, initiator = a.assignee ?? '王敏', extra: Partial<ActionRecord> = {}) => {
    const rule = RULES[a.ruleId];
    return mk({ action: rule.action as ActionName, source: { type: '告警', alertId: a.id, ruleId: rule.id, ruleVersion: rule.version },
      objKind: a.objKind, objId: a.objId, objLabel: a.objLabel, initiator, status, submittedAt, ...extra });
  };
  const al = (id: string) => ALERTS.find((a) => a.id === id)!;

  // —— 今天和昨天、与告警处理页对得上的几条 ——
  fromAlert(al('AL-20261025-0127'), '待确认', '2026-10-25 16:12:40', '王敏');
  fromAlert(al('AL-20261025-0136'), '执行中', '2026-10-25 15:20:16', '陈立军');
  fromAlert(al('AL-20261024-0118'), '失败', '2026-10-24 20:03:52', '刘婷', { failReason: 'NC-2071 供货仓可用库存不足，无法生成请购单' });
  fromAlert(al('AL-20261024-0105'), '结果未知', '2026-10-24 11:30:05', '孙浩然');
  fromAlert(al('AL-20261024-0112'), '成功', '2026-10-24 16:42:10', '陈立军');
  fromAlert(al('AL-20261023-0097'), '成功', '2026-10-23 21:40:33', '何静');
  fromAlert(al('AL-20261019-0068'), '成功', '2026-10-19 09:12:47', '王敏');
  fromAlert(al('AL-20261018-0061'), '成功', '2026-10-18 10:36:21', '陈立军');

  // —— 其余：问数建议、手动发起，和历史告警（已解决且规则带动作）关联的成功记录 ——
  // 状态按固定数量分配（近期多在途），保证 待确认、结果未知与对账 两个页签都超过一屏
  const shuffle = <T,>(arr: T[]) => { for (let i = arr.length - 1; i > 0; i--) { const j = Math.floor(r() * (i + 1)); [arr[i], arr[j]] = [arr[j], arr[i]]; } return arr; };
  const fill = (spec: [ActionStatus, number][]) => spec.flatMap(([st, n]) => Array<ActionStatus>(n).fill(st));
  const recent = shuffle(fill([['待确认', 23], ['审批中', 6], ['执行中', 3], ['结果未知', 12], ['失败', 6]]));
  const older = shuffle(fill([['结果未知', 6], ['已对账', 11], ['已撤回', 5], ['失败', 4], ['成功', 54]]));
  const histAlerts = ALERTS.filter((a) => a.status === '已解决' && RULES[a.ruleId].action && a.triggeredAt < '2026-10-18');
  const sessions = ['华东门店复购下降原因', '库存低于安全线的商品', '已出库未签收订单', '9 月毛利率按品类', '会员新增与流失', '双 11 备货门店清单'];
  for (let i = 0; i < 130; i++) {
    const day = 25 - Math.floor(i / 10);
    const time = `2026-10-${pad(Math.max(day, 4))} ${pad(8 + Math.floor(r() * (day >= 25 ? 8 : 12)))}:${pad(Math.floor(r() * 60))}:${pad(Math.floor(r() * 60))}`;
    const status: ActionStatus = (i < recent.length ? recent[i] : older.shift()) ?? '成功';
    const who = PEOPLE[Math.floor(r() * PEOPLE.length)];
    const pickSrc = r();
    if (pickSrc < 0.4 && histAlerts.length && i >= recent.length && status === '成功') {
      const a = histAlerts.splice(Math.floor(r() * histAlerts.length), 1)[0];
      fromAlert(a, '成功', addMin(a.triggeredAt, 25 + Math.floor(r() * 90)), a.assignee ?? who);
      continue;
    }
    const action = ACTION_NAMES[Math.floor(r() * ACTION_NAMES.length)];
    const kind = ACTION_META[action].kind;
    let objId: string; let objLabel: string;
    if (kind === 'order') { const o = ORDERS[Math.floor(r() * 400)]; objId = o.id; objLabel = o.id; }
    else if (kind === 'product') { const p = PRODUCTS[Math.floor(r() * PRODUCTS.length)]; objId = p.sku; objLabel = `${p.name} ${p.spec}`; }
    else { const s = STORES[Math.floor(r() * STORES.length)]; objId = s.id; objLabel = s.name; }
    const source: ActionSource = pickSrc < 0.75 ? { type: '问数', session: sessions[Math.floor(r() * sessions.length)] } : { type: '手动' };
    mk({ action, source, objKind: kind, objId, objLabel, initiator: who, status, submittedAt: time });
  }
  list.sort((a, b) => (a.submittedAt < b.submittedAt ? 1 : -1));
  // 编号按提交时间倒序连续编排；告警来源的幂等键与告警处理页一致：act-<告警编号>
  let seq = 412 + list.length;
  for (const x of list) {
    x.id = `ACT-${x.submittedAt.slice(0, 10).replace(/-/g, '')}-${pad(seq--, 4)}`;
    x.idemKey = x.source.alertId ? `act-${x.source.alertId.toLowerCase()}` : `act-${x.id.slice(4).toLowerCase()}`;
    x.log = buildLog(x);
  }
  return list;
}

export const ACTIONS: ActionRecord[] = build();
