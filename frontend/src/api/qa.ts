/**
 * 问数（qa）工作台接口。开关键 qa，前缀 /api/qa/v1（docs/backend/qa-api.md 第 3 节 W1–W17）。
 * 开关没切到真接口时页面照旧走 src/mock，这里的函数不会被调用。
 */
import type { QaSession, QaFavorite, QaPage, QaDemoTurn } from '@/mock/qa';
import type { CertQa, FreqQ, SemPlan, EvalRun, EvalQ, FailCase, FailKind, Feedback } from '@/mock/qa-tuning';
import { apiClient, workspaceFetch, currentApiContext, ApiError, API_BASE, type ApiErrorBody, type PageResult, type RequestOptions } from './http';

const api = apiClient('qa');
/** Freeze identity/history/preview for a multi-await UI operation. */
export function qaRequestOptions(signal?: AbortSignal): Omit<RequestOptions, 'method' | 'body'> {
  const c = currentApiContext();
  return { headers: { 'X-Demo-As': c.demoAs ?? '', 'X-Ontology-Id': c.ontologyId ?? '' }, readonlyParams: false, query: { ver: c.ver, previewAs: c.previewAs }, signal };
}
export const qaReal = () => api.isReal();

/**
 * PATCH / PUT / DELETE 走 apiClient（PDE 215819d 补的 put / patch / delete）。
 * W11 导出要拿文件（Blob + Content-Disposition 文件名），apiClient 只解析 JSON，暂留这个 GET 下载小工具；
 * 规则同 http.ts request：同一开关把关、X-Demo-As、自动带 ?ver / ?previewAs、统一错误体 → ApiError（待 apiClient 有下载方法后改回，已报 PDE）。
 */
async function download(path: string, query?: Record<string, string>): Promise<Response> {
  if (!api.isReal()) throw new ApiError(0, { code: 'MOCK_ONLY', message: '开关 qa 未切到真接口', details: { key: 'qa' } });
  const ctx = currentApiContext();
  const qs = new URLSearchParams(query);
  if (ctx.ver) qs.set('ver', ctx.ver);
  if (ctx.previewAs) qs.set('previewAs', ctx.previewAs);
  const headers: Record<string, string> = {};
  if (ctx.demoAs) headers['X-Demo-As'] = ctx.demoAs;
  if (ctx.ontologyId) headers['X-Ontology-Id'] = ctx.ontologyId;
  const url = `${API_BASE}/${api.prefix}/v1${path}${qs.size ? `?${qs}` : ''}`;
  let res: Response;
  try {
    res = await workspaceFetch(url, { headers });
  } catch (e) {
    if ((e as Error)?.name === 'AbortError') throw e;
    throw new ApiError(0, { code: 'NETWORK_ERROR', message: `无法连接后台：${(e as Error)?.message ?? e}` });
  }
  if (!res.ok) {
    let b: Partial<ApiErrorBody> | null = null;
    try { b = (await res.json()) as Partial<ApiErrorBody>; } catch { /* 非 JSON */ }
    throw new ApiError(res.status, { code: typeof b?.code === 'string' ? b.code : `HTTP_${res.status}`, message: String(b?.message ?? res.statusText), details: b?.details, requestId: b?.requestId ?? null });
  }
  return res;
}

/** W3 会话详情：轮次快照形状同 QA_DEMO_TURNS；链路新生成的回答是 card='table' + text（后台 chain.py） */
export interface QaChainTable { id: string; title: string; columns: { key: string; title: string; align?: string }[]; items: Record<string, unknown>[]; total: number; summary: string; paged: boolean }
export interface QaDynamicSummary {
  objKind: string; objKindLabel?: string; id: string; objLabel?: string;
  alerts: { available: boolean; openCount?: number; latest?: { id: string; ruleId?: string; level?: string; status?: string }[] };
  ruleHits: { available: boolean; total?: number };
  lifecycle: { available: boolean; defined?: boolean; state?: string | null; changeTotal?: number; illegalTotal?: number };
  actionRuns: { available: boolean; total?: number; statusCounts?: Record<string, number> };
}
export interface QaDynamicAnswer { dynamicSummary?: QaDynamicSummary; dynamicSummaries?: QaDynamicSummary[] }
export interface QaChainAnswer extends QaDynamicAnswer { card: 'table'; text: string; dataAsOf?: string; basis?: Record<string, string>; table: QaChainTable }
/** applied：越权轮次已提交待审批的申请（W16 之后 W3 返回）；vote：这一轮的赞踩（W12）；主卡 answer.caliber：W9 切过的口径 */
/** 链路反问（qa-reuse a35d3b6）：kind=answer、card='clarify'，不执行 SQL；选项点了以后前端再问一轮 */
export interface QaClarifyAnswer { card: 'clarify'; text: string; dataAsOf?: string; clarify: { term: string; chosen: string | null; options: QaOption[] }; suggest?: { followUps: string[] } }
/** 拒答 code（链路 refuse）：NO_PERMISSION 是权限拦截（带申请入口），其余是「无法回答」 */
/** 相关告警引用（只读，动态模块 alert；后台 intent.related_alerts） */
export interface QaAlertRef { id: string; rule: string; level: string; objKind: string; objId: string; objLabel: string | null; value: unknown; threshold: unknown; status: string; triggeredAt: string | null }
/**
 * 动作意图（后台 intent.action_snapshot）：只建议 + dry-run 结果。
 * policy = 动作已发布执行策略；canStage=false（suggest / 不允许 AI 发起）只显示建议、不给发起按钮。
 * 发起走 stageTurnAction（问数侧 W7a，只暂存待人工确认，auto 也不自动执行）→ 动作中心 confirm。
 */
export interface QaActionSuggest {
  type: string; name: string; reason: string; params: Record<string, unknown>; mode: 'suggest';
  runId?: string; stageVersion?: number;
  policy?: 'suggest' | 'confirm' | 'auto'; canStage?: boolean;
  dryRun: import('@/pages/ops/actionReview').ActionReview & { passed: boolean; checked: number; failed: { name?: string; msg?: string }[] };
  stage: { typeId: string; objKind: string | null; objId: string; objLabel: string; params: string | null };
}
export interface QaActionAnswer extends QaDynamicAnswer { card: 'action'; text: string; basis?: Record<string, string>; action: QaActionSuggest; alerts?: QaAlertRef[] }
export interface QaRuleAnswer { card: 'rule'; text: string; rule: { id: string; version: number; cardVersion: number; triggerKind?: string; eventId?: string; result?: Record<string, unknown> } }
export type QaRefuseCode = 'NOT_SELECT' | 'OUT_OF_SCOPE' | 'NO_PERMISSION' | 'NO_DATA';
export type QaRealTurn =
  | (QaDemoTurn & { applied?: boolean; vote?: '' | 'up' | 'down'; code?: QaRefuseCode })
  | { id: string; kind: 'answer'; time?: string; question: string; answer: QaChainAnswer }
  | { id: string; kind: 'answer'; time?: string; question: string; answer: QaClarifyAnswer }
  | { id: string; kind: 'answer'; time?: string; question: string; answer: QaActionAnswer }
  | { id: string; kind: 'answer'; time?: string; question: string; answer: QaRuleAnswer }
  | { id: string; kind: 'empty'; time?: string; question: string; lead: string; bold: string; tail: string; tag: string; retry?: undefined };
export interface QaSessionDetail extends QaSession { scope: string | null; caliber: string | null; ownerId: string; turns: QaRealTurn[] }
export interface QaOption { value: string; label: string }
export interface QaSessionOptions { scopes: QaOption[]; calibers: QaOption[] }
export interface QaAskOut { turnId: string; streamUrl: string; turn: QaRealTurn }

export const listSessions = (q: { q?: string; range?: string; page: number; size: number }, options: Omit<RequestOptions, 'method' | 'body'> = {}) =>
  api.get<PageResult<QaSession>>('/sessions', { ...options, query: { ...options.query, ...q, range: q.range === 'all' ? undefined : q.range } });
export const getSession = (sid: string, options: Omit<RequestOptions, 'method' | 'body'> = {}) => api.get<QaSessionDetail>(`/sessions/${encodeURIComponent(sid)}`, options);
export const createSession = (body: { scope: string; caliber: string }, options: Omit<RequestOptions, 'method' | 'body'> = {}) => api.post<QaSessionDetail>('/sessions', body, options);
export const patchSession = (sid: string, body: { title?: string; scope?: string; caliber?: string }, options: Omit<RequestOptions, 'method' | 'body'> = {}) =>
  api.patch<QaSessionDetail>(`/sessions/${encodeURIComponent(sid)}`, body, options);
export const deleteSession = (sid: string, options: Omit<RequestOptions, 'method' | 'body'> = {}) => api.delete<void>(`/sessions/${encodeURIComponent(sid)}`, options);
export const askTurn = (sid: string, question: string, options: Omit<RequestOptions, 'method' | 'body'> = {}) =>
  api.post<QaAskOut>(`/sessions/${encodeURIComponent(sid)}/turns`, { question, refs: [] }, options);
/** W7 SSE 事件（qa-api §3.1）；status 只在实时流里有（链路阶段），其余事件带 id、落 qa_turn_event 可续传 */
export interface QaStreamEvent { id: number | null; event: string; data: any }
/** 后台不支持流（非 event-stream / 流里没拿到 done）→ 调用方回退 W6 askTurn */
export class QaStreamUnsupported extends Error {}
/** 解析 SSE 文本块（id / event / data 多行），不依赖 EventSource（POST + 自定义头） */
export function parseSseBlock(block: string): QaStreamEvent | null {
  let id: number | null = null, event = 'message';
  const data: string[] = [];
  for (const line of block.split('\n')) {
    const k = line.indexOf(':');
    if (k <= 0) continue;
    const name = line.slice(0, k), v = line.slice(k + 1).replace(/^ /, '');
    if (name === 'id') id = Number(v); else if (name === 'event') event = v; else if (name === 'data') data.push(v);
  }
  if (!data.length) return null;
  try { return { id, event, data: JSON.parse(data.join('\n')) }; } catch { return null; }
}
/**
 * W7 流式提问：POST /sessions/{sid}/turns:stream，边收边回调 onEvent，最后返回 done 带的轮次（形状同 W6）。
 * 直接用 fetch 读 ReadableStream（http.ts 的 workspaceFetch 会把整个响应先读完）；头部 / ?ver 规则同 download。
 * 流前错误（404 / 403 / 409）照常抛 ApiError；流里的 error 事件抛 ApiError；拿不到流抛 QaStreamUnsupported。
 */
export async function askTurnStream(sid: string, question: string, onEvent: (e: QaStreamEvent) => void, signal?: AbortSignal): Promise<QaAskOut> {
  if (!api.isReal()) throw new QaStreamUnsupported('mock');
  const ctx = currentApiContext();
  const qs = new URLSearchParams();
  if (ctx.ver) qs.set('ver', ctx.ver);
  if (ctx.previewAs) qs.set('previewAs', ctx.previewAs);
  const headers: Record<string, string> = { Accept: 'text/event-stream', 'Content-Type': 'application/json' };
  if (ctx.demoAs) headers['X-Demo-As'] = ctx.demoAs;
  if (ctx.ontologyId) headers['X-Ontology-Id'] = ctx.ontologyId;
  const url = `${API_BASE}/${api.prefix}/v1/sessions/${encodeURIComponent(sid)}/turns:stream${qs.size ? `?${qs}` : ''}`;
  let res: Response;
  try { res = await fetch(url, { method: 'POST', headers, body: JSON.stringify({ question, refs: [] }), signal }); }
  catch (e) { if ((e as Error)?.name === 'AbortError') throw e; throw new QaStreamUnsupported(String(e)); }
  if (res.status === 404 && !(res.headers.get('content-type') ?? '').includes('json')) throw new QaStreamUnsupported('no endpoint');
  if (!res.ok) {
    let b: Partial<ApiErrorBody> | null = null;
    try { b = (await res.json()) as Partial<ApiErrorBody>; } catch { /* 非 JSON */ }
    if (res.status === 405 || res.status === 501) throw new QaStreamUnsupported(`HTTP ${res.status}`);
    throw new ApiError(res.status, { code: typeof b?.code === 'string' ? b.code : `HTTP_${res.status}`, message: String(b?.message ?? res.statusText), details: b?.details, requestId: b?.requestId ?? null });
  }
  if (!(res.headers.get('content-type') ?? '').startsWith('text/event-stream') || !res.body) throw new QaStreamUnsupported('not sse');
  const reader = res.body.pipeThrough(new TextDecoderStream()).getReader();
  let buf = '', done: QaStreamEvent | null = null, failed: QaStreamEvent | null = null;
  for (;;) {
    const { value, done: end } = await reader.read();
    if (value) buf += value.replace(/\r\n/g, '\n');
    let k: number;
    while ((k = buf.indexOf('\n\n')) >= 0) {
      const ev = parseSseBlock(buf.slice(0, k));
      buf = buf.slice(k + 2);
      if (!ev) continue;
      if (ev.event === 'error') failed = ev;
      if (ev.event === 'done') done = ev;
      onEvent(ev);
    }
    if (end) break;
  }
  if (failed) throw new ApiError(500, { code: String(failed.data?.code ?? 'INTERNAL_ERROR'), message: String(failed.data?.message ?? '回答失败') });
  if (!done?.data?.turn) throw new QaStreamUnsupported('no done');
  return { turnId: done.data.turnId, streamUrl: `/api/qa/v1/turns/${done.data.turnId}/stream`, turn: done.data.turn as QaRealTurn };
}
export const listFavorites = (q: { q?: string; page: number; size: number }, options: Omit<RequestOptions, 'method' | 'body'> = {}) => api.get<PageResult<QaFavorite>>('/favorites', { ...options, query: { ...options.query, ...q } });
export const deleteFavorite = (id: string) => api.delete<void>(`/favorites/${encodeURIComponent(id)}`);
/** P1 简版（左栏贴底、「我的页面 · 全部」抽屉） */
export const listPagesBrief = (q: { kw?: string; page: number; size: number }, options: Omit<RequestOptions, 'method' | 'body'> = {}) =>
  api.get<PageResult<QaPage>>('/pages', { ...options, query: { ...options.query, ...q, view: 'brief' } });
/** W16 申请数据权限；duration 为天数 30 | 90 | 180（前端单选文字转数字） */
export interface QaPermRequestIn { scope: string; object: string; reason: string; duration: number; sourceTurnId: string | null }
export const createPermissionRequest = (body: QaPermRequestIn) =>
  api.post<{ id: string; status: string; approver: string | null }>('/permission-requests', body);
export const getSessionOptions = () => api.get<QaSessionOptions>('/session-options');

/** W9 澄清条切换口径：返回这一轮的 Answer（带 caliber） */
export type QaCaliberKey = 'ex' | 'in' | 'both';
export const recalcTurn = (tid: string, caliber: QaCaliberKey) =>
  api.post<Record<string, unknown> & { caliber: QaCaliberKey }>(`/turns/${encodeURIComponent(tid)}:recalc`, { caliber });
/** W12 赞踩：'' = 取消；踩会在问数调优生成一条待处理反馈 */
export type QaVote = '' | 'up' | 'down';
export const voteTurn = (tid: string, vote: QaVote) => api.put<{ id: string; vote: QaVote }>(`/turns/${encodeURIComponent(tid)}/vote`, { vote });
/** W10 结果表翻页：summary 按全部数据算 */
export interface QaTurnResult<R = Record<string, unknown>> extends PageResult<R> { columns: { key: string; title: string; align?: string }[]; summary: Record<string, number | string | null> }
export const getTurnResult = <R = Record<string, unknown>>(tid: string, q: { page: number; size: number }) =>
  api.get<QaTurnResult<R>>(`/turns/${encodeURIComponent(tid)}/result`, { query: q });
/** W11 导出全部行：浏览器下载，返回文件名 */
export async function exportTurn(tid: string, format: 'xlsx' | 'csv' = 'xlsx'): Promise<string> {
  const res = await download(`/turns/${encodeURIComponent(tid)}/export`, { format });
  const cd = res.headers.get('Content-Disposition') ?? '';
  const m = /filename\*=UTF-8''([^;]+)/.exec(cd);
  const name = m ? decodeURIComponent(m[1]) : `${tid}.${format}`;
  const url = URL.createObjectURL(await res.blob());
  const a = document.createElement('a');
  a.href = url; a.download = name; document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 0);
  return name;
}

/**
 * X1 / X2 / X2b 动作（动态模块 /api/ontology/v1/action-runs，开关 `dynamic`）：问数只发起和引用。
 * 工作台「建议动作」/ 右栏「发起」：stage 得到待确认的 action_run → 确认卡 → confirm；确认卡取消 → withdraw。
 */
const dyn = apiClient('dynamic');
export const actionsReal = () => dyn.isReal();
export type QaActionRun = import('@/pages/ops/actionReview').ActionReview & { id: string; action: string; typeVersion: string | null; objLabel: string | null; approver: string | null; system: string | null; status: string; idemKey: string; params: string | null; extRef?: string | null; reconcile?: string | null; failReason?: string | null; unknownReason?: string | null; can?: { confirm?: boolean; reasons?: Record<string, string> } };
export interface QaStageIn { action?: string; typeId?: string; source: { type: '问数'; session?: string; sessionTitle?: string }; objKind?: string | null; objId: string; objLabel: string; params?: string | null }
export const stageAction = (body: QaStageIn) => dyn.post<QaActionRun>('/action-runs:stage', body);
/** W7a：这一轮的建议动作 → 动作中心待确认（来源 问数，确认人 = 自己；不自动执行） */
export const stageTurnAction = (tid: string, body: { objId?: string; objLabel?: string; params?: string | null } = {}) =>
  api.post<QaActionRun>(`/turns/${encodeURIComponent(tid)}/action:stage`, body);
/** 刷新带 ?confirm=1&run= 的页面时取回已暂存的动作，不再重复暂存 */
export const getAction = (code: string) => dyn.get<QaActionRun & { can?: { confirm?: boolean } }>(`/action-runs/${encodeURIComponent(code)}`);
export const confirmAction = (code: string) => dyn.post<QaActionRun>(`/action-runs/${encodeURIComponent(code)}:confirm`, {});
export const withdrawAction = (code: string, reason = '确认卡取消') => dyn.post<QaActionRun>(`/action-runs/${encodeURIComponent(code)}:withdraw`, { reason });

/* ───────── R5 问数调优 · 认证问答 / 高频问题（qa-api §7 T1 T3–T8 T18 T19） ───────── */
export interface QaTuningSummary { version: string; dataAsOf: string; recertCount: number; openFeedback: number; uncertFreq: number; evalTotal: number }
/** 后端 certifier / certifiedAt 待认证时为空（exclude_none），前端表格按 '--' 显示（同 mock createCert） */
type CertDto = Omit<CertQa, 'certifier' | 'certifiedAt'> & { certifier?: string; certifiedAt?: string; planText: string };
const certIn = (c: CertDto): CertQa => ({ ...c, variants: c.variants ?? [], certifier: c.certifier ?? '--', certifiedAt: c.certifiedAt ?? '--' });
export const getTuningSummary = (options: Omit<RequestOptions, 'method' | 'body'> = {}) => api.get<QaTuningSummary>('/tuning/summary', options);
export interface QaPlanOptions { metrics: { code: string; key: string; name: string; version: string; cr?: string }[]; dims: string[]; times: string[] }
/** T2 新建认证问答的指标 / 维度 / 时间选项 */
export const getPlanOptions = (options: Omit<RequestOptions, 'method' | 'body'> = {}) => api.get<QaPlanOptions>('/tuning/plan-options', options);
export async function listCerts(q: { kw?: string; status?: string; metric?: string; page: number; size: number }, options: Omit<RequestOptions, 'method' | 'body'> = {}): Promise<PageResult<CertQa>> {
  const r = await api.get<PageResult<CertDto>>('/tuning/certs', { ...options, query: { ...options.query, ...q } });
  return { ...r, items: r.items.map(certIn) };
}
export const getCert = async (id: string, options: Omit<RequestOptions, 'method' | 'body'> = {}) => certIn(await api.get<CertDto>(`/tuning/certs/${encodeURIComponent(id)}`, options));
export const createCert = async (question: string, plan: SemPlan) => certIn(await api.post<CertDto>('/tuning/certs', { question, plan }));
export const certifyCert = async (id: string) => certIn(await api.post<CertDto>(`/tuning/certs/${encodeURIComponent(id)}:certify`, {}));
export const toggleCert = async (id: string, enabled: boolean) => certIn(await api.patch<CertDto>(`/tuning/certs/${encodeURIComponent(id)}`, { enabled }));
export const putCertVariants = async (id: string, variants: string[]) => certIn(await api.put<CertDto>(`/tuning/certs/${encodeURIComponent(id)}/variants`, { variants }));
export type QaFreq = FreqQ & { rank: number; certStatus?: string };
export const listFreq = (q: { period?: string; cert?: string; kw?: string; page: number; size: number }, options: Omit<RequestOptions, 'method' | 'body'> = {}) => api.get<PageResult<QaFreq>>('/tuning/freq', { ...options, query: { ...options.query, ...q } });
export const certifyFreq = async (id: string) => certIn(await api.post<CertDto>(`/tuning/freq/${encodeURIComponent(id)}:certify`, {}));

/* ───────── R5 问数调优 · 评测题库 / 反馈与纠错（qa-api §7 T9–T17） ───────── */
/** T9 运行记录：失败用例不随列表返回，给 failCount；passRate 0–1 */
export type QaEvalRun = Omit<EvalRun, 'fails' | 'version' | 'status'> & { status: EvalRun['status'] | '失败'; awaitingExecution?: boolean; version?: string; passRate: number; failCount: number };
export type QaEvalQ = EvalQ & { expectText: string };
/** T10 抽屉：失败用例服务端分页（12 条一页），kindStats 是全部失败的原因分布；待运行的回放给题目和提示 */
export interface QaEvalRunDetail extends QaEvalRun { fails: PageResult<FailCase>; kindStats: { kind: FailKind; n: number }[]; replayQuestions: QaEvalQ[]; replayHints: Record<string, string> }
export interface QaEvalQList extends PageResult<QaEvalQ> { groupCounts: { group: string; n: number }[] }
export const listEvalRuns = (q: { trigger?: string; page: number; size: number }, options: Omit<RequestOptions, 'method' | 'body'> = {}) => api.get<PageResult<QaEvalRun>>('/tuning/eval/runs', { ...options, query: { ...options.query, ...q } });
export const getEvalRun = (id: string, q: { page: number; size: number }, options: Omit<RequestOptions, 'method' | 'body'> = {}) => api.get<QaEvalRunDetail>(`/tuning/eval/runs/${encodeURIComponent(id)}`, { ...options, query: { ...options.query, ...q } });
/** T11 运行评测（202，后台逐题跑；group 'all' = 全部）。轮询 T9 / T10 看进度 */
export const startEvalRun = (group: string, sessionId: string) => api.post<QaEvalRun>('/tuning/eval/runs', { group, sessionId });
export const listEvalQuestions = (q: { group?: string; last?: string; kw?: string; page: number; size: number }, options: Omit<RequestOptions, 'method' | 'body'> = {}) => api.get<QaEvalQList>('/tuning/eval/questions', { ...options, query: { ...options.query, ...q } });
/** T13 / T14 反馈：列表和详情同形（行内按钮要 certBlock / coveredBy） */
export type QaFeedback = Feedback & {
  userId?: string; certBlock: string; existingCert?: string; coveredBy?: string; relatedEvalCount: number;
  existingCertQuestion?: string; existingCertStatus?: string; plan?: SemPlan; planText?: string; coveredCrStatus?: string;
};
export const listFeedbacks = (q: { kw?: string; status?: string; fixKind?: string; page: number; size: number }, options: Omit<RequestOptions, 'method' | 'body'> = {}) => api.get<PageResult<QaFeedback>>('/tuning/feedbacks', { ...options, query: { ...options.query, ...q } });
export const getFeedback = (id: string, options: Omit<RequestOptions, 'method' | 'body'> = {}) => api.get<QaFeedback>(`/tuning/feedbacks/${encodeURIComponent(id)}`, options);
const fbAct = (id: string, act: string) => `/tuning/feedbacks/${encodeURIComponent(id)}:${act}`;
/** T15：词已在变更单里 → 关联（同词待处理反馈一起）；不在 → 新建（或 join 加入）草稿变更单，target kind=knowledge；知识被别的单占用 → 409 TARGET_IN_OTHER_CR（details.crId） */
export const feedbackToSynonym = (id: string, body: { join?: string; title?: string } = {}) =>
  api.post<{ crId: string; affected: string[]; feedback: QaFeedback }>(fbAct(id, 'to-synonym'), body);
/** T16：已有同题认证问答 → mode variant（原话加为同问法）；否则新建并认证 → mode new */
export const feedbackToCert = (id: string) => api.post<{ certId: string; mode: 'new' | 'variant'; feedback: QaFeedback }>(fbAct(id, 'to-cert'), {});
export const ignoreFeedback = (id: string) => api.post<QaFeedback>(fbAct(id, 'ignore'), {});
export const reopenFeedback = (id: string) => api.post<QaFeedback>(fbAct(id, 'reopen'), {});

export const ruleEvents = (tid: string, before?: number, q = '') => api.get<{ items: { id: string; label: string; at: string }[]; nextBefore?: number | null }>(`/turns/${encodeURIComponent(tid)}/rule-events`, { query: { before, q } });
export const runRule = (tid: string, eventId?: string) => api.post<Record<string, unknown>>(`/turns/${encodeURIComponent(tid)}/rule:run`, { eventId });
