<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue';
import { message } from 'ant-design-vue';
import { apiClient, currentApiContext } from '@/api/http';
import { useReadonly } from '@/composables/useReadonly';

const props = defineProps<{ systemId: string }>();
const open = defineModel<boolean>('open', { default: false });
const { readonly } = useReadonly();
const api = apiClient('data');
interface Action { id: string; name: string; version: string; object: string; parameters: { api: string; name: string }[] }
interface ObjectOption { id: string; name: string; source: string; columns: string[] }
interface Saved { version: number; datasourceId: string; objectTypeCode: string; username: string; receiptTable: string; enabled: boolean; hasPassword: boolean; actions: Record<string, { version: string; fields: Record<string, string> }> }
interface Detail { binding: Saved | null; datasources: { id: string; name: string }[]; objects: ObjectOption[]; actions: Action[] }
const data = ref<Detail>(), busy = ref(false), error = ref('');
const form = reactive({ version: 0, datasourceId: '', objectTypeCode: '', username: '', password: '', receiptTable: 'rengu_action_receipt', enabled: false, actionId: '', actionVersion: '', mapping: [] as { column: string; parameter: string }[] });
const object = computed(() => data.value?.objects.find(o => o.id === form.objectTypeCode));
const action = computed(() => data.value?.actions.find(a => a.id === form.actionId));
const staleAction = computed(() => !!form.actionId && (!action.value || form.actionVersion !== action.value.version));
const actionOptions = computed(() => actions.value.map(a => ({ value: a.id, label: a.id === form.actionId && staleAction.value ? `${a.name} · 已授权 ${form.actionVersion}（失效，当前 ${a.version}）` : `${a.name} · ${a.version}` })));
const source = computed(() => data.value?.datasources.find(s => s.id === form.datasourceId));
const objects = computed(() => data.value?.objects.filter(o => o.source === source.value?.id || o.source === source.value?.name) ?? []);
const actions = computed(() => data.value?.actions.filter(a => a.object === form.objectTypeCode) ?? []);
const valid = computed(() => !readonly.value && !busy.value && !!form.datasourceId && !!object.value && !!form.username && !!form.actionId && form.actionVersion === action.value?.version && !!form.mapping.length && form.mapping.every(m => object.value?.columns.includes(m.column) && action.value?.parameters.some(p => p.api === m.parameter)) && new Set(form.mapping.map(m => m.column)).size === form.mapping.length);
const context = () => JSON.stringify(currentApiContext());
const path = (id: string) => `/ext-systems/${encodeURIComponent(id)}/database-binding`;
let epoch = 0;
async function load() {
  const n = ++epoch, key = context(), id = props.systemId;
  busy.value = true; error.value = ''; data.value = undefined;
  try {
    const result = await api.get<Detail>(path(id));
    if (n !== epoch || key !== context() || id !== props.systemId || !open.value) return;
    data.value = result;
    const saved = result.binding, entry = saved ? Object.entries(saved.actions)[0] : undefined;
    Object.assign(form, { version: saved?.version ?? 0, datasourceId: saved?.datasourceId ?? '', objectTypeCode: saved?.objectTypeCode ?? '', username: saved?.username ?? '', password: '', receiptTable: saved?.receiptTable ?? 'rengu_action_receipt', enabled: saved?.enabled ?? false, actionId: entry?.[0] ?? '', actionVersion: entry?.[1].version ?? '', mapping: entry ? Object.entries(entry[1].fields).map(([column, parameter]) => ({ column, parameter })) : [] });
  } catch (e) { if (n === epoch && key === context()) error.value = e instanceof Error ? e.message : '配置加载失败'; }
  finally { if (n === epoch) busy.value = false; }
}
watch(() => [open.value, props.systemId, context()], () => { if (open.value) void load(); else epoch++; }, { immediate: true });
function chooseAction() { form.actionVersion = action.value?.version ?? ''; form.mapping = []; }
async function save() {
  if (!valid.value) return;
  const n = epoch, key = context(), id = props.systemId;
  busy.value = true; error.value = '';
  try {
    await api.put(path(id), { ...form, ...(form.password ? { password: form.password } : { password: undefined }) });
    if (n !== epoch || key !== context() || id !== props.systemId) return;
    form.password = ''; message.success('独立源数据库写回授权已保存'); await load();
  } catch (e) { if (n === epoch && key === context()) error.value = e instanceof Error ? e.message : '保存失败'; }
  finally { if (n === epoch) busy.value = false; }
}
async function probe() {
  if (busy.value || !data.value?.binding) return;
  const n = epoch, key = context(), id = props.systemId;
  busy.value = true; error.value = '';
  try { const result = await api.post<{ message: string }>(`${path(id)}:probe`); if (n === epoch && key === context()) message.info(result.message); }
  catch (e) { if (n === epoch && key === context()) error.value = e instanceof Error ? e.message : '探测失败'; }
  finally { if (n === epoch) busy.value = false; }
}
</script>
<template>
  <a-modal v-model:open="open" title="独立源数据库写回授权" :width="720" :closable="!busy" :mask-closable="!busy" :keyboard="!busy" :body-style="{ maxHeight: '70vh', overflowY: 'auto' }">
    <p>仅更新一个已发布对象的明确授权字段。必须使用独立写账号；业务更新与幂等回执在同一源事务提交。结果未知只对账，不重发。</p>
    <a-alert v-if="error" type="error" :message="error" show-icon />
    <a-spin :spinning="busy"><a-form v-if="data" layout="vertical" :disabled="readonly || busy">
      <a-form-item label="数据连接" required extra="系统基础地址须为 database://该连接编号"><a-select v-model:value="form.datasourceId" :options="(data?.datasources ?? []).map(s => ({ value: s.id, label: `${s.name} · ${s.id}` }))" @change="form.objectTypeCode = ''; form.actionId = ''; form.mapping = []" /></a-form-item>
      <a-form-item label="目标对象" required><a-select v-model:value="form.objectTypeCode" :options="objects.map(o => ({ value: o.id, label: o.name }))" @change="form.actionId = ''; form.mapping = []" /></a-form-item>
      <a-form-item label="已发布动作与固定版本" required extra="先在动作类型写回配置绑定该系统，不选择HTTP业务接口"><a-select v-model:value="form.actionId" :options="actionOptions" @change="chooseAction" /><span v-if="form.actionVersion" class="t-12">固定授权版本：{{ form.actionVersion }}</span><a-alert v-if="staleAction" type="warning" :message="`原固定授权 ${form.actionVersion || '未设置'} 已失效；${action ? `当前发布版本为 ${action.version}，请明确重新授权并核对字段映射` : '该动作当前未发布或已不可用'}`" show-icon /><a-button v-if="action && staleAction" size="small" @click="chooseAction">重新授权当前版本 {{ action.version }}（清空旧映射）</a-button></a-form-item>
      <a-form-item label="独立写账号" required><a-input v-model:value="form.username" autocomplete="off" /></a-form-item>
      <a-form-item label="独立写密码" :required="!data?.binding?.hasPassword" extra="只保存加密值；留空仅保留同数据连接、同账号的原密码"><a-input-password v-model:value="form.password" autocomplete="new-password" /></a-form-item>
      <a-form-item label="DBA预建幂等回执表" required extra="与业务表同schema；平台不会自动创建源表或授予数据库权限"><a-input v-model:value="form.receiptTable" /></a-form-item>
      <a-form-item label="授权字段 → 动作参数" required>
        <a-space v-for="(row, index) in form.mapping" :key="index" wrap>
          <a-select v-model:value="row.column" aria-label="源写列" :options="(object?.columns ?? []).map(c => ({ value: c, label: c }))" style="width: 220px" />
          <a-select v-model:value="row.parameter" aria-label="动作参数" :options="(action?.parameters ?? []).map(p => ({ value: p.api, label: `${p.name} · ${p.api}` }))" style="width: 220px" />
          <a-button danger @click="form.mapping.splice(index, 1)">移除</a-button>
        </a-space>
        <a-button :disabled="!object || !action" @click="form.mapping.push({ column: '', parameter: '' })">添加字段映射</a-button>
      </a-form-item>
      <a-form-item label="执行授权"><a-checkbox v-model:checked="form.enabled">显式启用该固定动作版本的源数据库写回</a-checkbox></a-form-item>
    </a-form></a-spin>
    <template #footer><a-button :disabled="busy" @click="open = false">关闭</a-button><a-button :disabled="readonly || busy || !data?.binding" @click="probe">探测已保存读取与回执结构</a-button><a-button type="primary" :disabled="!valid" :loading="busy" @click="save">保存独立授权</a-button></template>
  </a-modal>
</template>
