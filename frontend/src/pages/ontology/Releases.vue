<script setup lang="ts">
/**
 * 变更与发布：左侧变更单列表（简洁分页），右侧变更单详情。
 * 页签：改动清单 / 发布校验 / 影响分析 / 评审记录。整版发布：已通过的变更单合并校验后一起发布为下一个版本。
 * 选中变更单写进 ?cr=，页签写进 ?tab=，状态筛选写进 ?st=。查看历史版本时整页只读。
 * 权限：只有该单的审核人能通过 / 驳回，提交人不能审自己的单子；所有状态流转都走确认卡并写进评审记录。
 */
import InfoTip from '@/components/InfoTip.vue';
import { computed, reactive, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { message } from 'ant-design-vue';
import AppPagination from '@/components/AppPagination.vue';
import HoverCard from '@/components/HoverCard.vue';
import StatusTimeline from '@/components/StatusTimeline.vue';
import { useReadonly } from '@/composables/useReadonly';
import { useConfirmStore } from '@/stores/confirm';
import { useAppStore } from '@/stores/app';
import {
  CHANGE_REQUESTS, CURRENT_USER, ONTOLOGY_ADMINS, checkCounts, checkSummary, crDetail, editRoute, impactSummary, mergeCheck, reviewLog,
  type ChangeRequest, type CRDetail, type CheckResult, type CRStatus, type ReviewEvent,
} from '@/mock/changes';
import { loadMyProfile } from '@/api/profile';
import { currentApiContext, isApiError } from '@/api/http';
import { fetchCurrentRelease, loadReleases, RELEASES } from '@/api/releases';
import { useStaticPage } from './parts/useStaticPage';
import { invalidateActionSnapshot } from '@/api/action';
import { crAction, deleteCR, fetchCR, fetchReviewerOptions, patchCR, fetchWorkflowRecords, fetchStaticPage, fetchCurrentAfterWrite, isStaticReal, mergeCheckApi, publishDraft, publishRelease, publishFormalRelease, rollbackRelease, validateCR, type CRAction } from '@/api/static';

const route = useRoute();
const router = useRouter();
const confirm = useConfirmStore();
const app = useAppStore();
// 当前 / 下一版本号取 app store（mock 模式是 mock 值，versioning 走真接口时是 /releases/current 的 current / next）
const CURRENT_VERSION = computed(() => app.ontologyVersion);
const NEXT_VERSION = computed(() => app.ontologyNextVersion);
const { readonly, reason } = useReadonly();
const realMode = isStaticReal();
const realMe = ref(''), realMeId = ref('');
const releaseCapabilities = ref({ publish: false, rollback: false });
let meSeq = 0;
if (realMode) watch(() => JSON.stringify(currentApiContext()), async (key) => {
  const my = ++meSeq; realMe.value = ''; realMeId.value = ''; releaseCapabilities.value = { publish: false, rollback: false };
  try { const user = await loadMyProfile(); if (my === meSeq && key === JSON.stringify(currentApiContext())) { realMe.value = user?.name ?? ''; realMeId.value = user?.id ?? ''; releaseCapabilities.value = { publish: user?.capabilities.publish === true, rollback: user?.capabilities.rollback === true }; } }
  catch { if (my === meSeq && key === JSON.stringify(currentApiContext())) { realMe.value = ''; realMeId.value = ''; releaseCapabilities.value = { publish: false, rollback: false }; } }
}, { immediate: true });
const me = computed(() => realMode ? realMe.value : app.user?.name || CURRENT_USER);
const isAdmin = computed(() => realMode ? releaseCapabilities.value.publish : ONTOLOGY_ADMINS.includes(me.value));
const canRollback = computed(() => !realMode || releaseCapabilities.value.rollback);

type CRView = ChangeRequest & { ownerId?: string; reviewerId?: string };
const CRS = reactive<CRView[]>(realMode ? [] : CHANGE_REQUESTS.map((c) => ({ ...c })));
const emptyCR: CRView = { id: '', title: '', owner: '', reviewer: '', status: '草稿', level: '一般', changes: 0, updatedAt: '', reason: '' };
let loadSeq = 0;
const extraLog = reactive<Record<string, ReviewEvent[]>>({});
type AnalyzedDetail = CRDetail & { analysisScope?: string; trialUnavailable?: string };
const realDetails = reactive<Record<string, AnalyzedDetail>>({});
const loadedCRKey = ref('');
const visibleCRS = computed(() => !realMode || loadedCRKey.value === JSON.stringify(currentApiContext()) ? CRS : []);
const emptyDetail = (): CRDetail => ({ diffs: [], checks: [], impact: { direct: [], defs: [], usage: [] }, touches: [], times: { created: '' } });
// 完整摘要用于整版发布批次；列表按页查询，详情和事件只加载当前单。
const crFallback = ref(false);
const pageRows = ref<CRView[]>([]), pageTotal = ref(0);
const pageLoading = ref(false), detailLoading = ref(false), catalogLoading = ref(false);
let pageSeq = 0, detailSeq = 0;
let loadedPageQuery = '';
const pageKey = () => JSON.stringify([currentApiContext(), lpage.value, st.value, kw.value]);
const fixedOptions = () => {
  const ctx = currentApiContext();
  return { readonlyParams: false, headers: { 'X-Demo-As': ctx.demoAs ?? '', 'X-Ontology-Id': ctx.ontologyId ?? '' }, query: { ver: ctx.ver, previewAs: ctx.previewAs } };
};
function keepDetail(head: Awaited<ReturnType<typeof fetchCR>>) {
  const d = head.detail;
  realDetails[head.id] = { ...emptyDetail(), ...d, diffs: d.diffs ?? [], checks: d.checks ?? [], impact: { direct: d.impact?.direct ?? [], defs: d.impact?.defs ?? [], usage: d.impact?.usage ?? [] }, touches: d.touches ?? [], times: { created: '', ...d.times } };
  extraLog[head.id] = head.events;
}
async function loadPage(focusId?: string) {
  const my = ++pageSeq, key = pageKey(), ctx = JSON.stringify(currentApiContext());
  pageLoading.value = true;
  try {
    const result = await fetchStaticPage<CRView>('/change-requests', { page: lpage.value, size: LPAGE, status: st.value === '全部' ? undefined : st.value, kw: kw.value || undefined, focusId });
    if (my !== pageSeq || key !== pageKey() || ctx !== JSON.stringify(currentApiContext())) return false;
    pageRows.value = result.items; pageTotal.value = result.total;
    loadedPageQuery = JSON.stringify([currentApiContext(), result.page, st.value, kw.value]);
    lpage.value = result.page;
    return true;
  } catch (e) {
    if (my === pageSeq && key === pageKey()) { pageRows.value = []; crFallback.value = true; message.error(`变更单列表加载失败：${e instanceof Error ? e.message : String(e)}`); }
    return false;
  } finally { if (my === pageSeq) pageLoading.value = false; }
}
async function loadCRs() {
  const my = ++loadSeq, key = JSON.stringify(currentApiContext());
  catalogLoading.value = true;
  try {
    ++pageSeq; ++detailSeq;
    CRS.splice(0); loadedCRKey.value = '';
    pageRows.value = []; pageTotal.value = 0; loadedPageQuery = '';
    for (const id of Object.keys(realDetails)) delete realDetails[id];
    for (const id of Object.keys(extraLog)) delete extraLog[id];
    crFallback.value = true;
    let rows;
    try { rows = await fetchWorkflowRecords(fixedOptions()); } catch (e) { if (my !== loadSeq || key !== JSON.stringify(currentApiContext())) return false; crFallback.value = true; message.error(`变更单载入失败：${e instanceof Error ? e.message : String(e)}`); return false; }
    if (my !== loadSeq || key !== JSON.stringify(currentApiContext())) return false;
    const requested = String(route.query.cr || 'CR-0010');
    const id = rows.find((row) => row.id === requested)?.id ?? rows[0]?.id;
    detailLoading.value = !!id;
    try {
      const options = fixedOptions();
      const [head, ok] = await Promise.all([id ? fetchCR(id, { ...options, query: { ...options.query, includeDiffs: false } }) : Promise.resolve(null), loadPage(id)]);
      if (my !== loadSeq || key !== JSON.stringify(currentApiContext())) return false;
      if (!ok) return false;
      if (head) keepDetail(head);
    } catch (e) {
      if (my === loadSeq && key === JSON.stringify(currentApiContext())) message.error(`变更单详情加载失败：${e instanceof Error ? e.message : String(e)}`);
      return false;
    } finally { if (my === loadSeq) detailLoading.value = false; }
    CRS.splice(0, CRS.length, ...rows);
    loadedCRKey.value = key;
    crFallback.value = false;
    return true;
  } finally { if (my === loadSeq) catalogLoading.value = false; }
}
if (realMode) watch(() => JSON.stringify(currentApiContext()), () => { void loadCRs(); }, { immediate: true });
const rolledBack = ref(false);
const skippedSections = ref<string[]>([]);

const STATUSES = ['全部', '草稿', '审核中', '已通过', '已发布', '已驳回'];
const statusLabel = (status: string) => ({ 审核中: '历史待处理', 已通过: '历史已通过', 已驳回: '历史已驳回' } as Record<string, string>)[status] || status;
const st = computed({
  get: () => (route.query.st as string) || '全部',
  set: (v) => router.replace({ query: { ...route.query, st: v === '全部' ? undefined : v } }),
});
const kw = ref('');
const lpage = ref(1);
const LPAGE = 17;
watch([st, kw], () => { lpage.value = 1; });
const filtered = computed(() => visibleCRS.value.filter((c) => (st.value === '全部' || c.status === st.value) && (!kw.value || c.title.includes(kw.value) || c.id.includes(kw.value.toUpperCase()))));
const list = computed(() => realMode ? loadedCRKey.value === JSON.stringify(currentApiContext()) ? pageRows.value : [] : filtered.value.slice((lpage.value - 1) * LPAGE, lpage.value * LPAGE));
const totalRows = computed(() => realMode ? pageTotal.value : filtered.value.length);
if (realMode) watch([() => JSON.stringify(currentApiContext()), lpage, st, kw], ([key], [previous]) => {
  if (key !== previous || loadedPageQuery === pageKey()) return;
  if (loadedCRKey.value === key) void loadPage();
  else if (catalogLoading.value) void loadCRs();
});

const cur = computed(() => visibleCRS.value.find((c) => c.id === ((route.query.cr as string) || 'CR-0010')) ?? visibleCRS.value[0] ?? emptyCR);
const pick = (id: string) => router.replace({ query: { ...route.query, cr: id } });
// 选中的单子不在当前页时，翻到它所在的页
watch(() => cur.value.id, (id) => { if (realMode) return; const i = filtered.value.findIndex((c) => c.id === id); if (i >= 0) lpage.value = Math.floor(i / LPAGE) + 1; }, { immediate: true });
if (realMode) watch(() => route.query.cr, async () => {
  if (loadedCRKey.value !== JSON.stringify(currentApiContext()) || !cur.value.id) return;
  const my = ++detailSeq, id = cur.value.id, key = JSON.stringify(currentApiContext());
  detailLoading.value = true; crFallback.value = true;
  try {
    const options = fixedOptions();
    const head = await fetchCR(id, { ...options, query: { ...options.query, includeDiffs: false } });
    if (my !== detailSeq || id !== cur.value.id || key !== JSON.stringify(currentApiContext())) return;
    keepDetail(head); crFallback.value = false;
  } catch (e) {
    if (my === detailSeq && key === JSON.stringify(currentApiContext())) message.error(`变更单详情加载失败：${e instanceof Error ? e.message : String(e)}`);
  } finally { if (my === detailSeq) detailLoading.value = false; }
});
const tab = computed({
  get: () => (route.query.tab as string) || 'diff',
  set: (v) => router.replace({ query: { ...route.query, tab: v } }),
});
const detail = computed(() => realMode ? loadedCRKey.value === JSON.stringify(currentApiContext()) ? realDetails[cur.value.id] ?? emptyDetail() : emptyDetail() : crDetail(cur.value.id));
const rechecking = ref(false);
async function recheck() {
  if (!cur.value.id || crFallback.value || readonly.value || rechecking.value) return;
  rechecking.value = true;
  if (realMode) {
    const id = cur.value.id;
    const key = JSON.stringify(currentApiContext());
    try {
      const result = await validateCR(id);
      if (key !== JSON.stringify(currentApiContext())) return;
      if (await loadCRs()) message.success(`${id} 校验完成：${result.summary || '暂无结果'}`);
    } catch (e) { message.error(`校验失败：${e instanceof Error ? e.message : String(e)}`); }
    finally { rechecking.value = false; }
    return;
  }
  setTimeout(() => { rechecking.value = false; message.success(`${cur.value.id} 校验完成：${checkSummary(detail.value.checks) || '全部通过'}`); }, 800);
}
const log = computed(() => realMode ? extraLog[cur.value.id] ?? [] : [...(extraLog[cur.value.id] ?? []), ...reviewLog(cur.value)]);

const statusClass = (s: CRStatus) => (s === '审核中' ? 'c-warning' : 'c-3');
const tagColor = (s: CRStatus) => ({ 草稿: 'default', 审核中: 'warning', 已通过: 'default', 已发布: 'default', 已驳回: 'default' })[s];
const resultClass = (r: CheckResult) => (r === '阻断' ? 'c-error' : r === '警告' ? 'c-warning' : r === '通过' ? 'c-2' : 'c-3');

const counts = computed(() => checkCounts(detail.value.checks));
const blocked = computed(() => counts.value['阻断'] > 0);
const impactSum = computed(() => impactSummary(detail.value));

// —— 当前用户能做什么 ——
const isOwner = computed(() => realMode && cur.value.ownerId ? cur.value.ownerId === realMeId.value : !!me.value && cur.value.owner === me.value);
const directPublish = computed(() => realMode && app.workspace?.directPublishEnabled === true);
const isReviewer = computed(() => !isOwner.value && (realMode && cur.value.reviewerId ? cur.value.reviewerId === realMeId.value : !!me.value && cur.value.reviewer === me.value));
const canManage = computed(() => isOwner.value || isReviewer.value);
const canSetReviewer = computed(() => realMode && !directPublish.value && !crFallback.value && !readonly.value && !!cur.value.id && cur.value.status === '草稿' && isOwner.value);
const reviewerOpen = ref(false), reviewerLoading = ref(false), reviewerSaving = ref(false), reviewerError = ref('');
const reviewerOptions = ref<{ id: string; name: string }[]>([]), reviewerId = ref<string>(), reviewerLock = ref<number>(), storedReviewer = ref('');
let reviewerSeq = 0;
const reviewerKey = () => JSON.stringify([currentApiContext(), cur.value.id]);
watch(reviewerKey, () => { reviewerSeq++; reviewerOpen.value = false; reviewerId.value = undefined; reviewerOptions.value = []; reviewerLock.value = undefined; storedReviewer.value = ''; });
async function loadReviewers() {
  if (!canSetReviewer.value) return;
  const my = ++reviewerSeq, key = reviewerKey(), id = cur.value.id, selection = reviewerId.value;
  const ctx = currentApiContext();
  const options = { readonlyParams: false, query: { ver: ctx.ver, previewAs: ctx.previewAs }, headers: { 'X-Demo-As': ctx.demoAs ?? '', 'X-Ontology-Id': ctx.ontologyId ?? '' } };
  reviewerLoading.value = true; reviewerError.value = ''; reviewerOptions.value = []; reviewerLock.value = undefined;
  try {
    const head = await fetchCR(id, options); if (my !== reviewerSeq || key !== reviewerKey()) return;
    const choices = await fetchReviewerOptions(id, options); if (my !== reviewerSeq || key !== reviewerKey()) return;
    reviewerOptions.value = choices; reviewerLock.value = head.lockVersion; storedReviewer.value = head.reviewerId ? `${head.reviewer}（${head.reviewerId}）` : head.reviewer;
    reviewerId.value = choices.some((u) => u.id === selection) ? selection : choices.find((u) => head.reviewerId ? u.id === head.reviewerId : u.id === head.reviewer || u.name === head.reviewer)?.id;
  } catch (e) { if (my === reviewerSeq && key === reviewerKey()) reviewerError.value = e instanceof Error ? e.message : String(e); }
  finally { if (my === reviewerSeq && key === reviewerKey()) reviewerLoading.value = false; }
}
function openReviewer() { if (!canSetReviewer.value) return; reviewerOpen.value = true; reviewerSaving.value = false; reviewerId.value = undefined; storedReviewer.value = cur.value.reviewer; void loadReviewers(); }
const canSaveReviewer = computed(() => reviewerOpen.value && canSetReviewer.value && !reviewerLoading.value && !reviewerSaving.value && !reviewerError.value && reviewerLock.value !== undefined && reviewerOptions.value.some((u) => u.id === reviewerId.value));
async function saveReviewer() {
  if (!canSaveReviewer.value) return;
  const my = reviewerSeq, key = reviewerKey(), id = cur.value.id, ctx = currentApiContext();
  reviewerSaving.value = true;
  try {
    await patchCR(id, { reviewer: reviewerId.value, lockVersion: reviewerLock.value }, { readonlyParams: false, query: { ver: ctx.ver, previewAs: ctx.previewAs }, headers: { 'X-Demo-As': ctx.demoAs ?? '', 'X-Ontology-Id': ctx.ontologyId ?? '' } });
    if (my !== reviewerSeq || key !== reviewerKey()) return;
    reviewerOpen.value = false; if (await loadCRs()) message.success('审核人已保存');
  } catch (e) { if (my === reviewerSeq && key === reviewerKey()) reviewerError.value = `${e instanceof Error ? e.message : String(e)}；选择已保留，可重新加载后确认`; }
  finally { if (my === reviewerSeq && key === reviewerKey()) reviewerSaving.value = false; }
}
const editTo = computed(() => {
  const target = detail.value.edit as { kind?: string; id?: string } | undefined;
  return realMode && target?.kind === 'quality'
    ? { path: '/build/data/quality', query: { check: target.id, form: 'edit' } }
    : editRoute(detail.value.edit);
});
const latestRelease = computed(() => visibleCRS.value.find((c) => c.status === '已发布')?.release);
const isLatest = computed(() => !rolledBack.value && cur.value.release === latestRelease.value && cur.value.release === CURRENT_VERSION.value);
// 首个发布版本没有上一版可恢复（后台 409 ROLLBACK_FIRST_RELEASE）：按 /releases/current 的 parentVer 判断，隐藏回滚按钮
const firstRelease = ref(false);
watch(CURRENT_VERSION, async (v) => {
  firstRelease.value = false;
  if (!realMode || !v) return;
  const key = JSON.stringify(currentApiContext());
  try {
    const c = await fetchCurrentRelease();
    if (key === JSON.stringify(currentApiContext()) && v === CURRENT_VERSION.value) firstRelease.value = c.current === v && !!c.release && !c.release.parentVer;
  } catch { firstRelease.value = false; }
}, { immediate: true });
const isFirstRelease = computed(() => firstRelease.value && cur.value.release === CURRENT_VERSION.value);
/** 详情区有没有蓝色主按钮：有就把页头“发布”降为普通按钮，同屏只留一个主按钮 */
const detailPrimary = computed(() => !readonly.value && cur.value.status !== '已发布');

// —— 改动清单（简洁分页）——
const dpage = ref(1);
const DPAGE = 10;
watch(() => cur.value.id, () => { dpage.value = 1; });
const diffPage = useStaticPage<CRDetail['diffs'][number]>(() => cur.value.id ? `/change-requests/${encodeURIComponent(cur.value.id)}/diffs` : '', () => ({}), dpage, DPAGE, () => tab.value === 'diff' && !crFallback.value);
const drows = computed(() => realMode ? diffPage.items.value : detail.value.diffs.slice((dpage.value - 1) * DPAGE, dpage.value * DPAGE));
watch(() => realDetails[cur.value.id], () => { if (realMode) void diffPage.reload(); });
const diffCols = [
  { title: '类型', dataIndex: 'type', width: 96 },
  { title: '对象', dataIndex: 'target', width: 200 },
  { title: '内容', dataIndex: 'field', width: 220 },
  { title: '改动', key: 'kind', width: 80 },
  { title: '原值', key: 'before' },
  { title: '新值', key: 'after' },
];
const checkCols = [
  { title: '编号', dataIndex: 'code', width: 72 },
  { title: '校验项', dataIndex: 'name', width: 220 },
  { title: '结果', key: 'result', width: 88 },
  { title: '说明', dataIndex: 'note' },
];
const trialCols = [
  { title: '月份', dataIndex: 'period' },
  { title: '旧口径', dataIndex: 'before', align: 'right' as const },
  { title: '新口径', dataIndex: 'after', align: 'right' as const },
  { title: '差异', dataIndex: 'delta', align: 'right' as const },
];

// —— 状态流转：全部走确认卡，写进评审记录，记实际操作人 ——
const pad = (n: number) => String(n).padStart(2, '0');
const now = () => { const d = new Date(); return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`; };
const target = () => `${cur.value.id} ${cur.value.title}`;
/** real 模式的状态按钮：POST 后按库重新载入；失败提示后页面不变 */
async function moveReal(action: CRAction, done: string, rejectReason?: string) {
  const id = cur.value.id, key = JSON.stringify(currentApiContext());
  try { await crAction(id, action, rejectReason); } catch (e) { if (key === JSON.stringify(currentApiContext())) apiErr(e); return; }
  if (key !== JSON.stringify(currentApiContext())) return;
  await loadCRs();
  message.success(`${id} ${done}`);
}
function move(to: CRStatus, text: string, desc?: string, status?: string) {
  const c = cur.value;
  c.status = to;
  c.updatedAt = now();
  (extraLog[c.id] ??= []).unshift({ time: now(), text: `${me.value} ${text}`, desc, status });
}
async function submit() {
  const ok = await confirm.ask({ title: '提交审核', target: target(), okText: '提交',
    fields: [{ label: '审核人', value: cur.value.reviewer }, { label: '改动', value: `${cur.value.changes} 处` }, { label: '发布校验', value: checkSummary(detail.value.checks) || '暂无' }],
    impact: '提交后草稿锁定；审核人驳回或你撤回提交后才能继续修改。' });
  if (ok && isStaticReal()) await moveReal('submit', '已提交审核');
  else if (ok) { move('审核中', '提交审核', `审核人 ${cur.value.reviewer}`); message.success(`${cur.value.id} 已提交审核`); }
}
function edit() {
  if (!editTo.value) return;
  const back = router.resolve({ path: route.path, query: { cr: cur.value.id, tab: tab.value } }).fullPath;
  const to = router.resolve(editTo.value);
  router.push({ path: to.path, query: { ...to.query, back } });
}
async function removeDraft() {
  const ok = await confirm.ask({ title: '删除草稿', target: target(), okText: '删除', danger: true,
    fields: [{ label: '改动', value: `${cur.value.changes} 处` }],
    impact: '删除后草稿里的改动全部丢弃，不能恢复；已发布版本不受影响。' });
  if (!ok) return;
  const id = cur.value.id;
  if (realMode) {
    try { await deleteCR(id); } catch (e) { apiErr(e); return; }
    await loadCRs();
    message.success(`${id} 已删除`);
    pick(CRS.find((c) => c.status === '草稿')?.id ?? CRS[0].id);
    return;
  }
  CRS.splice(CRS.findIndex((c) => c.id === id), 1);
  message.success(`${id} 已删除`);
  pick(CRS.find((c) => c.status === '草稿')?.id ?? CRS[0].id);
}
async function recall() {
  const ok = await confirm.ask({ title: '撤回提交', target: target(), okText: '撤回',
    impact: `撤回后回到草稿，${cur.value.reviewer} 不再看到这张审核。` });
  if (ok && isStaticReal()) await moveReal('withdraw', '已撤回提交');
  else if (ok) move('草稿', '撤回提交');
}
async function approve() {
  const ok = await confirm.ask({ title: '审核通过', target: target(), okText: '通过',
    fields: [{ label: '提交人', value: cur.value.owner }, { label: '发布校验', value: checkSummary(detail.value.checks) }, { label: '目标版本', value: NEXT_VERSION.value }],
    impact: '通过后进入待发布，和其他已通过的变更单一起整版发布。' });
  if (ok && isStaticReal()) await moveReal('approve', '已审核通过');
  else if (ok) move('已通过', '审核通过', `等待整版发布 ${NEXT_VERSION.value}`);
}
async function reject() {
  const ok = await confirm.ask({ title: '驳回', target: target(), okText: '驳回', danger: true,
    fields: [{ label: '提交人', value: cur.value.owner }],
    picker: { label: '驳回原因', required: true, options: ['口径与财务不一致', '影响范围没写清楚', '迁移方式不完整', '与其他变更单冲突', '权限或可见范围未定'].map((v) => ({ value: v, label: v })) } });
  if (ok && isStaticReal()) await moveReal('reject', '已驳回', confirm.pickValue);
  else if (ok) { cur.value.rejectReason = confirm.pickValue; move('已驳回', '驳回', confirm.pickValue, 'error'); }
}
async function unapprove() {
  const ok = await confirm.ask({ title: '撤回', target: target(), okText: '撤回', danger: true,
    impact: `撤回后回到草稿，不会进入 ${NEXT_VERSION.value}；修改后需要重新提交审核。` });
  if (ok && isStaticReal()) await moveReal('unapprove', '已撤回审核结果');
  else if (ok) move('草稿', '撤回审核结果', `不进入 ${NEXT_VERSION.value}`);
}
async function reopen() {
  const ok = await confirm.ask({ title: '改回草稿', target: target(), okText: '改回草稿',
    fields: [{ label: '驳回原因', value: cur.value.rejectReason ?? '暂无' }],
    impact: '改回草稿后可以按驳回意见修改，再重新提交审核。' });
  if (ok && isStaticReal()) await moveReal('reopen', '已改回草稿');
  else if (ok) move('草稿', '改回草稿', '按驳回意见修改后重新提交');
}
const approved = computed(() => visibleCRS.value.filter((c) => c.status === '已通过'));
/** real 模式（VITE_API_REAL_MODULES 含 static）：合并校验、发布、回滚走接口；失败提示后不改页面 */
const apiErr = (e: unknown) => message.error(e instanceof Error ? e.message : String(e));
const formalPublishing = ref(false);
const formalIssues = ref<{ section: string; id: string; message: string }[]>([]);
async function publishFormal() {
  if (formalPublishing.value || readonly.value || !isAdmin.value) return;
  const key = JSON.stringify(currentApiContext());
  formalPublishing.value = true; formalIssues.value = [];
  try {
    const current = await fetchCurrentAfterWrite();
    if (key !== JSON.stringify(currentApiContext())) return;
    const ok = await confirm.ask({ title: '正式发布本体', target: '当前工作状态', okText: '正式发布', impact: '服务端将完整校验当前工作状态，校验通过后保存正式发布快照。' });
    if (!ok || key !== JSON.stringify(currentApiContext()) || readonly.value || !isAdmin.value) return;
    const result = await publishFormalRelease(current.lockVersion);
    if (key !== JSON.stringify(currentApiContext())) return;
    await loadReleases(); await syncVersion();
    message.success(`已正式发布 ${result.ver}`);
  } catch (e) {
    if (key !== JSON.stringify(currentApiContext())) return;
    if (isApiError(e) && e.code === 'FORMAL_RELEASE_BLOCKED') {
      const issues = (e.details as { issues?: unknown } | null)?.issues;
      if (Array.isArray(issues)) formalIssues.value = issues;
    }
    apiErr(e);
  } finally { formalPublishing.value = false; }
}
/** 发布 / 回滚成功后按 GET /releases/current 刷新 app store 的当前 / 下一版本号（顶栏、本页按钮跟着变）；刷新失败只提示，不影响已成功的写操作 */
async function syncVersion() {
  try {
    const r = await fetchCurrentAfterWrite();
    if (r.current) app.ontologyVersion = r.current;
    if (r.release?.publishedAt) app.ontologyPublishedAt = r.release.publishedAt;
    app.ontologyNextVersion = r.next;
  } catch (e) { message.warning(`版本号刷新失败：${e instanceof Error ? e.message : String(e)}`); }
}
const directPublishing = ref(false);
async function publishCurrentDraft() {
  const id = cur.value.id, key = JSON.stringify(currentApiContext());
  if (directPublishing.value || !directPublish.value || readonly.value || crFallback.value || !realMeId.value || cur.value.ownerId !== realMeId.value || cur.value.status !== '草稿') return;
  directPublishing.value = true;
  try {
  const ok = await confirm.ask({ title: '直接发布草稿', target: `${id} ${cur.value.title}`, okText: '直接发布',
    impact: '发布前由服务端重新校验，成功后生成真实本体版本。' });
  if (!ok || key !== JSON.stringify(currentApiContext()) || !directPublish.value || readonly.value || cur.value.id !== id) return;
    const result = await publishDraft(id);
    if (key !== JSON.stringify(currentApiContext())) return;
    skippedSections.value = result.skipped ?? []; invalidateActionSnapshot(); await syncVersion(); await loadCRs(); message.success(`已发布 ${result.ver}`);
  } catch (e) { apiErr(e); }
  finally { directPublishing.value = false; }
}
async function publish() {
  if (realMode && (readonly.value || crFallback.value || !isAdmin.value || !approved.value.length)) return;
  const key = JSON.stringify(currentApiContext());
  let m: ReturnType<typeof mergeCheck>;
  let lockVersion: number | undefined;
  try { m = isStaticReal() ? (await mergeCheckApi()) as ReturnType<typeof mergeCheck> : mergeCheck(approved.value); } catch (e) { apiErr(e); return; }
  if (realMode && key !== JSON.stringify(currentApiContext())) return;
  if (isStaticReal()) { try { lockVersion = (await fetchCurrentAfterWrite()).lockVersion; } catch (e) { apiErr(e); return; } }
  if (realMode && key !== JSON.stringify(currentApiContext())) return;
  const verdict = m.rows.map((r) => r.code === 'V1–V17' && r.result === '通过'
    ? { label: '各单校验', value: '各单无阻断（未执行项见单内校验）' }
    : { label: r.code === '提示' ? '提示' : `${r.code} ${r.result}`, value: `${r.name}：${r.note}` });
  const ok = await confirm.ask({ title: `整版发布 ${NEXT_VERSION.value}`, target: `本体（当前 ${CURRENT_VERSION.value}）`, okText: '发布',
    tag: m.blocked ? '合并校验有阻断' : '合并校验通过',
    fields: [...approved.value.map((c) => ({ label: c.id, value: c.title })), ...verdict],
    impact: m.blocked
      ? '合并校验有阻断，不能发布。请把冲突的变更单撤回或合并后再发布。'
      : '发布后新提问按新版本回答；已有答案、页面和告警保留各自的口径版本。可回滚到上一版本。',
    blocked: m.blocked ? m.rows.filter((r) => r.result === '阻断').map((r) => `${r.note}（${r.code}）`).join('；') : undefined });
  if (realMode && (key !== JSON.stringify(currentApiContext()) || readonly.value || !isAdmin.value)) return;
  if (ok && !m.blocked) {
    let ver = NEXT_VERSION.value;
    if (isStaticReal()) {
      try { const result = await publishRelease(undefined, lockVersion); ver = result.ver; skippedSections.value = result.skipped ?? []; } catch (e) { apiErr(e); return; }
      if (key !== JSON.stringify(currentApiContext())) return;
      invalidateActionSnapshot(); await syncVersion(); await loadCRs(); message.success(`已发布 ${ver}`); return;
    }
    approved.value.forEach((c) => { c.status = '已发布'; c.release = ver; c.updatedAt = now(); (extraLog[c.id] ??= []).unshift({ time: now(), text: `${me.value} 整版发布为 ${ver}`, desc: `合并校验：${m.rows.filter((r) => r.result !== '通过').map((r) => r.note).join('；') || '全部通过'}` }); });
    message.success(`已发布 ${ver}`);
  }
}
async function rollback() {
  if (realMode && (readonly.value || crFallback.value || !canRollback.value || !isLatest.value || isFirstRelease.value)) return;
  const key = JSON.stringify(currentApiContext());
  const prev = visibleCRS.value.find((c) => c.status === '已发布' && c.release !== cur.value.release)?.release;
  const ok = await confirm.ask({ title: `回滚 ${cur.value.release}`, target: `本体 ${cur.value.release}`, okText: '回滚', danger: true,
    fields: [{ label: '回滚到', value: prev ?? '上一版本' }, { label: '涉及变更单', value: visibleCRS.value.filter((c) => c.release === cur.value.release).map((c) => c.id).join('、') }],
    impact: '回滚会生成一个新版本，内容等于上一版本；只能回滚最近一次发布。' });
  if (realMode && (key !== JSON.stringify(currentApiContext()) || readonly.value || !canRollback.value)) return;
  if (ok && isStaticReal()) {
    const ver = cur.value.release!;
    try { await rollbackRelease(ver); } catch (e) { apiErr(e); return; }
    if (key !== JSON.stringify(currentApiContext())) return;
    invalidateActionSnapshot(); await syncVersion(); await loadCRs(); rolledBack.value = true; message.success(`已回滚 ${ver}`); return;
  }
  if (ok) { rolledBack.value = true; (extraLog[cur.value.id] ??= []).unshift({ time: now(), text: `${me.value} 回滚 ${cur.value.release}`, desc: `生成新版本，内容同 ${prev}`, status: 'warn' }); message.success(`已回滚 ${cur.value.release}`); }
}
</script>

<template>
  <div v-if="realMode" class="panel" style="padding: 16px; margin-bottom: 12px">
    <div style="display: flex; align-items: center; gap: 12px; flex-wrap: wrap">
      <strong>正式发布</strong><span class="t-12 c-3">保存的修改立即生效；正式发布会完整校验并记录当前快照。</span>
      <a-button type="primary" :loading="formalPublishing" :disabled="readonly || !isAdmin" @click="publishFormal">正式发布</a-button>
    </div>
    <a-alert v-if="formalIssues.length" type="error" show-icon message="发布校验未通过" style="margin-top: 12px"><template #description><div v-for="(issue, i) in formalIssues" :key="i">{{ issue.section }} · {{ issue.id }}：{{ issue.message }}</div></template></a-alert>
    <div class="t-12 c-3" style="margin-top: 12px">正式发布记录</div>
    <div v-if="!RELEASES.length" class="t-12 c-3">暂无正式发布记录</div>
    <div v-for="release in RELEASES" :key="release.ver" class="t-12">{{ release.ver }} · {{ release.publishedAt }} · {{ release.by }} · {{ release.note }}</div>
  </div>
  <a-alert v-if="crFallback && !catalogLoading && !detailLoading && !pageLoading" type="warning" show-icon class="rl-fallback" message="变更单加载失败，请重试"><template #action><a-button size="small" @click="loadCRs">重试</a-button></template></a-alert>
  <a-alert v-if="skippedSections.length" type="warning" show-icon class="rl-fallback" :message="`以下改动未生效：${skippedSections.join('、')}`" />
  <div class="rl">
    <div class="panel rl-list">
      <div class="rl-filter">
        <a-input-search v-model:value="kw" placeholder="搜索标题或变更单号" class="rl-kw" />
        <a-select v-model:value="st" :options="STATUSES.map((v) => ({ value: v, label: v === '全部' ? '全部状态' : statusLabel(v) }))" class="rl-st" />
      </div>
      <ul class="rl-items" :class="{ 'rl-loading': pageLoading }" :aria-busy="pageLoading">
        <li v-for="c in list" :key="c.id" :class="{ active: c.id === cur.id }" :title="`${c.title} · ${c.id} · ${c.owner} · ${c.updatedAt} · ${c.release ?? c.level}`" @click="pick(c.id)">
          <div class="rl-row">
            <span class="t-14 rl-name rl-ell">{{ c.title }}</span>
            <span class="t-12 c-2 num rl-ft">{{ c.owner }} · {{ c.updatedAt.slice(5, 10) }}</span>
            <span class="t-12" :class="statusClass(c.status)">{{ statusLabel(c.status) }}</span>
          </div>
        </li>
      </ul>
      <div class="rl-foot">
        <AppPagination v-model:page="lpage" variant="simple" :total="totalRows" :size="LPAGE" empty-text="没有符合条件的变更单" />
      </div>
    </div>

    <div v-if="catalogLoading && !cur.id" class="panel rl-detail rl-wait"><a-spin /></div>
    <a-empty v-else-if="!cur.id" description="暂无可显示的变更单" />
    <div v-else class="panel rl-detail">
      <div class="rl-head">
        <span class="t-16 fw-600 c-1 rl-title">{{ cur.title }}</span>
        <span class="t-14 c-3 num">{{ cur.id }}</span>
        <a-tag :color="tagColor(cur.status)">{{ statusLabel(cur.status) }}</a-tag>
        <a-tag>{{ cur.level }}</a-tag>
        <span class="rl-sp" />
        <template v-if="readonly">
          <span class="t-12 c-3">{{ reason }}</span>
        </template>
        <template v-else-if="['草稿', '审核中', '已通过', '已驳回'].includes(cur.status)">
          <a-tooltip v-if="cur.status === '草稿'" :title="isOwner ? '' : `只有提交人 ${cur.owner} 能删除`">
            <a-button danger :disabled="crFallback || !isOwner" @click="removeDraft">删除草稿</a-button>
          </a-tooltip>
          <a-tooltip v-if="cur.status === '草稿'" :title="editTo ? '' : '这张单子没有可跳转的草稿页面'">
            <a-button :disabled="crFallback || !editTo" @click="edit">编辑</a-button>
          </a-tooltip>
          <a-tooltip v-if="!realMode" :title="!directPublish ? '需要当前工作空间的发布权限' : cur.status !== '草稿' ? '历史记录不可直接发布' : !realMeId || cur.ownerId !== realMeId ? '只能发布本人创建的草稿' : !cur.changes ? '变更单里还没有改动' : ''">
            <a-button type="primary" :loading="directPublishing" :disabled="crFallback || !directPublish || cur.status !== '草稿' || !realMeId || cur.ownerId !== realMeId || !cur.changes" @click="publishCurrentDraft">直接发布</a-button>
          </a-tooltip>
        </template>
        <template v-else-if="cur.status === '已发布'">
          <span class="t-12 c-3 num">已发布为 {{ cur.release }}</span>
          <a-tooltip v-if="!isFirstRelease" :title="!canRollback ? '需要实际发布管理权限' : isLatest ? '' : rolledBack && cur.release === CURRENT_VERSION ? '已回滚' : '只能回滚最近一次发布'"><a-button danger :disabled="crFallback || !canRollback || !isLatest" @click="rollback">回滚</a-button></a-tooltip><span v-else class="t-12 c-3">首个发布版本，没有可回滚到的上一版</span>
        </template>
      </div>
      <a-tabs v-model:activeKey="tab" class="rl-tabs">
        <template #rightExtra>
          <span class="rl-meta t-12 c-3 num">
            <span>{{ cur.owner }} 提交 · {{ cur.status === '已发布' ? '已发布' : '待发布' }} · 改动 {{ cur.changes }} 处 · 更新于 {{ cur.updatedAt }}</span>
            <InfoTip :text="`原因：${cur.reason}`" />
          </span>
        </template>
        <a-tab-pane key="diff" tab="改动清单" />
        <a-tab-pane key="checks" tab="发布校验" />
        <a-tab-pane key="impact" tab="影响分析" />
        <a-tab-pane key="log" tab="操作记录" />
      </a-tabs>

      <!-- 改动清单 -->
      <div v-if="tab === 'diff'" class="rl-body">
        <a-alert v-if="diffPage.error.value" type="error" :message="diffPage.error.value" show-icon />
        <a-table :columns="diffCols" :data-source="drows" :loading="diffPage.loading.value" :pagination="false" size="small" row-key="key" :locale="{ emptyText: '暂无改动' }">
          <template #bodyCell="{ column, record }">
            <template v-if="column.key === 'kind'"><a-tag>{{ record.kind }}</a-tag></template>
            <template v-else-if="column.key === 'before'"><span v-if="record.before" class="c-3 rl-old">{{ record.before }}</span><span v-else class="c-3">--</span></template>
            <template v-else-if="column.key === 'after'"><span class="c-1">{{ record.after }}</span></template>
          </template>
        </a-table>
        <AppPagination v-model:page="dpage" variant="simple" :total="realMode ? diffPage.total.value : detail.diffs.length" :size="DPAGE" :empty="false" class="rl-pager" />
        <div class="rl-note t-14">
          <span class="fw-600 c-1 rl-note-k">迁移方式</span>
          <span :class="detail.migration ? 'c-2' : 'c-3'">{{ detail.migration ?? (cur.level === '重大' ? '暂无，重大改动提交前需要写明' : '暂无，本次改动不涉及迁移') }}</span>
        </div>
        <div class="rl-note rl-note-link t-14" @click="tab = 'impact'">
          <span class="fw-600 c-1 rl-note-k">影响摘要</span>
          <span class="c-2 num">影响 {{ impactSum.metrics }} 个指标、{{ impactSum.qa }} 条认证问答、{{ impactSum.evals }} 道评测题</span>
          <span class="t-12 c-3 num">发布校验：{{ checkSummary(detail.checks) || '暂无' }}</span>
          <span class="rl-sp" /><span class="t-12 c-3">查看影响分析 ›</span>
        </div>
      </div>

      <!-- 发布校验：固定 17 项，不分页 -->
      <div v-else-if="tab === 'checks'" class="rl-body">
        <div class="rl-sum t-14">
          <span :class="counts['阻断'] ? 'c-error' : 'c-3'">阻断 {{ counts['阻断'] }}</span><span :class="counts['警告'] ? 'c-warning' : 'c-3'">警告 {{ counts['警告'] }}</span>
          <span v-if="counts['信息']" class="c-3">信息 {{ counts['信息'] }}</span>
          <span class="c-2">通过 {{ counts['通过'] }}</span><span class="c-3">不适用 {{ counts['不适用'] }}</span>
          <span class="rl-sp" /><span class="t-12 c-3 num">校验于 {{ detail.times.checked ?? '--' }} · 检查范围见各项说明</span>
          <a-tooltip :title="readonly ? reason : cur.status === '已发布' ? '已发布的变更单不再校验' : ''"><a-button size="small" :loading="rechecking" :disabled="crFallback || (readonly || cur.status === '已发布')" @click="recheck">重新校验</a-button></a-tooltip>
        </div>
        <a-table :columns="checkCols" :data-source="detail.checks" :pagination="false" size="small" row-key="code" :locale="{ emptyText: '暂无校验结果' }">
          <template #bodyCell="{ column, record }">
            <template v-if="column.key === 'result'"><span :class="resultClass(record.result)">{{ record.result }}</span></template>
          </template>
        </a-table>
      </div>

      <!-- 影响分析 -->
      <div v-else-if="tab === 'impact'" class="rl-body rl-impact">
        <div v-if="realMode && (detail as AnalyzedDetail).analysisScope" class="t-12 c-3">{{ (detail as AnalyzedDetail).analysisScope }}</div>
        <div class="rl-cols">
          <div v-for="col in [{ k: 'direct', t: '直接改动' }, { k: 'defs', t: '受影响的定义' }, { k: 'usage', t: '下游使用' }]" :key="col.k" class="rl-col">
            <div class="t-14 fw-600 c-1 rl-col-title">{{ col.t }}</div>
            <div v-for="it in (detail.impact as any)[col.k]" :key="it.name" class="rl-card" :title="`${it.kind} · ${it.name}${it.note ? ` · ${it.note}` : ''}`">
              <span class="t-12 c-3 rl-ft">{{ it.kind }}</span>
              <span class="t-14 c-1 rl-ft"><HoverCard v-if="it.hover" :kind="it.hover.kind" :id="it.hover.id">{{ it.name }}</HoverCard><template v-else>{{ it.name }}</template></span>
              <span class="t-12 c-3 rl-ell">{{ it.note }}</span>
            </div>
            <div v-if="!(detail.impact as any)[col.k].length" class="t-12 c-3">暂无</div>
          </div>
        </div>
        <div class="rl-trial">
          <div class="t-14 fw-600 c-1 rl-col-title">{{ detail.trial?.title ?? '试跑对比' }}</div>
          <a-table v-if="detail.trial" :columns="trialCols" :data-source="detail.trial.rows" :pagination="false" size="small" row-key="period" />
          <a-alert v-if="realMode && (detail as AnalyzedDetail).trialUnavailable" type="warning" :message="(detail as AnalyzedDetail).trialUnavailable" show-icon />
          <div v-else-if="!detail.trial" class="t-12 c-3">{{ realMode ? '尚未执行试跑；重新校验后读取真实结果' : '暂无：本次改动不涉及指标口径，没有试跑。' }}</div>
        </div>
      </div>

      <!-- 评审记录 -->
      <div v-else class="rl-body rl-log">
        <StatusTimeline :items="log" />
      </div>
    </div>
  </div>
  <a-modal v-model:open="reviewerOpen" title="设置审核人" :width="520" :closable="!reviewerSaving" :mask-closable="!reviewerSaving" ok-text="保存审核人" :confirm-loading="reviewerSaving" :ok-button-props="{ disabled: !canSaveReviewer }" @ok="saveReviewer">
    <a-alert v-if="reviewerError" type="error" :message="reviewerError" show-icon><template #action><a-button size="small" @click="loadReviewers">重新加载</a-button></template></a-alert>
    <p class="t-14 c-3">{{ cur.id }} · 当前审核人：{{ storedReviewer || '未设置' }}。仅可选择具备实际审核权限且非提交人的用户。</p>
    <a-select v-model:value="reviewerId" show-search option-filter-prop="label" :loading="reviewerLoading" :disabled="reviewerSaving || reviewerLoading || !!reviewerError" :options="reviewerOptions.map((u) => ({ value: u.id, label: `${u.name}（${u.id}）` }))" placeholder="选择真实审核人" style="width:100%" />
    <a-alert v-if="!reviewerLoading && !reviewerError && !reviewerOptions.length" type="info" message="暂无符合条件的审核人，需管理员先为其他实际用户分配审核角色；此处不会自动授权。" show-icon />
  </a-modal>
</template>

<style scoped>
.rl-fallback { margin-bottom: 12px; }
.rl { flex: 1; min-height: 0; display: flex; gap: var(--gap); }
.rl-list { width: 360px; flex: none; }
.rl-filter { padding: 12px 12px 8px; display: flex; gap: 8px; flex: none; }
.rl-kw { flex: 1; }
.rl-st { width: 112px; flex: none; }
.rl-items { list-style: none; margin: 0; padding: 0 8px; flex: 1; min-height: 0; overflow: hidden; }
.rl-loading { opacity: 0.6; pointer-events: none; }
.rl-wait { align-items: center; justify-content: center; }
.rl-items li { padding: 8px; border-radius: var(--radius); cursor: pointer; }
.rl-items li:hover { background: var(--g3); }
.rl-items li.active { background: var(--brand-bg); }
.rl-items li.active .rl-name { color: var(--brand); }
.rl-row { display: flex; justify-content: space-between; align-items: center; gap: 8px; }
.rl-name { overflow: hidden; white-space: nowrap; text-overflow: ellipsis; }
.rl-foot { padding: 8px 12px 8px 16px; border-top: var(--border); flex: none; }
.rl-detail { flex: 1; }
.rl-head { display: flex; align-items: center; gap: 12px; height: 52px; padding: 0 16px; flex: none; }
.rl-head :deep(.ant-tag) { margin-inline-end: 0; }
.rl-sp { flex: 1; }
.rl-meta { display: inline-flex; align-items: center; white-space: nowrap; }
.rl-reason { overflow: hidden; white-space: nowrap; text-overflow: ellipsis; min-width: 0; }
.rl-tabs { padding: 0 16px; flex: none; }
.rl-tabs :deep(.ant-tabs-nav) { margin-bottom: 0; }
.rl-body { padding: 16px; flex: 1; min-height: 0; display: flex; flex-direction: column; gap: 12px; overflow: hidden; }
.rl-old { text-decoration: line-through; }
.rl-pager { margin-top: 0; }
.rl-note { display: flex; align-items: center; gap: 12px; padding: 12px; background: var(--g2); border-radius: var(--radius); flex: none; }
.rl-note-k { width: 64px; flex: none; }
.rl-note-link { cursor: pointer; }
.rl-note-link:hover { background: var(--g3); }
.rl-title { overflow: hidden; white-space: nowrap; text-overflow: ellipsis; min-width: 0; }
.rl-why { overflow: hidden; white-space: nowrap; text-overflow: ellipsis; max-width: 420px; }
.rl-sum { display: flex; align-items: center; gap: 16px; }
.rl-cols { display: flex; gap: 16px; }
.rl-col { flex: 1; display: flex; flex-direction: column; gap: 8px; }
.rl-col-title { margin-bottom: 4px; }
.rl-card { border: var(--border); border-radius: var(--radius); padding: 8px 12px; display: flex; align-items: baseline; gap: 8px; min-width: 0; }
.rl-trial { width: 50%; }
.rl-log { max-width: 720px; }

.rl-ell { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.rl-ft { flex-shrink: 0; }
</style>
