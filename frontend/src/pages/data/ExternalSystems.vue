<script setup lang="ts">
/**
 * 外部系统：动作写回用到的系统（CRM、WMS、短信、顺丰……）。
 * 左侧系统列表，右侧配置（地址、认证、幂等、超时 / 重试）+ 最近调用（simple 分页，按结果筛选）。
 * 调用结果颜色按时间线规则：成功 灰、结果未知 橙、失败 红。点“详情”打开调用抽屉（?call=），结果未知不在本页重试：写回调用到动作中心对账（?tab=unknown&act=），通知类不支持幂等的人工核对。
 * 关联动作：按动作类型的 sysId（mock/actionEvent.ts）过滤得到，点动作名进动作类型；不做写回的集成（短信、企业微信、顺丰、美团）显示用途。
 * 新建 / 编辑系统：配置抽屉（?edit=EXT-WMS / ?edit=new），保存前确认。
 * URL：?sys=EXT-WMS&result=失败&call=WMS-00012&edit=EXT-WMS
 * 真接口（开关 integration，src/api/integration.ts）：系统列表、关联动作、新建 / 编辑、测试调用、
 * 最近调用（筛选 / 分页 / 详情）、人工核对都走后台（writeback_call 表，见 action-event-api.md §3.1）。
 * 真实读取失败由共享错误处理提示，不切演示数据；写操作失败保留当前数据。
 */
import { computed, reactive, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { message } from 'ant-design-vue';
import { QuestionCircleOutlined } from '@ant-design/icons-vue';
import AppPagination from '@/components/AppPagination.vue';
import { useConfirmStore } from '@/stores/confirm';
import { useReadonly } from '@/composables/useReadonly';
import { useUnsavedGuard, confirmDiscard } from '@/composables/useUnsavedGuard';
import { paginate, EMPTY } from '@/mock';
import { EXT_SYSTEMS, extCalls, type CallResult, type ExtCall, type ExtOp, type ExtSample, type ExtSystem } from '@/mock/extsys';
import { ACTION_TYPES, runsOf, type ActionType, type ActionRun } from '@/mock/actionEvent';
import { ACTIONS, RULE_CONFIRMER, OBJ_KIND_LABEL, type ActionRecord } from '@/mock/actions';
import { currentApiContext, isApiError } from '@/api/http';
import { integrationApi, queryExtSystems, getExtSystem, createExtSystem, updateExtSystem, testExtSystem, listExtCalls, getWritebackCall, markWritebackChecked, type ExtCallPage, type ExtSystemDto, type LinkedActionTypeDto } from '@/api/integration';
import { getActionAuthorizations, saveActionAuthorizations } from '@/api/integration';

const route = useRoute();
const router = useRouter();
const q = (k: string) => (typeof route.query[k] === 'string' ? (route.query[k] as string) : '');
const setQuery = (patch: Record<string, string | undefined>) => router.replace({ query: { ...route.query, ...patch } });
const { readonly, reason } = useReadonly();

// 系统列表：直接包装共享的 EXT_SYSTEMS（本次会话里新建 / 编辑后，动作类型页的写回目标也能看到）
// 真接口：列表取后台（不改共享的 mock 数组），加载前用空表单占位
const real = integrationApi.isReal();
const systems = real ? reactive<(ExtSystem & Pick<ExtSystemDto, 'actionCount'>)[]>([]) : reactive(EXT_SYSTEMS);
const apiFail = (e: unknown) => { message.error(isApiError(e) ? `${e.message}（${e.code}）` : String(e)); };
const authorization = ref<Awaited<ReturnType<typeof getActionAuthorizations>> | null>(null);
const authorizationSystem = ref('');
async function openAuthorization() {
  try { const id = selected.value.id; const key = JSON.stringify(currentApiContext()); const data = await getActionAuthorizations(id); if (key !== JSON.stringify(currentApiContext()) || id !== selected.value.id) return; authorizationSystem.value = id; authorization.value = data; } catch (e) { apiFail(e); }
}
function toggleAuthorization(action: { id: string; version: string }, checked: boolean) {
  if (!authorization.value) return;
  authorization.value.selected = authorization.value.selected.filter(a => a.id !== action.id);
  if (checked) authorization.value.selected.push({ id: action.id, version: action.version, path: authorization.value.paths[0] ?? '' });
}
async function saveAuthorization() {
  if (!authorization.value) return;
  try { await saveActionAuthorizations(authorizationSystem.value, { version: authorization.value.version, actions: authorization.value.selected.filter(s => authorization.value!.actions.some(a => a.id === s.id)) }); authorization.value = null; message.success('动作授权已保存'); } catch (e) { apiFail(e); }
}
/** 后台没起来：页面按 mock 渲染（读取不再发请求） */
const live = computed(() => real);
const contextKey = () => JSON.stringify(currentApiContext());
watch(contextKey, () => { authorization.value = null; authorizationSystem.value = ''; });
const systemPage = ref(1), systemTotal = ref(0), systemsContext = ref('');
let systemSeq = 0;
async function loadSystems() {
  const seq = ++systemSeq, key = contextKey();
  if (!real) return;
  if (systemsContext.value !== key) { systems.splice(0); systemTotal.value = 0; systemsContext.value = key; }
  try { const result = await queryExtSystems(systemPage.value, 10);
    if (seq !== systemSeq || key !== contextKey()) return;
    systems.splice(0, systems.length, ...result.items); systemTotal.value = result.total;
    if (result.page !== systemPage.value) systemPage.value = result.page;
  } catch (e) { if (seq === systemSeq && key === contextKey()) apiFail(e); }
}
if (real) watch([contextKey, systemPage], loadSystems, { immediate: true });
const LOADING: ExtSystem = { id: '', name: '', vendor: '', category: '', endpoint: '', auth: '', authDetail: '', idempotent: false, idemKey: '-', timeoutMs: 0, maxRetry: 0, retry: '', rateLimit: '', owner: '', status: '正常', env: '' };
const realDetail = ref<{ key: string; system: ExtSystem }>();
const selectedId = computed(() => q('sys') || (systemsContext.value === contextKey() ? systems[0]?.id : ''));
const selected = computed<ExtSystem>(() => real ? realDetail.value?.key === contextKey() && realDetail.value.system.id === selectedId.value ? realDetail.value.system : LOADING : systems.find((s) => s.id === q('sys')) ?? systems[0] ?? LOADING);
const realLinked = ref<{ key: string; sysId: string; items: LinkedActionTypeDto[] } | null>(null);
let linkedSeq = 0;
async function loadLinked(id: string) {
  const seq = ++linkedSeq, key = contextKey(); realLinked.value = null; realDetail.value = undefined;
  if (!real || !id) return;
  try { const d = await getExtSystem(id); if (seq !== linkedSeq || key !== contextKey() || selectedId.value !== id) return;
    realLinked.value = { key, sysId: d.id, items: d.actionTypes }; realDetail.value = { key, system: d };
  } catch (e) { if (seq === linkedSeq && key === contextKey()) apiFail(e); }
}
if (real) watch([selectedId, contextKey], ([id]) => loadLinked(id ?? ''), { immediate: true });
/** 关联动作：动作类型里 sysId 指向该系统的（名称与动作类型页完全一致） */
const linked = computed<{ id: string; name: string }[]>(() => (live.value
  ? (realLinked.value?.key === contextKey() && realLinked.value.sysId === selected.value.id ? realLinked.value.items : [])
  : ACTION_TYPES.filter((a) => a.sysId === selected.value.id)));
const opsOf = (sys: ExtSystem): ExtOp[] => {
  const acts = ACTION_TYPES.filter((a) => a.sysId === sys.id);
  // 草稿动作没有执行过，不产生调用记录
  if (acts.length) return acts.filter((a) => a.status !== '草稿').map((a) => { const [method, path] = a.endpoint.split(' '); return { name: a.name, method, path, samples: samplesOf(a) }; });
  return (sys.usage?.ops ?? []).map((name) => ({ name }));
};
/**
 * 调用记录的对象 / 发起人取自动作执行记录：优先动作中心（mock/actions.ts，结果未知可按 ACT 编号跳去对账），
 * 动作中心未收录的动作类型取动作类型页的执行记录（只取已有结果的，不产生结果未知）。
 * 对象类型 = 动作类型的目标对象；发起人是人，或“规则 R-027 · 确认人 吴佳”（规则只暂存动作，由固定确认人提交；R-021 只告警）。
 */
const ruleActor = (rule: string) => `规则 ${rule} · 确认人 ${RULE_CONFIRMER[rule] ?? '—'}`;
const withType = (type: string, label: string) => (label.includes(type) ? label : `${type} ${label}`);
const fromCenter = (x: ActionRecord): ExtSample => ({
  obj: withType(OBJ_KIND_LABEL[x.objKind], x.objLabel), actor: x.source.ruleId ? ruleActor(x.source.ruleId) : x.initiator,
  runId: x.id, idemKey: x.idemKey, unknown: x.status === '结果未知',
  time: x.log.find((l) => l.text.startsWith('调用'))?.time ?? x.submittedAt,
});
const fromRun = (a: ActionType, x: ActionRun): ExtSample => ({
  obj: withType(a.target, x.objLabel.replace(/^分群 · /, '')), actor: x.ruleId ? ruleActor(x.ruleId) : x.initiator,
  runId: x.id, idemKey: x.idemKey, unknown: false,
});
const DONE = ['成功', '失败', '结果未知', '已对账', '执行中'];
function samplesOf(a: ActionType): ExtSample[] {
  const center = ACTIONS.filter((x) => x.action === a.name && DONE.includes(x.status));
  if (center.length) return center.map(fromCenter);
  return runsOf(a.id).filter((x) => DONE.includes(x.status)).map((x) => fromRun(a, x));
}
const actionCount = (sys: ExtSystem & Pick<ExtSystemDto, 'actionCount'>) => (live.value ? sys.actionCount ?? 0 : ACTION_TYPES.filter((a) => a.sysId === sys.id).length);
// 状态点：正常 / 停用 灰，降级 红（有失败调用）；橙色只留给 待确认 / 结果未知，不用绿、蓝
const statusBadge = (s: ExtSystem['status']) => ('default' as const);
const resultText = (r: CallResult) => (r === '结果未知' ? '结果未知 · 待对账' : r);

// 调用记录：本页内可改（重试 / 核对后结果会变）
const overrides = ref<Record<string, Partial<ExtCall>>>({});
const calls = computed(() => (real ? [] : extCalls(selected.value, opsOf(selected.value))).map((c) => ({ ...c, ...overrides.value[c.id] })));
const counts = computed(() => {
  if (live.value) return realPage.value?.key === contextKey() && realPage.value.sysId === selected.value.id ? realPage.value.counts : { 全部: 0, 成功: 0, 失败: 0, 结果未知: 0 };
  const m: Record<string, number> = { 全部: calls.value.length, 成功: 0, 失败: 0, 结果未知: 0 };
  calls.value.forEach((c) => { m[c.result] += 1; });
  return m;
});
const result = computed({ get: () => q('result') || '全部', set: (v: string) => setQuery({ result: v === '全部' ? undefined : v }) });
const filtered = computed(() => calls.value.filter((c) => result.value === '全部' || c.result === result.value));
const SIZE = 10;
const page = ref(1);
watch([() => selected.value.id, result], () => { page.value = 1; });
const paged = computed(() => (live.value ? { items: realPage.value?.key === contextKey() && realPage.value.sysId === selected.value.id ? realPage.value.items : [] } : paginate(filtered.value, { page: page.value, size: SIZE })));
const callTotal = computed(() => (live.value ? realPage.value?.key === contextKey() && realPage.value.sysId === selected.value.id ? realPage.value.total : 0 : filtered.value.length));
/** 真接口：当前系统 × 结果 × 页 的一页调用（counts 也取后台）；seq 防止切换快时旧响应覆盖新的 */
const realPage = ref<(ExtCallPage & { sysId: string; key: string }) | null>(null);
const callsLoading = ref(false);
const callsTick = ref(0);
let callsSeq = 0;
async function loadCalls() {
  const seq = ++callsSeq, sysId = selected.value.id, key = contextKey();
  if (!real || !sysId) return;
  callsLoading.value = true;
  try { const r = await listExtCalls(sysId, { result: result.value, page: page.value, size: SIZE });
    if (seq !== callsSeq || key !== contextKey() || selected.value.id !== sysId) return;
    realPage.value = { ...r, sysId, key };
    if (r.page !== page.value) page.value = r.page;
  } catch (e) { if (seq === callsSeq && key === contextKey()) apiFail(e); }
  finally { if (seq === callsSeq && key === contextKey()) callsLoading.value = false; }
}
if (real) watch([() => selected.value.id, contextKey, result, page, callsTick], loadCalls, { immediate: true });

const tl = (r: CallResult) => (r === '成功' ? 'tl-normal' : r === '结果未知' ? 'tl-attention' : 'tl-failed');
const cols = [
  { title: '时间', key: 'time', width: 160 },
  { title: '动作', dataIndex: 'action', key: 'action', width: 130, ellipsis: true },
  { title: '对象', key: 'target', width: 200, ellipsis: true },
  { title: '发起人', key: 'actor', width: 170, ellipsis: true },
  { title: '幂等键', key: 'idemKey', width: 130, ellipsis: true },
  { title: '耗时', key: 'ms', width: 80, align: 'right' as const },
  { title: '尝试', key: 'attempt', width: 50, align: 'right' as const },
  { title: 'HTTP', key: 'http', width: 60, align: 'right' as const },
  { title: '结果', key: 'result', width: 150 },
  { title: '操作', key: 'op', width: 60 },
];

// 调用详情抽屉
/** 真接口：抽屉按 ?call= 取详情（只认当前系统的调用，同 mock） */
const realCall = ref<ExtCall | null>(null);
let callSeq = 0; const callContext = ref('');
async function loadCall(id: string) {
  const seq = ++callSeq, key = contextKey(); realCall.value = null; callContext.value = key;
  if (!real || !id) return;
  try { const c = await getWritebackCall(id); if (seq === callSeq && key === contextKey() && q('call') === id) realCall.value = c; }
  catch (e) { if (seq === callSeq && key === contextKey()) apiFail(e); }
}
if (real) watch([() => q('call'), contextKey], ([id]) => loadCall(id), { immediate: true });

const call = computed(() => (live.value
  ? (callContext.value === contextKey() && realCall.value && realCall.value.id === q('call') && realCall.value.sysId === selected.value.id ? realCall.value : null)
  : calls.value.find((c) => c.id === q('call')) ?? null));
const openCall = (id: string) => setQuery({ call: id });
const closeCall = () => setQuery({ call: undefined });
const reqBody = (_c: ExtCall) => '未保存原始请求体';
const respBody = (_c: ExtCall) => '未保存原始响应体；请查看返回信息和已记录的 HTTP 状态';

const confirm = useConfirmStore();
/** 写回调用结果未知：不在本页重试，到动作中心按执行记录对账 */
const reconcileLink = (c: ExtCall) => ({ path: '/use/ops/actions', query: { tab: 'unknown', act: c.runId } });
async function markChecked(c: ExtCall) {
  if (readonly.value) return;
  const key = contextKey(), sysId = selected.value.id;
  const current = () => !readonly.value && key === contextKey() && sysId === selected.value.id;
  const ok = await confirm.ask({
    title: '人工核对结果', target: `${c.action} · ${c.target}`, okText: '确认对方已处理', tag: '结果未知 · 待对账',
    fields: [{ label: '系统', value: `${selected.value.name}（${selected.value.vendor}）` }, { label: '原调用', value: `${c.id} · ${c.time}` }],
    impact: `${selected.value.name} 不支持幂等，从不重试。请先在对方系统确认这条请求已处理，再标记为成功；没处理的话请重新发起动作。`,
  });
  if (!ok || !current()) return;
  if (real) {
    try { const saved = await markWritebackChecked(c.id); if (!current()) return; realCall.value = saved; } catch (e) { if (current()) apiFail(e); return; }
    callsTick.value += 1;
    message.success('已标记为成功');
    return;
  }
  overrides.value[c.id] = { result: '成功', msg: '人工核对：对方已处理（王敏 16:30 标记）' };
  message.success('已标记为成功');
}

const testUnavailable = '真实连接测试未启用';
const testing = ref(false);
const probe = ref<{ version: number; endpoint: string; probePath: string | null } | null>(null);
const probeSystem = ref(''), probeBusy = ref(false);
watch([contextKey, () => selected.value.id], () => { probe.value = null; probeSystem.value = ''; });
async function openProbe() {
  if (!real || readonly.value || probeBusy.value || !selected.value.id) return;
  const key = contextKey(), id = selected.value.id;
  probeBusy.value = true;
  try {
    const result = await integrationApi.get<{ version: number; endpoint: string; probePath: string | null }>(`/ext-systems/${encodeURIComponent(id)}/probe-config`);
    if (key === contextKey() && id === selected.value.id) { probeSystem.value = id; probe.value = result; }
  } catch (e) { if (key === contextKey()) apiFail(e); }
  finally { probeBusy.value = false; }
}
async function saveProbe() {
  if (!probe.value || probeBusy.value || readonly.value) return;
  const key = contextKey(), id = probeSystem.value, path = probe.value.probePath?.trim() || null;
  if (path && (!/^\/(?!\/)[^?#\\\s]*$/.test(path) || path.split('/').some(part => part === '.' || part === '..'))) { message.error('探测路径须为不含查询参数、片段或点段的同源绝对路径'); return; }
  probeBusy.value = true;
  try {
    await integrationApi.put(`/ext-systems/${encodeURIComponent(id)}/probe-config`, { version: probe.value.version, probePath: path });
    if (key === contextKey() && id === selected.value.id) { probe.value = null; message.success('探测路径已保存'); }
  } catch (e) { if (key === contextKey()) apiFail(e); }
  finally { probeBusy.value = false; }
}
async function testCall() {
  if (!real || readonly.value || testing.value || !selected.value.id) return;
  const key = contextKey(), id = selected.value.id;
  const ok = await confirm.ask({ title: '测试调用', target: selected.value.name, okText: '开始探测', impact: '仅请求已配置的只读探测接口；结果和耗时会保存到最近调用。需先配置探测路径并获得网络授权。' });
  if (!ok || key !== contextKey() || id !== selected.value.id || readonly.value) return;
  testing.value = true;
  try {
    const result = await testExtSystem(id);
    if (key !== contextKey() || id !== selected.value.id) return;
    callsTick.value += 1;
    if (result.result === '成功') message.success(result.msg);
    else message.error(result.msg);
  } catch (e) { apiFail(e); }
  finally { testing.value = false; }
}

// 新建 / 编辑外部系统（?edit=EXT-WMS / ?edit=new）
const CATEGORIES = ['CRM', 'ERP', 'OMS', 'WMS', 'SMS', '物流', '通知', '渠道', '审批', 'Webhook'];
const AUTHS = ['OAuth 2.0（客户端凭证）', 'AppID + AppSecret（OpenAPI）', 'mTLS + 服务令牌', 'API Key + HMAC 签名', 'AccessKey（RAM 子账号）', 'Token（服务账号）', 'CorpID + Secret', 'AppID + 签名', '无需授权'];
const OWNERS = ['王敏', '陈立军', '何静', '刘婷', '孙浩然', '吴佳'];
const blank = (): ExtSystem => ({
  id: '', name: '', vendor: '', category: 'ERP', endpoint: 'https://', auth: AUTHS[0], authDetail: '', idempotent: true, idemKey: 'X-Idempotency-Key',
  timeoutMs: 5000, maxRetry: 3, retry: '', rateLimit: '10 次/秒', owner: '王敏', status: '正常', env: '生产',
});
const editKey = computed(() => q('edit'));
const editingRecord = ref<{ key: string; item: ExtSystem }>();
const editing = computed(() => editKey.value === 'new' ? null : real ? editingRecord.value?.key === contextKey() && editingRecord.value.item.id === editKey.value ? editingRecord.value.item : null : systems.find((s) => s.id === editKey.value) ?? null);
const form = reactive<ExtSystem>(blank());
const formSnap = ref('');
let editSeq = 0;
watch([editKey, contextKey], async ([k, key]) => {
  const seq = ++editSeq; editingRecord.value = undefined;
  if (!k) return;
  Object.assign(form, blank()); formSnap.value = JSON.stringify(form);
  if (real && k !== 'new') {
    try { const item = await getExtSystem(k); if (seq !== editSeq || key !== contextKey() || k !== editKey.value) return;
      editingRecord.value = { key, item }; Object.assign(form, item); formSnap.value = JSON.stringify(form);
    } catch (e) { if (seq === editSeq && key === contextKey()) apiFail(e); }
    return;
  }
  Object.assign(form, k === 'new' ? blank() : JSON.parse(JSON.stringify(editing.value ?? blank())));
  formSnap.value = JSON.stringify(form);
}, { immediate: true });

const formDirty = computed(() => !!editKey.value && JSON.stringify(form) !== formSnap.value);
useUnsavedGuard(formDirty);
const retryText = (f: ExtSystem) => (f.idempotent && f.maxRetry
  ? `外部明确失败时最多 ${f.maxRetry} 次；超时不重试，标记结果未知`
  : '不重试：外部明确失败转人工；超时标记结果未知，人工对账');
watch(() => form.idempotent, (v) => { if (!v) form.maxRetry = 0; });
const formErrors = computed(() => {
  const e: string[] = [];
  if (!form.name.trim()) e.push('请填写系统名称');
  else if (systems.some((s) => s.name === form.name.trim() && s.id !== editing.value?.id)) e.push('系统名称已存在');
  if (!form.vendor.trim()) e.push('请填写供应商');
  const endpoint = form.endpoint.trim();
  try {
    const url = new URL(endpoint);
    if (!/^https?:\/\/[^/?#\s]+$/i.test(endpoint) || !['http:', 'https:'].includes(url.protocol) || !url.hostname || url.username || url.password || url.port === '0') throw new Error();
  } catch { e.push('接口地址需为不含路径、凭据或查询参数的 HTTP(S) 来源地址'); }
  if (form.idempotent && !form.idemKey.trim()) e.push('支持幂等时需填写幂等键');
  if (!(form.timeoutMs >= 1000 && form.timeoutMs <= 60000)) e.push('超时需在 1～60 秒之间');
  return e;
});
const usedBy = computed<{ name: string }[]>(() => {
  if (!editing.value) return [];
  if (live.value) return realLinked.value?.sysId === editing.value.id ? realLinked.value.items : [];
  return ACTION_TYPES.filter((a) => a.sysId === editing.value!.id);
});
const openEdit = (id: string) => setQuery({ edit: id, ...(id !== 'new' ? { sys: id } : {}), call: undefined });
async function closeEdit() {
  if (formDirty.value && !(await confirmDiscard())) return;
  formSnap.value = JSON.stringify(form);
  setQuery({ edit: undefined });
}
const savingEdit = ref(false);
async function saveEdit() {
  if (savingEdit.value) return;
  savingEdit.value = true;
  try {
  if (readonly.value || real && editKey.value !== 'new' && !editing.value) return;
  const key = contextKey(), seq = editSeq;
  const current = () => !readonly.value && key === contextKey() && seq === editSeq;
  const isNew = real ? editKey.value === 'new' : !editing.value;
  const before = editing.value;
  const diff = before ? (['name', 'endpoint', 'auth', 'timeoutMs', 'maxRetry', 'rateLimit', 'owner', 'status', 'idempotent'] as const)
    .filter((k) => before[k] !== form[k]).map((k) => ({ label: ({ name: '名称', endpoint: '接口地址', auth: '认证方式', timeoutMs: '超时', maxRetry: '最多重试', rateLimit: '限流', owner: '负责人', status: '状态', idempotent: '幂等' } as Record<string, string>)[k], value: `${String(before[k])} → ${String(form[k])}` })) : [];
  const ok = await confirm.ask({
    title: isNew ? '新建外部系统' : '保存外部系统配置', target: `${form.name || '未命名'}（${form.vendor || '—'}）`, okText: '保存',
    fields: isNew ? [{ label: '接口地址', value: form.endpoint }, { label: '环境', value: form.env }, { label: '负责人', value: form.owner }] : diff.length ? diff : [{ label: '修改', value: '无变化' }],
    impact: (isNew ? (form.auth === '无需授权' ? '保存后可在动作类型的“写回目标”里选择该系统。' : '保存后可在动作类型的“写回目标”里选择该系统；凭证由负责人在密钥库录入，界面不回显。')
      : usedBy.value.length ? `影响 ${usedBy.value.length} 个动作类型（${usedBy.value.map((a) => a.name).join('、')}）的后续写回。` : '没有动作类型写回该系统，保存后立即生效。')
      + (before?.auth === '无需授权' && form.auth !== '无需授权' ? ' 如适配器已配置令牌，后续调用将重新发送该令牌。' : ''),
    blocked: formErrors.value[0],
  });
  if (!ok || !current()) return;
  if (real) {
    const { id: _id, ...body } = { ...JSON.parse(JSON.stringify(form)) as ExtSystem, name: form.name.trim(), vendor: form.vendor.trim(), endpoint: form.endpoint.trim(), retry: retryText(form) };
    let saved: ExtSystem;
    try { saved = isNew ? await createExtSystem(body) : await updateExtSystem(before!.id, body); } catch (e) { if (current()) apiFail(e); return; }
    if (!current()) return;
    formSnap.value = JSON.stringify(form);
    setQuery({ edit: undefined, sys: saved.id });
    await loadSystems();
    await loadLinked(saved.id);
    message.success(isNew ? `已新建外部系统：${saved.name}` : `已保存：${saved.name}`);
    return;
  }
  const next: ExtSystem = { ...JSON.parse(JSON.stringify(form)), name: form.name.trim(), vendor: form.vendor.trim(), endpoint: form.endpoint.trim(), retry: retryText(form), idemKey: form.idempotent ? form.idemKey : '-' };
  if (isNew) {
    next.id = `EXT-N${String(systems.filter((s) => s.id.startsWith('EXT-N')).length + 1).padStart(2, '0')}`;
    systems.push(next);
  } else Object.assign(before!, next);
  formSnap.value = JSON.stringify(form);
  setQuery({ edit: undefined, sys: next.id });
  message.success(isNew ? `已新建外部系统：${next.name}` : `已保存：${next.name}`);
  } finally { savingEdit.value = false; }
}
</script>

<template>
  <Teleport defer to="#page-extra">
    <a-tooltip :title="reason"><a-button type="primary" :disabled="readonly" @click="openEdit('new')">新建外部系统</a-button></a-tooltip>
  </Teleport>
  <div class="ex">
    <div class="panel ex-list">
      <div class="panel-head">
        <span class="t-16 fw-600">外部系统</span>
        <span class="t-14 c-2">共 {{ real ? systemTotal : systems.length }} 个</span>
      </div>
      <div class="ex-items">
        <div v-for="s in systems" :key="s.id" class="ex-item" :class="{ on: s.id === selected.id }" @click="setQuery({ sys: s.id, result: undefined, call: undefined })">
          <div class="ex-l1" :title="`${s.name} · ${s.vendor} · ${s.env} · ${actionCount(s) ? actionCount(s) + ' 个动作' : '无动作写回'} · ${s.idempotent ? '支持幂等' : '不支持幂等'}`">
            <span class="ex-cat t-12">{{ s.category }}</span>
            <span class="t-14 fw-600 c-1 ex-ellip">{{ s.name }}</span>
            <a-tooltip :title="s.status === '降级' ? '最近 6 次调用有失败 / 结果未知' : undefined"><a-badge :status="statusBadge(s.status)" :text="s.status" class="ex-badge" /></a-tooltip>
          </div>
        </div>
      </div>
      <AppPagination v-if="real" v-model:page="systemPage" variant="simple" :total="systemTotal" :size="10" unit="个" />
    </div>

    <div v-if="!real || selected.id" class="panel ex-detail">
      <div class="ex-head">
        <div class="ex-title">
          <span class="t-20 fw-600 c-1">{{ selected.name }}</span>
          <a-tag>{{ selected.category }}</a-tag>
          <a-tag>{{ selected.env }}</a-tag>
          <a-badge :status="statusBadge(selected.status)" :text="selected.status" />
          <span v-if="selected.status === '降级'" class="t-12 c-2">最近 6 次调用有失败 / 结果未知；结果未知不自动重试，需对账</span>
        </div>
        <a-space>
          <a-tooltip :title="readonly ? reason : !real ? testUnavailable : undefined"><a-button :disabled="!real || readonly || !selected.id || selected.status === '停用'" :loading="testing" @click="testCall">测试调用</a-button></a-tooltip>
          <a-button v-if="real" :disabled="readonly || !selected.id" :loading="probeBusy" @click="openProbe">探测配置</a-button>
          <a-tooltip :title="reason"><a-button :disabled="readonly" @click="openEdit(selected.id)">编辑</a-button></a-tooltip>
          <a-button v-if="real" :disabled="readonly" @click="openAuthorization">动作授权</a-button>
        </a-space>
      </div>
      <div class="ex-cfg">
        <a-descriptions :column="3" bordered size="small" class="ex-desc">
          <a-descriptions-item label="接口地址" :span="2"><span class="num">{{ selected.endpoint }}</span></a-descriptions-item>
          <a-descriptions-item label="供应商">{{ selected.vendor }}</a-descriptions-item>
          <a-descriptions-item label="认证方式">{{ selected.auth }}</a-descriptions-item>
          <a-descriptions-item v-if="selected.auth !== '无需授权'" label="凭证" :span="2"><span class="num">{{ selected.authDetail }}</span></a-descriptions-item>
          <a-descriptions-item label="幂等">
            <span v-if="selected.idempotent">支持 · <span class="num">{{ selected.idemKey }}</span></span>
            <span v-else class="c-2">不支持（从不重试；结果未知需人工对账）</span>
          </a-descriptions-item>
          <a-descriptions-item label="超时"><span class="num">{{ selected.timeoutMs / 1000 }} 秒</span></a-descriptions-item>
          <a-descriptions-item label="限流">{{ selected.rateLimit }}</a-descriptions-item>
          <a-descriptions-item label="重试策略" :span="2">{{ selected.retry }}</a-descriptions-item>
          <a-descriptions-item label="负责人">{{ selected.owner }}</a-descriptions-item>
          <a-descriptions-item label="关联动作" :span="3">
            <template v-if="linked.length">
              <router-link v-for="a in linked" :key="a.id" :to="{ path: '/build/ontology/action-types', query: { action: a.id } }" class="ex-act">{{ a.name }}</router-link>
            </template>
            <span v-else-if="selected.usage" class="c-2">暂无关联动作类型 · {{ selected.usage.text }}</span>
            <span v-else class="c-3">暂无关联动作类型</span>
          </a-descriptions-item>
        </a-descriptions>
      </div>
      <div class="ex-calls-head">
        <span class="t-16 fw-600">最近调用</span>
        <a-radio-group v-model:value="result" size="small" button-style="solid">
          <a-radio-button v-for="k in ['全部', '成功', '失败', '结果未知']" :key="k" :value="k">{{ k }}</a-radio-button>
        </a-radio-group>
      </div>
      <div class="ex-table paged-scroll">
        <a-table size="small" row-key="id" :columns="cols" :loading="callsLoading" :data-source="paged.items" :pagination="false">
          <template #bodyCell="{ column, record }">
            <span v-if="column.key === 'time'" class="num">{{ record.time }}</span>
            <span v-else-if="column.key === 'target'" class="num">{{ record.target }}</span>
            <span v-else-if="column.key === 'idemKey'" class="num c-3">{{ record.idemKey ?? EMPTY }}</span>
            <span v-else-if="column.key === 'ms'" class="num">{{ record.ms }} ms</span>
            <span v-else-if="column.key === 'attempt'" class="num c-2">{{ record.attempt }}</span>
            <span v-else-if="column.key === 'http'" class="num" :class="record.http && record.http !== 200 ? 'tl-failed' : ''">{{ record.http ?? EMPTY }}</span>
            <a-tooltip v-else-if="column.key === 'actor'" :title="record.actor" placement="topLeft"><span class="ex-ell">{{ record.actor }}</span></a-tooltip>
            <span v-else-if="column.key === 'result'" class="ex-res" :class="tl(record.result)"><QuestionCircleOutlined v-if="record.result === '结果未知'" /><span v-else class="ex-dot" />{{ resultText(record.result) }}</span>
            <a v-else-if="column.key === 'op'" @click="openCall(record.id)">详情</a>
          </template>
        </a-table>
      </div>
      <AppPagination v-model:page="page" variant="simple" :total="callTotal" :size="SIZE" unit="次调用" class="ex-pager" />
    </div>
  </div>

  <a-drawer :open="!!call" :width="560" :title="call ? `调用详情 · ${call.id}` : ''" @close="closeCall">
    <template v-if="call">
      <div class="ex-dr-status" :class="tl(call.result)"><QuestionCircleOutlined v-if="call.result === '结果未知'" /><span v-else class="ex-dot" /><span class="t-16 fw-600">{{ resultText(call.result) }}</span></div>
      <div v-if="call.result === '结果未知'" class="t-12 c-2 ex-dr-note">
        处理方式：结果未知时不自动重试；请先在对方系统核实，再到动作中心人工对账，或按下方入口人工确认。
      </div>
      <a-descriptions :column="1" bordered size="small" class="ex-desc">
        <a-descriptions-item label="返回信息">{{ call.msg }}</a-descriptions-item>
        <a-descriptions-item label="系统">{{ selected.name }}（{{ selected.vendor }}）</a-descriptions-item>
        <a-descriptions-item label="动作">{{ call.action }}</a-descriptions-item>
        <a-descriptions-item label="对象"><span class="num">{{ call.target }}</span></a-descriptions-item>
        <a-descriptions-item label="发起人">{{ call.actor }}</a-descriptions-item>
        <a-descriptions-item v-if="call.runId" label="执行记录"><span class="num">{{ call.runId }}</span></a-descriptions-item>
        <a-descriptions-item label="时间"><span class="num">{{ call.time }}</span></a-descriptions-item>
        <a-descriptions-item label="请求"><span class="num">{{ call.method }} {{ call.path }}</span></a-descriptions-item>
        <a-descriptions-item label="幂等键"><span class="num">{{ call.idemKey ?? '不支持' }}</span></a-descriptions-item>
        <a-descriptions-item label="耗时 / 尝试"><span class="num">{{ call.ms }} ms · 第 {{ call.attempt }} 次</span></a-descriptions-item>
      </a-descriptions>
      <div class="t-14 fw-600 c-1 ex-dr-sub">请求体</div>
      <pre class="ex-pre num">{{ reqBody(call) }}</pre>
      <div class="t-14 fw-600 c-1 ex-dr-sub">响应</div>
      <pre class="ex-pre num">{{ respBody(call) }}</pre>
    </template>
    <template v-if="call && call.result === '结果未知'" #footer>
      <div class="ex-foot">
        <router-link v-if="call.runId" :to="reconcileLink(call)"><a-button type="link">去动作中心对账</a-button></router-link>
        <a-tooltip v-else :title="reason">
          <a-button type="primary" :disabled="readonly" @click="markChecked(call)">人工核对</a-button>
        </a-tooltip>
      </div>
    </template>
  </a-drawer>

  <a-drawer :open="!!editKey" :width="560" :title="editing ? `编辑外部系统 · ${editing.name}` : '新建外部系统'" @close="closeEdit">
    <a-form layout="vertical" class="ex-form">
      <div class="ex-row">
        <a-form-item label="系统名称" required class="ex-f2"><a-input v-model:value="form.name" placeholder="例如：仓储 WMS" :maxlength="20" /></a-form-item>
        <a-form-item label="类别" class="ex-f1"><a-select v-model:value="form.category" :options="CATEGORIES.map((v) => ({ value: v, label: v }))" /></a-form-item>
      </div>
      <div class="ex-row">
        <a-form-item label="供应商 / 产品" required class="ex-f2"><a-input v-model:value="form.vendor" placeholder="例如：富勒 FLUX" /></a-form-item>
        <a-form-item label="环境" class="ex-f1"><a-select v-model:value="form.env" :options="['生产', '测试'].map((v) => ({ value: v, label: v }))" /></a-form-item>
      </div>
      <a-form-item label="接口地址" required><a-input v-model:value="form.endpoint" class="num" /></a-form-item>
      <div class="ex-row">
        <a-form-item label="认证方式" class="ex-f2"><a-select v-model:value="form.auth" :options="AUTHS.map((v) => ({ value: v, label: v }))" /></a-form-item>
        <a-form-item label="负责人" class="ex-f1"><a-select v-model:value="form.owner" :options="OWNERS.map((v) => ({ value: v, label: v }))" /></a-form-item>
      </div>
      <a-form-item v-if="form.auth !== '无需授权'" label="凭证说明" extra="只写标识（如 AppKey、证书名），密钥在密钥库录入，界面不回显"><a-input v-model:value="form.authDetail" /></a-form-item>
      <div v-else class="t-12 c-3">不发送已配置的令牌；切回其他认证方式时会继续使用。消息签名按单独配置执行。</div>
      <div class="ex-row">
        <a-form-item label="幂等" class="ex-f1"><a-switch v-model:checked="form.idempotent" checked-children="支持" un-checked-children="不支持" /></a-form-item>
        <a-form-item label="幂等键" class="ex-f2"><a-input v-model:value="form.idemKey" :disabled="!form.idempotent" /></a-form-item>
      </div>
      <div class="ex-row">
        <a-form-item label="超时（毫秒）" class="ex-f1"><a-input-number v-model:value="form.timeoutMs" :min="1000" :max="60000" :step="1000" class="ex-w" /></a-form-item>
        <a-form-item label="明确失败时最多重试" class="ex-f1"><a-input-number v-model:value="form.maxRetry" :min="0" :max="5" :disabled="!form.idempotent" class="ex-w" /></a-form-item>
        <a-form-item label="限流" class="ex-f1"><a-input v-model:value="form.rateLimit" /></a-form-item>
      </div>
      <a-form-item label="状态"><a-radio-group v-model:value="form.status" :options="['正常', '降级', '停用'].map((v) => ({ value: v, label: v }))" /></a-form-item>
      <div class="t-12 c-3">重试规则：{{ retryText(form) }}</div>
      <div v-if="usedBy.length" class="t-12 c-2 ex-used">写回到该系统的动作类型：{{ usedBy.map((a) => a.name).join('、') }}</div>
    </a-form>
    <template #footer>
      <div class="ex-foot ex-foot-gap">
        <span v-if="formErrors.length" class="t-12 tl-failed ex-err">{{ formErrors[0] }}</span>
        <a-button @click="closeEdit">取消</a-button>
        <a-button type="primary" :loading="savingEdit" @click="saveEdit">保存</a-button>
      </div>
    </template>
  </a-drawer>
  <a-modal :open="!!probe" title="只读探测配置" ok-text="保存" :confirm-loading="probeBusy" :closable="!probeBusy" :mask-closable="!probeBusy" @cancel="probe = null" @ok="saveProbe">
    <template v-if="probe"><p class="t-12 c-3">来源地址：{{ probe.endpoint }}</p><a-form-item label="探测路径" extra="可留空以关闭探测；仅同源只读路径，实际探测还需部署网络白名单。业务动作授权另行配置。"><a-input v-model:value="probe.probePath" placeholder="例如 /health" /></a-form-item></template>
  </a-modal>
  <a-modal :open="!!authorization" title="动作授权" ok-text="保存授权" @cancel="authorization = null" @ok="saveAuthorization">
    <template v-if="authorization">
      <p>请选择已发布动作及部署允许的目标路径。</p>
      <div v-for="a in authorization.actions" :key="a.id" style="display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 12px">
        <a-checkbox :checked="authorization.selected.some(s => s.id === a.id)" @change="toggleAuthorization(a, $event.target.checked)">{{ a.name }} · {{ a.id }} · {{ a.version }}</a-checkbox>
        <a-select v-if="authorization.selected.find(s => s.id === a.id)" :value="authorization.selected.find(s => s.id === a.id)?.path" :options="authorization.paths.map(path => ({ label: path, value: path }))" style="min-width: 200px; flex: 1" @change="(path: string) => { const row = authorization!.selected.find(s => s.id === a.id); if (row) { row.path = path; row.version = a.version; } }" />
      </div>
      <p v-if="authorization.selected.some(s => !authorization!.actions.some(a => a.id === s.id))">部分原授权的动作已不再发布，保存时将移除。</p>
    </template>
  </a-modal>
</template>

<style scoped>
.ex-auth-row { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; margin-bottom: 12px; }
.ex-auth-row :deep(.ant-select) { min-width: 200px; flex: 1; }
.ex { flex: 1; min-height: 0; display: flex; gap: var(--gap); }
.ex-list { width: 320px; flex: none; }
.ex-items { flex: 1; min-height: 0; overflow: hidden; }
.ex-item { padding: 10px 16px; border-bottom: var(--border); border-left: 3px solid transparent; cursor: pointer; }
.ex-item:hover { background: var(--g2); }
.ex-item.on { background: var(--brand-bg); border-left-color: var(--brand); }
.ex-l1 { display: flex; align-items: center; gap: 8px; min-width: 0; }
.ex-cat { flex: none; min-width: 36px; text-align: center; border: var(--border); border-radius: 4px; color: var(--g8); line-height: 18px; padding: 0 4px; }
.ex-ellip { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; min-width: 0; }
.ex-badge { margin-left: auto; flex: none; }
.ex-detail { flex: 1; min-width: 0; }
.ex-head { display: flex; justify-content: space-between; align-items: center; padding: 14px 20px 10px; }
.ex-title { display: flex; align-items: center; gap: 10px; }
.ex-cfg { padding: 0 20px; flex: none; }
.ex-desc :deep(.ant-descriptions-item-label) { width: 120px; }
.ex-calls-head { display: flex; justify-content: space-between; align-items: center; padding: 14px 20px 8px; flex: none; }
.ex-table { flex: 1; min-height: 0; padding: 0 20px; }
.ex-pager { padding: 6px 20px 8px; }
.ex-res { display: inline-flex; align-items: center; gap: 6px; }
.ex-dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; background: currentColor; }
.ex-dr-status { display: flex; align-items: center; gap: 8px; }
.ex-dr-msg { margin: 4px 0 12px 16px; }
.ex-dr-note { margin: -4px 0 12px 16px; padding: 8px 12px; background: var(--g2); border-radius: var(--radius); }
.ex-dr-sub { margin: 16px 0 6px; }
.ex-pre { background: var(--g2); border: var(--border); border-radius: var(--radius); padding: 8px 12px; margin: 0; color: var(--g9); white-space: pre-wrap; }
.ex-foot { display: flex; justify-content: flex-end; }
.ex-foot-gap { gap: 8px; align-items: center; }
.ex-err { margin-right: auto; }
.ex-act { margin-right: 12px; color: var(--brand); }
.ex-row { display: flex; gap: 12px; }
.ex-f1 { flex: 1; min-width: 0; }
.ex-f2 { flex: 2; min-width: 0; }
.ex-w { width: 100%; }
.ex-used { margin-top: 6px; }
.ex-form :deep(.ant-form-item) { margin-bottom: 12px; }
.ex-ell { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
</style>
