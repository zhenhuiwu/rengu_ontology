/**
 * 动态本体：告警处理 + 动作中心。开关键 dynamic，前缀 ontology（docs/backend/dynamic-api.md §4，
 * 后台 backend/app/modules/dynamic/router.py，共 18 个接口）。
 *
 * 页面（pages/ops/Alerts.vue、Actions.vue）在 dynamicApi.isReal() 时走这里，否则保持原来的 mock 逻辑不变。
 * 字段名已和前端 mock 对齐（Alert / ActionRecord），这里只做类型收窄，不改值。
 */
import type { Alert, AlertStatus, AlertDim } from '@/mock/dynamic';
import type { ActionLog, ActionRecord, ActionStatus, SysId } from '@/mock/actions';
import { apiClient, isApiError, type PageResult } from './http';
import { DRAFTS } from '@/mock/releases';

export const dynamicApi = apiClient('dynamic');
const api = dynamicApi;
const enc = encodeURIComponent;

// ---------------- 告警 ----------------

export type AlertTab = 'open' | 'doing' | 'closed' | 'all';
export interface AlertLogDto { time: string; text: string; who: string; abnormal?: boolean | null }
/** 后台 AlertOut：Alert + 通用维度 dims + 对象类型显示名 + 关联动作（actionRef / actionStatus 同 Alerts.vue Row） */
export interface AlertDto extends Omit<Alert, 'storeId' | 'storeLabel'> {
  objKindLabel: string; dims: AlertDim[];
  actionRef?: string | null; actionStatus?: string | null; actionId?: string | null;
}
export interface AlertRuleDto { id: string; name: string; version: string; trigger: string; condition: string; schedule: string; dedupe: string; action?: string; metric?: string; confirmer?: string }
export interface AlertCan { claim: boolean; transfer: boolean; close: boolean; startAction: boolean; reasons: Record<string, string> }
/** approver：发起动作时的审批人，取规则绑定动作类型的审批人配置（无则 null） */
export interface AlertDetailDto extends AlertDto { rule: AlertRuleDto | null; approver: string | null; recent7d: number; locked: boolean; log: AlertLogDto[]; can: AlertCan }
export interface AlertPageDto extends PageResult<AlertDto> { counts: Record<AlertTab, number> }
export interface AlertQuery { status: AlertTab; level?: string; ruleId?: string; kw?: string; page: number; size: number }

const opt = (v?: string) => (v && v !== '全部' ? v : undefined);

export const listAlerts = (q: AlertQuery) =>
  api.get<AlertPageDto>('/alerts', { query: { status: q.status, level: opt(q.level), ruleId: opt(q.ruleId), kw: q.kw?.trim() || undefined, page: q.page, size: q.size } });
export const getAlert = (id: string) => api.get<AlertDetailDto>(`/alerts/${enc(id)}`);
export const claimAlert = (id: string) => api.post<AlertDetailDto>(`/alerts/${enc(id)}:claim`);
export const batchClaimAlerts = (ids: string[]) => api.post<{ claimed: number; ids: string[] }>('/alerts:batch-claim', { ids });
export const transferAlert = (id: string, to: string) => api.post<AlertDetailDto>(`/alerts/${enc(id)}:transfer`, { to });
export const closeAlert = (id: string, as: '已解决' | '已忽略', reason: string) => api.post<AlertDetailDto>(`/alerts/${enc(id)}:close`, { as, reason });
export const batchCloseAlerts = (ids: string[], as: '已解决' | '已忽略', reason: string) =>
  api.post<{ closed: number; ids: string[] }>('/alerts:batch-close', { ids, as, reason });
export const startAlertAction = (id: string) => api.post<{ alert: AlertDetailDto; action: ActionRunDetailDto }>(`/alerts/${enc(id)}:start-action`);
export const listPeople = () => api.get<string[]>('/people');

// ---------------- 动作 ----------------

export type ActionTab = 'pending' | 'records' | 'unknown';
/** 后台 ActionRunOut（动作中心记录 source 为对象）。可空字段在页面上按 mock 的可选字段处理 */
export type ActionRunDto = Omit<ActionRecord, 'log' | 'failReason' | 'unknownReason' | 'reconcile' | 'retryOf' | 'retriedAs'> & {
  typeId: string; confirmer?: string | null;
  failReason?: string; unknownReason?: string; reconcile?: string; retryOf?: string; retriedAs?: string;
};
export interface ActionCan { confirm: boolean; withdraw: boolean; approve: boolean; resubmit: boolean; reconcile: boolean; reasons: Record<string, string> }
export interface ActionRunDetailDto extends ActionRunDto { log: ActionLog[]; can: ActionCan }
export interface ActionRunPageDto extends PageResult<ActionRunDto> { counts: Record<ActionTab, number>; statusCounts: Record<string, number> }
export interface ActionRunQuery { tab: ActionTab; status?: string; action?: string; sysId?: string; kw?: string; page: number; size: number }
export interface ActionMetaDto {
  actionNames: string[];
  actionSystems: { id: SysId; name: string }[];
  actionMeta: Record<string, { sysId: SysId; kind: string; version: string; approver: string; call: string }>;
  /** 对象类型显示名（对象类型目录） */
  objKindLabel?: Record<string, string>;
}
export type ReconcileResult = '外部已成功' | '外部未执行' | '仍无法确认';

/** 后台 null → 前端可选字段 undefined（mock 里这些字段是 `?:`，页面用 `??` / 真假判断） */
const clean = <T extends object>(o: T): T => {
  const r = { ...o } as Record<string, unknown>;
  for (const k of ['failReason', 'unknownReason', 'reconcile', 'retryOf', 'retriedAs']) if (r[k] === null) r[k] = undefined;
  if (r.source && typeof r.source === 'object') {
    const s = { ...(r.source as Record<string, unknown>) };
    for (const k of Object.keys(s)) if (s[k] === null) s[k] = undefined;
    r.source = s;
  }
  return r as T;
};

export async function listActionRuns(q: ActionRunQuery): Promise<ActionRunPageDto> {
  const r = await api.get<ActionRunPageDto>('/action-runs', {
    query: { tab: q.tab, status: opt(q.status), action: opt(q.action), sysId: opt(q.sysId), kw: q.kw?.trim() || undefined, page: q.page, size: q.size },
  });
  return { ...r, items: r.items.map(clean) };
}
export const getActionRun = async (id: string) => clean(await api.get<ActionRunDetailDto>(`/action-runs/${enc(id)}`));
export const getActionMeta = () => api.get<ActionMetaDto>('/actions/meta');
export const confirmActionRun = async (id: string) => clean(await api.post<ActionRunDetailDto>(`/action-runs/${enc(id)}:confirm`));
export const withdrawActionRun = async (id: string, reason: string) => clean(await api.post<ActionRunDetailDto>(`/action-runs/${enc(id)}:withdraw`, { reason }));
export const approveActionRun = async (id: string) => clean(await api.post<ActionRunDetailDto>(`/action-runs/${enc(id)}:approve`));
export const resubmitActionRun = async (id: string) => {
  const r = await api.post<{ id: string; idemKey: string; action: ActionRunDetailDto; original: ActionRunDetailDto }>(`/action-runs/${enc(id)}:resubmit`);
  return { ...r, action: clean(r.action), original: clean(r.original) };
};
export const reconcileActionRun = async (id: string, result: ReconcileResult, note: string) =>
  clean(await api.post<ActionRunDetailDto>(`/action-runs/${enc(id)}:reconcile`, { result, note }));
/** 暂存动作（规则 / 问数 / 推演 / 手动入口用；动作中心、告警页本身没有暂存入口） */
export const stageActionRun = async (body: Record<string, unknown>) => clean(await api.post<ActionRunDetailDto>('/action-runs:stage', body));

export type { AlertStatus, ActionStatus };

// ---------------- 规则与自动化（pages/ontology/Rules.vue；后台 rules_router.py） ----------------

import type { RuleDef, AutoStage, Hit } from '@/mock/rules';

/** 后台 RuleDef + 显示状态（草稿中 / 审核中 / 已发布 / 已停用）+ 所在变更单；详情多带 draft（有草稿时） */
export interface RuleDto extends RuleDef { displayStatus: string; cr?: string | null; draft?: RuleDef | null }
export interface RuleQuery { trigger?: string; status?: string; kw?: string; page: number; size: number }
export interface RuleMetaDto { confirmers: string[]; mappingSources: string[]; triggers: string[]; statuses: string[] }
export interface RuleVersionDto { ver: number; status: string; by: string; at: string; change: string; cr?: string | null }
export interface TimelineDto { time: string; text: string; desc: string; status: string }
export interface RuleHitDetailDto extends Omit<Hit, 'alert'> { alert: AlertDetailDto | null; linkedAction: ActionRunDto | null; timeline: TimelineDto[] }
/** 命中的上下文维度（通用，替代旧的 storeId / storeLabel；后台目前只给 objKind=store 一种） */
export interface HitDim { objKind: string; id: string; label?: string | null }
export interface ReplaySampleDto { key: string; at: string; objKind: string; objId: string; objLabel: string; dims?: HitDim[]; value: string; diff: string }
export interface ReplayDto { actual: number; would: number; added: number; removed: number; deduped: number; dedupeReason: string; samples: ReplaySampleDto[] }

/** 后台 null → 前端可选字段 undefined（Hit.storeId / alertId 等在 mock 里是 `?:`） */
const undef = <T extends object>(o: T): T => {
  const r = { ...o } as Record<string, unknown>;
  for (const k of Object.keys(r)) if (r[k] === null) r[k] = undefined;
  return r as T;
};


export const listRules = (q: RuleQuery) =>
  api.get<PageResult<RuleDto>>('/rules', { query: { trigger: opt(q.trigger), status: opt(q.status), kw: q.kw?.trim() || undefined, page: q.page, size: q.size } });
export const getRulesMeta = () => api.get<RuleMetaDto>('/rules/meta');
export const getRule = (id: string) => api.get<RuleDto>(`/rules/${enc(id)}`);
/** 保存到草稿：后台同一事务把改动登记成变更单 conds / mapping / config 三段（单子在库里时）；带 lockVersion 乐观锁 */
export interface RuleDraftSaved { cr: string; draft: RuleDef; message: string; lockVersion?: number | null; crChanges?: number | null }
export async function saveRuleDraft(id: string, body: { cr: string; autoStage: AutoStage; thresholds: string[] }) {
  const r = await api.put<RuleDraftSaved>(`/rules/${enc(id)}/draft`, { ...body, lockVersion: await crLock(body.cr) });
  crLocked(body.cr, r);
  return r;
}
export async function listRuleHits(id: string, range: '7d' | '30d', page: number, size: number) {
  const r = await api.get<PageResult<Hit & { dims?: HitDim[] }>>(`/rules/${enc(id)}/hits`, { query: { range, page, size } });
  return { ...r, items: r.items.map(undef) };
}
export async function getRuleHit(id: string) {
  const r = await api.get<RuleHitDetailDto>(`/rule-hits/${enc(id)}`);
  return { ...undef(r), linkedAction: r.linkedAction ? clean(r.linkedAction) : null, alert: r.alert, timeline: r.timeline };
}
/** 新建规则（未发布、变更中）；带 cr 时同时开草稿 */
export const createRule = (body: { cr?: string; name?: string; objKind?: string } = {}) => api.post<RuleDto>('/rules', body);
/** 发起变更 / 加入已有变更单：登记规则草稿（幂等；已在别的草稿单 409 TARGET_IN_OTHER_CR） */
export const openRuleDraft = (id: string, cr: string) => api.post<RuleDto>(`/rules/${enc(id)}/draft`, { cr });
/** 放弃本规则草稿（从未发布的新规则一并删除）；后台同一事务删变更单里本规则的登记和草稿行，不留孤儿 */
export const discardRuleDraft = (id: string, cr?: string) => api.delete<void>(`/rules/${enc(id)}/draft`, { query: cr ? { cr } : {} });

/**
 * 变更单写接口（静态 static-r3 契约，handoff/static.md「变更单写接口契约」）。
 * VITE_DYNAMIC_CR_WRITE=1 才走真接口（发起 / 加入变更单、提交、放弃），否则草稿横幅保持 mock 行为。
 */
export const CR_WRITE = import.meta.env.VITE_DYNAMIC_CR_WRITE === '1';
export interface CrTarget { kind: 'rule' | 'object'; id: string; isNew?: boolean; name?: string }
export const submitCR = (cr: string) => api.post<{ id: string; status: string }>(`/change-requests/${enc(cr)}:submit`, {});
export const removeCRTarget = (cr: string, t: CrTarget) => api.delete<void>(`/change-requests/${enc(cr)}/targets/${t.kind}/${enc(t.id)}`);
interface CrFullDto { id: string; title: string; owner: string; updatedAt: string; changes: number; lockVersion: number }
/** 各单子最近一次看到的 lockVersion（保存时带上；VERSION_CONFLICT 后清掉，下次重新读） */
const LOCKS: Record<string, number> = {};
async function crLock(cr: string): Promise<number | undefined> {
  if (!CR_WRITE) return undefined;
  if (LOCKS[cr] === undefined) {
    // 读单子失败就把错误抛给调用方（页面按错误码提示），不带 lockVersion 偷偷保存会绕过乐观锁
    LOCKS[cr] = (await api.get<CrFullDto>(`/change-requests/${enc(cr)}`)).lockVersion;
  }
  return LOCKS[cr];
}
function crLocked(cr: string, r: { lockVersion?: number | null; crChanges?: number | null }) {
  if (r.lockVersion != null) LOCKS[cr] = r.lockVersion;
  const d = DRAFTS.find((x) => x.id === cr);
  if (d && r.crChanges != null) d.changes = r.crChanges;
}
/** 把库里的单子登记进 DRAFTS（useDraftView 的 allDrafts / draftOf 读它；同号替换） */
function registerCR(cr: CrFullDto) {
  LOCKS[cr.id] = cr.lockVersion;
  const row = { id: cr.id, title: cr.title, owner: cr.owner, updatedAt: cr.updatedAt, changes: cr.changes };
  const i = DRAFTS.findIndex((d) => d.id === cr.id);
  if (i >= 0) DRAFTS.splice(i, 1, row); else DRAFTS.unshift(row);
}
/**
 * 发起变更：新建变更单（带目标）或加入已有单；返回库里的单号。target 省略 = 只建空单（新建规则：规则建好后由 POST /rules 带 cr 登记）。
 * 占用他单 409 TARGET_IN_OTHER_CR，由 crErrText 提示“已在 CR-xxxx 中”。
 */
export async function crOpen(opts: { join?: string; title?: string; desc?: string }, target?: CrTarget): Promise<string> {
  const cr = opts.join
    ? (target ? await api.post<CrFullDto>(`/change-requests/${enc(opts.join)}/targets`, target) : await api.get<CrFullDto>(`/change-requests/${enc(opts.join)}`))
    : await api.post<CrFullDto>('/change-requests', { title: opts.title || '未命名变更', reason: opts.desc, target });
  registerCR(cr);
  return cr.id;
}
/** 撤销 crOpen 新建的空单（新建规则失败时；尽力而为） */
export async function crDropEmpty(cr: string) {
  try { await api.delete<void>(`/change-requests/${enc(cr)}`); } catch { /* 留一张空草稿单不影响业务 */ }
  const i = DRAFTS.findIndex((d) => d.id === cr);
  if (i >= 0) DRAFTS.splice(i, 1);
  delete LOCKS[cr];
}
/** 变更单相关错误的中文提示（其余错误用后台 message） */
export function crErrText(e: unknown): string {
  if (!isApiError(e)) return e instanceof Error ? e.message : String(e);
  const d = (e.details ?? {}) as Record<string, unknown>;
  switch (e.code) {
    case 'CR_EMPTY': return '变更单里还没有改动，先保存到草稿再提交审核';
    case 'CR_BLOCKED': {
      const b = Array.isArray(d.blocked) ? (d.blocked as unknown[]).map((x) => (typeof x === 'string' ? x : (x as { name?: string; id?: string }).name ?? (x as { id?: string }).id ?? '')).filter(Boolean) : [];
      return `合并校验有阻断项，不能提交审核${b.length ? `：${b.join('、')}` : ''}`;
    }
    case 'CR_NO_REVIEWER': return '变更单还没有指定审核人，请先在“变更与发布”里指定审核人';
    case 'CR_LOCKED': return `变更单当前是“${String(d.status ?? '非草稿')}”，只有草稿状态才能修改`;
    case 'VERSION_CONFLICT':
      if (typeof d.id === 'string') delete LOCKS[d.id];
      return '变更单刚被其他人修改过，请刷新后重试';
    case 'TARGET_IN_OTHER_CR': return `已在 ${String(d.crId ?? '其他变更单')} 中，请到该变更单里修改`;
    default: return e.message || e.code;
  }
}

export const replayRule = (id: string, range: '7d' | '30d', version: 'current' | 'draft') =>
  api.post<ReplayDto>(`/rules/${enc(id)}:replay`, { range, version });
export const listRuleVersions = (id: string) => api.get<RuleVersionDto[]>(`/rules/${enc(id)}/versions`);
/** 规则暂存过的动作（动作页“已暂存的动作”）：动作中心全部记录按 ruleId 过滤 */
export async function listRuleActionRuns(ruleId: string) {
  const r = await api.get<ActionRunPageDto>('/action-runs', { query: { ruleId, page: 1, size: 100 } });
  return { ...r, items: r.items.map(clean) };
}

// ---------------- 生命周期（pages/ontology/parts/object/ObjBehaviorLifecycle.vue；后台 rules_router.py L1–L6） ----------------

import type { Lifecycle, LcDraft, LcIllegal, LcChange } from '@/mock/lifecycle';

/** L1：当前（或 ?ver= 历史版本发布时生效的）状态机；view=draft 时带 draft = { cr, edits } */
export interface LifecycleDto extends Lifecycle { draft: { cr: string | null; edits: LcDraft } | null; readonly?: boolean }

export const getLifecycle = (objectId: string, draftCr?: string) =>
  api.get<LifecycleDto>(`/object-types/${enc(objectId)}/lifecycle`, { query: draftCr ? { view: 'draft', cr: draftCr } : {} });
export function listLifecycleChanges(objectId: string, illegal: boolean, page: number, size: number) {
  return api.get<PageResult<LcChange>>(`/object-types/${enc(objectId)}/lifecycle/changes`, { query: { illegal: illegal ? 1 : 0, page, size } });
}
export const listLifecycleIllegal = (objectId: string) => api.get<LcIllegal[]>(`/object-types/${enc(objectId)}/lifecycle/illegal`);
/** L3–L6：整份草稿改动（新增状态 / 新增·修改·删除迁移）一起保存 */
export async function saveLifecycleDraft(objectId: string, cr: string, edits: LcDraft) {
  // 后台同一事务登记到变更单（目标 kind=object，section lc_states / lc_transitions）
  const r = await api.put<{ cr: string; edits: LcDraft; lockVersion?: number | null; crChanges?: number | null }>(
    `/object-types/${enc(objectId)}/lifecycle/draft`, { cr, ...edits, lockVersion: await crLock(cr) });
  crLocked(cr, r);
  return r;
}

/* ───── 情景推演（ops/Scenarios.vue） ───── */
import type { Scenario, ScResult, ScImpact, StagePlan } from '@/mock/scenarios';
import type { ScCfg, ScTemplate } from '@/mock/scenarioTemplates';
/** 详情 / 运行 / 改名返回：result 不含 impacts（影响明细走分页接口） */
export interface ScenarioDto extends Omit<Scenario, 'result'> { result?: Omit<ScResult, 'impacts'> | null; lockReason?: string }
export interface ScenarioMetaDto { templates: ScTemplate[]; areas: string[]; categories: string[]; promoTypes: string[]; baseDrivers: ScCfg; statuses: string[] }
export const listScenarios = (q: { status?: string; kw?: string; page: number; size: number }) =>
  api.get<PageResult<ScenarioDto>>('/scenarios', { query: { status: opt(q.status), kw: q.kw?.trim() || undefined, page: q.page, size: q.size } });
export const getScenariosMeta = () => api.get<ScenarioMetaDto>('/scenarios/meta');
export const compareScenarios = (ids: string[]) => api.get<{ items: ScenarioDto[]; sameScope: boolean }>('/scenarios:compare', { query: { ids: ids.join(',') } });
export const createScenario = (from?: string) => api.post<ScenarioDto>('/scenarios', from ? { from } : {});
export const getScenario = (id: string) => api.get<ScenarioDto>(`/scenarios/${enc(id)}`);
export const renameScenario = (id: string, name: string) => api.patch<ScenarioDto>(`/scenarios/${enc(id)}`, { name });
export const runScenario = (id: string, body: { name?: string; scope: ScCfg; drivers: ScCfg }) => api.post<ScenarioDto>(`/scenarios/${enc(id)}:run`, body);
/** 运行中回 202 { status: '运行中' }（继续轮询）；失败回 409 SCENARIO_FAILED；完成回结果 */
export const getScenarioResult = (id: string) => api.get<Omit<ScResult, 'impacts'> | { status: string }>(`/scenarios/${enc(id)}/result`);
/** 轮询被页面卸载等中止时抛出，调用方静默忽略 */
export class PollAborted extends Error {}
/** :run 回 202（运行中）后轮询 /result，直到完成（或失败抛错 / 超时 / signal 中止抛 PollAborted） */
export async function waitScenarioResult(id: string, everyMs = 800, maxTries = 150, signal?: AbortSignal): Promise<Omit<ScResult, 'impacts'>> {
  for (let i = 0; i < maxTries; i += 1) {
    if (signal?.aborted) throw new PollAborted(id);
    const r = await getScenarioResult(id);
    if (signal?.aborted) throw new PollAborted(id);
    if (!('status' in r)) return r;
    await new Promise((ok) => window.setTimeout(ok, everyMs));
  }
  throw new Error(`${id} 推演超时，请稍后刷新查看`);
}
export const listScenarioImpacts = (id: string, page: number, size: number) => api.get<PageResult<ScImpact>>(`/scenarios/${enc(id)}/impacts`, { query: { page, size } });
export const getScenarioStagePlan = (id: string) => api.get<StagePlan[]>(`/scenarios/${enc(id)}/stage-plan`);
export const stageScenario = (id: string, keys: string[]) => api.post<{ staged: string[]; message: string }>(`/scenarios/${enc(id)}:stage`, { keys });
