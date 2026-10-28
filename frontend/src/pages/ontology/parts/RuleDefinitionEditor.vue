<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue';
import type { RuleDefinitionPatch, RuleMetaDto } from '@/api/dynamic';
import type { RuleCond, RuleDef, RuleTrigger } from '@/mock/rules';

const props = defineProps<{ rule: RuleDef; meta: RuleMetaDto; editable: boolean; section: 'conditions' | 'trigger'; conditionChanged?: boolean }>();
const emit = defineEmits<{ update: [patch: RuleDefinitionPatch] }>();
const error = ref('');
const dialog = reactive({ open: false, index: -1, source: 'object', field: '', metric: '', version: undefined as number | undefined,
  functionId: '', functionVersion: '', functionArgs: [] as string[], relationId: '', relationField: '',
  period: 'day' as 'day' | 'week', transform: 'value' as 'value' | 'prev' | 'wow', elapsed: 'value', op: '=', value: '' });
const exprEditing = ref(false), exprInput = ref('');
watch(() => props.rule.id, () => { dialog.open = false; exprEditing.value = false; error.value = ''; });
watch(() => props.editable, value => { if (!value) { dialog.open = false; exprEditing.value = false; } });
const objects = computed(() => props.meta.objects.map(o => ({ value: o.id, label: o.name })));
const object = computed(() => props.meta.objects.find(o => o.id === props.rule.objKind));
const events = computed(() => props.meta.events.filter(e => e.objKind === props.rule.objKind));
const event = computed(() => events.value.find(e => e.id === props.rule.trigger.eventId));
const fieldOptions = computed(() => (dialog.source === 'event' ? event.value?.fields : object.value?.fields)?.map(f => ({ value: f.expression, label: `${f.name} · ${f.api}` })) ?? []);
const functions = computed(() => [...new Map((props.meta.functions ?? []).map(f => [f.id, { value: f.id, label: `${f.name} · ${f.id}` }])).values()]);
const functionVersions = computed(() => (props.meta.functions ?? []).filter(f => f.id === dialog.functionId));
const selectedFunction = computed(() => functionVersions.value.find(f => f.version === dialog.functionVersion));
const argumentOptions = computed(() => [
  ...(object.value?.fields ?? []).map(f => ({ value: f.expression, label: `对象 · ${f.name}（${f.api}）` })),
  ...(props.rule.trigger.kind === '事件' ? event.value?.fields ?? [] : []).map(f => ({ value: f.expression, label: `事件 · ${f.name}（${f.api}）` })),
]);
const relations = computed(() => (props.meta.relations ?? []).filter(r => r.from === props.rule.objKind));
const selectedRelation = computed(() => relations.value.find(r => r.id === dialog.relationId));
function parseBoundExpression(expression: string) {
  const fn = /^rule_fn\(("[^"\\]*"),\s*("[^"\\]*")(?:,\s*(.*))?\)$/.exec(expression);
  const relation = /^related\(("[^"\\]*"),\s*("[^"\\]*")\)$/.exec(expression);
  try {
    if (fn) return { source: 'function', functionId: JSON.parse(fn[1]), functionVersion: JSON.parse(fn[2]), functionArgs: fn[3] ? fn[3].split(',').map(v => v.trim()) : [] };
    if (relation) return { source: 'relation', relationId: JSON.parse(relation[1]), relationField: JSON.parse(relation[2]) };
  } catch { /* Unrecognised legacy expression stays editable in the expression box. */ }
  return null;
}
function setFunction(id: string) { dialog.functionId = id; dialog.functionVersion = ''; dialog.functionArgs = []; }
function setFunctionVersion(version: string) { dialog.functionVersion = version; dialog.functionArgs = []; }
const metrics = computed(() => props.meta.metrics.map(m => ({ value: m.id, label: `${m.name} · ${m.id}` })));
const metric = computed(() => props.meta.metrics.find(m => m.id === dialog.metric));
const versions = computed(() => (metric.value?.versions ?? []).flatMap(v => {
  const match = /^v([1-9]\d*)$/.exec(v); return match ? [{ value: Number(match[1]), label: `口径 ${v}` }] : [];
}));
const cols = computed(() => [
  { title: '指标 / 字段', key: 'field' }, { title: '取值方式', key: 'binding', width: 200 },
  { title: '比较', dataIndex: 'op', width: 64 }, { title: '阈值', dataIndex: 'value', width: 140 },
  ...(props.editable ? [{ title: '操作', key: 'edit', width: 110 }] : []),
]);
const conditionRows = computed(() => props.rule.conds.map((condition, index) => ({ ...condition, _index: index })));
const periods = { day: '日', week: '周' }, transforms = { value: '当前值', prev: '上期值', wow: '周环比' };
function openCondition(index = -1) {
  if (!props.editable) return;
  const condition = props.rule.conds[index];
  const expression = condition?.expression ?? '';
  const elapsed = /^(hours_since|minutes_since)\((.+)\)$/.exec(expression);
  const field = elapsed?.[2] ?? expression;
  const bound = parseBoundExpression(expression);
  Object.assign(dialog, { open: true, index, functionId: '', functionVersion: '', functionArgs: [], relationId: '', relationField: '',
    field, metric: condition?.metric ?? '', version: condition?.metricVer, period: condition?.period ?? 'day',
    transform: condition?.transform ?? 'value', elapsed: elapsed?.[1] ?? 'value', op: condition?.op ?? '=', value: condition?.value ?? '', ...(bound ?? {}), source: condition?.metric ? 'metric' : bound?.source ?? (field.startsWith('event.') ? 'event' : 'object') });
  error.value = '';
}
function selectMetric(id: string) {
  dialog.metric = id; dialog.version = undefined;
}
function saveCondition() {
  if (!props.editable) return;
  const field = fieldOptions.value.find(f => f.value === dialog.field);
  if (!dialog.value.trim()) { error.value = '请填写明确的比较值'; return; }
  if (!['=', '!=', '<', '>', '≤', '≥'].includes(dialog.op)) { error.value = '请选择支持的比较方式'; return; }
  let condition: RuleCond;
  if (dialog.source === 'metric') {
    if (!metric.value || !versions.value.some(v => v.value === dialog.version)) { error.value = '请选择指标和明确口径版本'; return; }
    condition = { metric: dialog.metric, metricVer: dialog.version, field: metric.value.name, op: dialog.op,
      value: dialog.value.trim(), period: dialog.period, transform: dialog.transform };
  } else if (dialog.source === 'function') {
    const fn = selectedFunction.value;
    if (!fn || fn.inputs.length !== dialog.functionArgs.length || fn.inputs.some((_, i) => !argumentOptions.value.some(option => option.value === dialog.functionArgs[i]))) { error.value = '请选择固定函数版本并为每个入参选择字段'; return; }
    condition = { field: fn.name, op: dialog.op, value: dialog.value.trim(), expression: `rule_fn(${[JSON.stringify(fn.id), JSON.stringify(fn.version), ...dialog.functionArgs].join(', ')})` };
  } else if (dialog.source === 'relation') {
    const relation = selectedRelation.value;
    const prop = relation?.fields.find(f => f.api === dialog.relationField);
    if (!relation || !prop) { error.value = '请选择已发布关系和目标属性'; return; }
    condition = { field: `${relation.name} · ${prop.name}`, op: dialog.op, value: dialog.value.trim(), expression: `related(${JSON.stringify(relation.id)}, ${JSON.stringify(prop.api)})` };
  } else {
    if (!field) { error.value = '请选择真实字段，显示名称不能代替字段绑定'; return; }
    condition = { field: field.label.split(' · ')[0], op: dialog.op, value: dialog.value.trim(),
      expression: dialog.elapsed === 'value' ? dialog.field : `${dialog.elapsed}(${dialog.field})` };
  }
  const conds = props.rule.conds.map(c => ({ ...c }));
  if (dialog.index < 0) conds.push(condition); else conds[dialog.index] = condition;
  emit('update', { conds }); dialog.open = false; error.value = '';
}
function removeCondition(index: number) { if (props.editable) emit('update', { conds: props.rule.conds.filter((_, i) => i !== index) }); }
function updateTrigger(patch: Partial<RuleTrigger>) { if (props.editable) emit('update', { trigger: { ...props.rule.trigger, ...patch } }); }
function changeKind(kind: RuleTrigger['kind']) {
  if (!props.editable) return;
  updateTrigger({ kind, enabled: false, text: '', ...(kind === '定时' ? { cron: '', timezone: 'Asia/Shanghai', catchupMinutes: 0 } : { eventId: '', filter: '全部' }) });
}
function applyExpression() {
  if (!props.editable) return;
  if (!exprInput.value.trim()) { error.value = '表达式不能为空'; return; }
  emit('update', { expr: exprInput.value.trim() }); exprEditing.value = false; error.value = '';
}
const triggerEnabled = computed(() => props.rule.trigger.kind === '事件' ? props.rule.trigger.enabled !== false : props.rule.trigger.enabled === true);
</script>

<template>
  <div v-if="section === 'conditions'" class="rule-editor">
    <div class="rule-editor-tools">
      <span class="fw-600">命中条件</span>
      <a-select :value="rule.logic" :disabled="!editable" :options="[{value:'且',label:'全部满足'},{value:'或',label:'任一满足'}]" class="logic-select" aria-label="条件组合" @change="(v: any) => emit('update', { logic: v })" />
      <span class="rule-editor-spacer" />
      <a-button v-if="editable" size="small" :disabled="rule.conds.length >= 40" @click="openCondition()">添加条件</a-button>
    </div>
    <a-table :columns="cols" :data-source="conditionRows" :pagination="false" size="small" row-key="_index" :locale="{emptyText:'暂无条件'}">
      <template #bodyCell="{column, record}">
        <template v-if="column.key === 'field'">{{ record.field }}<div v-if="record.metric" class="t-12 c-3">{{ record.metric }} · 口径 v{{ record.metricVer }}</div></template>
        <template v-else-if="column.key === 'binding'">
          <span v-if="record.metric" class="t-12">{{ record.period ? periods[record.period as 'day' | 'week'] : '原表达式时间桶' }} · {{ record.transform ? transforms[record.transform as 'value' | 'prev' | 'wow'] : '原表达式取值' }}</span>
          <span v-else class="t-12 num rule-editor-wrap">{{ record.expression ?? '按原始表达式' }}</span>
        </template>
        <template v-else-if="column.key === 'edit'"><a-space><a-button size="small" type="link" @click="openCondition(record._index)">编辑</a-button><a-button size="small" type="link" danger @click="removeCondition(record._index)">删除</a-button></a-space></template>
      </template>
    </a-table>
    <div class="rule-editor-tools"><span class="fw-600">{{ conditionChanged ? '原始表达式（保存后更新）' : '原始表达式' }}</span><span class="rule-editor-spacer" /><a-button v-if="editable" type="link" size="small" @click="exprInput = rule.expr; exprEditing = true">编辑表达式</a-button></div>
    <template v-if="exprEditing">
      <p class="t-12 c-3">固定版本函数：rule_fn("函数编号", "v版本", 对象.字段)；一跳关系：related("关系编号", "目标属性API")。函数输入须与固定版本签名一致；历史事件缺少对象快照时不可读取关联对象。</p>
      <a-textarea v-model:value="exprInput" :rows="4" :maxlength="4096" placeholder="填写明确字段或指标版本表达式" />
      <div class="rule-editor-tools"><a-button size="small" @click="exprEditing = false">取消</a-button><a-button size="small" type="primary" @click="applyExpression">采用表达式</a-button></div>
    </template>
    <pre v-else class="rule-expression num t-12">{{ rule.expr || '尚未生成表达式' }}</pre>
    <a-alert v-if="error && !dialog.open" type="error" :message="error" show-icon />
    <a-form layout="vertical" class="rule-settings">
      <a-form-item label="对象类型"><a-select :value="rule.objKind" :options="objects" :disabled="!editable" show-search option-filter-prop="label" @change="(v: any) => emit('update', {objKind:v})" /></a-form-item>
      <a-form-item label="去重键"><a-input :value="rule.dedupe" :disabled="!editable" :maxlength="200" placeholder="如：规则 + 订单" @update:value="(v: string) => emit('update', {dedupe:v})" /></a-form-item>
      <a-form-item label="冷却时间"><a-input :value="rule.cooldown" :disabled="!editable" :maxlength="64" placeholder="如：24 小时 或 不重复" @update:value="(v: string) => emit('update', {cooldown:v})" /></a-form-item>
    </a-form>
  </div>
  <div v-else class="rule-editor">
    <a-form layout="vertical" class="trigger-form">
      <a-form-item label="触发方式"><a-segmented :value="rule.trigger.kind" :disabled="!editable" :options="['定时','事件']" @change="(v: any) => changeKind(v)" /></a-form-item>
      <a-form-item label="发布后启用"><a-switch :checked="triggerEnabled" :disabled="!editable" @change="(v: any) => updateTrigger({enabled:!!v})" /><span class="t-12 c-3 trigger-note">{{ triggerEnabled ? '随已发布版本运行' : '未启用' }}</span></a-form-item>
      <template v-if="rule.trigger.kind === '定时'">
        <a-form-item label="计划表达式" required><a-input :value="rule.trigger.cron" :disabled="!editable" placeholder="五段计划，如：0 8 * * *" :maxlength="100" @update:value="(v: string) => updateTrigger({cron:v})" /></a-form-item>
        <a-form-item label="时区" required><a-select :value="rule.trigger.timezone ?? 'Asia/Shanghai'" :disabled="!editable" :options="[{value:'Asia/Shanghai',label:'Asia/Shanghai（UTC+8）'},{value:'UTC',label:'UTC'}]" @change="(v: any) => updateTrigger({timezone:v})" /></a-form-item>
        <a-form-item label="重启后补偿（分钟）"><a-input-number :value="rule.trigger.catchupMinutes ?? 0" :disabled="!editable" :min="0" :max="1440" :precision="0" class="trigger-number" @update:value="(v: any) => updateTrigger({catchupMinutes:v ?? 0})" /></a-form-item>
      </template>
      <template v-else>
        <a-form-item label="订阅事件" required><a-select :value="rule.trigger.eventId" :disabled="!editable" :options="events.map(e=>({value:e.id,label:`${e.name} · ${e.id}`}))" show-search option-filter-prop="label" placeholder="选择与对象绑定的已发布事件" @change="(v: any) => updateTrigger({eventId:v})" /></a-form-item>
        <a-form-item label="事件过滤"><a-input :value="rule.trigger.filter ?? '全部'" :disabled="!editable" :maxlength="1000" placeholder="全部，或明确载荷字段过滤条件" @update:value="(v: string) => updateTrigger({filter:v})" /></a-form-item>
      </template>
      <a-form-item label="计划说明"><a-input :value="rule.trigger.text" :disabled="!editable" :maxlength="200" placeholder="填写便于阅读的触发说明" @update:value="(v: string) => updateTrigger({text:v})" /></a-form-item>
      <a-form-item label="运行身份"><span class="num">{{ rule.trigger.runAsUserId ?? (editable ? meta.runAsUserId : '尚未绑定') }}</span></a-form-item>
    </a-form>
  </div>
  <a-modal v-model:open="dialog.open" :title="dialog.index < 0 ? '添加条件' : '编辑条件'" ok-text="采用条件" cancel-text="取消" :width="580" @ok="saveCondition">
    <a-alert v-if="error" type="error" :message="error" show-icon class="condition-error" />
    <a-form layout="vertical">
      <a-form-item label="取值来源"><a-select v-model:value="dialog.source" :options="[{value:'object',label:'对象属性'},{value:'event',label:'事件载荷',disabled:rule.trigger.kind !== '事件'},{value:'metric',label:'指标口径'},{value:'function',label:'固定版本函数'},{value:'relation',label:'关联对象属性'}]" @change="dialog.field = ''; dialog.metric = ''; dialog.version = undefined" /></a-form-item>
      <template v-if="dialog.source === 'metric'">
        <a-form-item label="指标" required><a-select :value="dialog.metric || undefined" :options="metrics" show-search option-filter-prop="label" placeholder="选择已发布指标" @change="(v: any) => selectMetric(v)" /></a-form-item>
        <a-form-item label="口径版本" required><a-select v-model:value="dialog.version" :options="versions" placeholder="明确选择版本" /></a-form-item>
        <div class="condition-pair"><a-form-item label="时间桶"><a-select v-model:value="dialog.period" :options="[{value:'day',label:'日'},{value:'week',label:'周'}]" /></a-form-item><a-form-item label="取值方式"><a-select v-model:value="dialog.transform" :options="[{value:'value',label:'当前值'},{value:'prev',label:'上期值'},{value:'wow',label:'周环比'}]" /></a-form-item></div>
      </template>
      <template v-else-if="dialog.source === 'function'">
        <a-form-item label="已发布函数" required><a-select :value="dialog.functionId || undefined" :options="functions" show-search option-filter-prop="label" placeholder="选择函数" @change="(v: string) => setFunction(v)" /></a-form-item>
        <a-form-item label="固定版本" required><a-select :value="dialog.functionVersion || undefined" :options="functionVersions.map(f => ({ value: f.version, label: f.version }))" placeholder="选择真实保存版本" @change="(v: string) => setFunctionVersion(v)" /></a-form-item>
        <a-form-item v-for="(input, i) in selectedFunction?.inputs ?? []" :key="input" :label="`入参 · ${input}`" required><a-select v-model:value="dialog.functionArgs[i]" :options="argumentOptions" show-search option-filter-prop="label" placeholder="选择字段" /></a-form-item>
        <p class="t-12 c-3">历史事件回放只能使用保存的事件字段；对象字段使用实时对象。</p>
      </template>
      <template v-else-if="dialog.source === 'relation'">
        <a-form-item label="已发布一跳关系" required><a-select :value="dialog.relationId || undefined" :options="relations.map(r => ({ value: r.id, label: `${r.name} · ${r.id}` }))" show-search option-filter-prop="label" placeholder="选择关系" @change="(v: string) => { dialog.relationId = v; dialog.relationField = ''; }" /></a-form-item>
        <a-form-item label="目标属性" required><a-select :value="dialog.relationField || undefined" :options="selectedRelation?.fields.map(f => ({ value: f.api, label: `${f.name} · ${f.api}` })) ?? []" show-search option-filter-prop="label" placeholder="选择目标属性" @change="(v: string) => dialog.relationField = v" /></a-form-item>
        <p class="t-12 c-3">仅显示权限可见、已发布且有受控外键的关系；缺少历史对象快照时无法回放。</p>
      </template>
      <template v-else>
        <a-form-item label="明确字段" required><a-select v-model:value="dialog.field" :options="fieldOptions" show-search option-filter-prop="label" placeholder="选择真实字段" /></a-form-item>
        <a-form-item label="取值方式"><a-select v-model:value="dialog.elapsed" :options="[{value:'value',label:'字段值'},{value:'hours_since',label:'距该时间的小时数'},{value:'minutes_since',label:'距该时间的分钟数'}]" /></a-form-item>
      </template>
      <div class="condition-pair"><a-form-item label="比较" required><a-select v-model:value="dialog.op" :options="['=','!=','<','>','≤','≥'].map(value=>({value,label:value}))" /></a-form-item><a-form-item label="比较值" required><a-input v-model:value="dialog.value" :maxlength="500" placeholder="数值、百分比或状态值" /></a-form-item></div>
    </a-form>
  </a-modal>
</template>

<style scoped>
.rule-editor { display: flex; flex-direction: column; gap: 12px; }
.rule-editor-tools { display: flex; align-items: center; gap: 12px; }
.rule-editor-spacer { flex: 1; }
.logic-select { width: 130px; }
.rule-editor-wrap { overflow-wrap: anywhere; }
.rule-expression { margin: 0; padding: 12px; border: var(--border); border-radius: var(--radius); background: var(--g2); white-space: pre-wrap; overflow-wrap: anywhere; }
.rule-settings { display: grid; grid-template-columns: repeat(3,minmax(0,1fr)); gap: 12px; }
.rule-settings :deep(.ant-form-item) { margin-bottom: 0; }
.trigger-form { max-width: 620px; display: grid; grid-template-columns: repeat(2,minmax(0,1fr)); gap: 0 20px; }
.trigger-note { margin-left: 12px; }
.trigger-number { width: 100%; }
.condition-error { margin-bottom: 16px; }
.condition-pair { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
@media (max-width: 1100px) { .rule-settings, .trigger-form { grid-template-columns: 1fr; } }
</style>
