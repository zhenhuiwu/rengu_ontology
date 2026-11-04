<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { message } from 'ant-design-vue';
import AppPagination from '@/components/AppPagination.vue';
import InfoTip from '@/components/InfoTip.vue';
import FunctionInput from './FunctionInput.vue';
import { useDefinitionDelete } from '@/composables/useDefinitionDelete';
import { useConfirmStore } from '@/stores/confirm';
import { useReadonly } from '@/composables/useReadonly';
import { confirmDiscard, useUnsavedGuard } from '@/composables/useUnsavedGuard';
import { currentApiContext, isApiError, type RequestOptions } from '@/api/http';
import { consumeAssistantPrefill, isAssistantPrefillCurrent } from '@/api/assistant';
import { invalidateStaticAfterWrite } from '@/api/static';
import { listFunctions, getFunction, functionVersions, createFunction, saveFunction, rollbackFunction, testFunction, functionTargets, type FunctionRow, type FunctionSave, type FnVersion, type RemoteFunctionTarget, type FunctionSchema } from '@/api/functions';

const route = useRoute(), router = useRouter(), confirm = useConfirmStore();
const { readonly, reason } = useReadonly();
let alive = true;
onBeforeUnmount(() => { alive = false; });
const q = (key: string) => typeof route.query[key] === 'string' ? route.query[key] as string : '';
const setQuery = (patch: Record<string, string | undefined>) => router.replace({ query: { ...route.query, ...patch } });
const context = () => JSON.stringify(currentApiContext());
function captured() {
  const ctx = { ...currentApiContext() }, key = JSON.stringify(ctx);
  const options: RequestOptions = { readonlyParams: false, query: { ver: ctx.ver, previewAs: ctx.previewAs }, headers: { 'X-Demo-As': ctx.demoAs ?? '', 'X-Ontology-Id': ctx.ontologyId ?? '' } };
  return { key, options, current: () => alive && key === context() };
}
const fail = (e: unknown) => isApiError(e) ? `${e.message}（${e.code}）` : e instanceof Error ? e.message : String(e);
const items = ref<FunctionRow[]>([]), total = ref(0), lpage = ref(1), kw = ref(''), statusF = ref('全部');
const loading = ref(false), loadError = ref(''), fn = ref<FunctionRow>(), detailError = ref('');
const detailContext = ref('');
const versions = ref<FnVersion[]>([]), versionTotal = ref(0), vpage = ref(1), versionError = ref('');
const PAGE_SIZE = 10;
const listScope = ref(''), versionScope = ref('');
const tab = computed({ get: () => q('tab') || 'def', set: (v: string) => setQuery({ tab: v === 'def' ? undefined : v }) });
let listSeq = 0, detailSeq = 0, versionSeq = 0;
async function loadList() {
  const c = captured(), seq = ++listSeq;
  const scope = JSON.stringify([c.key, kw.value, statusF.value]);
  if (listScope.value !== scope) { listScope.value = scope; total.value = 0; }
  loading.value = true; items.value = []; loadError.value = '';
  try {
    const data = await listFunctions({ kw: kw.value, status: statusF.value, page: lpage.value, size: PAGE_SIZE }, c.options);
    if (!c.current() || seq !== listSeq) return;
    items.value = data.items; total.value = data.total;
    if (lpage.value !== data.page) lpage.value = data.page;
    if (!q('fn') && data.items[0]) await setQuery({ fn: data.items[0].id });
  } catch (e) { if (c.current() && seq === listSeq) loadError.value = fail(e); }
  finally { if (c.current() && seq === listSeq) loading.value = false; }
}
async function loadDetail() {
  const c = captured(), id = q('fn'), seq = ++detailSeq;
  fn.value = undefined; detailContext.value = ''; detailError.value = ''; testOut.value = null; testError.value = ''; testInput.value = '{}';
  if (!id) return;
  try {
    const data = await getFunction(id, c.options);
    if (!c.current() || seq !== detailSeq || id !== q('fn')) return;
    fn.value = data; detailContext.value = c.key; resetTest();
  } catch (e) { if (c.current() && seq === detailSeq) detailError.value = fail(e); }
}
async function loadVersions() {
  const c = captured(), id = q('fn'), seq = ++versionSeq;
  const scope = JSON.stringify([c.key, id]);
  if (versionScope.value !== scope) { versionScope.value = scope; versionTotal.value = 0; }
  versions.value = []; versionError.value = '';
  if (!id) return;
  try {
    const data = await functionVersions(id, { ...c.options, query: { ...c.options.query, page: vpage.value, size: PAGE_SIZE } });
    if (!c.current() || seq !== versionSeq || id !== q('fn')) return;
    versions.value = data.items; versionTotal.value = data.total;
    if (vpage.value !== data.page) vpage.value = data.page;
  } catch (e) { if (c.current() && seq === versionSeq) versionError.value = fail(e); }
}
watch([kw, statusF], () => { lpage.value = 1; });
watch(() => JSON.stringify([context(), kw.value, statusF.value, lpage.value]), () => { void loadList(); }, { immediate: true });
watch(() => JSON.stringify([context(), q('fn')]), () => { vpage.value = 1; void loadDetail(); });
watch(() => JSON.stringify([context(), q('fn'), vpage.value]), () => { void loadVersions(); });

const testInput = ref('{}'), testOut = ref<{ output: unknown; ms: number; at: string } | null>(null), testError = ref(''), running = ref(false);
const remoteInput = ref<Record<string, unknown>>({});
function initialInput(schema: FunctionSchema): unknown {
  if (schema.enum?.length) return schema.enum[0];
  if (schema.type === 'object') return Object.fromEntries(Object.entries(schema.properties ?? {}).filter(([name]) => schema.required?.includes(name)).map(([name, field]) => [name, initialInput(field)]));
  return schema.type === 'array' ? [] : schema.type === 'string' ? '' : schema.type === 'boolean' ? false : schema.type === 'null' ? null : 0;
}
function resetTest() { testInput.value = JSON.stringify(fn.value?.sampleIn ?? {}, null, 2); remoteInput.value = fn.value?.remote?.inputSchema ? initialInput(fn.value.remote.inputSchema) as Record<string, unknown> : {}; testOut.value = null; testError.value = ''; }
function testState() { try { return JSON.stringify(fn.value?.runtimeMode === 'remote_http' ? remoteInput.value : JSON.parse(testInput.value)); } catch { return ''; } }
async function runTest() {
  if (running.value || readonly.value || !fn.value || detailContext.value !== context()) return;
  let input: Record<string, unknown>;
  try { input = fn.value.runtimeMode === 'remote_http' ? remoteInput.value : JSON.parse(testInput.value); if (!input || Array.isArray(input) || typeof input !== 'object') throw new Error(); }
  catch { message.error('输入须为合法的 JSON 对象'); return; }
  const c = captured(), id = fn.value.id, inputText = JSON.stringify(input), version = fn.value.version;
  running.value = true; testOut.value = null; testError.value = '';
  try { const result = await testFunction(id, input, c.options, version === '草稿' ? undefined : version); if (c.current() && id === q('fn') && fn.value?.version === version && inputText === testState()) testOut.value = result; }
  catch (e) { if (c.current() && id === q('fn')) testError.value = fail(e); }
  finally { running.value = false; }
}

const blankForm = () => ({ name: '', title: '', desc: '', timeoutS: 5, code: '', runtimeMode: 'local' as 'local' | 'remote_http', target: '' });
const form = reactive(blankForm());
const targets = ref<RemoteFunctionTarget[]>([]), targetsError = ref(''), targetsLoading = ref(false);
const selectedTarget = computed(() => targets.value.find(target => `${target.key}@${target.revision}` === form.target));
let targetsSeq = 0;
watch(context, async () => {
  const c = captured(), seq = ++targetsSeq;
  targets.value = []; targetsError.value = ''; targetsLoading.value = true;
  try { const data = await functionTargets(c.options); if (c.current() && seq === targetsSeq) targets.value = data.items; }
  catch (e) { if (c.current() && seq === targetsSeq) targetsError.value = fail(e); }
  finally { if (c.current() && seq === targetsSeq) targetsLoading.value = false; }
}, { immediate: true });
function chooseTarget(value: string) { form.target = value; if (selectedTarget.value) form.timeoutS = Math.min(form.timeoutS, selectedTarget.value.timeoutS); }
function sourceForm(source: FunctionRow) { return { name: source.name, title: source.title, desc: source.desc, timeoutS: source.timeoutS, code: source.code, runtimeMode: source.runtimeMode ?? 'local', target: source.remote ? `${source.remote.targetKey}@${source.remote.revision}` : '' }; }
const formInit = ref(''), formContext = ref(''), saving = ref(false), saveError = ref('');
const isNew = computed(() => q('form') === 'new');
const formOpen = computed(() => q('form') === 'edit' || isNew.value);
const formDirty = computed(() => formOpen.value && JSON.stringify(form) !== formInit.value);
const guard = useUnsavedGuard(formDirty);
watch(() => JSON.stringify([context(), formOpen.value, isNew.value, isNew.value ? null : fn.value]), () => {
  if (!formOpen.value) return;
  if (formContext.value && formContext.value !== context() && formDirty.value) {
    saveError.value = '当前身份或本体已切换，输入已保留；请复制内容后重新打开表单。';
    return;
  }
  if (isNew.value) Object.assign(form, blankForm());
  else if (fn.value) {
    const source = fn.value;
    Object.assign(form, sourceForm(source));
  }
  formInit.value = JSON.stringify(form); formContext.value = context(); saveError.value = '';
});
let assistantRequestId = 0;
watch(context, () => { assistantRequestId++; }, { flush: 'sync' });
async function receiveAssistantDraft() {
  const proposal = consumeAssistantPrefill('function', currentApiContext().ontologyId ?? '');
  if (!proposal) return;
  const c = captured(), key = c.key, path = route.fullPath, formText = JSON.stringify(form), request = ++assistantRequestId;
  const current = () => request === assistantRequestId && c.current() && isAssistantPrefillCurrent(proposal) && !readonly.value && !saving.value;
  if (readonly.value || saving.value) { message.warning('当前为只读或正在保存，未载入 AI 建议'); return; }
  if (formDirty.value && !(await confirmDiscard())) { message.info('已保留当前输入，可从助手重新采用建议'); return; }
  if (!current() || path !== route.fullPath || formText !== JSON.stringify(form)) { message.info('当前上下文或输入已变化，未载入 AI 建议'); return; }
  if (proposal.edit) {
    const edit = proposal.edit;
    try {
      const fresh = await getFunction(edit.targetId, c.options);
      if (!current() || path !== route.fullPath || formText !== JSON.stringify(form) || q('fn') !== edit.targetId || fresh.lockVersion !== edit.lockVersion || fresh.version !== edit.version) { message.error('函数版本或上下文已变化，请重新生成建议'); return; }
      fn.value = fresh; detailContext.value = key;
      await setQuery({ form: 'edit' }); await nextTick();
      if (!current() || q('fn') !== edit.targetId || q('form') !== 'edit') return;
      if (fresh.runtimeMode === 'remote_http') { message.warning('远程函数请在表单中选择获准目标及修订'); return; }
      Object.assign(form, sourceForm(fresh));
      formInit.value = JSON.stringify(form); formContext.value = key;
      for (const field of ['title', 'desc', 'code'] as const) if (typeof proposal.draft[field] === 'string') form[field] = proposal.draft[field] as string;
      if (typeof proposal.draft.timeoutS === 'number') form.timeoutS = proposal.draft.timeoutS;
      message.info('已载入函数修改建议，请核对后保存新版本');
    } catch (e) { if (current()) message.error(fail(e)); }
    return;
  }
  await setQuery({ form: 'new' });
  await nextTick();
  if (!current() || !isNew.value) return;
  Object.assign(form, blankForm());
  formInit.value = JSON.stringify(form); formContext.value = key;
  for (const field of ['name', 'title', 'desc', 'code'] as const) {
    if (typeof proposal.draft[field] === 'string') form[field] = proposal.draft[field] as string;
  }
  if (typeof proposal.draft.timeoutS === 'number') form.timeoutS = proposal.draft.timeoutS;
  message.info('AI 建议已填入表单，请核对后保存');
}
function assistEdit() {
  if (!fn.value || fn.value.runtimeMode === 'remote_http' || readonly.value || saving.value || detailContext.value !== context()) return;
  const f = fn.value;
  window.dispatchEvent(new CustomEvent('assistant-edit-function', { detail: { targetId: f.id, lockVersion: f.lockVersion, version: f.version, name: f.name, base: { title: f.title, desc: f.desc, timeoutS: f.timeoutS, code: f.code } } }));
}
onMounted(() => { window.addEventListener('assistant-prefill', receiveAssistantDraft); void receiveAssistantDraft(); });
onBeforeUnmount(() => window.removeEventListener('assistant-prefill', receiveAssistantDraft));
const closeForm = async () => { if (saving.value) return; if (formDirty.value && !(await confirmDiscard())) return; await setQuery({ form: undefined }); };
type Intent = { kind: 'save'; name: string; body: FunctionSave; fresh: boolean; id?: string; formText: string } | { kind: 'rollback'; id: string; version: string; lockVersion: number };
const intent = ref<Intent>(), intentContext = ref('');
function currentIntent(value: Intent, key: string) { return alive && key === context() && (value.kind === 'rollback' ? value.id === q('fn') : value.formText === JSON.stringify(form) && (value.fresh || value.id === q('fn'))); }
async function persist(value: Intent) {
  if (saving.value) return;
  if (readonly.value || !currentIntent(value, intentContext.value)) { saveError.value = '当前上下文或表单已变化，输入已保留；请重新打开表单后再保存。'; return; }
  const c = captured();
  saving.value = true; saveError.value = '';
  try {
    const result = value.kind === 'rollback'
      ? await rollbackFunction(value.id, { toVersion: value.version, lockVersion: value.lockVersion }, c.options)
      : value.fresh ? await createFunction({ ...value.body, name: value.name }, c.options)
        : await saveFunction(value.id!, value.body, c.options);
    if (!c.current() || !currentIntent(value, intentContext.value)) { saveError.value = '保存请求已返回，但上下文或输入已变化；请刷新核对结果，输入已保留。'; return; }
    if (result.status !== '已发布') throw new Error('函数未发布，请查看当前状态');
    invalidateStaticAfterWrite(result.ver);
    if (value.kind === 'save') formInit.value = JSON.stringify(form);
    await setQuery({ fn: result.id, form: undefined });
    message.success('函数已保存并生效');
    await Promise.all([loadList(), loadDetail(), loadVersions()]);
  } catch (e) { saveError.value = fail(e); if (c.current()) message.error(saveError.value); }
  finally { saving.value = false; }
}
async function save() {
  if (saving.value) return;
  if (readonly.value || formContext.value !== context()) { saveError.value = '当前身份、本体或版本已变化，输入已保留；请重新打开表单后再保存。'; return; }
  if (!form.name.trim() || !form.title.trim() || (form.runtimeMode === 'local' ? !form.code.trim() : !selectedTarget.value)) { message.warning('请填写函数名、中文名，并完善当前执行模式配置'); return; }
  const value: Intent = { kind: 'save', name: form.name.trim(), fresh: isNew.value, id: fn.value?.id,
    body: { title: form.title.trim(), desc: form.desc, code: form.runtimeMode === 'local' ? form.code : '', runtimeMode: form.runtimeMode, remote: form.runtimeMode === 'remote_http' && selectedTarget.value ? { targetKey: selectedTarget.value.key, revision: selectedTarget.value.revision } : null, timeoutS: form.timeoutS, lockVersion: isNew.value ? undefined : fn.value?.lockVersion }, formText: JSON.stringify(form) };
  intent.value = value; intentContext.value = context();
  await persist(value);
}
async function rollback(version: FnVersion) {
  if (saving.value || readonly.value || !fn.value) return;
  const c = captured(), id = fn.value.id;
  saving.value = true;
  const ok = await confirm.ask({ title: '恢复函数版本', target: `${fn.value.name} ${version.ver}`, impact: '恢复后将生成新版本并立即生效。' });
  saving.value = false;
  if (!ok || !c.current() || id !== q('fn')) return;
  const value: Intent = { kind: 'rollback', id, version: version.ver, lockVersion: fn.value.lockVersion };
  intent.value = value; intentContext.value = c.key;
  await persist(value);
}
const signature = computed(() => fn.value ? `def ${fn.value.name}(${fn.value.inputs.map(p => `${p.name}: ${p.type}`).join(', ')}) -> ${fn.value.output.type}` : '');
const contractColumns = [{ title: '字段', dataIndex: 'name' }, { title: '类型', dataIndex: 'type' }, { title: '说明', dataIndex: 'desc' }];
function contractRows(schema: FunctionSchema | undefined, prefix: string): { name: string; type: string; desc: string }[] {
  if (!schema) return [];
  const rows: { name: string; type: string; desc: string }[] = [{ name: prefix, type: schema.type, desc: schema.description ?? '' }];
  for (const [name, child] of Object.entries(schema.properties ?? {})) rows.push(...contractRows(child, `${prefix}.${name}${schema.required?.includes(name) ? ' *' : ''}`));
  if (schema.items) rows.push(...contractRows(schema.items, `${prefix}[]`));
  return rows;
}
const vcols = [{ title: '版本', dataIndex: 'ver', key: 'ver', width: 100 }, { title: '时间', dataIndex: 'time', key: 'time', width: 150 }, { title: '作者', dataIndex: 'author', key: 'author', width: 90 }, { title: '说明', dataIndex: 'note', key: 'note', ellipsis: true }, { title: '状态', dataIndex: 'status', key: 'status', width: 80 }, { title: '操作', key: 'op', width: 120 }];
// Immediate detail loading follows ref initialization, including direct function URL entry.
void loadDetail(); void loadVersions();
const deletion = useDefinitionDelete('function', {
  target: () => fn.value ? { id: fn.value.id, name: `${fn.value.title}（${fn.value.name}）` } : undefined,
  enabled: () => !readonly.value && !saving.value && !running.value && !formOpen.value,
  context,
  afterDelete: async (id, current) => {
    ++detailSeq; ++versionSeq;
    fn.value = undefined; versions.value = []; versionTotal.value = 0; detailError.value = ''; versionError.value = '';
    items.value = items.value.filter(item => item.id !== id);
    await setQuery({ fn: undefined, form: undefined });
    if (current()) await loadList();
  },
});
</script>

<template>
  <Teleport defer to="#page-extra"><a-tooltip :title="readonly ? reason : deletion.error.value || '删除当前函数'"><a-button danger :disabled="!deletion.allowed.value" :loading="deletion.busy.value" @click="deletion.run">删除</a-button></a-tooltip><a-tooltip :title="reason"><a-button type="primary" :disabled="readonly || saving" @click="guard(() => setQuery({ form: 'new' }))">新建函数</a-button></a-tooltip></Teleport>
  <div class="fn">
    <div class="panel fn-list side-list">
      <div class="fn-filters"><a-input-search v-model:value="kw" placeholder="搜索函数" allow-clear /><a-select v-model:value="statusF" class="fn-sel" :options="['全部', '已发布', '草稿', '已停用'].map(v => ({ value: v, label: v }))" /></div>
      <a-alert v-if="deletion.error.value || loadError" type="error" :message="deletion.error.value || loadError" show-icon />
      <a-spin :spinning="loading" class="fn-items side-list-items"><a-empty v-if="!items.length && !loading" description="没有函数记录" /><div v-for="item in items" :key="item.id" class="fn-item side-list-item" :class="{ on: item.id === q('fn'), active: item.id === q('fn') }" @click="guard(() => setQuery({ fn: item.id, form: undefined }))"><div class="fn-l1 side-list-row"><span class="t-14 num fn-ellip side-list-name">{{ item.name }}</span><a-badge status="default" :text="item.status" /></div></div></a-spin>
      <div class="fn-pager-foot side-list-foot">
        <AppPagination v-model:page="lpage" variant="simple" :total="total" :size="PAGE_SIZE" unit="个函数" />
      </div>
    </div>
    <div class="panel fn-detail">
      <a-result v-if="detailError" status="error" title="函数加载失败" :sub-title="detailError" />
      <a-empty v-else-if="!fn" description="请选择函数" />
      <template v-else>
        <div class="fn-head"><div class="fn-title"><span class="t-20 fw-600 c-1 num">{{ fn.name }}</span><span class="t-16 c-2">{{ fn.title }}<InfoTip :text="fn.desc" /></span><a-tag>{{ fn.runtime }}</a-tag><a-tag>{{ fn.version }}</a-tag><a-badge status="default" :text="fn.status" /></div><a-space><a-button @click="tab = 'test'">测试运行</a-button><a-tooltip :title="reason"><a-button :disabled="readonly || saving || fn.status !== '已发布' || fn.runtimeMode === 'remote_http'" @click="assistEdit">AI 辅助修改</a-button></a-tooltip><a-tooltip :title="reason"><a-button :disabled="readonly || saving" @click="setQuery({ form: 'edit' })">编辑</a-button></a-tooltip></a-space></div>
        <div class="t-12 c-2 fn-sub">负责人 {{ fn.owner }} · 更新于 <span class="num">{{ fn.updatedAt }}</span><span v-if="fn.pending"> · 变更中：<a @click="router.push({ path: '/build/ontology/releases', query: { cr: fn.pending.cr } })">{{ fn.pending.cr }}</a>（{{ fn.pending.status }}）</span></div>
        <a-tabs v-model:active-key="tab" class="fn-tabs"><a-tab-pane key="def" tab="定义" /><a-tab-pane key="test" tab="测试运行" /><a-tab-pane key="versions" tab="版本历史" /></a-tabs>
        <div v-if="tab === 'def'" class="fn-body fn-def"><div class="fn-col"><div class="t-14 fw-600 c-1 fn-h">签名</div><pre class="fn-pre num">{{ signature }}</pre><div class="t-14 fw-600 c-1 fn-h">入参</div><a-table size="small" row-key="name" :pagination="false" :data-source="fn.inputs" :columns="[{ title: '参数', dataIndex: 'name' }, { title: '类型', dataIndex: 'type' }, { title: '说明', dataIndex: 'desc' }]" /><div class="t-14 fw-600 c-1 fn-h">出参</div><a-descriptions :column="1" bordered size="small"><a-descriptions-item label="类型">{{ fn.output.type }}</a-descriptions-item><a-descriptions-item label="说明">{{ fn.output.desc || '—' }}</a-descriptions-item></a-descriptions><template v-if="fn.runtimeMode === 'remote_http'"><div class="t-14 fw-600 c-1 fn-h">远程计算合同</div><p>{{ fn.remote?.targetKey }} · {{ fn.remote?.revision }}</p><a-table size="small" row-key="name" :pagination="false" :columns="contractColumns" :data-source="contractRows(fn.remote?.inputSchema, 'input')" /><a-table size="small" row-key="name" :pagination="false" :columns="contractColumns" :data-source="contractRows(fn.remote?.outputSchema, 'output')" /></template><template v-else><div class="t-14 fw-600 c-1 fn-h">代码</div><pre class="fn-pre num">{{ fn.code }}</pre></template></div><div class="fn-side"><div class="t-14 fw-600 c-1 fn-h">被引用（{{ fn.usedBy.length }}）</div><div v-if="!fn.usedBy.length" class="t-12 c-3">没有动作或规则引用记录</div><div v-for="use in fn.usedBy" :key="`${use.kind}:${use.name}`"><a-tag>{{ use.kind }}</a-tag>{{ use.name }}</div><div class="t-14 fw-600 c-1 fn-h">运行参数</div><a-descriptions :column="1" bordered size="small"><a-descriptions-item label="执行方式">{{ fn.runtime }}</a-descriptions-item><a-descriptions-item label="超时上限">{{ fn.timeoutS }} 秒{{ fn.runtimeMode === 'remote_http' ? '（同时受目标部署上限约束）' : '（纯计算预算最多 1 秒）' }}</a-descriptions-item><a-descriptions-item label="平均耗时">{{ fn.avgMs || '—' }}</a-descriptions-item><a-descriptions-item label="近 7 天调用">{{ fn.calls7d }} 次</a-descriptions-item></a-descriptions><p class="t-12 c-3">测试不计入生产调用统计。动作可绑定已发布的固定函数版本。</p></div></div>
        <div v-else-if="tab === 'test'" class="fn-body fn-test"><div class="fn-tcol"><div class="fn-thead"><span class="t-14 fw-600">输入</span><a @click="resetTest">恢复登记示例</a></div><div v-if="fn.runtimeMode === 'remote_http' && fn.remote?.inputSchema" class="fn-remote-input"><FunctionInput v-model="remoteInput" :schema="fn.remote.inputSchema" :disabled="running || readonly" /></div><a-textarea v-else v-model:value="testInput" class="fn-ta num" :auto-size="false" /><div class="fn-trun"><span class="t-12 c-3">{{ fn.runtimeMode === 'remote_http' ? '仅请求获准远程纯计算服务，不执行平台动作' : '仅 JSON 纯计算，无数据库或网络访问' }}</span><a-tooltip :title="reason"><a-button :loading="running" :disabled="readonly || saving" @click="runTest">运行</a-button></a-tooltip></div><p v-if="fn.runtimeMode === 'remote_http'" class="t-12 c-3">按固定修订及输入输出合同校验；输入与响应最多 64 KiB，超时或失败不自动重试。</p><p v-else class="t-12 c-3">支持 return、简单赋值、if、算术比较、字典/列表和下标；不支持导入、调用、属性访问、循环或空实现。源码/输入/结果各最多 64 KiB。</p></div><div class="fn-tcol"><div class="fn-thead"><span class="t-14 fw-600">{{ testOut ? '本次运行结果' : '登记示例输出' }}</span><span v-if="testOut" class="t-12 c-2">成功 · {{ testOut.ms }} ms · {{ testOut.at }}</span><span v-else class="t-12 c-3">示例不代表实际运行</span></div><a-alert v-if="testError" type="error" :message="testError" show-icon /><pre class="fn-pre fn-out num">{{ testOut ? JSON.stringify(testOut.output, null, 2) : JSON.stringify(fn.sampleOut, null, 2) }}</pre></div></div>
        <div v-else class="fn-body fn-vers"><a-alert v-if="versionError" type="error" :message="versionError" show-icon /><div class="fn-vtable"><a-table size="small" row-key="ver" :columns="vcols" :data-source="versions" :pagination="false"><template #bodyCell="{ column, record }"><span v-if="column.key === 'op'"><a v-if="record.cr" @click="router.push({ path: '/build/ontology/releases', query: { cr: record.cr } })">查看 {{ record.cr }}</a><a-button v-else-if="record.status === '历史'" type="link" size="small" :disabled="readonly || saving" @click="rollback(record)">恢复此版本</a-button></span></template></a-table></div><AppPagination v-model:page="vpage" variant="simple" :total="versionTotal" :size="PAGE_SIZE" unit="个版本" class="fn-pager" /></div>
      </template>
    </div>
  </div>
  <a-drawer :open="formOpen" :width="640" :title="isNew ? '新建函数' : `编辑函数 · ${fn?.name ?? ''}`" @close="closeForm"><a-alert v-if="saveError" type="error" :message="saveError" show-icon /><a-form layout="vertical" :model="form"><a-form-item label="函数名" required><a-input v-model:value="form.name" :disabled="!isNew || saving" placeholder="英文小写加下划线，如 calc_score" /></a-form-item><a-form-item label="中文名" required><a-input v-model:value="form.title" :disabled="saving" :maxlength="128" /></a-form-item><a-form-item label="说明"><a-textarea v-model:value="form.desc" :disabled="saving" :rows="2" /></a-form-item><a-form-item label="执行模式"><a-select v-model:value="form.runtimeMode" :disabled="saving" :options="[{value:'local',label:'本地纯计算'}, {value:'remote_http',label:'受控远程纯计算'}]" /></a-form-item><template v-if="form.runtimeMode === 'remote_http'"><a-alert v-if="targetsError" type="error" :message="targetsError" show-icon /><a-alert v-else-if="!targets.length && !targetsLoading" type="info" message="当前本体尚未配置获准的远程计算目标，请联系部署管理员。" show-icon /><a-form-item label="获准目标与实现修订" required><a-select :value="form.target || undefined" :disabled="saving" :loading="targetsLoading" :options="targets.map(target => ({value:`${target.key}@${target.revision}`,label:`${target.label} · ${target.revision}`}))" @change="chooseTarget" /></a-form-item><template v-if="selectedTarget"><a-table size="small" row-key="name" :pagination="false" :columns="contractColumns" :data-source="[...contractRows(selectedTarget.inputSchema, 'input'), ...contractRows(selectedTarget.outputSchema, 'output')]" /><p class="t-12 c-3">合同由部署目标提供，随函数版本固定。这里只选择目标，不接收服务地址或密钥。</p></template></template><a-form-item label="超时（秒）"><a-input-number v-model:value="form.timeoutS" :disabled="saving" :min="1" :max="form.runtimeMode === 'remote_http' ? selectedTarget?.timeoutS ?? 60 : 60" /></a-form-item><a-form-item v-if="form.runtimeMode === 'local'" label="代码" required><a-textarea v-model:value="form.code" :disabled="saving" :rows="10" class="num" placeholder="def calc_score(value: float) -> float:&#10;    return value * 2" /></a-form-item></a-form><p class="t-12 c-3">{{ form.runtimeMode === 'remote_http' ? '入参和出参取自获准目标的固定合同。' : '入参和出参类型取自代码签名。' }}保存后立即生效。<template v-if="!isNew">已绑定固定版本的动作继续使用原版本，需要时请在动作配置中更新。</template></p><template #footer><div class="fn-foot"><a-button :disabled="saving" @click="closeForm">取消</a-button><a-button type="primary" :loading="saving" :disabled="readonly || !formDirty || (!isNew && !fn)" @click="save">保存</a-button></div></template></a-drawer>
</template>

<style scoped>
.fn { flex: 1; min-height: 0; display: flex; gap: var(--gap); }
.fn-list { flex: none; }
.fn-filters { display: flex; gap: 8px; padding: 12px 12px 8px; flex: none; }
.fn-sel { width: 100px; flex: none; }
.fn-items { flex: 1; min-height: 0; }
.fn-l1 { display: flex; align-items: center; justify-content: space-between; gap: 8px; min-width: 0; }
.fn-ellip { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; min-width: 0; }
.fn-item :deep(.ant-badge-status-text) { color: var(--g9); font-size: 12px; line-height: 18px; font-weight: 400; }
.fn-detail { flex: 1; min-width: 0; }
.fn-head { display: flex; flex-wrap: wrap; justify-content: space-between; align-items: center; gap: 8px; padding: 14px 20px 4px; }
.fn-title { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; min-width: 0; }
.fn-title > span { max-width: 100%; overflow-wrap: anywhere; }
.fn-sub { padding: 0 20px; }
.fn-tabs { padding: 0 20px; flex: none; }
.fn-tabs :deep(.ant-tabs-nav) { margin-bottom: 0; }
.fn-body { flex: 1; min-height: 0; padding: 12px 20px 16px; }
.fn-def { display: flex; gap: 24px; overflow: auto; }
.fn-col { flex: 1; min-width: 0; }
.fn-side { width: 320px; flex: none; }
.fn-h { margin: 12px 0 6px; }
.fn-h:first-child { margin-top: 0; }
.fn-pre { background: var(--g2); border: var(--border); border-radius: 4px; padding: 10px 12px; white-space: pre-wrap; overflow-wrap: anywhere; }
.fn-test { display: flex; gap: 20px; }
.fn-tcol { flex: 1; min-width: 0; min-height: 0; display: flex; flex-direction: column; }
.fn-thead { display: flex; justify-content: space-between; gap: 12px; align-items: center; padding: 0 0 8px; }
.fn-ta { flex: 1; min-height: 120px; resize: none; }
.fn-trun { display: flex; justify-content: space-between; align-items: center; margin-top: 8px; }
.fn-out { flex: 1; min-height: 0; overflow: auto; margin: 0; }
.fn-vers { display: flex; flex-direction: column; }
.fn-vtable { flex: 1; min-height: 0; overflow: auto; }
.fn-foot { display: flex; justify-content: flex-end; gap: 8px; }
</style>

<style scoped>
.fn-remote-input { flex: 1; min-height: 0; overflow: auto; padding: 12px 0; }
</style>
