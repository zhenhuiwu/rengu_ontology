<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import { message } from 'ant-design-vue';
import { apiClient, currentApiContext } from '@/api/http';
import { useReadonly } from '@/composables/useReadonly';

const props = defineProps<{ objectId: string }>();
const open = defineModel<boolean>('open', { default: false });
const api = apiClient('data');
const { readonly } = useReadonly();
interface Column { column: string; name: string; api: string; writable: boolean }
interface Supplement { table: string; mainKey: string; joinKey: string; fields: Record<string, string> }
interface Binding { table: string; columns: Column[]; readColumns: string[]; writeColumns: string[]; bound: boolean; supplementaryAvailable: boolean; bindingVersion: string | null; supplementary: Supplement | null }
interface Candidates { table: string; columns: { name: string; type: string }[]; properties: { api: string; name: string; column: string }[] }
const data = ref<Binding>(), reads = ref<string[]>([]), writes = ref<string[]>([]), busy = ref(false), error = ref('');
const extraEnabled = ref(false), extra = ref<Supplement>({ table: '', mainKey: '', joinKey: '', fields: {} }), candidates = ref<Candidates>(), inspecting = ref(false);
const key = () => JSON.stringify(currentApiContext());
const valid = computed(() => !readonly.value && !busy.value && !inspecting.value && !!data.value && reads.value.length > 0 && (!extraEnabled.value || !!data.value.supplementaryAvailable && candidates.value?.table === extra.value.table && reads.value.includes(extra.value.mainKey) && candidates.value.columns.some(c => c.name === extra.value.joinKey) && Object.entries(extra.value.fields).length > 0 && Object.entries(extra.value.fields).every(([api, column]) => candidates.value?.properties.some(p => p.api === api && p.column === column))));
let seq = 0;
watch(() => [open.value, props.objectId, key()], () => { if (open.value) void load(); else seq++; }, { immediate: true });
async function load() {
  const n = ++seq, ctx = key(); data.value = undefined; error.value = ''; busy.value = true;
  try {
    const result = await api.get<Binding>(`/governed-bindings/${encodeURIComponent(props.objectId)}`);
    if (n !== seq || ctx !== key() || !open.value) return;
    data.value = result; reads.value = [...result.readColumns]; writes.value = [...result.writeColumns];
    extraEnabled.value = !!result.supplementary; extra.value = result.supplementary ? structuredClone(result.supplementary) : { table: '', mainKey: '', joinKey: '', fields: {} }; candidates.value = undefined;
    if (result.supplementary) await inspect();
  } catch (e) { if (n === seq) error.value = e instanceof Error ? e.message : '加载失败'; }
  finally { if (n === seq) busy.value = false; }
}
watch(reads, value => { writes.value = writes.value.filter(v => value.includes(v)); });
async function inspect() {
  const table = extra.value.table.trim(), ctx = key(), n = seq;
  candidates.value = undefined; error.value = '';
  if (!/^[a-z][a-z0-9_]{0,62}$/.test(table)) { error.value = '请输入 public 业务表名（小写字母、数字或下划线）'; return; }
  inspecting.value = true;
  try { const result = await api.get<Candidates>(`/governed-bindings/${encodeURIComponent(props.objectId)}/supplementary-columns`, { query: { table } }); if (n === seq && ctx === key() && open.value && table === extra.value.table) candidates.value = result; }
  catch (e) { if (n === seq && ctx === key()) error.value = e instanceof Error ? e.message : '检查表失败'; }
  finally { if (n === seq) inspecting.value = false; }
}
function setFields(apis: string[]) { extra.value.fields = Object.fromEntries((candidates.value?.properties ?? []).filter(p => apis.includes(p.api)).map(p => [p.api, p.column])); }
async function save() {
  if (!valid.value) return;
  busy.value = true;
  try { await api.put(`/governed-bindings/${encodeURIComponent(props.objectId)}`, { readColumns: reads.value, writeColumns: writes.value, ...(data.value?.supplementaryAvailable ? { bindingVersion: data.value.bindingVersion, supplementary: extraEnabled.value ? extra.value : null } : {}) }); message.success('受控绑定已保存'); open.value = false; }
  catch (e) { error.value = e instanceof Error ? e.message : '保存失败'; }
  finally { busy.value = false; }
}
async function remove() {
  busy.value = true;
  try { await api.delete(`/governed-bindings/${encodeURIComponent(props.objectId)}`); message.success('受控绑定已解除'); open.value = false; }
  catch (e) { error.value = e instanceof Error ? e.message : '解除失败'; }
  finally { busy.value = false; }
}
</script>
<template>
  <a-modal v-model:open="open" title="配置受控业务表" :confirm-loading="busy" :ok-button-props="{ disabled: !valid }" ok-text="保存授权" @ok="save">
    <a-alert type="info" show-icon message="仅支持本应用 PostgreSQL 的 public 业务表。对象需已发布并映射到此表；表必须使用标准工作空间 RLS。读取和写入字段需要分别授权，行权限与动作权限仍独立生效。" />
    <a-alert v-if="error" type="error" :message="error" show-icon style="margin-top:12px" />
    <a-spin :spinning="busy"><template v-if="data"><p>源表：{{ data.table }}</p>
      <a-form layout="vertical" :disabled="readonly || busy">
        <a-form-item label="允许读取的属性"><a-checkbox-group v-model:value="reads" :options="data.columns.map(c => ({ label: `${c.name}（${c.column}）`, value: c.column }))" /></a-form-item>
        <a-form-item label="允许动作修改的属性"><a-checkbox-group v-model:value="writes" :options="data.columns.filter(c => c.writable && reads.includes(c.column)).map(c => ({ label: `${c.name}（${c.column}）`, value: c.column }))" /></a-form-item>
        <a-divider orientation="left">补充表（1:1）</a-divider>
        <a-alert type="info" show-icon :message="data.supplementaryAvailable ? '补充表须通过唯一键和工作空间隔离校验，读取继承主表行权限。' : '补充表功能尚未完成数据库迁移。'" />
        <a-checkbox v-model:checked="extraEnabled" :disabled="!data.supplementaryAvailable || readonly">启用补充表</a-checkbox>
        <template v-if="extraEnabled">
          <a-form-item label="补充表"><a-space><a-input v-model:value="extra.table" placeholder="public 表名" @change="candidates = undefined" /><a-button :loading="inspecting" @click="inspect">检查表</a-button></a-space></a-form-item>
          <a-form-item label="主表关联列"><a-select v-model:value="extra.mainKey" :options="data.columns.filter(c => reads.includes(c.column)).map(c => ({ value: c.column, label: `${c.name}（${c.column}）` }))" /></a-form-item>
          <a-form-item label="补充表关联列"><a-select v-model:value="extra.joinKey" :options="(candidates?.columns ?? []).map(c => ({ value: c.name, label: `${c.name} · ${c.type}` }))" /></a-form-item>
          <a-form-item label="补充字段映射"><a-select mode="multiple" :value="Object.keys(extra.fields)" :options="(candidates?.properties ?? []).map(p => ({ value: p.api, label: `${p.name}（${p.api} → ${p.column}）` }))" @update:value="setFields" /></a-form-item>
        </template>
        <p class="t-12 c-3">1:N 数据请分别建立子对象，并在关系类型中配置对象之间的关系。</p>
      </a-form>
      <a-popconfirm v-if="data.bound" title="解除后该对象将无法通过此绑定查询或修改，确定解除？" @confirm="remove"><a-button danger :disabled="readonly || busy">解除绑定</a-button></a-popconfirm>
    </template></a-spin>
  </a-modal>
</template>
