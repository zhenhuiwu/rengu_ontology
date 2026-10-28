<script setup lang="ts">
const props = defineProps<{ node: any; modelValue: any }>();
const emit = defineEmits<{ 'update:modelValue': [value: any] }>();
function initial(node: any): any { return node.type === '对象' ? {} : node.type === '数组' ? [] : undefined; }
function set(key: string | number, value: any) { const next = Array.isArray(props.modelValue) ? [...props.modelValue] : { ...props.modelValue }; next[key as any] = value; emit('update:modelValue', next); }
</script>
<template>
  <div v-if="node.type === '对象'">
    <div v-for="field in node.schema?.fields" :key="field.api" style="margin: 8px 0 8px 16px">
      <div>{{ field.name }} <span v-if="field.required" style="color: #c33">（必填）</span></div>
      <PayloadValueEditor :node="field" :model-value="modelValue?.[field.api]" @update:model-value="set(field.api, $event)" />
    </div>
  </div>
  <div v-else-if="node.type === '数组'">
    <div v-for="(_, i) in (modelValue ?? [])" :key="i" style="margin: 8px 0 8px 16px">
      <PayloadValueEditor :node="node.schema.item" :model-value="modelValue[i]" @update:model-value="set(i, $event)" />
      <a-button size="small" @click="emit('update:modelValue', modelValue.filter((_: any, n: number) => n !== i))">删除</a-button>
    </div>
    <a-button size="small" @click="emit('update:modelValue', [...(modelValue ?? []), initial(node.schema.item)])">添加一项</a-button>
  </div>
  <a-select v-else-if="node.type === '布尔'" :value="modelValue" :options="[{ label: '是', value: true }, { label: '否', value: false }]" allow-clear style="width: 100%" @update:value="emit('update:modelValue', $event)" />
  <a-input-number v-else-if="['整数', '金额', '数值', '小数', '百分比'].includes(node.type)" :value="modelValue" style="width: 100%" @update:value="emit('update:modelValue', $event)" />
  <a-input v-else :value="modelValue" @update:value="emit('update:modelValue', $event || undefined)" />
</template>
