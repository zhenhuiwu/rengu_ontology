<script setup lang="ts">
import { watch } from 'vue';
const props = defineProps<{ node: any; depth?: number }>();
const depth = props.depth ?? 0;
const types = ['字符串', '整数', '金额', '日期', '日期时间', '枚举', '文本', '布尔', '对象', '数组'];
watch(() => props.node.type, type => {
  props.node.schema = type === '对象' ? { fields: [] } : type === '数组' ? { item: { type: '字符串', schema: null } } : null;
});
function add() { props.node.schema.fields.push({ name: '', api: '', type: '字符串', required: false, schema: null }); }
</script>
<template>
  <div v-if="node.type === '对象' && node.schema" class="nested">
    <div class="nested-head"><strong>对象字段</strong><a-button size="small" :disabled="depth >= 4 || node.schema.fields.length >= 100" @click="add">添加字段</a-button></div>
    <div v-for="(field, i) in node.schema.fields" :key="i" class="nested-field">
      <div class="nested-row"><a-input v-model:value="field.name" placeholder="字段名" /><a-input v-model:value="field.api" placeholder="API 名" /><a-button danger size="small" @click="node.schema.fields.splice(i, 1)">删除</a-button></div>
      <div class="nested-row"><a-select v-model:value="field.type" :options="types.map(value => ({ value, label: value, disabled: depth >= 3 && (value === '对象' || value === '数组') }))" /><a-checkbox v-model:checked="field.required">必填</a-checkbox></div>
      <PayloadSchemaEditor :node="field" :depth="depth + 1" />
    </div>
    <div v-if="!node.schema.fields.length" class="hint">请至少添加一个字段</div>
  </div>
  <div v-else-if="node.type === '数组' && node.schema" class="nested">
    <div class="nested-head"><strong>数组元素类型</strong></div>
    <a-select v-model:value="node.schema.item.type" style="width: 100%" :options="types.map(value => ({ value, label: value, disabled: depth >= 3 && (value === '对象' || value === '数组') }))" />
    <PayloadSchemaEditor :node="node.schema.item" :depth="depth + 1" />
  </div>
</template>
<style scoped>
.nested { margin: 10px 0; border-left: 2px solid #dbe5f5; padding-left: 12px; }
.nested-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 8px; }
.nested-field { padding: 10px; margin-bottom: 8px; background: #f7f9fc; border-radius: 6px; }
.nested-row { display: flex; gap: 8px; align-items: center; margin-bottom: 8px; }
.nested-row :deep(.ant-select) { min-width: 120px; }
.hint { color: #9b6b15; font-size: 12px; }
</style>
