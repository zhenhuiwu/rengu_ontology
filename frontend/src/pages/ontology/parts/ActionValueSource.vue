<script setup lang="ts">
import { computed, ref } from 'vue';
import type { ActionParam } from '@/mock/actionEvent';
import type { ActionExecutionOptions } from '@/api/action';
const props = defineProps<{ params: ActionParam[]; functions: ActionExecutionOptions['functions']; nested?: boolean; functionOnly?: boolean }>();
const model = defineModel<Record<string, any>>({ required: true });
const error = ref('');
const kind = computed(() => 'param' in model.value ? 'param' : 'function' in model.value ? 'function' : 'value');
const literal = ref(JSON.stringify(model.value.value ?? ''));
function setLiteral(value: string) {
  literal.value = value;
  try { model.value = { value: JSON.parse(value) }; error.value = ''; }
  catch { model.value = { invalid: true }; error.value = '请输入有效值：文本加双引号，数字和 true/false 可直接填写'; }
}
const selected = computed(() => props.functions.find(f => f.id === model.value.function && f.version === model.value.version));
function changeKind(value: string) {
  error.value = '';
  literal.value = '""';
  model.value = value === 'param' ? { param: '' } : value === 'function' ? { function: '', version: '', inputs: {} } : { value: '' };
}
function changeFunction(value: string) {
  const fn = props.functions.find(f => `${f.id}@${f.version}` === value);
  if (fn) model.value = { function: fn.id, version: fn.version, inputs: Object.fromEntries(fn.inputs.map(p => [p.name, { param: '' }])) };
}
defineExpose({ validate: () => !error.value });
</script>

<template>
  <div class="value-source">
    <a-select :value="kind" aria-label="取值方式" :options="functionOnly ? [{value:'function',label:'已发布函数'}] : [{value:'param',label:'动作参数'}, {value:'value',label:'固定值'}, ...(!nested ? [{value:'function',label:'已发布函数'}] : [])]" @change="changeKind" />
    <a-select v-if="kind === 'param'" :value="model.param" aria-label="动作参数" placeholder="选择动作参数" :options="params.map(p => ({value:p.api,label:`${p.name}（${p.api}）`}))" @change="model = {param: $event}" />
    <a-input v-else-if="kind === 'value'" :value="literal" aria-label="固定值" placeholder='例如 "待出库"、0、false' @update:value="setLiteral" />
    <div v-else class="function-value">
      <a-select :value="selected ? `${selected.id}@${selected.version}` : undefined" aria-label="函数和版本" placeholder="选择已发布函数及固定版本" :options="functions.map(f => ({value:`${f.id}@${f.version}`,label:`${f.name} · ${f.version}`}))" @change="changeFunction" />
      <div v-for="input in selected?.inputs ?? []" :key="input.name" class="fn-input">
        <span>{{ input.name }} · {{ input.type }}</span>
        <ActionValueSource v-model="model.inputs[input.name]" :params="params" :functions="functions" nested />
      </div>
    </div>
    <div v-if="error" role="alert" class="source-error">{{ error }}</div>
  </div>
</template>

<style scoped>
.value-source { display: grid; grid-template-columns: 112px minmax(0, 1fr); gap: 8px; width: 100%; }
.function-value { min-width: 0; display: grid; gap: 8px; }
.function-value > :first-child { width: 100%; }
.fn-input { display: grid; gap: 4px; font-size: 12px; color: var(--text-3); }
.source-error { grid-column: 1 / -1; color: var(--error, #c43131); font-size: 12px; }
</style>
