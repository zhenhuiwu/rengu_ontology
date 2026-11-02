<script setup lang="ts">
import { computed, watch } from 'vue';
interface PayloadField { name: string; api: string; type: string; required: boolean; schema: any }
const props = defineProps<{ node: any; depth?: number; disabled?: boolean }>();
const fields = computed<PayloadField[]>(() => props.node.schema?.fields ?? []);
const depth = props.depth ?? 0;
const types = ['字符串', '整数', '金额', '日期', '日期时间', '枚举', '文本', '布尔', '对象', '数组'];
watch(() => props.node.type, type => {
  // Keep an existing schema when a preceding row is removed and Vue reuses this editor.
  if (type === '对象') { if (!Array.isArray(props.node.schema?.fields)) props.node.schema = { fields: [] }; }
  else if (type === '数组') { if (!props.node.schema?.item) props.node.schema = { item: { type: '字符串', schema: null } }; }
  else props.node.schema = null;
}, { immediate: true });
function nameError(field: PayloadField) { return String(field.name ?? '').trim() ? '' : '请填写字段名'; }
function apiError(field: PayloadField, index: number) {
  const value = String(field.api ?? '');
  if (!value) return '请填写 API 名';
  if (!/^[A-Za-z_][A-Za-z0-9_]{0,63}$/.test(value)) return '使用英文字母、数字和下划线，不能以数字开头，最多 64 字符';
  return fields.value.some((other, i) => i !== index && other.api === value) ? '同级 API 名不能重复' : '';
}
function add() { if (props.disabled) return; props.node.schema.fields.push({ name: '', api: '', type: '字符串', required: false, schema: null }); }
</script>
<template>
  <div v-if="node.type === '对象' && node.schema" class="nested">
    <div class="nested-head"><strong>对象字段</strong><a-button size="small" :disabled="disabled || depth >= 4 || node.schema.fields.length >= 100" @click="add">添加字段</a-button></div>
    <div v-for="(field, i) in fields" :key="i" class="nested-field">
      <div class="nested-row nested-fields">
        <label class="nested-cell"><span>字段名 <span class="nested-required">*</span></span><a-input v-model:value="field.name" :disabled="disabled" placeholder="例如：是否启用" aria-label="嵌套字段名" :status="nameError(field) ? 'error' : undefined" :aria-invalid="!!nameError(field)" /><span v-if="nameError(field)" class="nested-error" role="alert">{{ nameError(field) }}</span></label>
        <label class="nested-cell"><span>API 名 <span class="nested-required">*</span></span><a-input v-model:value="field.api" :disabled="disabled" placeholder="例如：enabled" aria-label="嵌套 API 名" :status="apiError(field, i) ? 'error' : undefined" :aria-invalid="!!apiError(field, i)" /><span v-if="apiError(field, i)" class="nested-error" role="alert">{{ apiError(field, i) }}</span></label>
        <a-button danger size="small" :disabled="disabled" class="nested-delete" @click="node.schema.fields.splice(i, 1)">删除</a-button>
      </div>
      <div class="nested-row"><a-select v-model:value="field.type" :disabled="disabled" :options="types.map(value => ({ value, label: value, disabled: depth >= 3 && (value === '对象' || value === '数组') }))" /><a-checkbox v-model:checked="field.required" :disabled="disabled">必填</a-checkbox></div>
      <PayloadSchemaEditor :node="field" :depth="depth + 1" :disabled="disabled" />
    </div>
    <div v-if="!node.schema.fields.length" class="hint">请至少添加一个字段</div>
  </div>
  <div v-else-if="node.type === '数组' && node.schema" class="nested">
    <div class="nested-head"><strong>数组元素类型</strong></div>
    <a-select v-model:value="node.schema.item.type" :disabled="disabled" style="width: 100%" :options="types.map(value => ({ value, label: value, disabled: depth >= 3 && (value === '对象' || value === '数组') }))" />
    <PayloadSchemaEditor :node="node.schema.item" :depth="depth + 1" :disabled="disabled" />
  </div>
</template>
<style scoped>
.nested { margin: 10px 0; border-left: 2px solid #dbe5f5; padding-left: 12px; }
.nested-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 8px; }
.nested-field { padding: 10px; margin-bottom: 8px; background: var(--g2); border-radius: 6px; }
.nested-row { display: flex; gap: 8px; align-items: center; margin-bottom: 8px; }
.nested-fields { align-items: flex-start; }
.nested-cell { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 4px; font-size: 12px; }
.nested-delete { flex: none; margin-top: 24px; }
.nested-error, .nested-required { color: var(--error, #c73535); }
.nested-error { overflow-wrap: anywhere; line-height: 1.5; }
.nested-row :deep(.ant-select) { min-width: 120px; }
.hint { color: var(--warning); font-size: 12px; }
</style>
