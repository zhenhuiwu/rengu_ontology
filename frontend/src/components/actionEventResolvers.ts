/**
 * 动作类型 / 事件类型页面用到的抽屉解析（规则、动作执行记录、事件）。
 * 由 objectRegistry.ts 的 RESOLVERS 展开引入；这里只新增 kind，不改已有 kind。
 */
import type { ObjView } from './objectRegistry';
import { RULES, ALERTS } from '@/mock/dynamic';
import { runById, actionTypeById, eventById, eventTypeById, ACTION_TYPES, type RunStatus, type EventOutcome } from '@/mock/actionEvent';

const runColor = (s: RunStatus): NonNullable<ObjView['status']>['color'] =>
  s === '失败' ? 'error' : s === '结果未知' || s === '待审批' ? 'warning' : 'default';
const runText = (s: RunStatus) => (s === '结果未知' ? '结果未知 · 待对账' : s);
const evColor = (o: EventOutcome): NonNullable<ObjView['status']>['color'] =>
  o === '投递失败' ? 'error' : o === '部分投递' ? 'warning' : 'default';
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
    const actions = ACTION_TYPES.filter((a) => a.usedBy.includes(id));
    const alerts = ALERTS.filter((a) => a.ruleId === id);
    return {
      kind: 'rule', kindLabel: '规则', id: r.id, title: r.name, code: `${r.id} · ${r.version}`,
      status: { text: '运行中', color: 'default' },
      brief: [{ label: '触发', value: r.trigger }, { label: '条件', value: r.condition }, { label: '执行', value: r.schedule }, { label: '动作', value: r.action ?? '—' }],
      fields: [
        { label: '规则编号', value: r.id }, { label: '版本', value: r.version }, { label: '触发方式', value: r.trigger },
        { label: '执行计划', value: r.schedule }, { label: '条件', value: r.condition }, { label: '去重', value: r.dedupe },
        { label: '命中后动作', value: r.action ?? '仅生成告警' }, { label: '使用的动作类型', value: actions.map((a) => a.name).join('、') || '—' },
        { label: '近期告警', value: `${alerts.length} 条（待处理 ${alerts.filter((a) => a.status === '待处理').length}）` },
      ],
      timeline: alerts.slice(0, 24).map((a) => ({
        time: a.triggeredAt, text: `命中 ${a.objLabel}`, desc: `${a.value}（阈值 ${a.threshold}）· ${a.status}`,
        status: a.status === '待处理' ? 'warn' : 'done',
      })),
      related: r.metric ? [{ kind: 'metric', id: r.metric, label: r.metric === 'gmv' ? '销售额（不含税）' : '毛利率', note: '规则使用的指标' }] : undefined,
    };
  },
  // 动作执行记录（AR-xxx）
  action_run(id) {
    const run = runById(id);
    if (!run) return null;
    const a = actionTypeById(run.typeId)!;
    const internal = a.endpoint === '—';
    const tl: ObjView['timeline'] = [
      { time: run.at, text: `提交（${run.by}）`, desc: run.summary, status: 'done' },
      { time: addSec(run.at, 0), text: run.code === '提交条件未通过' ? '提交条件未通过' : '提交条件校验通过', status: run.code === '提交条件未通过' ? 'error' : 'done' },
    ];
    if (a.approval !== '免审') tl.push({ time: addSec(run.at, 1), text: run.status === '待审批' ? '等待审批' : run.status === '已驳回' ? '审批驳回' : '审批通过', desc: a.approval, status: run.status === '待审批' ? 'warn' : run.status === '已驳回' ? 'error' : 'done' });
    if (run.status !== '待审批' && run.status !== '已驳回' && run.code !== '提交条件未通过') {
      if (!internal) {
        tl.push({ time: addSec(run.at, 2), text: `写回 ${a.system}`, desc: `${a.endpoint} · 幂等键 ${a.idemKey}`,
          status: run.status === '结果未知' ? 'unknown' : run.status === '失败' ? 'error' : 'done' });
        if (run.status === '结果未知') tl.push({ time: addSec(run.at, 2 + Math.round(run.ms / 1000)), text: `超时（${a.timeout}）未收到响应，进入对账队列`, desc: '每 15 分钟按幂等键查询一次，确认前不会重复写回', status: 'unknown' });
        if (run.status === '失败') tl.push({ time: addSec(run.at, 3), text: run.code, desc: a.idempotent ? '已按幂等键重试 3 次，仍失败' : '非幂等接口，未自动重试', status: 'error' });
      }
      if (run.status === '成功') tl.push({ time: addSec(run.at, 3), text: '本体编辑已提交，产生事件 ActionExecuted', status: 'done' });
    }
    const related: NonNullable<ObjView['related']> = [];
    if (run.objKind) related.push({ kind: run.objKind, id: run.objId, label: run.objLabel, note: `动作目标（${a.target}）` });
    if (run.ruleId) related.push({ kind: 'rule', id: run.ruleId, label: `${run.ruleId} ${RULES[run.ruleId].name}`, note: '触发规则' });
    return {
      kind: 'action_run', kindLabel: '动作执行', id: run.id, title: `${a.name} · ${run.objLabel}`, code: run.id,
      status: { text: runText(run.status), color: runColor(run.status) },
      brief: [{ label: '动作', value: `${a.name} ${a.version}` }, { label: '执行人', value: run.by }, { label: '时间', value: run.at }, { label: '结果', value: runText(run.status) }],
      fields: [
        { label: '动作类型', value: `${a.name}（${a.api}）` }, { label: '版本', value: a.version },
        { label: '执行人', value: run.by }, { label: '来源', value: run.source },
        { label: '目标对象', value: `${a.target} · ${run.objLabel}` }, { label: '参数摘要', value: run.summary },
        { label: '写回系统', value: a.system }, { label: '幂等键', value: a.idemKey },
        { label: '返回', value: run.code }, { label: '耗时', value: `${(run.ms / 1000).toFixed(2)} 秒` },
        { label: '关联告警', value: run.alertId ?? '—' }, { label: '结果', value: runText(run.status) },
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
      status: ev.outcome === '投递失败' ? 'error' : ev.outcome === '部分投递' ? 'warn' : 'done' });
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
