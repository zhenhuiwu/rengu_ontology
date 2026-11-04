<script setup lang="ts">
import { ref, watch } from 'vue';
import { message } from 'ant-design-vue';
import { currentApiContext, isApiError } from '@/api/http';
import { getExtSystem, getHttpEndpoints, type HttpEndpoint } from '@/api/integration';
const props = defineProps<{ modelValue: Record<string, any> }>();
const emit = defineEmits<{ 'update:modelValue': [value: Record<string, any>] }>();
const endpoints = ref<HttpEndpoint[]>([]);
const loading = ref(false);
const database = ref(false);
let sequence = 0;
watch([() => props.modelValue.sysId, () => JSON.stringify(currentApiContext())], async ([system], old) => {
  const seq = ++sequence; endpoints.value = []; database.value = false; loading.value = false;
  if (old?.length && system !== old[0]) emit('update:modelValue', { ...props.modelValue, httpEndpointId: null, endpoint: system ? '' : '—', httpMapping: null });
  if (!system) return;
  loading.value = true;
  try {
    const detail = await getExtSystem(String(system));
    if (seq !== sequence) return;
    database.value = detail.endpoint.startsWith('database://');
    if (database.value) {
      if (props.modelValue.httpEndpointId || props.modelValue.httpMapping) emit('update:modelValue', { ...props.modelValue, httpEndpointId: null, httpMapping: null });
      return;
    }
    const result = await getHttpEndpoints(String(system)); if (seq === sequence) endpoints.value = result.endpoints.filter(x => x.purpose === 'action');
  }
  catch (e) { if (seq === sequence) message.error(isApiError(e) ? e.message : String(e)); }
  finally { if (seq === sequence) loading.value = false; }
}, { immediate: true });
function choose(id: string) {
  const item = endpoints.value.find(x => x.id === id);
  if (item) emit('update:modelValue', { ...props.modelValue, httpEndpointId: item.id, endpoint: `${item.method} ${item.path}`, httpMapping: item.httpMapping ? JSON.parse(JSON.stringify(item.httpMapping)) : null });
}
function databaseEndpoint(value: string) {
  emit('update:modelValue', { ...props.modelValue, endpoint: value, httpEndpointId: null, httpMapping: null });
}
</script>
<template>
  <a-input v-if="database" :value="modelValue.endpoint" :maxlength="255" placeholder="填写数据库动作逻辑接口标识，例如 office_device" @update:value="databaseEndpoint" />
  <a-select v-else :value="modelValue.httpEndpointId || undefined" :loading="loading" :disabled="!modelValue.sysId || loading" placeholder="选择系统中已配置的业务接口" :options="endpoints.map(x => ({value:x.id,label:`${x.name} · ${x.method} ${x.path}`}))" @select="choose" />
  <div v-if="database" class="hint">填写逻辑标识与下方幂等键规则；发布后在外部系统“数据库写回授权”配置独立写账号、固定动作版本与字段参数映射，不填写HTTP接口。</div>
  <div v-else-if="modelValue.endpoint && !modelValue.httpEndpointId && modelValue.sysId" class="hint">当前为旧版接口配置：{{ modelValue.endpoint }}。可选择新接口替换。</div>
  <div v-if="!database && modelValue.httpEndpointId" class="hint">请求和响应映射来自系统接口；修改接口后需重新选择并保存动作。</div>
</template>
<style scoped>.hint { color: var(--g6); font-size: 12px; margin-top: 4px; }</style>
