<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue';
import type { ActionParam, ActionType } from '@/mock/actionEvent';
import { actionContextKey, getActionExecutionOptions, type ActionExecutionOptions } from '@/api/action';
import ActionValueSource from './ActionValueSource.vue';
const props = defineProps<{ section: 'edits' | 'effects'; action: ActionType; params: ActionParam[] }>();
const model = defineModel<Record<string, any>>({ required: true });
const options = ref<ActionExecutionOptions>({ objects: [], relations: [], functions: [], events: [], targets: [] });
const loading = ref(true), error = ref(''), legacy = ref('');
const validationError = ref('');
const target = ref('context'), relationTarget = ref('');
const assignments = ref<{ field: string; source: Record<string, any> }[]>([]);
const contextKey = actionContextKey();
const object = computed(() => options.value.objects.find(o => o.id === model.value.obj));
const relation = computed(() => options.value.relations.find(o => o.id === model.value.obj));
const event = computed(() => options.value.events.find(e => e.id === model.value.target));
const fields = computed(() => props.section === 'edits' ? object.value?.properties ?? [] : event.value?.payload ?? []);
const standard = computed(() => props.section === 'effects' && model.value.kind === '事件' && ['ActionExecuted', 'ActionOutcomeUnknown'].includes(model.value.target.split(' ')[0]));
const isRelation = computed(() => props.section === 'edits' && model.value.op === '关联');
function targets(kind?: string) {
  const obj = options.value.objects.find(o => o.id === kind);
  return [ ...(kind === props.action.targetKind ? [{ value: 'context', label: '当前动作目标对象' }] : []),
    ...props.params.filter(p => p.type.startsWith('对象引用 · ') && [kind, obj?.api, obj?.name].includes(p.type.split(' · ')[1]))
      .map(p => ({ value: p.api, label: `参数：${p.name}` })) ];
}
const objectTargets = computed(() => targets(isRelation.value ? relation.value?.from : props.section === 'edits' ? object.value?.id : event.value?.objectKind));
const toTargets = computed(() => targets(relation.value?.to));
const source = (key: string) => key === 'context' ? { context: 'id' } : { param: key };
const sourceKey = (value: any) => value?.context === 'id' ? 'context' : value?.param ?? '';
function hydrate() {
  const raw = props.section === 'edits' ? model.value.field : model.value.note;
  legacy.value = '';
  if (!raw || standard.value) { assignments.value = []; return; }
  try {
    const value = JSON.parse(raw);
    if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error();
    target.value = sourceKey(isRelation.value ? value.from : props.section === 'edits' ? value.target ?? { context: 'id' } : value.object);
    relationTarget.value = sourceKey(value.to);
    const values = props.section === 'edits' ? value.values ?? (isRelation.value ? {} : value) : value.payload;
    assignments.value = Object.entries(values ?? {}).map(([field, value]) => ({field, source: value as Record<string, any>}));
  } catch { legacy.value = raw; assignments.value = []; }
}
function sync() {
  if (legacy.value || standard.value) return;
  const values = Object.fromEntries(assignments.value.map(row => [row.field, row.source]));
  if (props.section === 'edits') model.value.field = JSON.stringify(isRelation.value
    ? { from: source(target.value), to: source(relationTarget.value) }
    : { target: source(target.value), values });
  else model.value.note = JSON.stringify(model.value.kind === '事件'
    ? { version: event.value?.version ?? '', object: source(target.value), payload: values } : { payload: values });
}
function reset() { legacy.value = ''; assignments.value = []; target.value = objectTargets.value[0]?.value ?? ''; relationTarget.value = toTargets.value[0]?.value ?? ''; sync(); }
watch([assignments, target, relationTarget], sync, { deep: true });
function selectedObject() { reset(); }
function addField() { assignments.value.push({field: '', source: {param: ''}}); }
function validSource(value: Record<string, any>): boolean {
  if (!value || typeof value !== 'object') return false;
  if ('param' in value) return props.params.some(p => p.api === value.param);
  if ('value' in value) return true;
  return !!options.value.functions.find(f => f.id === value.function && f.version === value.version)
    && Object.values(value.inputs ?? {}).every(v => validSource(v as Record<string, any>));
}
function validate() {
  sync();
  validationError.value = '';
  const invalid = (message: string) => { validationError.value = message; return false; };
  if (loading.value) return invalid('正在加载可选定义，请稍候');
  if (error.value) return invalid('定义加载失败，请关闭后重试');
  if (legacy.value) return invalid('请先点击“重新配置”填写可执行操作');
  if (standard.value) return true;
  if (props.section === 'edits' && (isRelation.value ? !relation.value : !object.value)) return invalid(`请选择${isRelation.value ? '关系' : '对象类型'}`);
  if (props.section === 'effects' && !model.value.target) return invalid('请选择副作用目标');
  if (props.section === 'edits' || model.value.kind === '事件') {
    if (!objectTargets.value.some(t => t.value === target.value)) return invalid(`请选择${model.value.op === '新建' ? '新建模板对象' : '目标对象'}`);
  }
  if (isRelation.value) return toTargets.value.some(t => t.value === relationTarget.value) || invalid('请选择关联目标对象');
  if (props.section === 'effects' && !(model.value.kind === '事件' ? !!event.value : options.value.targets.some(t => t.id === model.value.target && t.actions.includes(props.action.id)))) return invalid('请选择有效的已发布或已配置目标');
  if (props.section === 'edits' && !assignments.value.length) return invalid('请至少添加一项属性赋值');
  const missing = assignments.value.findIndex(r => !r.field || !validSource(r.source));
  if (missing >= 0) return invalid(`第 ${missing + 1} 项：${!assignments.value[missing].field ? '请选择映射字段' : '请完整填写取值参数、固定值或计算函数版本与输入'}`);
  if (new Set(assignments.value.map(r => r.field)).size !== assignments.value.length) return invalid('同一字段只能赋值一次，请删除重复项');
  return true;
}
defineExpose({ validate });
onMounted(async () => {
  try { const result = await getActionExecutionOptions(); if (contextKey === actionContextKey()) options.value = result; }
  catch (e) { error.value = e instanceof Error ? e.message : String(e); }
  finally { loading.value = false; hydrate(); }
});
</script>

<template>
  <a-spin :spinning="loading">
    <a-alert v-if="validationError" type="error" :message="validationError" show-icon role="alert" />
    <a-alert v-if="error" type="error" :message="error" show-icon />
    <a-alert v-else-if="legacy" type="warning" message="原定义尚无可执行映射，保留内容如下。重新配置后保存到变更单。" show-icon>
      <template #description><p>{{ legacy }}</p><a-button size="small" @click="reset">重新配置</a-button></template>
    </a-alert>
    <a-form layout="vertical" class="execution-form">
      <template v-if="section === 'edits'">
        <a-form-item label="操作" required><a-select v-model:value="model.op" :options="['修改','新建','关联'].map(value => ({value,label:value}))" @change="model.obj = ''; reset()" /></a-form-item>
        <a-form-item :label="isRelation ? '关系' : '对象类型'" required><a-select v-model:value="model.obj" :options="(isRelation ? options.relations : options.objects).map(o => ({value:o.id,label:o.name}))" @change="selectedObject" /></a-form-item>
      </template>
      <template v-else>
        <a-form-item label="副作用" required><a-select v-model:value="model.kind" :options="['通知','事件','Webhook'].map(value => ({value,label:value}))" @change="model.target = ''; reset()" /></a-form-item>
        <a-form-item label="目标" required><a-select v-model:value="model.target" :options="model.kind === '事件' ? [...options.events.map(e => ({value:e.id,label:`${e.name} · ${e.version}`})), {value:'ActionExecuted',label:'标准执行成功事件'},{value:'ActionOutcomeUnknown',label:'标准结果未知事件'}] : options.targets.filter(t => t.actions.includes(action.id)).map(t => ({value:t.id,label:t.name}))" placeholder="选择已发布事件或已配置投递目标" @change="reset" /></a-form-item>
        <a-form-item label="执行时机" required><a-select v-model:value="model.when" :options="[{value:'执行成功后',label:'执行成功后'},{value:'结果未知时',label:'结果未知时'}]" /></a-form-item>
        <p v-if="model.kind !== '事件' && !options.targets.length" class="form-note">尚无已启用的外部投递目标，请由管理员配置系统、目标路径及动作授权。</p>
      </template>
      <template v-if="!standard">
        <a-form-item v-if="section === 'edits' || model.kind === '事件'" :label="model.op === '新建' && section === 'edits' ? '新建模板对象' : isRelation ? '关联发起对象' : '目标对象'" required><a-select v-model:value="target" :options="objectTargets" placeholder="选择当前目标或对象引用参数" /></a-form-item>
        <a-form-item v-if="isRelation" label="关联目标对象" required><a-select v-model:value="relationTarget" :options="toTargets" /></a-form-item>
        <template v-if="!isRelation">
          <div v-for="(row, index) in assignments" :key="index" class="assignment">
            <a-select v-if="fields.length" v-model:value="row.field" aria-label="映射字段" placeholder="选择属性/载荷字段" :options="fields.map(f => ({value:f.api,label:`${f.name}（${f.api}）`}))" />
            <a-input v-else v-model:value="row.field" aria-label="载荷字段名称" placeholder="载荷字段名称" />
            <ActionValueSource v-model="row.source" :params="params" :functions="options.functions" />
            <a-button danger type="text" @click="assignments.splice(index, 1)">删除</a-button>
          </div>
          <a-button size="small" @click="addField">添加{{ section === 'edits' ? '属性赋值' : '载荷字段' }}</a-button>
        </template>
      </template>
      <p v-else class="form-note">标准事件使用系统实际执行上下文，不需要自定义载荷。</p>
    </a-form>
  </a-spin>
</template>

<style scoped>
.execution-form { padding-top: 12px; }
.assignment { display: grid; grid-template-columns: minmax(130px, 1fr) minmax(240px, 2fr) 48px; gap: 8px; align-items: start; margin-bottom: 12px; }
.form-note { color: var(--text-3); font-size: 12px; line-height: 1.6; margin-bottom: 16px; }
@media (max-width: 680px) { .assignment { grid-template-columns: 1fr; } }
</style>
