<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import { message } from 'ant-design-vue';
import { useReadonly } from '@/composables/useReadonly';
import { useUnsavedGuard } from '@/composables/useUnsavedGuard';
import { currentApiContext } from '@/api/http';
import { isDataReal } from '@/api/datasources';
import { definitionDetail } from '@/api/definitionForms';
import { mappingObjectChoices, commitTableMapping } from '@/api/objectImport';
import StartChangeModal from './StartChangeModal.vue';
const props = defineProps<{ datasourceId: string; datasourceName: string; schema: string; table: string; currentObjectId?: string }>();
const open = defineModel<boolean>('open', { default: false });
const emit = defineEmits<{ saved: [crId: string] }>();
const { readonly, reason } = useReadonly();
const options = ref<{ id: string; name: string; api: string }[]>([]), objectId = ref<string>();
const current = ref<{ source: string; table: string }>();
const loading = ref(false), detailLoading = ref(false), busy = ref(false), error = ref(''), detailError = ref(''), changeOpen = ref(false);
let seq = 0, detailSeq = 0;
const ctxKey = () => JSON.stringify(currentApiContext());
const guard = useUnsavedGuard(computed(() => open.value && objectId.value !== props.currentObjectId));
function close() { if (!busy.value) void guard(() => { open.value = false; }); }
watch(open, (v) => { if (v) void load(); else { seq++; detailSeq++; changeOpen.value = false; } });
watch(ctxKey, () => { if (open.value) { open.value = false; message.info('身份或版本已切换，请重新打开映射编辑'); } });
async function load() {
  const my = ++seq, key = ctxKey(); options.value = []; objectId.value = undefined; current.value = undefined; error.value = ''; busy.value = false; loading.value = true;
  if (!isDataReal()) { loading.value = false; return; }
  try { const rows = await mappingObjectChoices(); if (my === seq && key === ctxKey() && open.value) { options.value = rows; objectId.value = rows.some((o) => o.id === props.currentObjectId) ? props.currentObjectId : undefined; } }
  catch (e) { if (my === seq && key === ctxKey()) error.value = e instanceof Error ? e.message : String(e); }
  finally { if (my === seq && key === ctxKey()) loading.value = false; }
}
async function loadObject() {
  const my = ++detailSeq, key = ctxKey(), id = objectId.value; current.value = undefined; detailError.value = ''; detailLoading.value = false;
  if (!id || !open.value) return;
  detailLoading.value = true;
  try {
    const detail = await definitionDetail('object', id);
    if (my === detailSeq && key === ctxKey() && open.value) current.value = { source: String(detail.fields.source ?? '—'), table: String(detail.fields.table ?? '—') };
  } catch (e) { if (my === detailSeq && key === ctxKey()) detailError.value = e instanceof Error ? e.message : String(e); }
  finally { if (my === detailSeq && key === ctxKey()) detailLoading.value = false; }
}
watch(objectId, loadObject);
const canSave = computed(() => isDataReal() && !readonly.value && !loading.value && !detailLoading.value && !busy.value && !error.value && !detailError.value && !!current.value && options.value.some((o) => o.id === objectId.value));
async function save(opts: { join?: string; title?: string; desc?: string }) {
  if (!canSave.value || !objectId.value) return;
  const my = seq, key = ctxKey(); busy.value = true;
  try {
    const result = await commitTableMapping(props.datasourceId, props.schema, props.table, objectId.value, opts);
    if (my !== seq || key !== ctxKey() || !open.value) return;
    message.success(`映射改动已记入 ${result.crId}，审核并整版发布后生效`); open.value = false; emit('saved', result.crId);
  } catch (e) { if (my === seq && key === ctxKey()) message.error(`${e instanceof Error ? e.message : String(e)}；已保留对象选择`); }
  finally { if (my === seq && key === ctxKey()) busy.value = false; }
}
</script>
<template>
  <a-modal :open="open" title="修改对象来源映射" :width="640" :closable="!busy" :mask-closable="!busy" :confirm-loading="busy" ok-text="选择变更单" :ok-button-props="{ disabled: !canSave }" @cancel="close" @ok="changeOpen = true">
    <a-alert type="info" message="仅将对象定义的source/table改动记入CR；审核发布后生效。后台重新检查源表字段与主键，不改变任意SQL执行权限。" show-icon />
    <a-alert v-if="readonly" type="warning" :message="reason" show-icon />
    <a-alert v-if="error || detailError" type="error" :message="error || detailError" show-icon><template #action><a-button size="small" @click="error ? load() : loadObject()">重试加载</a-button></template></a-alert>
    <a-form layout="vertical" :disabled="readonly || busy || loading" style="margin-top:16px">
      <a-form-item label="目标源表"><span class="t-14">{{ datasourceName }} · {{ schema }}.{{ table }}</span></a-form-item>
      <a-form-item label="映射对象类型" required><a-select v-model:value="objectId" :loading="loading" :options="options.map((o) => ({ value: o.id, label: `${o.name}（${o.api}）` }))" /></a-form-item>
      <a-form-item label="对象当前已发布映射"><a-spin v-if="detailLoading" /><span v-else class="t-14">{{ current ? `${current.source} · ${current.table}` : '请选择真实对象' }}</span></a-form-item>
    </a-form>
  </a-modal>
  <StartChangeModal v-model:open="changeOpen" :target-name="`修改对象来源映射：${schema}.${table}`" @ok="save" />
</template>
