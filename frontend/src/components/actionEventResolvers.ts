/**
 * 动作类型 / 事件类型页面用到的抽屉解析（规则、动作执行（kind = action，ACT- 编号）、事件）。
 * 由 objectRegistry.ts 的 RESOLVERS 展开引入；这里只新增 kind，不改已有 kind。
 */
import type { ObjView } from './objectRegistry';
import { RULES, ALERTS } from '@/mock/dynamic';
import { runById, actionTypeById, eventById, eventTypeById, ACTION_TYPES, RULE_ACTION, ruleConfirmer, extSystemOf, UNKNOWN_FOLLOWUP, type RunStatus, type EventOutcome } from '@/mock/actionEvent';

const runColor = (s: RunStatus): NonNullable<ObjView['status']>['color'] =>
  s === '失败' ? 'error' : s === '结果未知' || s === '待确认' ? 'warning' : 'default';
const runText = (s: RunStatus) => (s === '结果未知' ? '结果未知 · 待对账' : s);
const evColor = (o: EventOutcome): NonNullable<ObjView['status']>['color'] =>
  o === '投递失败' ? 'error' : 'default';
const addSec = (t: string, s: number) => {
  const d = new Date(t.replace(' ', 'T') + '+08:00');
  d.setSeconds(d.getSeconds() + s);
  const p = (n: number) => String(n).padStart(2, '0');
  return `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
};

export const ACTION_EVENT_RESOLVERS: Record<string, (id: string) => ObjView | null> = {
  // 规则（R-xxx / SM-ORD）
  rule(id) {
    const r = RULES[id];
    if (!r) return null;
    const act = RULE_ACTION[id] ? actionTypeById(RULE_ACTION[id]) : undefined;
    const actText = act ? `生成“${act.name}”，待 ${ruleConfirmer(id)} 确认后提交` : '仅生成告警（不绑定动作）';
    const manual = ACTION_TYPES.filter((a) => a.usedBy.includes(id) && a.id !== act?.id).map((a) => a.name);
    const alerts = ALERTS.filter((a) => a.ruleId === id);
    return {
      kind: 'rule', kindLabel: '规则', id: r.id, title: r.name, code: `${r.id} · ${r.version}`,
      status: { text: '运行中', color: 'default' },
      brief: [{ label: '触发', value: r.trigger }, { label: '条件', value: r.condition }, { label: '执行', value: r.schedule }, { label: '命中后', value: act ? `生成“${act.name}”（待确认）` : '仅生成告警' }],
      fields: [
        { label: '规则编号', value: r.id }, { label: '版本', value: r.version }, { label: '触发方式', value: r.trigger },
        { label: '执行计划', value: r.schedule }, { label: '条件', value: r.condition }, { label: '去重', value: r.dedupe },
        { label: '命中后动作', value: actText },
        { label: '处理告警时可用', value: manual.join('、') || '—' },
        { label: '近期告警', value: `${alerts.length} 条（待处理 ${alerts.filter((a) => a.status === '待处理').length}）` },
      ],
      timeline: alerts.slice(0, 24).map((a) => ({
        time: a.triggeredAt, text: `命中 ${a.objLabel}`, desc: `${a.value}（阈值 ${a.threshold}）· ${a.status}`,
        status: a.status === '待处理' ? 'warn' : 'done',
      })),
      related: r.metric ? [{ kind: 'metric', id: r.metric, label: r.metric === 'gmv' ? '销售额（不含税）' : '毛利率', note: '规则使用的指标' }] : undefined,
    };
  },
  // 动作执行（ACT-YYYYMMDD-NNNN，与动作中心编号一致）
  action(id) {
    const run = runById(id);
    if (!run) return null;
    const a = actionTypeById(run.typeId)!;
    const sys = extSystemOf(a);
    const st = run.status;
    const tl: ObjView['timeline'] = [];
    if (run.ruleId) tl.push({ time: run.at, text: `规则 ${run.ruleId} 命中，生成动作`, desc: run.summary, status: 'done' });
    else tl.push({ time: run.at, text: `${run.initiator} 发起（${run.source}）`, desc: run.summary, status: 'done' });
    if (run.ruleId) {
      if (st === '待确认') { tl.push({ time: addSec(run.at, 0), text: `待确认（确认人 ${run.confirmer}）`, desc: '规则生成的动作不会直接执行，需确认人确认后才提交', status: 'warn' }); }
      else if (st === '已撤回') { tl.push({ time: addSec(run.at, 300), text: `${run.confirmer} 撤回`, desc: '未提交，未写回外部系统', status: 'done' }); }
      else tl.push({ time: addSec(run.at, 120), text: `${run.confirmer} 确认提交`, status: 'done' });
    }
    const base = run.ruleId ? 120 : 0;
    const condFail = st === '失败' && !sys;
    if (st !== '待确认' && st !== '已撤回') {
      tl.push({ time: addSec(run.at, base), text: condFail ? `提交条件未通过：${run.code}` : '提交条件校验通过', status: condFail ? 'error' : 'done' });
      if (run.approver) tl.push({ time: addSec(run.at, base + 1), text: st === '审批中' ? `审批中（${run.approver}）` : `${run.approver} 审批通过`, desc: a.approval, status: 'done' });
      if (st !== '审批中' && !condFail) {
        if (sys) {
          const t0 = base + 2;
          tl.push({ time: addSec(run.at, t0), text: `调用 ${sys.name}（${sys.id}）`, desc: `${a.endpoint} · 幂等键 ${run.idemKey}`, status: 'done' });
          if (st === '失败') {
            tl.push({ time: addSec(run.at, t0 + 1), text: `外部明确返回失败：${run.code}`, status: 'error' });
            if (run.retries) tl.push({ time: addSec(run.at, t0 + 8), text: `按同一幂等键自动重试 ${run.retries} 次，仍失败`, desc: sys.retry, status: 'error' });
            else tl.push({ time: addSec(run.at, t0 + 1), text: '未自动重试', desc: sys.retry, status: 'error' });
          }
          if (st === '成功' && run.retries) tl.push({ time: addSec(run.at, t0 + 1), text: '首次 HTTP 503 明确失败，按同一幂等键自动重试', desc: sys.retry, status: 'done' });
          if (st === '结果未知' || st === '已对账') {
            const t1 = t0 + Math.round(run.ms / 1000);
            tl.push({ time: addSec(run.at, t1), text: `超时 ${sys.timeoutMs / 1000} 秒未响应：记为结果未知，不重试`, desc: '对方可能已处理，重试可能造成重复写回', status: 'unknown' });
            tl.push({ time: addSec(run.at, t1 + 1), text: UNKNOWN_FOLLOWUP, desc: `幂等键 ${run.idemKey}`, status: st === '结果未知' ? 'unknown' : 'done' });
            if (st === '已对账') tl.push({ time: addSec(run.at, t1 + 1800), text: '已对账', desc: run.reconcile, status: 'done' });
          }
        }
        if (st === '成功' || st === '已对账') tl.push({ time: addSec(run.at, base + 4), text: '本体编辑已提交，产生事件 ActionExecuted', desc: run.extRef ? `外部单号 ${run.extRef}` : undefined, status: 'done' });
      }
    }
    const related: NonNullable<ObjView['related']> = [];
    if (run.objKind) related.push({ kind: run.objKind, id: run.objId, label: run.objLabel, note: `动作目标（${a.target}）` });
    if (run.ruleId) related.push({ kind: 'rule', id: run.ruleId, label: `${run.ruleId} ${RULES[run.ruleId].name}`, note: '发起规则' });
    return {
      kind: 'action', kindLabel: '动作执行', id: run.id, title: `${a.name} · ${run.objLabel}`, code: run.id,
      status: { text: runText(st), color: runColor(st) },
      brief: [{ label: '动作', value: `${a.name} ${a.version}` }, { label: '发起人', value: run.initiator }, { label: '确认人', value: run.confirmer }, { label: '结果', value: runText(st) }],
      fields: [
        { label: '动作类型', value: `${a.name}（${a.api}）` }, { label: '版本', value: a.version },
        { label: '发起人', value: run.initiator }, { label: '确认人', value: run.confirmer },
        { label: '来源', value: run.source }, { label: '审批人', value: run.approver ?? '免审' },
        { label: '目标对象', value: `${a.target} · ${run.objLabel}` }, { label: '参数摘要', value: run.summary },
        { label: '外部系统', value: sys ? `${sys.name}（${sys.id}）` : a.system }, { label: '幂等键', value: run.idemKey },
        { label: '返回', value: run.code }, { label: '自动重试', value: run.retries ? `${run.retries} 次（外部明确失败）` : '0 次' },
        { label: '时间', value: run.at }, { label: '关联告警', value: run.alertId ?? '—' },
      ],
      timeline: tl,
      related: related.length ? related : undefined,
    };
  },
  // 事件（EV-xxx）
  event(id) {
    const ev = eventById(id);
    if (!ev) return null;
    const t = eventTypeById(ev.typeId)!;
    const tl: ObjView['timeline'] = [
      { time: ev.at, text: `接收（${t.sourceRef}）`, status: 'done' },
      { time: addSec(ev.at, 0), text: ev.outcome === '已去重' ? `去重命中，丢弃（${t.dedupKey}，${t.dedupWindow}）` : '去重检查通过', status: 'done' },
    ];
    if (ev.outcome !== '已去重') tl.push({ time: addSec(ev.at, 1), text: ev.outcome === '投递失败' ? '投递失败，3 次重试后进入死信队列' : ev.outcome === '部分投递' ? `投递 ${ev.subscribers} 个订阅方，1 个超时待重试` : `投递 ${ev.subscribers} 个订阅方`,
      status: ev.outcome === '投递失败' ? 'error' : 'done' });
    const related: NonNullable<ObjView['related']> = [];
    if (ev.objKind) related.push({ kind: ev.objKind, id: ev.objId, label: ev.objLabel, note: `事件对象（${t.object}）` });
    if (t.ruleId) related.push({ kind: 'rule', id: t.ruleId, label: `${t.ruleId} ${RULES[t.ruleId].name}`, note: '产生该事件的规则' });
    return {
      kind: 'event', kindLabel: '事件', id: ev.id, title: `${t.name} · ${ev.objLabel}`, code: ev.id,
      status: { text: ev.outcome, color: evColor(ev.outcome) },
      brief: [{ label: '事件类型', value: t.name }, { label: '时间', value: ev.at }, { label: '对象', value: ev.objLabel }, { label: '结果', value: ev.outcome }],
      fields: [
        { label: '事件类型', value: `${t.name}（${t.api}）` }, { label: '来源', value: `${t.source} · ${t.sourceRef}` },
        { label: '发生时间', value: ev.at }, { label: '处理延迟', value: `${ev.latencyMs} 毫秒` },
        { label: '对象', value: `${t.object} · ${ev.objLabel}` }, { label: '载荷摘要', value: ev.summary },
        { label: '去重键', value: ev.dedupKey }, { label: '投递结果', value: ev.outcome },
      ],
      timeline: tl,
      related: related.length ? related : undefined,
    };
  },
};
