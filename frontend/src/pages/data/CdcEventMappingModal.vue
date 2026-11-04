<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import { message } from 'ant-design-vue';
import { apiClient, currentApiContext, isApiError } from '@/api/http';
import { useReadonly } from '@/composables/useReadonly';
const props = defineProps<{ objectId: string; version: number; paused: boolean }>();
const open = defineModel<boolean>('open', { default: false });
const api = apiClient('data'), { readonly } = useReadonly();
interface Mapping { eventTypeId: string; eventVersion: string; operations: string[]; fields: { target: string; source: string }[] }
interface Options { mapping: Mapping | null; mappingVersion: number; sourcePaths: string[]; eventTypes: { id: string; name: string; version: string; fields: { path: string; name: string }[] }[] }
const data = ref<Options>(), mapping = ref<Mapping | null>(null), busy = ref(false), error = ref('');
const selected = computed(() => data.value?.eventTypes.find(t => t.id === mapping.value?.eventTypeId));
const staleVersion = computed(() => !!mapping.value && (!selected.value || selected.value.version !== mapping.value.eventVersion));
const eventOptions = computed(() => (data.value?.eventTypes ?? []).map(t => ({ value: t.id, label: t.id === mapping.value?.eventTypeId && staleVersion.value ? `${t.name} · 已配置 ${mapping.value?.eventVersion}（失效，当前 ${t.version}）` : `${t.name} · ${t.id} · ${t.version}` })));
const path = () => `/external-read-bindings/${encodeURIComponent(props.objectId)}/cdc/event-mapping`;
const context = () => JSON.stringify(currentApiContext());
let epoch = 0;
async function load() {
  const n = ++epoch, ctx = context(), id = props.objectId;
  busy.value = true; error.value = ''; data.value = undefined;
  try { const result = await api.get<Options>(path()); if (n !== epoch || ctx !== context() || id !== props.objectId || !open.value) return; data.value = result; mapping.value = result.mapping ? structuredClone(result.mapping) : null; }
  catch (e) { if (n === epoch && ctx === context()) error.value = isApiError(e) ? e.message : '事件映射加载失败'; }
  finally { if (n === epoch) busy.value = false; }
}
function choose(id: string | undefined) { const item = data.value?.eventTypes.find(t => t.id === id); mapping.value = item ? { eventTypeId: item.id, eventVersion: item.version, operations: ['insert', 'update', 'delete'], fields: [] } : null; }
function adoptVersion() { if (!busy.value && !readonly.value && props.paused && mapping.value && selected.value) mapping.value = { ...mapping.value, eventVersion: selected.value.version }; }
const valid = computed(() => !busy.value && !readonly.value && props.paused && !!data.value && (!mapping.value || (!!selected.value && selected.value.version === mapping.value.eventVersion && mapping.value.operations.length > 0 && mapping.value.fields.every(f => selected.value?.fields.some(field => field.path === f.target) && data.value?.sourcePaths.includes(f.source)))));
async function save() {
  if (!valid.value || !data.value) return;
  const n = ++epoch, ctx = context(), id = props.objectId;
  busy.value = true; error.value = '';
  try { await api.put(path(), { version: props.version, mappingVersion: data.value.mappingVersion, mapping: mapping.value }); if (n !== epoch || ctx !== context() || id !== props.objectId || !open.value) return; message.success('CDC事件映射已保存；恢复后仅处理尚未提交的源事实'); open.value = false; }
  catch (e) { if (n === epoch && ctx === context()) error.value = isApiError(e) ? e.message : '事件映射保存失败'; }
  finally { if (n === epoch) busy.value = false; }
}
watch([open, () => props.objectId, context], () => { ++epoch; if (open.value) void load(); }, { immediate: true });
</script>
<template>
  <a-modal v-model:open="open" title="CDC行事实 → 事件映射" :width="760" :closable="!busy" :mask-closable="!busy" :keyboard="!busy" :body-style="{ maxHeight: '70vh', overflowY: 'auto' }">
    <p>选择事件并映射需要传递的字段。</p>
    <a-alert v-if="error" type="error" :message="error" show-icon />
    <a-alert v-if="!paused" type="info" message="请先暂停消费后修改映射；保留游标，恢复后继续未提交事实。" show-icon />
    <a-spin :spinning="busy"><a-form v-if="data" layout="vertical" :disabled="busy || readonly || !paused">
      <a-form-item label="已发布事件与固定版本" extra="留空解除映射，不补发此前未配置的历史事实。事件去重键使用稳定源事实ID。"><a-select :value="mapping?.eventTypeId" allow-clear :options="eventOptions" @change="choose" /><span v-if="mapping" class="t-12">固定事件版本：{{ mapping.eventVersion }}</span><a-alert v-if="staleVersion" type="warning" :message="`原固定版本 ${mapping?.eventVersion} 已失效；${selected ? `当前发布版本为 ${selected.version}` : '事件当前未发布或已不可用'}`" show-icon /><a-button v-if="selected && staleVersion" @click="adoptVersion">采用当前版本 {{ selected.version }} 并复核字段映射</a-button></a-form-item>
      <template v-if="mapping">
        <a-form-item label="源操作"><a-checkbox-group v-model:value="mapping.operations" :options="[{ label: '新增', value: 'insert' }, { label: '修改', value: 'update' }, { label: '删除', value: 'delete' }]" /></a-form-item>
        <a-form-item label="事件载荷字段 ← 源事实" extra="新增无before，删除无after。小数仅在精度可完整保留时转为事件数值；缺值、类型或精度不符将暂停，修正后恢复。">
          <a-space v-for="(field, i) in mapping.fields" :key="i" wrap style="margin-bottom:8px">
            <a-select v-model:value="field.target" show-search option-filter-prop="label" aria-label="事件载荷字段" :options="(selected?.fields ?? []).map(f => ({ value: f.path, label: `${f.name} · ${f.path}` }))" style="width:300px" />
            <a-select v-model:value="field.source" show-search option-filter-prop="label" aria-label="源事实字段" :options="(data?.sourcePaths ?? []).map(p => ({ value: p, label: p }))" style="width:240px" />
            <a-button danger @click="mapping?.fields.splice(i, 1)">移除</a-button>
          </a-space>
          <a-button @click="mapping?.fields.push({ target: '', source: '' })">添加字段映射</a-button>
        </a-form-item>
      </template>
    </a-form></a-spin>
    <template #footer><a-button :disabled="busy" @click="open = false">取消</a-button><a-button type="primary" :loading="busy" :disabled="!valid" @click="save">保存映射</a-button></template>
  </a-modal>
</template>
