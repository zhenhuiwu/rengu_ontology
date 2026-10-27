<script setup lang="ts">
import { computed, onScopeDispose, reactive, ref, watch } from 'vue';
import AppPagination from '@/components/AppPagination.vue';
import { apiClient, currentApiContext, type PageResult } from '@/api/http';
import { actionContextKey, condsOf, dryRunAction, loadActionParams, paramsOf, type ActionTrialContext } from '@/api/action';
import { fetchCR, fetchCRItems } from '@/api/static';
import { dynamicApi, listAlerts } from '@/api/dynamic';
import { exploreSearch } from '@/api/qaObjects';
import type { ActionParam, ActionType, SubmitCond } from '@/mock/actionEvent';
import { draftFields, draftRows } from './useStaticCr';

const props = defineProps<{ action: ActionType; cr?: string; disabled: boolean }>();
const open = defineModel<boolean>('open', { default: false });
const emit = defineEmits<{ result: [] }>();
const staticApi = apiClient('static'), qaApi = apiClient('qa');
const loading = ref(false), busy = ref(false), error = ref('');
const params = ref<ActionParam[]>([]), values = reactive<Record<string, any>>({});
const target = reactive({ kind: '', name: '', id: '' });
const alertId = ref(''), needsAlert = ref(false);
const errors = reactive<Record<string, string>>({});
const result = ref<Awaited<ReturnType<typeof dryRunAction>> | null>(null);
const resultError = ref(''), submitted = ref('');
const resultInputs = ref<{ label: string; value: string }[]>([]);
const contextKey = actionContextKey();
let mounted = true, definitionSeq = 0, searchSeq = 0, definitionSignature = '';
onScopeDispose(() => { mounted = false; });
const current = () => mounted && contextKey === actionContextKey();
const errText = (e: unknown) => e instanceof Error ? e.message : String(e);
const signature = () => JSON.stringify([values, target.id, alertId.value]);
const resultChanged = computed(() => !!submitted.value && submitted.value !== signature());
const isRef = (p: ActionParam) => /^对象引用(?:列表)? · /.test(p.type);
const isComputed = (p: ActionParam) => { try { return !!JSON.parse(p.def)?.function; } catch { return false; } };
const isList = (p: ActionParam) => p.type === '枚举列表' || p.type.startsWith('对象引用列表 ·');
const isNumber = (p: ActionParam) => ['整数', '金额', '百分比', '小数', '数值'].includes(p.type);
const clearErrors = () => Object.keys(errors).forEach(k => delete errors[k]);

watch(open, value => { if (value) void loadDefinition(); });
async function loadDefinition() {
  const n = ++definitionSeq;
  loading.value = true; error.value = '';
  try {
    await loadActionParams(props.action.id);
    if (!current() || n !== definitionSeq) return;
    let definitions = paramsOf(props.action), conditions = condsOf(props.action);
    let kind: string = props.action.targetKind ?? '', name = props.action.target;
    if (props.cr) {
      const ctx = currentApiContext();
      const options = { readonlyParams: false, query: { ver: ctx.ver, previewAs: ctx.previewAs }, headers: { 'X-Demo-As': ctx.demoAs ?? '', 'X-Ontology-Id': ctx.ontologyId ?? '' } };
      await fetchCR(props.cr, options);
      if (!current() || n !== definitionSeq) return;
      const items = await fetchCRItems(props.cr, { kind: 'action', targetId: props.action.id }, options);
      if (!current() || n !== definitionSeq) return;
      definitions = draftRows(definitions, items, 'params', p => p.api, (row, p) => ({ ...p, ...(row.after as ActionParam) }));
      conditions = draftRows(conditions, items, 'conds', c => c.name, (row, c) => ({ ...c, ...(row.after as SubmitCond) }));
      const fields = draftFields(items, 'def');
      if ('targetKind' in fields) {
        kind = typeof fields.targetKind === 'string' ? fields.targetKind : '';
        name = kind;
        if (kind) {
          const object = await staticApi.get<{ id: string; name: string }>(`/object-types/${encodeURIComponent(kind)}`, options);
          name = object.name;
        }
      }
    }
    if (!current() || n !== definitionSeq) return;
    const nextSignature = JSON.stringify([definitions, conditions, kind]);
    if (definitionSignature && nextSignature !== definitionSignature) {
      Object.keys(values).forEach(k => delete values[k]); target.id = ''; alertId.value = '';
      result.value = null; resultError.value = ''; submitted.value = ''; resultInputs.value = []; clearErrors();
    }
    definitionSignature = nextSignature;
    params.value = definitions; Object.assign(target, { kind, name });
    needsAlert.value = !definitions.some(p => p.type === '对象引用 · 告警') && (name === '告警' || conditions.some(c => c.expr.includes('告警')));
  } catch (e) { if (current() && n === definitionSeq) error.value = errText(e); }
  finally { if (current() && n === definitionSeq) loading.value = false; }
}

const picker = reactive({ open: false, field: '', title: '', kind: '', alert: false, multiple: false, kw: '', page: 1, total: 0, loading: false, error: '', rows: [] as { id: string; title: string }[] });
async function choose(field: string, name: string, kind = '', multiple = false) {
  Object.assign(picker, { open: true, field, title: name, kind, alert: name === '告警', multiple, kw: '', page: 1, total: 0, error: '', rows: [] });
  await search();
}
async function search() {
  const n = ++searchSeq;
  picker.loading = true; picker.error = ''; picker.rows = [];
  try {
    if (picker.alert) {
      if (!dynamicApi.isReal()) throw new Error('告警数据服务未接入');
      const r = await listAlerts({ status: 'all', kw: picker.kw, page: picker.page, size: 10 });
      if (!current() || n !== searchSeq) return;
      picker.rows = r.items.map(a => ({ id: a.id, title: `${a.objLabel} · ${a.status}${a.assignee ? ` · ${a.assignee}` : ''}` })); picker.total = r.total;
      if (r.page !== picker.page) picker.page = r.page;
    } else {
      if (!staticApi.isReal() || !qaApi.isReal()) throw new Error('对象元数据或数据服务未接入');
      let kind = picker.kind;
      if (!kind) {
        const r = await staticApi.get<PageResult<{ id: string; name: string; status: string }>>('/object-types', { query: { kw: picker.title, page: 1, size: 10 } });
        if (!current() || n !== searchSeq) return;
        const matches = r.items.filter(t => t.name === picker.title && t.status === '已发布');
        if (matches.length !== 1 || r.total > 10) throw new Error('未找到唯一已发布的引用对象类型');
        kind = matches[0].id;
      }
      const r = await exploreSearch(kind, { kw: picker.kw, page: picker.page, size: 10, withFacets: false });
      if (!current() || n !== searchSeq) return;
      if (!r.bound) throw new Error('此对象类型尚未接入数据');
      picker.kind = kind; picker.rows = r.items; picker.total = r.total;
      if (r.page !== picker.page) picker.page = r.page;
    }
  } catch (e) { if (current() && n === searchSeq) { picker.total = 0; picker.error = errText(e); } }
  finally { if (current() && n === searchSeq) picker.loading = false; }
}
function selectObject(id: string) {
  if (picker.field === '$target') target.id = id;
  else if (picker.field === '$alert') alertId.value = id;
  else if (picker.multiple) values[picker.field] = [...new Set([...(values[picker.field] ?? []), id])];
  else values[picker.field] = id;
  if (!picker.multiple) picker.open = false;
}
function validate(): Record<string, unknown> | null {
  clearErrors();
  const payload: Record<string, unknown> = {};
  for (const p of params.value) {
    const v = values[p.api];
    if (v === undefined || v === null || v === '' || (Array.isArray(v) && !v.length)) {
      if (p.required && !isComputed(p)) errors[p.api] = `请填写${p.name}`;
      continue;
    }
    if (isList(p) ? !Array.isArray(v) || v.some(x => typeof x !== 'string')
      : p.type === '整数' ? !Number.isSafeInteger(v)
        : isNumber(p) ? typeof v !== 'number' || !Number.isFinite(v)
          : p.type === '布尔' ? typeof v !== 'boolean'
            : typeof v !== 'string') errors[p.api] = `${p.name}不符合${p.type}类型`;
    payload[p.api] = v;
  }
  if (target.kind && !target.id) errors.$target = '请选择目标对象';
  return Object.keys(errors).length ? null : payload;
}
async function submit() {
  if (busy.value || loading.value || error.value || props.disabled || !current()) return;
  const payload = validate(); if (!payload) return;
  const ctx = { ...currentApiContext() };
  if (ctx.previewAs) return;
  const context: ActionTrialContext = { ...(target.kind && target.id ? { objKind: target.kind, objId: target.id } : {}), ...(alertId.value ? { alertId: alertId.value } : {}) };
  busy.value = true; result.value = null; resultError.value = ''; submitted.value = signature();
  resultInputs.value = [...(context.objId ? [{ label: `目标对象 · ${target.name}`, value: context.objId }] : []), ...(context.alertId ? [{ label: '上下文告警', value: context.alertId }] : []), ...params.value.filter(p => p.api in payload).map(p => ({ label: `${p.name} · ${p.api}`, value: JSON.stringify(payload[p.api]) }))];
  try {
    const r = await dryRunAction(props.action.id, payload, props.cr ? 'draft' : 'published', props.cr,
      { readonlyParams: false, query: { ver: ctx.ver, previewAs: ctx.previewAs }, headers: { 'X-Demo-As': ctx.demoAs ?? '', 'X-Ontology-Id': ctx.ontologyId ?? '' } }, context);
    if (current()) { result.value = r; emit('result'); }
  } catch (e) { if (current()) { resultError.value = errText(e); emit('result'); } }
  finally { if (current()) busy.value = false; }
}
</script>

<template>
  <a-modal v-model:open="open" :title="`试运行：${action.name}`" :width="680" :mask-closable="!busy" :closable="!busy" :cancel-button-props="{ disabled: busy }"
    ok-text="开始校验" cancel-text="关闭" :confirm-loading="busy" :ok-button-props="{ disabled: loading || !!error || disabled }" @ok="submit">
    <p class="t-14 c-2">{{ cr ? `已保存草稿 ${cr}` : currentApiContext().ver || '当前已发布定义' }} · 仅校验参数与提交条件</p>
    <a-alert v-if="error" type="error" show-icon message="试运行定义加载失败" :description="error" />
    <a-button v-if="error" type="link" @click="loadDefinition">重试</a-button>
    <a-spin v-else :spinning="loading">
      <a-form layout="vertical" class="trial-form" :disabled="loading || busy || disabled">
        <a-form-item v-if="target.kind" :label="`目标对象 · ${target.name}`" required :validate-status="errors.$target ? 'error' : undefined" :help="errors.$target">
          <div class="trial-ref"><a-input :value="target.id" readonly placeholder="请选择真实目标对象" /><a-button @click="choose('$target', target.name, target.kind)">选择对象</a-button></div>
        </a-form-item>
        <a-form-item v-if="needsAlert" label="上下文告警">
          <div class="trial-ref"><a-input v-model:value="alertId" readonly allow-clear placeholder="可选择当前告警" /><a-button @click="choose('$alert', '告警')">选择告警</a-button></div>
        </a-form-item>
        <a-form-item v-for="p in params" :key="p.api" :label="`${p.name} · ${p.api}`" :required="p.required" :validate-status="errors[p.api] ? 'error' : undefined" :help="errors[p.api]">
          <span v-if="isComputed(p)" class="c-2">由已绑定函数版本计算</span>
          <div v-else-if="isRef(p)" class="trial-ref">
            <a-select v-if="isList(p)" v-model:value="values[p.api]" mode="multiple" :open="false" :options="(values[p.api] ?? []).map((v: string) => ({ value: v, label: v }))" placeholder="请选择对象" />
            <a-input v-else v-model:value="values[p.api]" readonly allow-clear placeholder="请选择对象" />
            <a-button @click="choose(p.api, p.type.split(' · ')[1], p.type.split(' · ')[1] === target.name ? target.kind : '', isList(p))">选择</a-button>
          </div>
          <a-input-number v-else-if="isNumber(p)" v-model:value="values[p.api]" :precision="p.type === '整数' ? 0 : undefined" class="trial-wide" :placeholder="p.type" />
          <a-select v-else-if="p.type === '布尔'" v-model:value="values[p.api]" allow-clear :options="[{ label: '是', value: true }, { label: '否', value: false }]" placeholder="请选择" />
          <a-date-picker v-else-if="p.type === '日期' || p.type === '日期时间'" v-model:value="values[p.api]" :show-time="p.type === '日期时间'" :value-format="p.type === '日期' ? 'YYYY-MM-DD' : 'YYYY-MM-DDTHH:mm:ss'" class="trial-wide" />
          <a-select v-else-if="p.type === '枚举列表'" v-model:value="values[p.api]" mode="tags" :token-separators="[',', '，']" placeholder="输入实际枚举值，按回车添加" />
          <a-textarea v-else-if="p.type === '文本'" v-model:value="values[p.api]" :rows="2" />
          <a-input v-else v-model:value="values[p.api]" :placeholder="p.type === '枚举' ? '输入实际枚举值' : p.type" />
          <div v-if="p.rule && p.rule !== '—'" class="t-12 c-3 trial-rule">{{ p.rule }}</div>
        </a-form-item>
        <p v-if="!loading && !params.length && !target.kind && !needsAlert" class="c-2">此定义无需填写参数或对象上下文。</p>
      </a-form>
    </a-spin>
    <section v-if="result || resultError" class="trial-result" aria-live="polite">
      <div class="fw-600 trial-result-title">最近校验结果<span v-if="resultChanged" class="t-12 c-warning"> · 输入已修改，请重新校验</span></div>
      <a-alert v-if="resultError" type="error" show-icon message="校验请求失败" :description="resultError" />
      <template v-else-if="result">
        <a-alert :type="result.passed ? 'success' : 'warning'" show-icon :message="`校验${result.passed ? '通过' : '未通过'} · 已检查 ${result.checked} 项提交条件`" />
        <ul v-if="result.failed.length"><li v-for="(item, i) in result.failed" :key="i"><strong>{{ item.name }}</strong>：{{ item.fail }}</li></ul>
      </template>
      <a-collapse ghost><a-collapse-panel key="inputs" header="查看本次校验输入"><a-descriptions :column="1" size="small" bordered><a-descriptions-item v-for="(item, i) in resultInputs" :key="i" :label="item.label"><span class="trial-value">{{ item.value }}</span></a-descriptions-item></a-descriptions><p v-if="!resultInputs.length" class="c-2">未提供参数与对象上下文。</p></a-collapse-panel></a-collapse>
    </section>
  </a-modal>
  <a-modal v-model:open="picker.open" :title="`选择${picker.title}`" :width="620" :footer="null" @cancel="searchSeq++">
    <a-input-search v-model:value="picker.kw" allow-clear placeholder="搜索对象名称或编号" @search="picker.page = 1; search()" />
    <a-alert v-if="picker.error" class="trial-search-error" type="error" show-icon :message="picker.error" />
    <a-table :loading="picker.loading" :pagination="false" size="small" row-key="id" :data-source="picker.rows" :columns="[{ title: '编号', dataIndex: 'id' }, { title: picker.title, dataIndex: 'title' }, { title: '', key: 'select', width: 72 }]" :locale="{ emptyText: picker.loading ? '正在读取…' : '没有可查看的对象' }">
      <template #bodyCell="{ column, record }"><a-button v-if="column.key === 'select'" type="link" size="small" @click="selectObject(record.id)">选择</a-button></template>
    </a-table>
    <AppPagination :page="picker.page" :size="10" :total="picker.total" :empty="false" variant="simple" @update:page="picker.page = $event; search()" />
  </a-modal>
</template>

<style scoped>
.trial-form { margin-top: 16px; max-height: 48vh; overflow-y: auto; padding-right: 4px; }
.trial-ref { display: flex; gap: 8px; }
.trial-ref > :first-child { flex: 1; min-width: 0; }
.trial-wide { width: 100%; }
.trial-rule { margin-top: 4px; }
.trial-result { border-top: var(--border); padding-top: 12px; margin-top: 12px; max-height: 30vh; overflow-y: auto; }
.trial-result-title { margin-bottom: 8px; }
.trial-value { white-space: pre-wrap; overflow-wrap: anywhere; }
.trial-search-error { margin: 12px 0; }
</style>
