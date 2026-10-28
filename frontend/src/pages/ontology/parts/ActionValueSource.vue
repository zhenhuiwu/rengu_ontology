<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import type { ActionParam } from '@/mock/actionEvent';
import type { ActionExecutionOptions } from '@/api/action';
const props = defineProps<{ params: ActionParam[]; functions: ActionExecutionOptions['functions']; nested?: boolean; functionOnly?: boolean }>();
const model = defineModel<Record<string, any>>({ required: true });
const kind = computed(() => 'param' in model.value ? 'param' : 'function' in model.value ? 'function' : 'value');
type LiteralType = 'text' | 'number' | 'boolean' | 'null' | 'structured';
function valueType(value: unknown): LiteralType {
  return value === null ? 'null' : typeof value === 'number' ? 'number' : typeof value === 'boolean' ? 'boolean' : typeof value === 'string' ? 'text' : 'structured';
}
const literalType = ref<LiteralType>('value' in model.value ? valueType(model.value.value) : 'text');
watch(() => model.value, value => { if ('value' in value) literalType.value = valueType(value.value); });
function changeLiteralType(type: LiteralType) {
  literalType.value = type;
  model.value = { value: type === 'number' ? 0 : type === 'boolean' ? true : type === 'null' ? null : '' };
}
const selected = computed(() => props.functions.find(f => f.id === model.value.function && f.version === model.value.version));
function changeKind(value: string) {
  literalType.value = 'text';
  model.value = value === 'param' ? { param: '' } : value === 'function' ? { function: '', version: '', inputs: {} } : { value: '' };
}
function changeFunction(value: string) {
  const fn = props.functions.find(f => `${f.id}@${f.version}` === value);
  if (fn) model.value = { function: fn.id, version: fn.version, inputs: Object.fromEntries(fn.inputs.map(p => [p.name, { param: '' }])) };
}
defineExpose({ validate: () => !model.value.invalid });
</script>

<template>
  <div class="value-source">
    <a-select :value="kind" aria-label="取值方式" :options="functionOnly ? [{value:'function',label:'已发布函数'}] : [{value:'param',label:'动作参数'}, {value:'value',label:'固定值'}, ...(!nested ? [{value:'function',label:'已发布函数'}] : [])]" @change="changeKind" />
    <a-select v-if="kind === 'param'" :value="model.param" aria-label="动作参数" placeholder="选择动作参数" :options="params.map(p => ({value:p.api,label:`${p.name}（${p.api}）`}))" @change="model = {param: $event}" />
    <div v-else-if="kind === 'value'" class="literal-value">
      <a-select :value="literalType" aria-label="固定值类型" :options="[{value:'text',label:'文本'}, {value:'number',label:'数字'}, {value:'boolean',label:'是或否'}, {value:'null',label:'空值'}, ...(literalType === 'structured' ? [{value:'structured',label:'已有结构化值'}] : [])]" @change="changeLiteralType" />
      <a-input v-if="literalType === 'text'" :value="model.value" aria-label="固定文本" placeholder="直接输入文本" @update:value="model = {value: $event}" />
      <a-input-number v-else-if="literalType === 'number'" :value="model.value" aria-label="固定数字" placeholder="输入数字" @update:value="model = $event == null ? {invalid: true} : {value: $event}" />
      <a-select v-else-if="literalType === 'boolean'" :value="model.value" aria-label="固定是或否" :options="[{value:true,label:'是'}, {value:false,label:'否'}]" @change="model = {value: $event}" />
      <span v-else-if="literalType === 'structured'" class="source-note">保留已有结构化值；如需更改，请选择其他类型。</span>
      <span v-else class="source-note">使用空值</span>
    </div>
    <div v-else class="function-value">
      <a-select :value="selected ? `${selected.id}@${selected.version}` : undefined" aria-label="函数和版本" placeholder="选择已发布函数及固定版本" :options="functions.map(f => ({value:`${f.id}@${f.version}`,label:`${f.name} · ${f.version}`}))" @change="changeFunction" />
      <div v-for="input in selected?.inputs ?? []" :key="input.name" class="fn-input">
        <span>{{ input.name }} · {{ input.type }}</span>
        <ActionValueSource v-model="model.inputs[input.name]" :params="params" :functions="functions" nested />
      </div>
    </div>
    <div v-if="model.invalid" role="alert" class="source-error">请输入有效数字</div>
  </div>
</template>

<style scoped>
.value-source { display: grid; grid-template-columns: 112px minmax(0, 1fr); gap: 8px; width: 100%; }
.function-value { min-width: 0; display: grid; gap: 8px; }
.function-value > :first-child { width: 100%; }
.literal-value { display: grid; grid-template-columns: 110px minmax(0, 1fr); gap: 8px; min-width: 0; }
.literal-value > * { width: 100%; }
.source-note { color: var(--text-3); font-size: 12px; line-height: 32px; }
.fn-input { display: grid; gap: 4px; font-size: 12px; color: var(--text-3); }
.source-error { grid-column: 1 / -1; color: var(--error, #c43131); font-size: 12px; }
</style>
