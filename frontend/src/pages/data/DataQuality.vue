<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue';
import { useConfirmStore } from '@/stores/confirm';
import { useUnsavedGuard, confirmDiscard } from '@/composables/useUnsavedGuard';
import { useRoute, useRouter } from 'vue-router';
import { message } from 'ant-design-vue';
import AppPagination from '@/components/AppPagination.vue';
import StartChangeModal from '@/pages/ontology/parts/StartChangeModal.vue';
import { fetchCR } from '@/api/static';
import InfoTip from '@/components/InfoTip.vue';
import { usePagedQuery } from '@/composables/usePagedQuery';
import { useDrawer } from '@/composables/useDrawer';
import { useReadonly } from '@/composables/useReadonly';
import { paginate } from '@/mock';
import { DATA_CUTOFF, QUALITY_CHECKS, Q_OBJECT_TYPES, type CheckKind } from '@/mock/quality';
import { currentApiContext } from '@/api/http';
import { isQualityReal, fetchQualityMeta, fetchQualityChecks, fetchQualityRuns, createQualityCheck, updateQualityCheck, retireQualityCheck, runQualityCheck, runAllQualityChecks, type QualityCheck, type QualityRun, type QualityObject, type QualitySummary } from '@/api/quality';

const route = useRoute();
const router = useRouter();
const q = (k: string) => typeof route.query[k] === 'string' ? route.query[k] as string : '';
const setQuery = (patch: Record<string, string | undefined>) => router.replace({ query: { ...route.query, ...patch } });
const drawer = useDrawer();
const { readonly, reason } = useReadonly();
const real = isQualityReal();
const contextKey = () => JSON.stringify(currentApiContext());
const KINDS: CheckKind[] = ['新鲜度', '空值率', '唯一性', '引用完整性'];
const typeF = computed({ get: () => q('type') || undefined, set: (v?: string) => setQuery({ type: v || undefined }) });
const kindF = computed({ get: () => q('kind') || undefined, set: (v?: string) => setQuery({ kind: v || undefined }) });
const resultF = computed({ get: () => q('result') || '全部', set: (v: string) => setQuery({ result: v === '全部' ? undefined : v }) });
const pq = usePagedQuery({ defaultSize: 10, filters: [typeF, kindF, resultF] });
const state = reactive<{ key: string; items: QualityCheck[]; objects: QualityObject[]; total: number; cutoff: string; summary: QualitySummary; error: string; loading: boolean }>({ key: '', items: [], objects: [], total: 0, cutoff: '', summary: { total: 0, passed: 0, failed: 0, unrun: 0, lastRun: '' }, error: '', loading: false });
const metaContext = ref('');
const checks = computed<QualityCheck[]>(() => real ? state.key === listKey() ? state.items : [] : QUALITY_CHECKS);
const objects = computed(() => real ? metaContext.value === contextKey() ? state.objects : [] : Q_OBJECT_TYPES.map((t) => ({ ...t, kinds: KINDS })));
const dataCutoff = computed(() => real ? state.key === listKey() ? state.cutoff || '—' : '—' : DATA_CUTOFF);
const rows = computed(() => checks.value.filter((c) => (!typeF.value || c.typeKey === typeF.value) && (!kindF.value || c.kind === kindF.value) && (resultF.value === '全部' || c.result === resultF.value)).sort((a, b) => a.result === b.result ? 0 : a.result === '失败' ? -1 : 1));
const paged = computed(() => real ? { items: checks.value, total: state.total } : paginate(rows.value, { page: pq.page.value, size: pq.size.value }));
const failed = computed(() => checks.value.filter((c) => c.result === '失败'));
const failedTypes = computed(() => new Set(failed.value.map((c) => c.typeKey)));
const totalCount = computed(() => real ? state.key === listKey() ? state.summary.total : 0 : checks.value.length);
const passedCount = computed(() => real ? state.key === listKey() ? state.summary.passed : 0 : checks.value.length - failed.value.length);
const failedCount = computed(() => real ? state.key === listKey() ? state.summary.failed : 0 : failed.value.length);
const passRate = computed(() => totalCount.value ? (passedCount.value / totalCount.value * 100).toFixed(1) : '—');
const lastRun = computed(() => real ? state.key === listKey() ? state.summary.lastRun : '' : checks.value.reduce((m, c) => c.lastRun > m ? c.lastRun : m, ''));
const listKey = () => JSON.stringify([contextKey(), typeF.value, kindF.value, resultF.value, pq.page.value, pq.size.value]);
let listSeq = 0;
const errorText = (e: unknown) => e instanceof Error ? e.message : String(e);
async function loadList() {
  const seq = ++listSeq; const key = listKey(); const context = contextKey();
  if (!real) return;
  if (metaContext.value !== context) Object.assign(state, { total: 0, cutoff: '', summary: { total: 0, passed: 0, failed: 0, unrun: 0, lastRun: '' } });
  Object.assign(state, { key, items: [], error: '', loading: true });
  try {
    const [meta, result] = await Promise.all([fetchQualityMeta(), fetchQualityChecks({ typeKey: typeF.value, kind: kindF.value, result: resultF.value, page: pq.page.value, size: pq.size.value })]);
    if (seq !== listSeq || key !== listKey() || context !== contextKey()) return;
    metaContext.value = context;
    Object.assign(state, { objects: meta.objects, items: result.items, total: result.total, cutoff: result.dataCutoff, summary: result.summary });
  } catch (e) { if (seq === listSeq && key === listKey()) state.error = errorText(e); }
  finally { if (seq === listSeq && key === listKey()) state.loading = false; }
}
const cols = [
  { title: '编号', dataIndex: 'id', key: 'id', width: 160 }, { title: '对象类型', key: 'type', width: 110 },
  { title: '检查', dataIndex: 'kind', key: 'kind', width: 90 }, { title: '规则', dataIndex: 'rule', key: 'rule', ellipsis: true },
  { title: '阈值', dataIndex: 'threshold', key: 'threshold', width: 110 }, { title: '最近结果', key: 'actual', width: 260, ellipsis: true },
  { title: '结果', key: 'result', width: 80 },
  ...(real ? [{ title: '定义状态', key: 'definition', width: 150 }, { title: '操作', key: 'edit', width: 100 }] : []), { title: '最近运行', key: 'lastRun', width: 140 },
];
const selected = computed(() => checks.value.find((c) => c.id === q('check')) ?? rows.value[0]);
const HSIZE = 15; const hpage = ref(1);
const history = reactive<{ key: string; items: QualityRun[]; total: number; error: string }>({ key: '', items: [], total: 0, error: '' });
const historyKey = () => JSON.stringify([contextKey(), selected.value?.id, hpage.value]);
const hist = computed(() => real ? history.key === historyKey() ? history : { items: [], total: 0 } : paginate(selected.value?.history ?? [], { page: hpage.value, size: HSIZE }));
const histFails = computed(() => hist.value.items.filter((r) => r.result === '失败').length);
let histSeq = 0; const historyScope = ref('');
async function loadHistory() {
  const seq = ++histSeq; const key = historyKey(); const id = selected.value?.id;
  if (!real) return;
  const scope = JSON.stringify([contextKey(), id]);
  Object.assign(history, { key, items: [], total: historyScope.value === scope ? history.total : 0, error: '' });
  historyScope.value = scope;
  if (!id || selected.value?.hasPublished === false) return;
  try { const r = await fetchQualityRuns(id, hpage.value, HSIZE); if (seq === histSeq && key === historyKey()) Object.assign(history, r); }
  catch (e) { if (seq === histSeq && key === historyKey()) history.error = errorText(e); }
}
watch(() => selected.value?.id, () => { hpage.value = 1; });
watch(listKey, loadList, { immediate: true });
watch(historyKey, loadHistory, { immediate: true });
const tl = (r: string) => r === '失败' ? 'tl-failed' : 'tl-normal';
const openType = (key: string) => drawer.open('object-type', key);
const rowProps = (c: QualityCheck) => ({ class: c.id === selected.value?.id ? 'dq-row-on' : '', onClick: () => setQuery({ check: c.id }) });
const busy = ref(false);
let mutationSeq = 0;
async function runNow(all = false) {
  if (readonly.value || busy.value) return;
  if (!real) { message.success('演示：已提交检查'); return; }
  if (!all && (!selected.value || selected.value.retired || selected.value.hasPublished === false)) return;
  const context = contextKey(), mutation = ++mutationSeq; const current = () => mutation === mutationSeq && context === contextKey(); busy.value = true;
  try {
    const result = all ? await runAllQualityChecks() : await runQualityCheck(selected.value!.id);
    if (!current()) return;
    if (!result.completed) throw new Error('运行尚未完成');
    message.success('检查已完成'); await loadList(); await loadHistory();
  } catch (e) { if (current()) message.error(errorText(e)); }
  finally { if (current()) busy.value = false; }
}
const dqConfirm = useConfirmStore(); const ncOpen = ref(false);
const editingCheck = ref<QualityCheck>();
const changeOpen = ref(false), retiring = ref(false), lastSavedCr = ref('');
const changeName = computed(() => `${retiring.value ? '删除质量检查' : editingCheck.value ? '修改质量检查' : '新建质量检查'}：${nc.name}`);
const ncBlank = () => ({ name: '', obj: objects.value.find((o) => o.kinds.length)?.key ?? '', kind: '新鲜度' as CheckKind, threshold: '', schedule: real ? '手动' : '每天 23:30' });
const nc = reactive(ncBlank()); const ncBase = ref('');
const availableKinds = computed(() => objects.value.find((o) => o.key === nc.obj)?.kinds ?? []);
watch(() => nc.obj, () => { if (!availableKinds.value.includes(nc.kind)) nc.kind = availableKinds.value[0] ?? '新鲜度'; });
watch(contextKey, () => { ncOpen.value = false; changeOpen.value = false; editingCheck.value = undefined; lastSavedCr.value = ''; ++mutationSeq; busy.value = false; Object.assign(nc, ncBlank()); ncBase.value = JSON.stringify(nc); });
const ncDirty = computed(() => ncOpen.value && JSON.stringify(nc) !== ncBase.value); useUnsavedGuard(ncDirty);
function openNew() { if (readonly.value) return; editingCheck.value = undefined; retiring.value = false; Object.assign(nc, ncBlank()); if (!availableKinds.value.includes(nc.kind)) nc.kind = availableKinds.value[0] ?? '新鲜度'; ncBase.value = JSON.stringify(nc); ncOpen.value = true; }
async function closeNew() { if (ncDirty.value && !(await confirmDiscard())) return; ncOpen.value = false; }
const ncOk = computed(() => !!nc.name.trim() && !!nc.obj && !!nc.threshold.trim() && availableKinds.value.includes(nc.kind));
function openEdit(check: QualityCheck) {
  if (readonly.value || busy.value || check.retired || check.pending && check.pending.status !== '草稿') return;
  editingCheck.value = check; retiring.value = false;
  const value = check.pending?.definition ?? check;
  Object.assign(nc, { name: value.name ?? '', obj: value.typeKey, kind: value.kind, threshold: value.threshold, schedule: value.schedule ?? '手动' });
  ncBase.value = JSON.stringify(nc); ncOpen.value = true;
}
watch(() => [q('form'), selected.value?.id, selected.value?.pending?.lockVersion], () => {
  if (real && q('form') === 'edit' && selected.value?.id === q('check') && !ncOpen.value) { openEdit(selected.value); void setQuery({ form: undefined }); }
});
async function saveNew() {
  if (readonly.value || !ncOk.value || busy.value) return;
  const context = contextKey(), mutation = mutationSeq;
  const ok = await dqConfirm.ask({ title: editingCheck.value ? '保存检查定义' : '新建检查', target: nc.name.trim(), okText: real ? '保存到变更单' : '新建', fields: [{ label: '对象', value: objects.value.find((t) => t.key === nc.obj)?.name ?? nc.obj }, { label: '规则类型', value: nc.kind }, { label: '阈值', value: nc.threshold }, { label: '调度', value: nc.schedule }], impact: real ? '只保存变更草稿，审核发布后检查定义才生效；运行始终使用已发布定义，历史结果保留。' : '演示新建质量检查' });
  if (!ok || readonly.value || context !== contextKey() || mutation !== mutationSeq) return;
  if (!real) { ncBase.value = JSON.stringify(nc); ncOpen.value = false; message.success('已保存检查定义'); return; }
  retiring.value = false;
  if (editingCheck.value?.pending) await saveDefinition({ join: editingCheck.value.pending.crId });
  else changeOpen.value = true;
}
async function deleteCheck(check: QualityCheck) {
  if (!real || readonly.value || busy.value || check.retired || check.hasPublished === false || check.pending && check.pending.status !== '草稿') return;
  const context = contextKey(), mutation = mutationSeq;
  const ok = await dqConfirm.ask({ title: '删除质量检查', target: check.name ?? check.id, okText: '生成删除草稿', impact: '仅在审核发布后退休检查，发布前仍使用当前定义；所有已保存运行历史永久保留。' });
  if (!ok || readonly.value || context !== contextKey() || mutation !== mutationSeq) return;
  editingCheck.value = check; retiring.value = true;
  Object.assign(nc, { name: check.name ?? '', obj: check.typeKey, kind: check.kind, threshold: check.threshold, schedule: check.schedule ?? '手动' });
  if (check.pending) await saveDefinition({ join: check.pending.crId });
  else changeOpen.value = true;
}
async function saveDefinition(opts: { join?: string; title?: string; desc?: string }) {
  if (!real || readonly.value || busy.value || (!retiring.value && !ncOk.value)) return;
  const context = contextKey(), selectedCheck = editingCheck.value, mutation = ++mutationSeq;
  const current = () => mutation === mutationSeq && context === contextKey();
  busy.value = true;
  try {
    let lockVersion = selectedCheck?.pending?.lockVersion;
    if (opts.join && opts.join !== selectedCheck?.pending?.crId) {
      const ctx = currentApiContext();
      const head = await fetchCR(opts.join, { readonlyParams: false, headers: { 'X-Demo-As': ctx.demoAs ?? '', 'X-Ontology-Id': ctx.ontologyId ?? '' }, query: { ver: ctx.ver, previewAs: ctx.previewAs } });
      if (!current() || readonly.value) return;
      lockVersion = head.lockVersion;
    }
    const options = { crId: opts.join, lockVersion, title: opts.title, reason: opts.desc };
    const value = { name: nc.name.trim(), typeKey: nc.obj, kind: nc.kind, threshold: nc.threshold, schedule: nc.schedule, ...options };
    const result = retiring.value ? await retireQualityCheck(selectedCheck!.id, options) : selectedCheck ? await updateQualityCheck(selectedCheck.id, value) : await createQualityCheck(value);
    if (!current()) return;
    lastSavedCr.value = result.crId; changeOpen.value = false; ncOpen.value = false; editingCheck.value = undefined;
    ncBase.value = JSON.stringify(nc); message.success(`已保存到 ${result.crId}，审核发布后生效`); await loadList();
  } catch (e) { if (current()) message.error(errorText(e)); }
  finally { if (current()) busy.value = false; }
}

</script>

<template>
  <Teleport defer to="#page-extra">
    <a-space>
      <span v-if="real" class="t-12 c-3">我的质量检查</span>
      <span class="t-12 c-3">检测数据快照 <span class="num">{{ dataCutoff }}</span></span>
      <a-tooltip :title="reason"><a-button :disabled="readonly || busy" @click="openNew">新建检查</a-button></a-tooltip>
      <a-tooltip :title="reason"><a-button type="primary" :disabled="readonly || busy" @click="runNow(true)">全部运行</a-button></a-tooltip>
    </a-space>
  </Teleport>
  <StartChangeModal v-model:open="changeOpen" :target-name="changeName" :close-on-ok="false" :busy="busy" @ok="saveDefinition" />
  <a-drawer :open="ncOpen && !readonly" :width="480" :title="editingCheck ? '编辑检查' : '新建检查'" @close="closeNew">
    <a-form layout="vertical" :model="nc">
      <a-form-item label="名称" required><a-input v-model:value="nc.name" placeholder="如：订单 · 新鲜度" /></a-form-item>
      <a-form-item label="对象" required><a-select v-model:value="nc.obj" :options="objects.map((t) => ({ value: t.key, label: `${t.name}${t.kinds.length ? '' : '（不可用）'}`, disabled: !t.kinds.length }))" /></a-form-item>
      <a-form-item label="规则类型"><a-radio-group v-model:value="nc.kind" :options="KINDS.map((v) => ({ value: v, label: v, disabled: !availableKinds.includes(v) }))" /></a-form-item>
      <a-form-item label="阈值" required extra="格式：≤ 2 小时、≤ 1%、重复 0 条、找不到 0 条"><a-input v-model:value="nc.threshold" /></a-form-item>
      <a-form-item label="调度"><a-select :disabled="real" v-model:value="nc.schedule" :options="['手动', '每 2 小时', '每天 23:30', '每周一 06:00'].map((v) => ({ value: v, label: v }))" /></a-form-item>
    </a-form>
    <template #footer>
      <div style="display: flex; justify-content: flex-end; gap: 8px">
        <a-button @click="closeNew">取消</a-button>
        <a-button type="primary" :disabled="!ncOk || busy" @click="saveNew">{{ real ? '保存草稿' : '新建' }}</a-button>
      </div>
    </template>
  </a-drawer>
  <div class="dq">
    <a-alert v-if="real && lastSavedCr" type="info" message="检查定义已保存到变更单，审核发布后生效"><template #description><router-link :to="`/build/ontology/releases?cr=${lastSavedCr}`">{{ lastSavedCr }}</router-link></template></a-alert>
    <div v-if="state.error" role="alert">加载失败：{{ state.error }} <a @click="loadList">重试</a></div>
    <div class="dq-sum">
      <div class="panel dq-kpi"><div class="t-14 c-2">{{ real ? '生效检查项' : '检查项' }}<InfoTip v-if="!real" :text="`覆盖 ${objects.length} 个对象类型`" /></div><div class="t-24 fw-600 num c-1">{{ totalCount }}</div></div>
      <div class="panel dq-kpi"><div class="t-14 c-2">通过</div><div class="dq-kv"><span class="t-24 fw-600 num c-1">{{ passedCount }}</span><span class="t-14 c-2 num">{{ passRate }}{{ totalCount ? '%' : '' }}</span></div></div>
      <div class="panel dq-kpi"><div class="t-14 c-2">失败<InfoTip :text="`${real ? '本页' : ''}涉及 ${failedTypes.size} 个对象类型`" /></div><div class="t-24 fw-600 num tl-failed">{{ failedCount }}</div></div>
      <div class="panel dq-kpi"><div class="t-14 c-2">检测快照<InfoTip text="检查使用的数据截至时间" /></div><div class="t-24 fw-600 num c-1">{{ dataCutoff === '—' ? '—' : dataCutoff.slice(5) }}</div></div>
      <div class="panel dq-kpi"><div class="t-14 c-2">最近运行<InfoTip text="最近一次检查时间" /></div><div class="t-24 fw-600 num c-1">{{ lastRun ? lastRun.slice(5) : '—' }}</div></div>
    </div>

    <div class="dq-main">
      <div class="panel dq-left">
        <div class="dq-filters">
          <a-select v-model:value="typeF" allow-clear placeholder="对象类型" class="dq-sel" :options="objects.map((t) => ({ value: t.key, label: `${t.name}${t.kinds.length ? '' : '（不可用）'}`, disabled: !t.kinds.length }))" />
          <a-select v-model:value="kindF" allow-clear placeholder="检查类型" class="dq-sel" :options="KINDS.map((k) => ({ value: k, label: k }))" />
          <a-radio-group v-model:value="resultF" button-style="solid">
            <a-radio-button v-for="k in ['全部', '通过', '失败']" :key="k" :value="k">{{ k }}</a-radio-button>
          </a-radio-group>
        </div>
        <div class="dq-table" :class="{ 'paged-scroll': pq.overflow.value }">
          <a-table size="small" row-key="id" :loading="state.loading" :columns="cols" :data-source="paged.items" :pagination="false" :custom-row="rowProps">
            <template #bodyCell="{ column, record }">
              <template v-if="real && column.key === 'definition'">
                <span v-if="record.retired" class="c-3">已退休</span>
                <router-link v-else-if="record.pending" :to="`/build/ontology/releases?cr=${record.pending.crId}`" @click.stop>{{ record.pending.retire ? '删除草稿' : record.pending.status }} · {{ record.pending.crId }}</router-link>
                <span v-else>已发布</span>
              </template>
              <a-space v-else-if="real && column.key === 'edit'" @click.stop>
                <a-button type="link" size="small" :disabled="readonly || busy || record.retired || !!record.pending && record.pending.status !== '草稿'" @click="openEdit(record)">编辑</a-button>
                <a-button type="link" size="small" :disabled="readonly || busy || record.retired || record.hasPublished === false || !!record.pending && record.pending.status !== '草稿'" @click="deleteCheck(record)">删除</a-button>
              </a-space>
              <span v-else-if="column.key === 'type'">
                <a v-if="record.result === '失败'" @click.stop="openType(record.typeKey)">{{ record.typeName }}</a>
                <span v-else class="c-1">{{ record.typeName }}</span>
              </span>
              <span v-else-if="column.key === 'actual'" :class="record.result === '失败' ? 'tl-failed' : 'c-2'">{{ record.actual }}</span>
              <span v-else-if="column.key === 'result'" class="dq-res" :class="tl(record.result)"><span class="dq-dot" />{{ record.result }}</span>
              <span v-else-if="column.key === 'lastRun'" class="num c-2">{{ record.lastRun.slice(5) }}</span>
            </template>
          </a-table>
        </div>
        <AppPagination v-model:page="pq.page.value" v-model:size="pq.size.value" variant="full" :total="paged.total" :default-size="pq.defaultSize" unit="项" class="dq-pager" />
      </div>

      <div v-if="selected" class="panel dq-right">
        <div class="panel-head">
          <span class="t-16 fw-600">运行历史</span>
          <a-tooltip :title="reason"><a-button size="small" :disabled="readonly || busy || selected.retired || selected.hasPublished === false" @click="runNow(false)">立即运行</a-button></a-tooltip>
        </div>
        <div class="dq-hhead">
          <div class="dq-htitle">
            <span class="num c-3 t-12">{{ selected.id }}</span>
            <a v-if="selected.result === '失败'" class="t-14 fw-600" @click="openType(selected.typeKey)">{{ selected.typeName }}</a>
            <span v-else class="t-14 fw-600 c-1">{{ selected.typeName }}</span>
            <span class="t-14 fw-600 c-1">· {{ selected.kind }}</span>
            <span class="dq-res t-12" :class="tl(selected.result)"><span class="dq-dot" />{{ selected.result }}</span>
            <InfoTip :text="`规则：${selected.rule} · 阈值 ${selected.threshold}${selected.note ? '。说明：' + selected.note : ''}。当前页运行记录失败 ${histFails} 次${selected.failSince ? '，自 ' + selected.failSince + ' 起持续失败' : ''}`" />
            <span class="t-14 c-2 dq-hfail">本页失败 <span class="num">{{ histFails }}</span> 次</span>
          </div>
        </div>
        <div v-if="history.error" role="alert">历史加载失败：{{ history.error }}</div>
        <div class="dq-hist">
          <div v-for="r in hist.items" :key="r.id ?? r.time" class="dq-hrow">
            <span class="num c-2 dq-htime">{{ r.time }}</span>
            <span class="dq-res dq-hres" :class="tl(r.result)"><span class="dq-dot" />{{ r.result }}</span>
            <span class="dq-ellip" :class="r.result === '失败' ? 'tl-failed' : 'c-2'">{{ r.actual }}</span>
            <span class="num c-3 dq-hms">{{ (r.ms / 1000).toFixed(1) }} 秒</span>
          </div>
        </div>
        <AppPagination v-model:page="hpage" variant="simple" :total="hist.total" :size="HSIZE" unit="次运行" class="dq-pager" />
      </div>
    </div>
  </div>
</template>

<style scoped>
.dq { flex: 1; min-height: 0; display: flex; flex-direction: column; gap: var(--gap); }
.dq-sum { display: grid; grid-template-columns: repeat(5, 1fr); gap: var(--gap); flex: none; }
.dq-kpi { padding: 12px 16px; flex-direction: row; align-items: center; justify-content: space-between; gap: 8px; }
.dq-kv { display: flex; align-items: baseline; gap: 8px; }
.dq-hfail { margin-left: auto; }
.dq-main { flex: 1; min-height: 0; display: flex; gap: var(--gap); }
.dq-left { flex: 1; min-width: 0; }
.dq-right { width: 460px; flex: none; }
.dq-filters { display: flex; gap: 8px; align-items: center; padding: 12px 16px; flex: none; }
.dq-sel { width: 160px; }
.dq-table { flex: 1; min-height: 0; padding: 0 16px; }
.dq-table :deep(.ant-table-row) { cursor: pointer; }
.dq-table :deep(.dq-row-on > td) { background: var(--brand-bg) !important; }
.dq-pager { padding: 6px 16px 8px; }
.dq-res { display: inline-flex; align-items: center; gap: 6px; }
.dq-dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; background: currentColor; }
.dq-hhead { padding: 0 16px 10px; border-bottom: var(--border); display: flex; flex-direction: column; gap: 4px; flex: none; }
.dq-htitle { display: flex; align-items: center; gap: 8px; }
.dq-hist { flex: 1; min-height: 0; overflow: hidden; }
.dq-hrow { display: flex; align-items: center; gap: 12px; padding: 0 16px; height: 36px; border-bottom: var(--border); }
.dq-htime { flex: none; width: 128px; }
.dq-hres { flex: none; width: 52px; }
.dq-hms { flex: none; margin-left: auto; }
.dq-ellip { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; min-width: 0; }
</style>
