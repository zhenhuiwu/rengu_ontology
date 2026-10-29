<script setup lang="ts">
import { computed } from 'vue';
const props = defineProps<{ modelValue: any; params: { api: string; name: string }[] }>();
const emit = defineEmits<{ 'update:modelValue': [value: any] }>();
const mapping = computed({ get: () => props.modelValue, set: value => emit('update:modelValue', value) });
const sources = computed(() => [
  ...[['ontologyId', '本体编号'], ['actionRunId', '动作记录编号'], ['actionType', '动作类型'], ['typeVersion', '动作版本'], ['idemKey', '幂等键'], ['objKind', '对象类型'], ['objId', '对象编号']].map(([value, label]) => ({ value, label })),
  ...props.params.map(p => ({ value: `params.${p.api}`, label: `参数：${p.name}（${p.api}）` })),
]);
function enable(on: boolean) {
  mapping.value = on ? { request: [{ target: '', source: 'objId' }], response: { status: '', receiptId: '', reason: '', code: '', success: 'SUCCESS', failed: 'FAILED', unknown: 'UNKNOWN' } } : null;
}
function validate(): boolean {
  if (!mapping.value) return true;
  const { request, response } = mapping.value;
  const key = /^[A-Za-z_][A-Za-z0-9_]{0,63}$/;
  return request.length > 0 && request.length <= 32 && new Set(request.map((r: any) => r.target)).size === request.length && request.every((r: any) => key.test(r.target) && sources.value.some(s => s.value === r.source)) && key.test(response.status) && key.test(response.receiptId) && (!response.reason || key.test(response.reason)) && (!response.code || key.test(response.code)) && ['success', 'failed', 'unknown'].every(k => typeof response[k] === 'string' && !!response[k] && response[k].length <= 64 && !/[\x00-\x1f\x7f]/.test(response[k])) && new Set([response.success, response.failed, response.unknown]).size === 3;
}
defineExpose({ validate });
</script>
<template>
  <section class="http-map">
    <div class="heading"><strong>HTTP 字段映射</strong><a-switch :checked="!!mapping" @change="enable" /></div>
    <p class="hint">关闭时使用平台标准请求与响应。支持已授权 HTTP/HTTPS POST 的顶层 JSON 字段；实际地址由受控执行通道配置，对账仍使用标准接口。</p>
    <template v-if="mapping">
      <div class="heading"><b>请求字段</b><a-button size="small" :disabled="mapping.request.length >= 32" @click="mapping.request.push({ target: '', source: 'objId' })">添加</a-button></div>
      <div v-for="(row, i) in mapping.request" :key="i" class="row"><a-input v-model:value="row.target" placeholder="外部字段名" /><a-select v-model:value="row.source" :options="sources" /><a-button danger size="small" @click="mapping.request.splice(i, 1)">删除</a-button></div>
      <div class="heading"><b>响应字段</b></div>
      <div class="grid"><label>状态字段 <a-input v-model:value="mapping.response.status" placeholder="必填" /></label><label>回执字段 <a-input v-model:value="mapping.response.receiptId" placeholder="必填" /></label><label>原因字段 <a-input v-model:value="mapping.response.reason" placeholder="可选" /></label><label>返回码字段 <a-input v-model:value="mapping.response.code" placeholder="可选" /></label><label>成功值 <a-input v-model:value="mapping.response.success" /></label><label>失败值 <a-input v-model:value="mapping.response.failed" /></label><label>未知值 <a-input v-model:value="mapping.response.unknown" /></label></div>
      <p class="hint">响应字段名和请求字段名仅支持字母、数字、下划线，且以字母或下划线开头。成功响应必须包含非空回执。</p>
    </template>
  </section>
</template>
<style scoped>
.http-map { border-top: 1px solid #e5eaf1; padding-top: 14px; }
.heading { display: flex; justify-content: space-between; align-items: center; margin: 8px 0; }
.hint { font-size: 12px; color: #64748b; }
.row { display: flex; gap: 8px; margin: 8px 0; }
.row :deep(.ant-select) { width: 210px; flex: none; }
.grid { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
.grid label { display: grid; gap: 4px; }
</style>
