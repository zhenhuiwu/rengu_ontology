<script setup lang="ts">
import { computed } from 'vue';
import type { FunctionSchema } from '@/api/functions';
const props = defineProps<{ schema: FunctionSchema; disabled?: boolean }>();
const model = defineModel<any>({ required: true });
const fields = computed(() => Object.entries(props.schema.properties ?? {}));
const has = (key: string) => Object.prototype.hasOwnProperty.call(model.value ?? {}, key);
function initial(schema: FunctionSchema): any {
  if (schema.enum?.length) return schema.enum[0];
  if (schema.type === 'object') return Object.fromEntries(Object.entries(schema.properties ?? {}).filter(([key]) => schema.required?.includes(key)).map(([key, value]) => [key, initial(value)]));
  return schema.type === 'array' ? [] : schema.type === 'string' ? '' : schema.type === 'boolean' ? false : schema.type === 'null' ? null : 0;
}
function fieldValue(key: string, value: unknown) { model.value = { ...(model.value ?? {}), [key]: value }; }
function toggle(key: string, schema: FunctionSchema, checked: boolean) {
  const value = { ...(model.value ?? {}) };
  if (checked) value[key] = initial(schema); else delete value[key];
  model.value = value;
}
function itemValue(index: number, value: unknown) { const items = [...(model.value ?? [])]; items[index] = value; model.value = items; }
</script>

<template>
  <a-select v-if="schema.enum" :value="model" :disabled="disabled" :options="schema.enum.map(value => ({ value: value as any, label: String(value) }))" @change="model = $event" />
  <div v-else-if="schema.type === 'object'" class="function-object">
    <div v-for="[key, child] in fields" :key="key" class="function-field">
      <div class="function-label"><strong>{{ key }}</strong><span>{{ child.description }}</span><a-checkbox v-if="!schema.required?.includes(key)" :checked="has(key)" :disabled="disabled" @change="toggle(key, child, $event.target.checked)">填写</a-checkbox><span v-else class="c-3">必填</span></div>
      <FunctionInput v-if="has(key) || schema.required?.includes(key)" :schema="child" :model-value="model?.[key]" :disabled="disabled" @update:model-value="fieldValue(key, $event)" />
    </div>
    <span v-if="!fields.length" class="c-3">无输入字段</span>
  </div>
  <div v-else-if="schema.type === 'array' && schema.items" class="function-array">
    <div v-for="(item, index) in model ?? []" :key="index" class="function-item"><FunctionInput :schema="schema.items" :model-value="item" :disabled="disabled" @update:model-value="itemValue(Number(index), $event)" /><a-button :disabled="disabled" @click="model = model.filter((_: unknown, i: number) => i !== Number(index))">移除</a-button></div>
    <a-button :disabled="disabled || (model?.length ?? 0) >= 512" @click="model = [...(model ?? []), initial(schema.items!)]">添加一项</a-button>
  </div>
  <a-input v-else-if="schema.type === 'string'" :value="model" :disabled="disabled" @update:value="model = $event" />
  <a-input-number v-else-if="schema.type === 'integer' || schema.type === 'number'" :value="model" :precision="schema.type === 'integer' ? 0 : undefined" :disabled="disabled" @update:value="model = $event" />
  <a-select v-else-if="schema.type === 'boolean'" :value="model" :options="[{ value: true, label: '是' }, { value: false, label: '否' }]" :disabled="disabled" @change="model = $event" />
  <span v-else>空值</span>
</template>

<style scoped>
.function-object, .function-array { display: grid; gap: 12px; min-width: 0; }
.function-field { display: grid; gap: 6px; min-width: 0; }
.function-label { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; }
.function-label span { font-size: 12px; color: var(--c-2); }
.function-item { display: flex; gap: 8px; align-items: flex-start; }
.function-item > :first-child { flex: 1; min-width: 0; }
.function-field > :deep(.ant-input-number), .function-field > :deep(.ant-select) { width: 100%; }
</style>
