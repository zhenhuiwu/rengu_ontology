/**
 * 问数（qa）工作台接口。开关键 qa，前缀 /api/qa/v1（docs/backend/qa-api.md 第 3 节 W1–W17）。
 * 开关没切到真接口时页面照旧走 src/mock，这里的函数不会被调用。
 */
import type { QaSession, QaFavorite, QaPage, QaDemoTurn } from '@/mock/qa';
import type { CertQa, FreqQ, SemPlan, EvalRun, EvalQ, FailCase, FailKind, Feedback } from '@/mock/qa-tuning';
import { apiClient, currentApiContext, ApiError, API_BASE, type ApiErrorBody, type PageResult } from './http';

const api = apiClient('qa');
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
  const url = `${API_BASE}/${api.prefix}/v1${path}${qs.size ? `?${qs}` : ''}`;
  let res: Response;
  try {
    res = await fetch(url, { headers });
  } catch (e) {
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
export interface QaChainAnswer { card: 'table'; text: string; dataAsOf?: string; basis?: Record<string, string>; table: QaChainTable }
/** applied：越权轮次已提交待审批的申请（W16 之后 W3 返回）；vote：这一轮的赞踩（W12）；主卡 answer.caliber：W9 切过的口径 */
/** 链路反问（qa-reuse a35d3b6）：kind=answer、card='clarify'，不执行 SQL；选项点了以后前端再问一轮 */
export interface QaClarifyAnswer { card: 'clarify'; text: string; dataAsOf?: string; clarify: { term: string; chosen: string | null; options: QaOption[] }; suggest?: { followUps: string[] } }
/** 拒答 code（链路 refuse）：NO_PERMISSION 是权限拦截（带申请入口），其余是「无法回答」 */
export type QaRefuseCode = 'NOT_SELECT' | 'OUT_OF_SCOPE' | 'NO_PERMISSION' | 'NO_DATA';
export type QaRealTurn =
  | (QaDemoTurn & { applied?: boolean; vote?: '' | 'up' | 'down'; code?: QaRefuseCode })
  | { id: string; kind: 'answer'; time?: string; question: string; answer: QaChainAnswer }
  | { id: string; kind: 'answer'; time?: string; question: string; answer: QaClarifyAnswer }
  | { id: string; kind: 'empty'; time?: string; question: string; lead: string; bold: string; tail: string; tag: string; retry?: undefined };
export interface QaSessionDetail extends QaSession { scope: string | null; caliber: string | null; ownerId: string; turns: QaRealTurn[] }
export interface QaOption { value: string; label: string }
export interface QaSessionOptions { scopes: QaOption[]; calibers: QaOption[] }
export interface QaAskOut { turnId: string; streamUrl: string; turn: QaRealTurn }

export const listSessions = (q: { q?: string; range?: string; page: number; size: number }) =>
  api.get<PageResult<QaSession>>('/sessions', { query: { ...q, range: q.range === 'all' ? undefined : q.range } });
export const getSession = (sid: string) => api.get<QaSessionDetail>(`/sessions/${encodeURIComponent(sid)}`);
export const createSession = (body: { scope: string; caliber: string }) => api.post<QaSessionDetail>('/sessions', body);
export const patchSession = (sid: string, body: { title?: string; scope?: string; caliber?: string }) =>
  api.patch<QaSessionDetail>(`/sessions/${encodeURIComponent(sid)}`, body);
export const deleteSession = (sid: string) => api.delete<void>(`/sessions/${encodeURIComponent(sid)}`);
export const askTurn = (sid: string, question: string) =>
  api.post<QaAskOut>(`/sessions/${encodeURIComponent(sid)}/turns`, { question, refs: [] });
export const listFavorites = (q: { q?: string; page: number; size: number }) => api.get<PageResult<QaFavorite>>('/favorites', { query: q });
export const deleteFavorite = (id: string) => api.delete<void>(`/favorites/${encodeURIComponent(id)}`);
/** P1 简版（左栏贴底、「我的页面 · 全部」抽屉） */
export const listPagesBrief = (q: { kw?: string; page: number; size: number }) =>
  api.get<PageResult<QaPage>>('/pages', { query: { ...q, view: 'brief' } });
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
export interface QaActionRun { id: string; action: string; typeVersion: string | null; objLabel: string | null; approver: string | null; system: string | null; status: string; idemKey: string; params: string | null }
export interface QaStageIn { action: string; source: { type: '问数'; session?: string; sessionTitle?: string }; objKind: string; objId: string; objLabel: string; params?: string }
export const stageAction = (body: QaStageIn) => dyn.post<QaActionRun>('/action-runs:stage', body);
/** 刷新带 ?confirm=1&run= 的页面时取回已暂存的动作，不再重复暂存 */
export const getAction = (code: string) => dyn.get<QaActionRun & { can?: { confirm?: boolean } }>(`/action-runs/${encodeURIComponent(code)}`);
export const confirmAction = (code: string) => dyn.post<QaActionRun>(`/action-runs/${encodeURIComponent(code)}:confirm`, {});
export const withdrawAction = (code: string, reason = '确认卡取消') => dyn.post<QaActionRun>(`/action-runs/${encodeURIComponent(code)}:withdraw`, { reason });

/* ───────── R5 问数调优 · 认证问答 / 高频问题（qa-api §7 T1 T3–T8 T18 T19） ───────── */
export interface QaTuningSummary { version: string; dataAsOf: string; recertCount: number; openFeedback: number; uncertFreq: number; evalTotal: number }
/** 后端 certifier / certifiedAt 待认证时为空（exclude_none），前端表格按 '--' 显示（同 mock createCert） */
type CertDto = Omit<CertQa, 'certifier' | 'certifiedAt'> & { certifier?: string; certifiedAt?: string; planText: string };
const certIn = (c: CertDto): CertQa => ({ ...c, variants: c.variants ?? [], certifier: c.certifier ?? '--', certifiedAt: c.certifiedAt ?? '--' });
export const getTuningSummary = () => api.get<QaTuningSummary>('/tuning/summary');
export interface QaPlanOptions { metrics: { key: string; name: string; version: string; cr?: string }[]; dims: string[]; times: string[] }
/** T2 新建认证问答的指标 / 维度 / 时间选项 */
export const getPlanOptions = () => api.get<QaPlanOptions>('/tuning/plan-options');
export async function listCerts(q: { kw?: string; status?: string; metric?: string; page: number; size: number }): Promise<PageResult<CertQa>> {
  const r = await api.get<PageResult<CertDto>>('/tuning/certs', { query: q });
  return { ...r, items: r.items.map(certIn) };
}
export const getCert = async (id: string) => certIn(await api.get<CertDto>(`/tuning/certs/${encodeURIComponent(id)}`));
export const createCert = async (question: string, plan: SemPlan) => certIn(await api.post<CertDto>('/tuning/certs', { question, plan }));
export const certifyCert = async (id: string) => certIn(await api.post<CertDto>(`/tuning/certs/${encodeURIComponent(id)}:certify`, {}));
export const toggleCert = async (id: string, enabled: boolean) => certIn(await api.patch<CertDto>(`/tuning/certs/${encodeURIComponent(id)}`, { enabled }));
export const putCertVariants = async (id: string, variants: string[]) => certIn(await api.put<CertDto>(`/tuning/certs/${encodeURIComponent(id)}/variants`, { variants }));
export type QaFreq = FreqQ & { rank: number; certStatus?: string };
export const listFreq = (q: { period?: string; cert?: string; kw?: string; page: number; size: number }) => api.get<PageResult<QaFreq>>('/tuning/freq', { query: q });
export const certifyFreq = async (id: string) => certIn(await api.post<CertDto>(`/tuning/freq/${encodeURIComponent(id)}:certify`, {}));

/* ───────── R5 问数调优 · 评测题库 / 反馈与纠错（qa-api §7 T9–T17） ───────── */
/** T9 运行记录：失败用例不随列表返回，给 failCount；passRate 0–1 */
export type QaEvalRun = Omit<EvalRun, 'fails' | 'version'> & { version?: string; passRate: number; failCount: number };
export type QaEvalQ = EvalQ & { expectText: string };
/** T10 抽屉：失败用例服务端分页（12 条一页），kindStats 是全部失败的原因分布；待运行的回放给题目和提示 */
export interface QaEvalRunDetail extends QaEvalRun { fails: PageResult<FailCase>; kindStats: { kind: FailKind; n: number }[]; replayQuestions: QaEvalQ[]; replayHints: Record<string, string> }
export interface QaEvalQList extends PageResult<QaEvalQ> { groupCounts: { group: string; n: number }[] }
export const listEvalRuns = (q: { trigger?: string; page: number; size: number }) => api.get<PageResult<QaEvalRun>>('/tuning/eval/runs', { query: q });
export const getEvalRun = (id: string, q: { page: number; size: number }) => api.get<QaEvalRunDetail>(`/tuning/eval/runs/${encodeURIComponent(id)}`, { query: q });
/** T11 运行评测（202，后台逐题跑；group 'all' = 全部）。轮询 T9 / T10 看进度 */
export const startEvalRun = (group: string) => api.post<QaEvalRun>('/tuning/eval/runs', { group });
export const listEvalQuestions = (q: { group?: string; last?: string; kw?: string; page: number; size: number }) => api.get<QaEvalQList>('/tuning/eval/questions', { query: q });
/** T13 / T14 反馈：列表和详情同形（行内按钮要 certBlock / coveredBy） */
export type QaFeedback = Feedback & {
  userId?: string; certBlock: string; existingCert?: string; coveredBy?: string; relatedEvalCount: number;
  existingCertQuestion?: string; existingCertStatus?: string; plan?: SemPlan; planText?: string; coveredCrStatus?: string;
};
export const listFeedbacks = (q: { kw?: string; status?: string; fixKind?: string; page: number; size: number }) => api.get<PageResult<QaFeedback>>('/tuning/feedbacks', { query: q });
export const getFeedback = (id: string) => api.get<QaFeedback>(`/tuning/feedbacks/${encodeURIComponent(id)}`);
const fbAct = (id: string, act: string) => `/tuning/feedbacks/${encodeURIComponent(id)}:${act}`;
/** T15：词已在变更单里 → 关联（同词待处理反馈一起）；不在 → 新建（或 join 加入）草稿变更单，target kind=knowledge；知识被别的单占用 → 409 TARGET_IN_OTHER_CR（details.crId） */
export const feedbackToSynonym = (id: string, body: { join?: string; title?: string } = {}) =>
  api.post<{ crId: string; affected: string[]; feedback: QaFeedback }>(fbAct(id, 'to-synonym'), body);
/** T16：已有同题认证问答 → mode variant（原话加为同问法）；否则新建并认证 → mode new */
export const feedbackToCert = (id: string) => api.post<{ certId: string; mode: 'new' | 'variant'; feedback: QaFeedback }>(fbAct(id, 'to-cert'), {});
export const ignoreFeedback = (id: string) => api.post<QaFeedback>(fbAct(id, 'ignore'), {});
export const reopenFeedback = (id: string) => api.post<QaFeedback>(fbAct(id, 'reopen'), {});
