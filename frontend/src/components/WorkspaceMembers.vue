<script setup lang="ts">
import { computed, onBeforeUnmount, reactive, ref, watch } from 'vue';
import { message } from 'ant-design-vue';
import { currentApiContext } from '@/api/http';
import {
  getWorkspaceRoleBindings, listWorkspaceMembers, saveWorkspaceMember, saveWorkspaceRoleBindings, workspaceMemberOptions,
  type OntologyWorkspace, type WorkspaceMember, type WorkspaceRole, type WorkspaceRoleBindings,
} from '@/api/workspaces';
import { useAppStore } from '@/stores/app';
import { usePreviewStore } from '@/stores/preview';
import { useConfirmStore } from '@/stores/confirm';
import { useUnsavedGuard } from '@/composables/useUnsavedGuard';
import AppPagination from './AppPagination.vue';

const props = defineProps<{ workspace: OntologyWorkspace }>();
const emit = defineEmits<{ close: [] }>();
const app = useAppStore(), preview = usePreviewStore(), confirm = useConfirmStore();
const tab = ref('members'), page = ref(1), size = ref(10), total = ref(0);
const members = ref<WorkspaceMember[]>([]), loading = ref(false), error = ref('');
const edits = reactive<Record<string, WorkspaceRole>>({});
const userId = ref<string>(), role = ref<WorkspaceRole>('viewer'), saving = ref(false);
const userOptions = ref<{ value: string; label: string }[]>([]), optionsLoading = ref(false), optionsError = ref('');
const bindings = ref<WorkspaceRoleBindings | null>(null), selectedRoles = ref<string[]>([]), bindingsLoading = ref(false), bindingsError = ref('');
const roleLabels: Record<WorkspaceRole, string> = { owner: '所有者', editor: '编辑者', viewer: '只读成员' };
const roleOptions = Object.entries(roleLabels).map(([value, label]) => ({ value, label }));
const readonly = computed(() => props.workspace.role !== 'owner' || preview.active || !!app.viewVersion);
const reason = computed(() => preview.active ? '权限预览期间只读' : app.viewVersion ? '历史版本期间只读' : '');
const identity = () => JSON.stringify([props.workspace.id, currentApiContext().demoAs]);
const bindingChanged = computed(() => !!bindings.value && JSON.stringify([...selectedRoles.value].sort()) !== JSON.stringify([...bindings.value.roleCodes].sort()));
const membersChanged = computed(() => Object.keys(edits).length > 0);
const dirty = computed(() => !!userId.value || membersChanged.value || bindingChanged.value);
const guard = useUnsavedGuard(dirty);
let memberSeq = 0, optionSeq = 0, bindingSeq = 0, alive = true;
onBeforeUnmount(() => { alive = false; memberSeq++; optionSeq++; bindingSeq++; });
const current = (key: string) => alive && key === identity();
const failText = (e: unknown) => e instanceof Error ? e.message : '请求失败';
async function loadMembers() {
  const seq = ++memberSeq, key = identity();
  loading.value = true; error.value = ''; members.value = [];
  try {
    const result = await listWorkspaceMembers(props.workspace.id, page.value, size.value);
    if (seq !== memberSeq || !current(key)) return;
    members.value = result.items; total.value = result.total;
  } catch (e) { if (seq === memberSeq && current(key)) { total.value = 0; error.value = failText(e); } }
  finally { if (seq === memberSeq && current(key)) loading.value = false; }
}
async function loadUserOptions(kw = '') {
  const seq = ++optionSeq, key = identity();
  optionsLoading.value = true; optionsError.value = '';
  try {
    const result = await workspaceMemberOptions(props.workspace.id, kw);
    if (seq === optionSeq && current(key)) userOptions.value = result.items.filter(user => user.id !== currentApiContext().demoAs).map(user => ({ value: user.id, label: `${user.name} · ${user.id}` }));
  } catch (e) { if (seq === optionSeq && current(key)) { userOptions.value = []; optionsError.value = failText(e); } }
  finally { if (seq === optionSeq && current(key)) optionsLoading.value = false; }
}
async function loadBindings() {
  const seq = ++bindingSeq, key = identity();
  bindingsLoading.value = true; bindingsError.value = ''; bindings.value = null;
  try {
    const result = await getWorkspaceRoleBindings(props.workspace.id);
    if (seq === bindingSeq && current(key)) { bindings.value = result; selectedRoles.value = [...result.roleCodes]; }
  } catch (e) { if (seq === bindingSeq && current(key)) bindingsError.value = failText(e); }
  finally { if (seq === bindingSeq && current(key)) bindingsLoading.value = false; }
}
watch([page, size, identity], () => void loadMembers(), { immediate: true });
watch(identity, () => { discardChanges(); void loadUserOptions(); void loadBindings(); }, { immediate: true });
function discardChanges() {
  userId.value = undefined;
  for (const id of Object.keys(edits)) delete edits[id];
  selectedRoles.value = [...(bindings.value?.roleCodes ?? [])];
}
function editRole(member: WorkspaceMember, value: WorkspaceRole) {
  if (value === member.role) delete edits[member.userId]; else edits[member.userId] = value;
}
function changePage(value: number) { void guard(() => { discardChanges(); page.value = value; }); }
function changeSize(value: number) { void guard(() => { discardChanges(); page.value = 1; size.value = value; }); }
function close() { void guard(() => emit('close')); }
async function saveMember(memberId: string, nextRole: WorkspaceRole, name: string) {
  if (readonly.value || saving.value) return;
  const key = identity(), workspaceId = props.workspace.id;
  const ok = await confirm.ask({ title: '保存本体成员权限', target: `${props.workspace.name} / ${name}`, fields: [{ label: '用户编号', value: memberId }, { label: '本体权限', value: roleLabels[nextRole] }], impact: '此权限仅在当前本体工作空间生效，实际操作仍受平台功能权限限制。', okText: '保存权限' });
  if (!ok || !current(key) || readonly.value) return;
  saving.value = true;
  try {
    await saveWorkspaceMember(workspaceId, memberId, nextRole);
    if (!current(key)) return;
    delete edits[memberId];
    if (userId.value === memberId) { userId.value = undefined; role.value = 'viewer'; }
    message.success('成员权限已保存'); await loadMembers();
  } catch (e) { if (current(key)) message.error(failText(e)); }
  finally { if (current(key)) saving.value = false; }
}
function addMember() {
  if (!userId.value) return;
  const selected = userOptions.value.find(user => user.value === userId.value) ?? { value: userId.value, label: userId.value };
  void saveMember(selected.value, role.value, selected.label);
}
async function saveBindings() {
  if (readonly.value || saving.value || !bindings.value || !bindingChanged.value) return;
  const key = identity(), workspaceId = props.workspace.id, codes = [...selectedRoles.value];
  const names = bindings.value.items.filter(item => codes.includes(item.code)).map(item => item.name).join('、') || '未启用任何角色';
  const ok = await confirm.ask({ title: '保存本体对象权限', target: props.workspace.name, fields: [{ label: '启用角色', value: names }], impact: '仅启用这些平台角色在当前本体中的对象与行级权限；未获得对象授权的成员无法读取业务对象数据。', okText: '保存权限' });
  if (!ok || !current(key) || readonly.value) return;
  saving.value = true;
  try {
    await saveWorkspaceRoleBindings(workspaceId, codes);
    if (!current(key)) return;
    message.success('本体对象权限已保存'); await loadBindings();
  } catch (e) { if (current(key)) message.error(failText(e)); }
  finally { if (current(key)) saving.value = false; }
}
const columns = [{ title: '成员', dataIndex: 'name' }, { title: '用户编号', dataIndex: 'userId', width: 140 }, { title: '本体权限', key: 'role', width: 150 }, { title: '操作', key: 'action', width: 80 }];
</script>

<template>
  <a-drawer :open="true" :width="760" :title="`${workspace.name} · 成员管理`" :body-style="{ display: 'flex', flexDirection: 'column', minHeight: 0 }" @close="close">
    <a-alert v-if="reason" :message="reason" type="info" show-icon class="wm-notice" />
    <a-tabs v-model:active-key="tab" class="wm-tabs"><a-tab-pane key="members" tab="成员" /><a-tab-pane key="objects" tab="对象权限" /></a-tabs>
    <template v-if="tab === 'members'">
      <a-form layout="vertical" class="wm-add" @finish="addMember">
        <a-form-item label="用户" class="wm-user"><a-select v-model:value="userId" show-search allow-clear :filter-option="false" :options="userOptions" :loading="optionsLoading" :disabled="readonly || saving" placeholder="搜索用户姓名或编号" @search="loadUserOptions" /></a-form-item>
        <a-form-item label="本体权限"><a-select v-model:value="role" :options="roleOptions" :disabled="readonly || saving" class="wm-role" /></a-form-item>
        <a-form-item><a-button type="primary" html-type="submit" :disabled="readonly || !userId || saving" :loading="saving">添加成员</a-button></a-form-item>
      </a-form>
      <a-alert v-if="optionsError" type="error" :message="optionsError" show-icon class="wm-notice"><template #action><a-button size="small" @click="loadUserOptions()">重试候选</a-button></template></a-alert>
      <a-alert v-if="error" type="error" :message="error" show-icon class="wm-notice"><template #action><a-button size="small" @click="loadMembers">重试</a-button></template></a-alert>
      <div class="wm-list"><a-table :columns="columns" :data-source="members" :loading="loading" :pagination="false" row-key="userId" size="middle">
        <template #bodyCell="{ column, record }">
          <template v-if="column.key === 'role'"><a-select :value="edits[record.userId] || record.role" :options="roleOptions" :disabled="readonly || saving || record.userId === currentApiContext().demoAs" class="wm-role" @change="(value: WorkspaceRole) => editRole(record, value)" /></template>
          <template v-else-if="column.key === 'action'"><a-button type="link" size="small" :disabled="readonly || saving || !edits[record.userId] || edits[record.userId] === record.role" @click="saveMember(record.userId, edits[record.userId]!, record.name)">保存</a-button></template>
        </template>
      </a-table></div>
      <AppPagination :page="page" :size="size" :total="total" :empty="false" @update:page="changePage" @update:size="changeSize" />
    </template>
    <div v-else class="wm-object">
      <a-alert v-if="bindingsError" type="error" show-icon :message="bindingsError"><template #action><a-button size="small" @click="loadBindings">重试</a-button></template></a-alert>
      <a-spin :spinning="bindingsLoading"><a-form layout="vertical">
        <a-form-item label="启用对象权限的平台角色"><a-select v-model:value="selectedRoles" mode="multiple" :options="bindings?.items.map(item => ({ value: item.code, label: item.name })) || []" :max-tag-count="4" :disabled="readonly || saving || !bindings" placeholder="选择平台角色" option-filter-prop="label" /></a-form-item>
        <p class="t-12 c-3">选中角色的对象与行级权限在此本体生效；功能权限仍由平台角色决定。</p>
        <a-button type="primary" :disabled="readonly || !bindingChanged || saving" :loading="saving" @click="saveBindings">保存对象权限</a-button>
      </a-form></a-spin>
    </div>
  </a-drawer>
</template>

<style scoped>
.wm-tabs { flex: none; }
.wm-add { display: flex; align-items: end; gap: 12px; flex: none; }
.wm-add :deep(.ant-form-item) { margin-bottom: 16px; }
.wm-user { flex: 1; min-width: 0; }
.wm-role { width: 120px; }
.wm-list { flex: 1; min-height: 0; overflow: auto; }
.wm-notice { margin-bottom: 16px; flex: none; }
.wm-object { flex: 1; min-height: 0; overflow: auto; }
</style>
