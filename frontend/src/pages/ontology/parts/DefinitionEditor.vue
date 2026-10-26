<script setup lang="ts">
/** Approved definition forms. All writes are CR sections; no runtime metrics or arbitrary SQL. */
import { computed, reactive, ref, watch } from 'vue';
import { message } from 'ant-design-vue';
import { useUnsavedGuard } from '@/composables/useUnsavedGuard';
import { useConfirmStore } from '@/stores/confirm';
import { useReadonly } from '@/composables/useReadonly';
import { currentApiContext } from '@/api/http';
import { definitionChoices, definitionDetail, definitionProperties, definitionContext, saveDefinition, type DefinitionKind, type DefinitionValues, type DefinitionProperty, type DefinitionOption } from '@/api/definitionForms';
import { fetchCR, fetchCRItems, isStaticReal } from '@/api/static';
import { draftFields, draftRows } from './useStaticCr';
import StartChangeModal from './StartChangeModal.vue';
import AppPagination from '@/components/AppPagination.vue';
const props = defineProps<{ kind: DefinitionKind; targetId?: string; crId?: string; newTarget?: boolean }>();
const open = defineModel<boolean>('open', { default: false });
const emit = defineEmits<{ saved: [result: { id: string; crId: string }] }>();
const { readonly, reason } = useReadonly();
const confirm = useConfirmStore();
const kindName = computed(() => ({ object: '对象类型', metric: '指标', dimension: '维度与字典' })[props.kind]);
const opts = ref<{ join?: string; title?: string; desc?: string }>();
const choose = computed({ get: () => open.value && !props.targetId && !opts.value, set: (v: boolean) => { if (!v && !opts.value) open.value = false; } });
const visible = computed(() => open.value && (!!props.targetId || !!opts.value));
const resumeApi = ref('');
const loading = ref(false), busy = ref(false), error = ref(''), sourceError = ref('');
const original = ref<DefinitionValues>(), originalProperties = ref<DefinitionProperty[]>([]), resume = ref<string>();
const values = reactive<DefinitionValues>({ name: '', api: '', group: '', owner: '' });
let lockedProperties = new WeakSet<object>();
const previousProperties = ref<DefinitionProperty[]>([]);
const propertyPage = ref(1);
const properties = ref<DefinitionProperty[]>([]), sourceProperties = ref<DefinitionProperty[]>([]);
const choices = reactive<{ owners: DefinitionOption[]; objects: DefinitionOption[]; metrics: DefinitionOption[]; dimensions: DefinitionOption[] }>({ owners: [], objects: [], metrics: [], dimensions: [] });
interface Level { name: string; api: string; source: string; note?: string }
interface Variant { code: string; label: string; default: boolean; api?: string; version?: string }
const previousLevels = ref<Level[]>([]);
const levels = ref<Level[]>([]), variants = ref<Variant[]>([]);
const initialSnapshot = ref('');
const formSignature = () => JSON.stringify([values, properties.value, variants.value, levels.value]);
const dirty = computed(() => visible.value && !loading.value && !!initialSnapshot.value && initialSnapshot.value !== formSignature());
const guard = useUnsavedGuard(dirty);
function close() { if (!busy.value) void guard(() => { open.value = false; }); }
let seq = 0, sourceSeq = 0;
const contextKey = () => JSON.stringify(currentApiContext());
watch(open, (v) => { if (v) { opts.value = undefined; resume.value = props.crId; resumeApi.value = props.targetId ?? ''; busy.value = false; } });
watch(() => [visible.value, props.kind, props.targetId, props.crId, contextKey()], () => { void load(); });
async function load() {
  const my = ++seq, key = contextKey();
  if (!visible.value) return;
  if (!isStaticReal()) { error.value = '定义表单仅用于真实接口模式'; loading.value = false; return; }
  loading.value = true; error.value = ''; sourceProperties.value = []; initialSnapshot.value = '';
  const defaults: DefinitionValues = { name: '', api: props.targetId ?? '', group: '', owner: '', domain: '', desc: '', conflict: '用户编辑优先', source: '', table: '', pk: '', sync: '', unit: '', version: 'v1', formula: '', dims: [], kind: '维度', object: '', attr: '', column: '', valueSrc: '', levels: [], variants: [] };
  Object.keys(values).forEach((k) => delete values[k]); Object.assign(values, defaults);
  properties.value = []; propertyPage.value = 1; variants.value = []; levels.value = []; original.value = undefined; originalProperties.value = [];
  try {
    const options = await definitionChoices(props.kind);
    if (my !== seq || key !== contextKey()) return;
    Object.assign(choices, options);
    if (props.targetId) {
      let fields = defaults, baseProperties: DefinitionProperty[] = [], items;
      if (props.newTarget) {
        if (!props.crId) throw new Error('新定义草稿未关联变更单');
        const ctx = definitionContext();
        await fetchCR(props.crId, ctx.options); if (my !== seq || key !== contextKey()) return;
        items = await fetchCRItems(props.crId, { kind: props.kind, targetId: props.targetId }, ctx.options);
      } else {
        const detail = await definitionDetail(props.kind, props.targetId, props.crId);
        fields = detail.fields; baseProperties = detail.properties; items = detail.items;
        original.value = structuredClone(fields); originalProperties.value = structuredClone(baseProperties);
      }
      if (my !== seq || key !== contextKey()) return;
      Object.assign(values, fields, draftFields(items, 'def'));
      properties.value = draftRows(baseProperties, items, 'properties', (p) => p.api, (r, p) => ({ ...p, ...(r.after as DefinitionProperty) }));
    }
    if (props.targetId) resumeApi.value = values.api;
    previousProperties.value = properties.value.map((p) => ({ ...p }));
    lockedProperties = new WeakSet(properties.value);
    if (!choices.owners.some((o) => o.id === values.owner)) {
      const matching = choices.owners.filter((o) => o.name === values.owner);
      if (matching.length === 1) values.owner = matching[0].id;
    }
    variants.value = ((values.variants ?? []) as Variant[]).map((v) => ({ ...v }));
    levels.value = ((values.levels ?? []) as Level[]).map(({ name, api, source, note }) => ({ name, api, source, note }));
    previousLevels.value = levels.value.map((l) => ({ ...l }));
    initialSnapshot.value = formSignature();
  } catch (e) { if (my === seq && key === contextKey()) error.value = e instanceof Error ? e.message : String(e); }
  finally { if (my === seq && key === contextKey()) loading.value = false; }
}
async function loadSourceProperties() {
  const id = values.object;
  const my = ++sourceSeq, key = contextKey(); sourceProperties.value = []; sourceError.value = '';
  if (props.kind !== 'dimension' || !id || !visible.value) return;
  try { const rows = await definitionProperties(String(id)); if (my === sourceSeq && key === contextKey() && visible.value) sourceProperties.value = rows; }
  catch (e) { if (my === sourceSeq && key === contextKey() && visible.value) sourceError.value = `来源属性加载失败：${e instanceof Error ? e.message : String(e)}`; }
}
watch(() => values.object, loadSourceProperties);
watch(contextKey, () => { if (open.value) { open.value = false; message.info('身份或版本已切换，请重新打开定义表单'); } });
const ownerOptions = computed(() => choices.owners.map((o) => ({ value: o.id, label: o.name })));
const objectOptions = computed(() => choices.objects.map((o) => ({ value: o.id, label: `${o.name}（${o.api}）` })));
const propertyOptions = computed(() => sourceProperties.value.map((p) => ({ value: p.api, label: `${p.name}（${p.api}）` })));
const metricOptions = computed(() => choices.metrics.filter((m) => m.status === '已发布').map((m) => ({ value: m.id, label: `${m.name}（${m.api}）` })));
const dimensionOptions = computed(() => choices.dimensions.map((d) => ({ value: d.name, label: `${d.name}（${d.api}）` })));
const validation = computed(() => {
  if (!values.name.trim() || !values.api.trim() || !values.owner || !choices.owners.some((o) => o.id === values.owner)) return '请填写名称、API名并选择负责人';
  if (values.name.length > 64 || values.group.length > 32) return '名称最多64字，分组最多32字';
  if (!/^[A-Za-z][A-Za-z0-9_]{0,63}$/.test(values.api)) return 'API名需字母开头，仅含字母、数字和下划线，最多64位';
  if (resume.value && resumeApi.value && values.api !== resumeApi.value) return '已保存草稿的API名不能改，请继续编辑原目标';
  const existing = props.kind === 'object' ? choices.objects : props.kind === 'metric' ? choices.metrics : choices.dimensions;
  if (!props.targetId && existing.some((item) => item.api === values.api || item.id === values.api)) return 'API名已存在，请使用新的API名';
  if (props.kind === 'object') {
    if (properties.value.some((p) => !p.api.trim() || !p.name.trim() || !/^[a-z][a-z0-9_]*$/.test(p.api))) return '属性名称必填，属性API需小写字母开头，仅含字母、数字和下划线';
    if (new Set(properties.value.map((p) => p.api)).size !== properties.value.length) return '属性API名不能重复';
    if (values.pk && !properties.value.some((p) => p.api === values.pk)) return '主键须引用当前属性';
    if (properties.value.filter((p) => p.title).length > 1) return '标题属性只能有一个';
  }
  if (props.kind === 'dimension') {
    if (!values.object) return '请选择来源对象';
    if (values.attr && !sourceProperties.value.some((p) => p.api === values.attr)) return '请选择该对象的真实属性';
    if (levels.value.length > 32) return '层级至多32行';
    if (levels.value.some((l) => !l.name.trim() || !l.api.trim())) return '层级名称和API名必填';
    if (new Set(levels.value.map((l) => l.api)).size !== levels.value.length || new Set(levels.value.map((l) => l.name)).size !== levels.value.length) return '层级API名和名称分别不能重复';
  }
  if (props.kind === 'metric') {
    if (variants.value.length > 32) return '口径变体至多32行';
    if (variants.value.some((v) => !v.code || !v.label.trim() || !choices.metrics.some((m) => m.id === v.code && m.status === '已发布'))) return '口径变体须选择已发布指标并填写标签';
    if (new Set(variants.value.map((v) => v.code)).size !== variants.value.length || new Set(variants.value.map((v) => v.label.trim())).size !== variants.value.length) return '变体指标和标签分别不能重复';
    if (variants.value.length && variants.value.filter((v) => v.default).length !== 1) return '非空口径变体须有且只有一个默认项';
  }
  return '';
});
const canSave = computed(() => isStaticReal() && !readonly.value && !loading.value && !busy.value && !error.value && !sourceError.value && !validation.value);
function isExistingProperty(p: DefinitionProperty) { return lockedProperties.has(p); }
function addProperty() { properties.value.push({ api: '', name: '', type: 'string', group: '其他', source: '', desc: '', visibility: 'normal', searchable: false, filterable: false, sortable: false, listed: true }); propertyPage.value = Math.ceil(properties.value.length/4); }
function changeAttr(value: string) { const p = sourceProperties.value.find((p) => p.api === value); values.column = p?.source ?? ''; }
function setDefault(index: number) { variants.value.forEach((v, i) => v.default = i === index); }
function moveLevel(index: number, by: number) { const other = index + by; if (other < 0 || other >= levels.value.length) return; [levels.value[index], levels.value[other]] = [levels.value[other], levels.value[index]]; }
function variantTarget(code: string) { return choices.metrics.find((m) => m.id === code); }
async function save() {
  if (!canSave.value) return;
  const key = contextKey(), my = seq; busy.value = true;
  try {
    const removed = props.kind === 'object' ? previousProperties.value.filter((p) => !properties.value.some((v) => v.api === p.api)).map((p) => p.name) : props.kind === 'dimension' ? previousLevels.value.filter((l) => !levels.value.some((v) => v.api === l.api)).map((l) => l.name) : [];
    if (removed.length && !await confirm.ask({ title: '确认从定义草稿中移除', target: values.name, fields: [{ label: '移除条目', value: removed.join('、') }], impact: '改动先保存到草稿，审核并整版发布后生效；已发布定义在发布前保持可用。', okText: '保存移除改动', danger: true })) return;
    if (my !== seq || key !== contextKey() || readonly.value) return;
    const payload = { ...values, levels: levels.value.map(({ name, api, source, note }) => ({ name, api, source, note })), variants: variants.value.map((v) => ({ code: v.code, label: v.label.trim(), default: v.default })) };
    const rows = properties.value.map((p) => ({ ...p, pk: p.api === values.pk ? true : p.pk ? false : undefined, ...(p.visibility === 'hidden' ? { filterable: false, sortable: false, listed: false } : {}), ...(p.sensitive ? { filterable: false } : {}) }));
    const result = await saveDefinition(props.kind, payload, rows, original.value, originalProperties.value, opts.value ?? { join: props.crId }, resume.value, previousProperties.value);
    if (my !== seq || key !== contextKey()) return;
    initialSnapshot.value = formSignature();
    message.success(`已保存到草稿 ${result.crId}，审核发布后生效`); emit('saved', result); open.value = false;
  } catch (e) { if (my === seq && key === contextKey()) { resume.value = (e as { crId?: string }).crId ?? resume.value; if (resume.value && !resumeApi.value) resumeApi.value = values.api; message.error(`${e instanceof Error ? e.message : String(e)}${resume.value ? `（草稿 ${resume.value}，输入已保留）` : ''}`); } }
  finally { if (my === seq && key === contextKey()) busy.value = false; }
}
</script>
<template>
  <StartChangeModal v-model:open="choose" :target-name="`新建${kindName}`" @ok="(o) => opts = o" />
  <a-drawer :open="visible" :title="`${targetId ? '编辑' : '新建'}${kindName}`" :width="1000" :closable="!busy" :mask-closable="!busy" @close="close">
    <a-alert v-if="readonly" type="warning" :message="reason" show-icon />
    <a-alert v-if="error" type="error" :message="error" show-icon><template #action><a-button size="small" @click="load">重试加载</a-button></template></a-alert>
    <a-alert v-if="sourceError" type="error" :message="sourceError" show-icon><template #action><a-button size="small" @click="loadSourceProperties">重试来源属性</a-button></template></a-alert>
    <a-spin :spinning="loading">
      <a-form layout="vertical" :disabled="readonly || busy || loading || !!error" class="definition-form">
        <div class="definition-grid">
          <a-form-item label="名称" required><a-input v-model:value="values.name" :maxlength="64" /></a-form-item>
          <a-form-item label="API名" required><a-input v-model:value="values.api" :disabled="!!targetId || !!resume" :maxlength="64" /></a-form-item>
          <a-form-item label="分组"><a-input v-model:value="values.group" :maxlength="32" /></a-form-item>
          <a-form-item label="负责人" required><a-select v-model:value="values.owner" :options="ownerOptions" placeholder="选择真实用户" /></a-form-item>
        </div>
        <template v-if="kind === 'object'">
          <div class="definition-grid">
            <a-form-item label="业务域"><a-input v-model:value="values.domain" :maxlength="64" /></a-form-item>
            <a-form-item label="冲突策略"><a-select v-model:value="values.conflict" :options="['用户编辑优先','源系统优先'].map((v) => ({ value: v }))" /></a-form-item>
            <a-form-item label="数据连接（导入时填入）"><a-input v-model:value="values.source" disabled /></a-form-item>
            <a-form-item label="源表（导入时填入）"><a-input v-model:value="values.table" disabled /></a-form-item>
            <a-form-item label="主键属性"><a-select v-model:value="values.pk" allow-clear :options="properties.filter((p) => p.api).map((p) => ({ value: p.api, label: p.name || p.api }))" /></a-form-item>
            <a-form-item label="同步说明"><a-input v-model:value="values.sync" :maxlength="128" /></a-form-item>
          </div>
          <a-form-item label="说明"><a-textarea v-model:value="values.desc" :rows="2" /></a-form-item>
          <div class="definition-heading"><span class="t-14 fw-600">属性</span><a-button size="small" @click="addProperty">新增属性</a-button></div>
          <div v-for="(p, localIndex) in properties.slice((propertyPage-1)*4,propertyPage*4)" :key="(propertyPage-1)*4+localIndex" class="definition-row">
            <div class="definition-grid">
              <a-form-item label="名称"><a-input v-model:value="p.name" :maxlength="64" /></a-form-item><a-form-item label="API名"><a-input v-model:value="p.api" :maxlength="64" :disabled="isExistingProperty(p)" /></a-form-item>
              <a-form-item label="类型"><a-select v-model:value="p.type" :options="['string','int','integer','decimal','boolean','timestamp','date','enum','json'].map((v) => ({ value: v }))" /></a-form-item><a-form-item label="来源列"><a-input v-model:value="p.source" :maxlength="256" /></a-form-item>
              <a-form-item label="分组"><a-input v-model:value="p.group" :maxlength="32" /></a-form-item><a-form-item label="枚举字典"><a-input v-model:value="p.enumName" :maxlength="64" :disabled="p.type !== 'enum'" /></a-form-item>
            </div>
            <div class="definition-grid"><a-form-item label="展示层级"><a-select v-model:value="p.visibility" :options="[{ value: 'prominent', label: '突出' }, { value: 'normal', label: '普通' }, { value: 'hidden', label: '隐藏' }]" /></a-form-item><a-form-item label="排序序号"><a-input-number v-model:value="p.order" :min="0" :precision="0" /></a-form-item><a-form-item label="列表列宽"><a-input-number v-model:value="p.listWidth" :min="1" :precision="0" /></a-form-item><a-form-item label="显示格式 / 单位"><a-space><a-input v-model:value="p.format" :maxlength="32" placeholder="格式" /><a-input v-model:value="p.unit" :maxlength="16" placeholder="单位" /></a-space></a-form-item></div>
            <a-input v-model:value="p.desc" placeholder="属性说明" /><a-space class="definition-flags"><a-checkbox v-model:checked="p.title">标题</a-checkbox><a-checkbox v-model:checked="p.dim">可分组</a-checkbox><a-checkbox v-model:checked="p.sensitive">敏感</a-checkbox><a-checkbox v-model:checked="p.searchable">可搜索</a-checkbox><a-checkbox v-model:checked="p.filterable" :disabled="p.visibility === 'hidden' || p.sensitive">可筛选</a-checkbox><a-checkbox v-model:checked="p.sortable" :disabled="p.visibility === 'hidden'">可排序</a-checkbox><a-checkbox v-model:checked="p.listed" :disabled="p.visibility === 'hidden'">列表显示</a-checkbox><a-button size="small" @click="properties.splice((propertyPage-1)*4+localIndex, 1); propertyPage = Math.max(1, Math.min(propertyPage, Math.ceil(properties.length/4)))">移除属性</a-button></a-space>
          </div>
          <AppPagination v-model:page="propertyPage" :total="properties.length" :size="4" variant="simple" />
          <a-alert type="info" message="此处只保存定义草稿；源表未加入受控映射时不提供业务查询，不自动授予SQL权限。" />
        </template>
        <template v-else-if="kind === 'metric'">
          <div class="definition-grid"><a-form-item label="单位"><a-input v-model:value="values.unit" :maxlength="16" /></a-form-item><a-form-item label="口径版本"><a-input v-model:value="values.version" :maxlength="16" /></a-form-item></div>
          <a-form-item label="公式（口径定义）"><a-textarea v-model:value="values.formula" :rows="3" placeholder="填写指标计算公式" /></a-form-item>
          <a-form-item label="允许维度"><a-select v-model:value="values.dims" mode="multiple" :options="dimensionOptions" /></a-form-item>
          <div class="definition-heading"><span class="t-14 fw-600">口径变体</span><a-button size="small" :disabled="variants.length >= 32" @click="variants.push({ code: '', label: '', default: !variants.length })">新增变体</a-button></div>
          <div v-for="(v, i) in variants" :key="i" class="definition-variant"><a-select v-model:value="v.code" :options="metricOptions" placeholder="已发布指标" /><a-input v-model:value="v.label" placeholder="口径标签" :maxlength="64" /><span class="t-12 c-3">{{ variantTarget(v.code)?.api ?? '—' }} · {{ variantTarget(v.code)?.version ?? '—' }}</span><a-radio :checked="v.default" @change="setDefault(i)">默认</a-radio><a-button size="small" @click="variants.splice(i, 1)">移除</a-button></div>
        </template>
        <template v-else>
          <div class="definition-grid"><a-form-item label="类型"><a-select v-model:value="values.kind" :options="[{ value: '维度' }, { value: '码表' }]" /></a-form-item><a-form-item label="来源对象" required><a-select v-model:value="values.object" :options="objectOptions" @change="values.attr = ''; values.column = ''" /></a-form-item><a-form-item label="来源属性"><a-select v-model:value="values.attr" allow-clear :options="propertyOptions" @change="changeAttr" /></a-form-item><a-form-item label="源列"><a-input v-model:value="values.column" disabled /></a-form-item><a-form-item label="取值来源"><a-input v-model:value="values.valueSrc" :maxlength="256" /></a-form-item><a-form-item label="同步说明"><a-input v-model:value="values.sync" :maxlength="128" /></a-form-item></div>
          <a-form-item label="说明"><a-textarea v-model:value="values.desc" :rows="2" /></a-form-item>
          <div class="definition-heading"><span class="t-14 fw-600">层级（从上到下）</span><a-button size="small" :disabled="levels.length >= 32" @click="levels.push({ name: '', api: '', source: '', note: '' })">新增层级</a-button></div>
          <div v-for="(l, i) in levels" :key="i" class="definition-row"><div class="definition-grid"><a-input v-model:value="l.name" :maxlength="64" placeholder="层级名称" /><a-input v-model:value="l.api" :maxlength="64" placeholder="API名" /><a-input v-model:value="l.source" :maxlength="256" placeholder="来源说明" /><a-input v-model:value="l.note" :maxlength="4000" placeholder="层级说明" /></div><a-space><a-button size="small" :disabled="i === 0" @click="moveLevel(i, -1)">上移</a-button><a-button size="small" :disabled="i === levels.length-1" @click="moveLevel(i, 1)">下移</a-button><a-button size="small" @click="levels.splice(i, 1)">移除</a-button></a-space></div>
          <span class="t-12 c-3">取值、映射与问数设置保存定义后在已有页签编辑；层级运行数量由后台保留。</span>
        </template>
      </a-form>
    </a-spin>
    <template #footer><div class="definition-footer"><span class="t-12 c-3">{{ validation || (resume ? `草稿 ${resume} · 审核发布后生效` : '保存到变更单，审核发布后生效') }}</span><a-button :disabled="busy" @click="close">取消</a-button><a-button type="primary" :loading="busy" :disabled="!canSave" @click="save">保存到草稿</a-button></div></template>
  </a-drawer>
</template>
<style scoped>
.definition-form { font-size: 14px; }
.definition-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 0 16px; }
.definition-heading, .definition-footer, .definition-flags { display: flex; align-items: center; gap: 12px; }
.definition-heading { justify-content: space-between; margin: 16px 0 12px; }
.definition-row { border: 1px solid var(--g5); padding: 12px; margin-bottom: 12px; border-radius: 4px; }
.definition-flags { margin-top: 12px; flex-wrap: wrap; }
.definition-variant { display: grid; grid-template-columns: 240px 180px 1fr auto auto; gap: 8px; align-items: center; margin-bottom: 12px; }
.definition-footer > span { flex: 1; }
.definition-row .definition-grid { row-gap: 12px; margin-bottom: 12px; }
</style>
