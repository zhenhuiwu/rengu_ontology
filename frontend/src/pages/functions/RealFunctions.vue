<script setup lang="ts">
import { computed, onBeforeUnmount, reactive, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { message } from 'ant-design-vue';
import AppPagination from '@/components/AppPagination.vue';
import InfoTip from '@/components/InfoTip.vue';
import StartChangeModal from '@/pages/ontology/parts/StartChangeModal.vue';
import { useConfirmStore } from '@/stores/confirm';
import { useReadonly } from '@/composables/useReadonly';
import { confirmDiscard, useUnsavedGuard } from '@/composables/useUnsavedGuard';
import { currentApiContext, isApiError, type RequestOptions } from '@/api/http';
import { fetchCR } from '@/api/static';
import { listFunctions, getFunction, functionVersions, createFunction, saveFunction, rollbackFunction, testFunction, type FunctionRow, type FunctionSave, type FnVersion } from '@/api/functions';

const route = useRoute(), router = useRouter(), confirm = useConfirmStore();
const { readonly, reason } = useReadonly();
let alive = true;
onBeforeUnmount(() => { alive = false; });
const q = (key: string) => typeof route.query[key] === 'string' ? route.query[key] as string : '';
const setQuery = (patch: Record<string, string | undefined>) => router.replace({ query: { ...route.query, ...patch } });
const context = () => JSON.stringify(currentApiContext());
function captured() {
  const ctx = { ...currentApiContext() }, key = JSON.stringify(ctx);
  const options: RequestOptions = { readonlyParams: false, query: { ver: ctx.ver, previewAs: ctx.previewAs }, headers: { 'X-Demo-As': ctx.demoAs ?? '' } };
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
function resetTest() { testInput.value = JSON.stringify(fn.value?.sampleIn ?? {}, null, 2); testOut.value = null; testError.value = ''; }
async function runTest() {
  if (running.value || readonly.value || !fn.value || detailContext.value !== context()) return;
  let input: Record<string, unknown>;
  try { input = JSON.parse(testInput.value); if (!input || Array.isArray(input) || typeof input !== 'object') throw new Error(); }
  catch { message.error('输入须为合法的 JSON 对象'); return; }
  const c = captured(), id = fn.value.id, inputText = testInput.value;
  running.value = true; testOut.value = null; testError.value = '';
  try { const result = await testFunction(id, input, c.options); if (c.current() && id === q('fn') && inputText === testInput.value) testOut.value = result; }
  catch (e) { if (c.current() && id === q('fn')) testError.value = fail(e); }
  finally { running.value = false; }
}

const form = reactive({ name: '', title: '', desc: '', timeoutS: 5, code: '' });
const formInit = ref(''), formContext = ref(''), saving = ref(false), changeOpen = ref(false);
const isNew = computed(() => q('form') === 'new');
const formOpen = computed(() => q('form') === 'edit' || isNew.value);
const formDirty = computed(() => formOpen.value && JSON.stringify(form) !== formInit.value);
const guard = useUnsavedGuard(formDirty);
watch(() => JSON.stringify([context(), formOpen.value, isNew.value, fn.value]), () => {
  if (!formOpen.value) return;
  if (isNew.value) Object.assign(form, { name: '', title: '', desc: '', timeoutS: 5, code: '' });
  else if (fn.value) {
    const source = { ...fn.value, ...fn.value.pending?.definition };
    Object.assign(form, { name: fn.value.name, title: source.title, desc: source.desc, timeoutS: source.timeoutS, code: source.code });
  }
  formInit.value = JSON.stringify(form); formContext.value = context();
});
const closeForm = async () => { if (saving.value) return; if (formDirty.value && !(await confirmDiscard())) return; await setQuery({ form: undefined }); };
type Intent = { kind: 'save'; name: string; body: FunctionSave; fresh: boolean; id?: string; formText: string } | { kind: 'rollback'; id: string; version: string; lockVersion: number };
const intent = ref<Intent>(), intentContext = ref('');
function currentIntent(value: Intent, key: string) { return alive && key === context() && (value.kind === 'rollback' ? value.id === q('fn') : value.formText === JSON.stringify(form) && (value.fresh || value.id === q('fn'))); }
async function persist(value: Intent, opts: { join?: string; title?: string; desc?: string } = {}) {
  if (saving.value || readonly.value || !currentIntent(value, intentContext.value)) return;
  const c = captured(), pending = value.kind === 'save' && value.fresh ? undefined : fn.value?.pending;
  saving.value = true;
  try {
    const crId = opts.join || pending?.cr;
    let crLockVersion = pending?.crLockVersion;
    if (crId && crId !== pending?.cr) { const head = await fetchCR(crId, c.options); crLockVersion = head.lockVersion; }
    if (!c.current() || !currentIntent(value, intentContext.value)) return;
    const change = { crId, crLockVersion, changeTitle: opts.title, reason: opts.desc };
    const result = value.kind === 'rollback'
      ? await rollbackFunction(value.id, { ...change, toVersion: value.version, lockVersion: value.lockVersion }, c.options)
      : value.fresh ? await createFunction({ ...value.body, ...change, name: value.name }, c.options)
        : await saveFunction(value.id!, { ...value.body, ...change }, c.options);
    if (!c.current() || !currentIntent(value, intentContext.value)) return;
    if (value.kind === 'save') formInit.value = JSON.stringify(form);
    await setQuery({ fn: result.id, form: undefined });
    message.success(`已保存到变更单 ${result.crId}，发布后生效`);
    await Promise.all([loadList(), loadDetail(), loadVersions()]);
  } catch (e) { if (c.current()) message.error(fail(e)); }
  finally { saving.value = false; }
}
async function save() {
  if (saving.value || readonly.value || formContext.value !== context()) return;
  if (!form.name.trim() || !form.title.trim() || !form.code.trim()) { message.warning('请填写函数名、中文名和函数代码'); return; }
  const value: Intent = { kind: 'save', name: form.name.trim(), fresh: isNew.value, id: fn.value?.id,
    body: { title: form.title.trim(), desc: form.desc, code: form.code, timeoutS: form.timeoutS, lockVersion: isNew.value ? undefined : fn.value?.lockVersion }, formText: JSON.stringify(form) };
  intent.value = value; intentContext.value = context();
  if (fn.value?.pending && !isNew.value) {
    saving.value = true;
    const ok = await confirm.ask({ title: '保存函数草稿', target: form.name, impact: `更新变更单 ${fn.value.pending.cr}；审核通过并整版发布后才生效。` });
    saving.value = false;
    if (ok && currentIntent(value, intentContext.value)) await persist(value);
  } else changeOpen.value = true;
}
async function rollback(version: FnVersion) {
  if (saving.value || readonly.value || !fn.value) return;
  const c = captured(), id = fn.value.id;
  saving.value = true;
  const ok = await confirm.ask({ title: '恢复函数版本到草稿', target: `${fn.value.name} ${version.ver}`, impact: '内容先进入变更单；审核通过并整版发布后追加新函数版本，当前生效内容暂不变化。' });
  saving.value = false;
  if (!ok || !c.current() || id !== q('fn')) return;
  const value: Intent = { kind: 'rollback', id, version: version.ver, lockVersion: fn.value.lockVersion };
  intent.value = value; intentContext.value = c.key;
  if (fn.value.pending) await persist(value); else changeOpen.value = true;
}
function changeOk(opts: { join?: string; title?: string; desc?: string }) { if (intent.value) void persist(intent.value, opts); }
const signature = computed(() => fn.value ? `def ${fn.value.name}(${fn.value.inputs.map(p => `${p.name}: ${p.type}`).join(', ')}) -> ${fn.value.output.type}` : '');
const vcols = [{ title: '版本', dataIndex: 'ver', key: 'ver', width: 100 }, { title: '时间', dataIndex: 'time', key: 'time', width: 150 }, { title: '作者', dataIndex: 'author', key: 'author', width: 90 }, { title: '说明', dataIndex: 'note', key: 'note', ellipsis: true }, { title: '状态', dataIndex: 'status', key: 'status', width: 80 }, { title: '操作', key: 'op', width: 120 }];
// Immediate detail loading follows ref initialization, including direct function URL entry.
void loadDetail(); void loadVersions();
</script>

<template>
  <Teleport defer to="#page-extra"><a-tooltip :title="reason"><a-button type="primary" :disabled="readonly || saving" @click="guard(() => setQuery({ form: 'new' }))">新建函数</a-button></a-tooltip></Teleport>
  <div class="fn">
    <div class="panel fn-list">
      <div class="fn-filters"><a-input-search v-model:value="kw" placeholder="函数名 / 中文名" allow-clear /><a-select v-model:value="statusF" class="fn-sel" :options="['全部', '已发布', '草稿', '已停用'].map(v => ({ value: v, label: v }))" /></div>
      <a-alert v-if="loadError" type="error" :message="loadError" show-icon />
      <a-spin :spinning="loading" class="fn-items"><a-empty v-if="!items.length && !loading" description="没有函数记录" /><div v-for="item in items" :key="item.id" class="fn-item" :class="{ on: item.id === q('fn') }" @click="guard(() => setQuery({ fn: item.id, form: undefined }))"><div class="fn-l1"><span class="t-14 fw-600 c-1 num fn-ellip">{{ item.name }}</span><a-badge status="default" :text="item.status" /></div></div></a-spin>
      <AppPagination v-model:page="lpage" variant="simple" :total="total" :size="PAGE_SIZE" unit="个函数" class="fn-pager" />
    </div>
    <div class="panel fn-detail">
      <a-result v-if="detailError" status="error" title="函数加载失败" :sub-title="detailError" />
      <a-empty v-else-if="!fn" description="请选择函数" />
      <template v-else>
        <div class="fn-head"><div class="fn-title"><span class="t-20 fw-600 c-1 num">{{ fn.name }}</span><span class="t-16 c-2">{{ fn.title }}<InfoTip :text="fn.desc" /></span><a-tag>{{ fn.runtime }}</a-tag><a-tag>{{ fn.version }}</a-tag><a-badge status="default" :text="fn.status" /></div><a-space><a-button @click="tab = 'test'">测试运行</a-button><a-tooltip :title="reason"><a-button :disabled="readonly || saving" @click="setQuery({ form: 'edit' })">编辑</a-button></a-tooltip></a-space></div>
        <div class="t-12 c-2 fn-sub">负责人 {{ fn.owner }} · 更新于 <span class="num">{{ fn.updatedAt }}</span><span v-if="fn.pending"> · 变更中：<a @click="router.push({ path: '/build/ontology/releases', query: { cr: fn.pending.cr } })">{{ fn.pending.cr }}</a>（{{ fn.pending.status }}）</span></div>
        <a-tabs v-model:active-key="tab" class="fn-tabs"><a-tab-pane key="def" tab="定义" /><a-tab-pane key="test" tab="测试运行" /><a-tab-pane key="versions" :tab="`版本历史（${versionTotal}）`" /></a-tabs>
        <div v-if="tab === 'def'" class="fn-body fn-def"><div class="fn-col"><div class="t-14 fw-600 c-1 fn-h">签名</div><pre class="fn-pre num">{{ signature }}</pre><div class="t-14 fw-600 c-1 fn-h">入参</div><a-table size="small" row-key="name" :pagination="false" :data-source="fn.inputs" :columns="[{ title: '参数', dataIndex: 'name' }, { title: '类型', dataIndex: 'type' }, { title: '说明', dataIndex: 'desc' }]" /><div class="t-14 fw-600 c-1 fn-h">出参</div><a-descriptions :column="1" bordered size="small"><a-descriptions-item label="类型">{{ fn.output.type }}</a-descriptions-item><a-descriptions-item label="说明">{{ fn.output.desc || '—' }}</a-descriptions-item></a-descriptions><div class="t-14 fw-600 c-1 fn-h">代码</div><pre class="fn-pre num">{{ fn.code }}</pre></div><div class="fn-side"><div class="t-14 fw-600 c-1 fn-h">被引用（{{ fn.usedBy.length }}）</div><div v-if="!fn.usedBy.length" class="t-12 c-3">没有动作或规则引用记录</div><div v-for="use in fn.usedBy" :key="`${use.kind}:${use.name}`"><a-tag>{{ use.kind }}</a-tag>{{ use.name }}</div><div class="t-14 fw-600 c-1 fn-h">运行参数</div><a-descriptions :column="1" bordered size="small"><a-descriptions-item label="执行方式">受限纯计算解释器</a-descriptions-item><a-descriptions-item label="超时上限">{{ fn.timeoutS }} 秒（纯计算预算最多 1 秒）</a-descriptions-item><a-descriptions-item label="平均耗时">{{ fn.avgMs || '—' }}</a-descriptions-item><a-descriptions-item label="近 7 天调用">{{ fn.calls7d }} 次</a-descriptions-item></a-descriptions><p class="t-12 c-3">测试不计入生产调用统计。当前未接动作和规则的函数调用执行器。</p></div></div>
        <div v-else-if="tab === 'test'" class="fn-body fn-test"><div class="fn-tcol"><div class="fn-thead"><span class="t-14 fw-600">输入</span><a @click="resetTest">恢复登记示例</a></div><a-textarea v-model:value="testInput" class="fn-ta num" :auto-size="false" /><div class="fn-trun"><span class="t-12 c-3">仅 JSON 纯计算，无数据库或网络访问</span><a-tooltip :title="reason"><a-button :loading="running" :disabled="readonly || saving" @click="runTest">运行</a-button></a-tooltip></div><p class="t-12 c-3">支持 return、简单赋值、if、算术比较、字典/列表和下标；不支持导入、调用、属性访问、循环或空实现。源码/输入/结果各最多 64 KiB。</p></div><div class="fn-tcol"><div class="fn-thead"><span class="t-14 fw-600">{{ testOut ? '本次运行结果' : '登记示例输出' }}</span><span v-if="testOut" class="t-12 c-2">成功 · {{ testOut.ms }} ms · {{ testOut.at }}</span><span v-else class="t-12 c-3">示例不代表实际运行</span></div><a-alert v-if="testError" type="error" :message="testError" show-icon /><pre class="fn-pre fn-out num">{{ testOut ? JSON.stringify(testOut.output, null, 2) : JSON.stringify(fn.sampleOut, null, 2) }}</pre></div></div>
        <div v-else class="fn-body fn-vers"><a-alert v-if="versionError" type="error" :message="versionError" show-icon /><div class="fn-vtable"><a-table size="small" row-key="ver" :columns="vcols" :data-source="versions" :pagination="false"><template #bodyCell="{ column, record }"><span v-if="column.key === 'op'"><a v-if="record.cr" @click="router.push({ path: '/build/ontology/releases', query: { cr: record.cr } })">查看 {{ record.cr }}</a><a-button v-else-if="record.status === '历史'" type="link" size="small" :disabled="readonly || saving" @click="rollback(record)">恢复到草稿</a-button></span></template></a-table></div><AppPagination v-model:page="vpage" variant="simple" :total="versionTotal" :size="PAGE_SIZE" unit="个版本" class="fn-pager" /></div>
      </template>
    </div>
  </div>
  <a-drawer :open="formOpen" :width="640" :title="isNew ? '新建函数' : `编辑函数 · ${fn?.name ?? ''}`" @close="closeForm"><a-form layout="vertical" :model="form"><a-form-item label="函数名" required><a-input v-model:value="form.name" :disabled="!isNew || saving" placeholder="英文小写加下划线，如 calc_score" /></a-form-item><a-form-item label="中文名" required><a-input v-model:value="form.title" :disabled="saving" :maxlength="128" /></a-form-item><a-form-item label="说明"><a-textarea v-model:value="form.desc" :disabled="saving" :rows="2" /></a-form-item><a-form-item label="超时（秒）"><a-input-number v-model:value="form.timeoutS" :disabled="saving" :min="1" :max="60" /></a-form-item><a-form-item label="代码" required><a-textarea v-model:value="form.code" :disabled="saving" :rows="10" class="num" placeholder="def calc_score(value: float) -> float:&#10;    return value * 2" /></a-form-item></a-form><p class="t-12 c-3">入参和出参类型取自代码签名。保存只写变更单草稿，审核通过并整版发布后生效。</p><template #footer><div class="fn-foot"><a-button :disabled="saving" @click="closeForm">取消</a-button><a-button type="primary" :loading="saving" :disabled="readonly || !formDirty || (!isNew && !fn)" @click="save">保存到草稿</a-button></div></template></a-drawer>
  <StartChangeModal v-model:open="changeOpen" :target-name="intent?.kind === 'save' ? intent.name : fn?.name ?? '函数'" @ok="changeOk" />
</template>

<style scoped>
.fn { flex: 1; min-height: 0; display: flex; gap: var(--gap); }
.fn-list { width: 340px; flex: none; }
.fn-filters { display: flex; gap: 8px; padding: 12px 16px; flex: none; }
.fn-sel { width: 100px; flex: none; }
.fn-items { flex: 1; min-height: 0; overflow: hidden; }
.fn-item { padding: 10px 16px; border-bottom: var(--border); border-left: 3px solid transparent; cursor: pointer; }
.fn-item:hover { background: var(--g2); }
.fn-item.on { background: var(--brand-bg); border-left-color: var(--brand); }
.fn-l1 { display: flex; align-items: center; justify-content: space-between; gap: 8px; min-width: 0; }
.fn-ellip { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; min-width: 0; }
.fn-pager { padding: 6px 16px 8px; }
.fn-detail { flex: 1; min-width: 0; }
.fn-head { display: flex; justify-content: space-between; align-items: center; padding: 14px 20px 4px; }
.fn-title { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
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
