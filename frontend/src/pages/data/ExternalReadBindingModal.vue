<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import { message } from 'ant-design-vue';
import { apiClient, currentApiContext } from '@/api/http';
import { useReadonly } from '@/composables/useReadonly';
import { getObjectMirror, getObjectCdc, type CdcState, type MirrorState } from '@/api/datasources';
const props = defineProps<{ objectId: string }>();
const emit = defineEmits<{ saved: [] }>();
const open = defineModel<boolean>('open', { default: false });
const { readonly } = useReadonly();
const api = apiClient('data');
interface Binding { version: number; datasourceId: string; schema: string; table: string; availableColumns: string[]; requiredColumn: string; readColumns: string[]; sourceKind?: 'table' | 'view'; declaredUniqueKey?: string | null }
const declareViewKey = ref(false);
const binding = ref<Binding>(), columns = ref<string[]>([]), busy = ref(false), error = ref('');
const mirror = ref<MirrorState>();
const cdc = ref<CdcState>();
const cdcLocked = computed(() => !!cdc.value?.enabled);
const mirrorUpgradeMessage = '数据库尚未完成 0048 增量升级，请联系管理员应用该迁移，完成后刷新状态；无需重建或解除源绑定。';
const search = ref('');
const filtered = computed(() => (binding.value?.availableColumns ?? []).filter(c => c.toLowerCase().includes(search.value.trim().toLowerCase())));
const context = () => JSON.stringify(currentApiContext());
const hasChanges = computed(() => !binding.value?.version || JSON.stringify([...columns.value].sort()) !== JSON.stringify([...binding.value.readColumns].sort()) || (binding.value.sourceKind === 'view' && declareViewKey.value !== (binding.value.declaredUniqueKey === binding.value.requiredColumn)));
const valid = computed(() => !readonly.value && !busy.value && (!cdcLocked.value || !hasChanges.value) && !mirror.value?.migrationRequired && !!binding.value && (binding.value.sourceKind !== 'view' || declareViewKey.value) && columns.value.includes(binding.value.requiredColumn) && columns.value.every(c => binding.value!.availableColumns.includes(c)));
const removedColumns = computed(() => binding.value?.readColumns.filter(c => !binding.value!.availableColumns.includes(c)) ?? []);
const mirrorSummary = computed(() => { const s = mirror.value; return !s ? '状态暂不可用' : s.migrationRequired ? '需升级数据库' : s.stale || (s.restartRequired && !s.complete) ? '配置需重新确认' : s.status === 'running' || s.status === 'queued' ? '正在同步，尚未完整' : s.complete ? s.deletionsAligned === true ? '本轮扫描及删除核对已完成' : '扫描完成，删除待核对' : s.status === 'not_started' ? '尚未同步' : '尚未完整同步'; });
function mirrorTime(value?: string | null) { if (!value) return '—'; const date = new Date(value); return Number.isNaN(date.getTime()) ? '—' : date.toLocaleString('zh-CN', { hour12: false }); }
let seq = 0;
watch(() => [open.value, props.objectId, context()], () => { if (open.value) void load(); else seq++; }, { immediate: true });
async function load() {
  const n = ++seq, key = context(), id = props.objectId; binding.value = undefined; mirror.value = undefined; cdc.value = undefined; error.value = ''; search.value = ''; busy.value = true;
  try { const [result, status, capture] = await Promise.allSettled([api.get<Binding>(`/external-read-bindings/${encodeURIComponent(id)}`), getObjectMirror(id), getObjectCdc(id)]); if (n !== seq || key !== context() || !open.value) return;
    if (status.status === 'fulfilled') mirror.value = status.value;
    if (capture.status === 'fulfilled') cdc.value = capture.value;
    if (result.status === 'rejected') throw mirror.value?.migrationRequired ? new Error(mirrorUpgradeMessage) : result.reason;
    binding.value = result.value; declareViewKey.value = result.value.declaredUniqueKey === result.value.requiredColumn; columns.value = [...new Set([...result.value.readColumns.filter(c => result.value.availableColumns.includes(c)), result.value.requiredColumn])];
  }
  catch (e) { if (n === seq) error.value = e instanceof Error ? e.message : '加载失败'; }
  finally { if (n === seq) busy.value = false; }
}
async function save() {
  if (!valid.value || !binding.value) return;
  const n = seq, key = context(), id = props.objectId;
  busy.value = true; error.value = '';
  try { const result = await api.put<{ changed?: boolean }>(`/external-read-bindings/${encodeURIComponent(id)}`, { version: binding.value.version, readColumns: columns.value, ...(binding.value.sourceKind === 'view' ? { declaredUniqueKey: declareViewKey.value ? binding.value.requiredColumn : null } : {}) }); if (n !== seq || key !== context() || id !== props.objectId || !open.value) return; if (result.changed === false) message.info('读取授权未变化'); else { message.success('读取授权已保存，请在同步计划核对并更新同步'); emit('saved'); } open.value = false; }
  catch (e) { if (n === seq && key === context()) error.value = e instanceof Error ? e.message : '保存失败'; } finally { if (n === seq) busy.value = false; }
}
async function remove() {
  if (!binding.value || readonly.value || busy.value || cdcLocked.value || mirror.value?.migrationRequired) return;
  const n = seq, key = context(), id = props.objectId;
  busy.value = true;
  try { await api.delete(`/external-read-bindings/${encodeURIComponent(id)}`, { query: { version: binding.value.version } }); if (n !== seq || key !== context() || id !== props.objectId) return; message.success('只读绑定已解除'); emit('saved'); open.value = false; }
  catch (e) { if (n === seq && key === context()) error.value = e instanceof Error ? e.message : '解除失败'; } finally { if (n === seq) busy.value = false; }
}
</script>
<template>
  <a-modal v-model:open="open" title="配置外部只读绑定" :width="640" :body-style="{ maxHeight: '65vh', overflowY: 'auto' }" :closable="!busy" :mask-closable="!busy" :keyboard="!busy" @cancel="open = false">
    <a-alert v-if="error" type="error" :message="error" show-icon />
    <a-spin :spinning="busy"><template v-if="binding">
      <p class="binding-source" :title="`${binding.datasourceId} · ${binding.schema}.${binding.table}`">{{ binding.datasourceId }} · {{ binding.schema }}.{{ binding.table }}</p>
      <a-form-item v-if="binding.sourceKind === 'view'" label="视图唯一键声明" required extra="这不是数据库主键约束；每批读取仍验证空值与重复，失败不发布镜像。视图不能启用binlog CDC。"><a-checkbox v-model:checked="declareViewKey" :disabled="readonly || busy || cdcLocked">确认 {{ binding.requiredColumn }} 唯一且非空，用于此视图镜像身份</a-checkbox></a-form-item>
      <p class="binding-note">读取镜像：{{ mirrorSummary }}<template v-if="mirror?.dataCheckedAt"> · 核验于 {{ mirrorTime(mirror.dataCheckedAt) }}</template><template v-if="!mirror?.migrationRequired && !cdc?.enabled">。保存授权后请在“同步计划”更新镜像。</template></p>
      <p v-if="cdc?.installed" class="binding-note">{{ cdc.captureMode === 'binlog' ? 'MySQL binlog CDC' : '达梦事务触发器 CDC' }}：{{ cdc.enabled ? '消费已启用；修改字段或解除绑定前，请先在同步计划暂停消费。' : '消费已暂停或未启用；修改字段后须核对捕获配置并重新初始化。' }}</p>
      <p v-if="removedColumns.length" class="binding-note">{{ removedColumns.length }} 个原授权字段已不可用，请核对当前字段后保存。</p>
      <p v-if="mirror?.migrationRequired || mirror?.error" class="tl-failed">{{ mirror?.migrationRequired ? mirrorUpgradeMessage : mirror?.error }}</p>
      <div class="binding-head"><span>读取字段 · 已选 {{ columns.length }} / {{ binding.availableColumns.length }}</span><a-input-search v-model:value="search" placeholder="搜索字段" allow-clear class="binding-search" /></div>
      <div class="binding-list"><a-checkbox-group v-model:value="columns" :disabled="readonly || busy || cdcLocked || mirror?.migrationRequired" class="binding-fields"><a-checkbox v-for="column in binding.availableColumns" v-show="filtered.includes(column)" :key="column" :value="column" :disabled="column === binding.requiredColumn">{{ column }}<span v-if="column === binding.requiredColumn" class="binding-note">（主键）</span></a-checkbox></a-checkbox-group><a-empty v-if="!filtered.length" description="没有匹配的字段" :image-style="{ height: '40px' }" /></div>
    </template></a-spin>
    <template #footer><div class="binding-footer"><a-popconfirm v-if="binding?.version" title="解除后对象无法通过此绑定读取，确定解除？" @confirm="remove"><a-button danger :disabled="readonly || busy || cdcLocked || mirror?.migrationRequired">解除绑定</a-button></a-popconfirm><span class="binding-spacer" /><a-button :disabled="busy" @click="open = false">取消</a-button><a-button type="primary" :loading="busy" :disabled="!valid" @click="save">保存授权</a-button></div></template>
  </a-modal>
</template>
<style scoped>
.binding-source { overflow-wrap: anywhere; color: var(--g8); }
.binding-head { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 8px; margin-bottom: 8px; }
.binding-search { width: 200px; max-width: 100%; }
.binding-list { max-height: 42vh; overflow: auto; border: var(--border); border-radius: var(--radius); }
.binding-fields { display: flex; flex-direction: column; }
.binding-fields :deep(.ant-checkbox-wrapper) { margin: 0; padding: 8px 12px; overflow-wrap: anywhere; }
.binding-note { color: var(--g8); }
.binding-footer { display: flex; align-items: center; gap: 8px; }
.binding-spacer { flex: 1; }
</style>
