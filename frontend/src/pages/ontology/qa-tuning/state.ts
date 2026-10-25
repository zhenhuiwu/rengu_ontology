/** 问数调优四个页签共用的可变状态（反馈转认证问答、高频问题设为认证问答都会写进认证问答列表） */
import { reactive } from 'vue';
import { CERT_QAS, EVAL_RUNS, FEEDBACKS, FREQ_QS, TUNED_SYNONYMS, type CertQa, type EvalRun, type Feedback, type FreqQ, type SemPlan, type TunedSynonym } from '@/mock/qa-tuning';

export const tuning = reactive({
  certs: CERT_QAS.map((c) => ({ ...c, variants: [...c.variants] })) as CertQa[],
  runs: EVAL_RUNS.map((r) => ({ ...r })) as EvalRun[],
  feedbacks: FEEDBACKS.map((f) => ({ ...f })) as Feedback[],
  freq: FREQ_QS.map((f) => ({ ...f })) as FreqQ[],
  /** 已走变更单的同义词（CR-0008 / CR-0015 已通过，待 v2026.10.4 发布） */
  synonyms: TUNED_SYNONYMS.map((x) => ({ ...x })) as TunedSynonym[],
});

export const NOW = '2026-10-25 17:10';
export const ME = '王敏';

export function addCert(question: string, plan: SemPlan, source: string, extra: Partial<CertQa> = {}): CertQa {
  const n = tuning.certs.reduce((m, c) => Math.max(m, parseInt(c.id.slice(3), 10)), 0) + 1;
  const c: CertQa = { id: `CQ-${String(n).padStart(3, '0')}`, question, variants: [], plan, status: '已认证', certifier: ME, certifiedAt: NOW, hits: 0, source, ...extra };
  tuning.certs.unshift(c);
  tuning.freq.filter((f) => f.question === question).forEach((f) => { f.certId = c.id; });
  return c;
}
/** 同义词改动走变更单：编号由公共 useDraftView().start 在确认时分配（见 FeedbackTab），这里不预占 */

/** 手动运行评测：新增一条“运行中”的记录 */
export function startRun(scope: string, total: number, version: string): EvalRun {
  const n = tuning.runs.reduce((m, r) => Math.max(m, parseInt(r.id.slice(3), 10)), 0) + 1;
  const r: EvalRun = { id: `ER-${String(n).padStart(4, '0')}`, time: NOW, trigger: '手动运行', version, scope, total, passed: 0, status: '运行中', operator: ME, fails: [] };
  const i = tuning.runs.findIndex((x) => x.status !== '待运行');
  tuning.runs.splice(i < 0 ? 0 : i, 0, r);
  return r;
}
