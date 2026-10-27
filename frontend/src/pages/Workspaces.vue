<script setup lang="ts">
import { reactive, ref, watch } from 'vue';
import { useRouter } from 'vue-router';
import { message } from 'ant-design-vue';
import { listWorkspaces, createWorkspace, type OntologyWorkspace } from '@/api/workspaces';
import { currentApiContext } from '@/api/http';
import { useAppStore } from '@/stores/app';
import { usePagedQuery } from '@/composables/usePagedQuery';
import WorkspaceMembers from '@/components/WorkspaceMembers.vue';
import AppPagination from '@/components/AppPagination.vue';

const app = useAppStore(), router = useRouter();
const pq = usePagedQuery({ defaultSize: 10 });
const items = ref<OntologyWorkspace[]>([]), total = ref(0), loading = ref(false), error = ref(''), canCreate = ref(false);
const managedWorkspace = ref<OntologyWorkspace | null>(null);
const open = ref(false), creating = ref(false);
const form = reactive({ code: '', name: '', description: '', domain: '' });
let seq = 0;
const roleLabel = { owner: '所有者', editor: '编辑者', viewer: '只读成员' };
async function load() {
  const my = ++seq, identity = app.persona;
  loading.value = true; error.value = ''; items.value = [];
  try {
    const result = await listWorkspaces(pq.page.value, pq.size.value);
    if (my !== seq || identity !== app.persona) return;
    items.value = result.items; total.value = result.total ?? result.items.length; canCreate.value = result.canCreate;
  } catch (e) { if (my === seq) { total.value = 0; error.value = e instanceof Error ? e.message : '本体工作空间加载失败'; } }
  finally { if (my === seq) loading.value = false; }
}
watch([pq.page, pq.size, () => app.persona], () => void load(), { immediate: true });
function enter(workspace: OntologyWorkspace) {
  return router.push({ name: 'ontology-overview', query: { ontology: String(workspace.id) } });
}
function startCreate() { Object.assign(form, { code: '', name: '', description: '', domain: '' }); open.value = true; }
async function create() {
  if (!/^[a-z][a-z0-9-]{1,62}$/.test(form.code)) { message.error('标识需为2–63位小写字母、数字或连字符，以字母开头'); return; }
  if (!form.name.trim()) { message.error('请输入本体名称'); return; }
  const identity = currentApiContext().demoAs;
  creating.value = true;
  try {
    const workspace = await createWorkspace({ ...form, name: form.name.trim() });
    if (identity !== currentApiContext().demoAs) return;
    open.value = false; message.success('本体工作空间已创建'); await enter(workspace);
  } catch (e) { message.error(e instanceof Error ? e.message : '创建失败'); }
  finally { creating.value = false; }
}
const columns = [
  { title: '本体名称', key: 'name', width: '24%' }, { title: '标识', dataIndex: 'code', width: '18%' },
  { title: '领域', dataIndex: 'domain', width: '16%' }, { title: '描述', dataIndex: 'description', ellipsis: true },
  { title: '我的权限', key: 'role', width: 120 }, { title: '操作', key: 'action', width: 180 },
];
</script>

<template>
  <section class="ws-page">
    <div class="ws-toolbar"><span class="t-14 c-2">选择本体工作空间</span><a-button v-if="canCreate" type="primary" @click="startCreate"><PlusOutlined />新建本体</a-button></div>
    <a-alert v-if="error" type="error" show-icon :message="error"><template #action><a-button size="small" @click="load">重试</a-button></template></a-alert>
    <div class="ws-table scroll-area"><a-table :columns="columns" :data-source="items" :loading="loading" :pagination="false" row-key="id" size="middle">
      <template #bodyCell="{ column, record }">
        <template v-if="column.key === 'name'"><a @click="enter(record)">{{ record.name }}</a><a-tag v-if="String(record.id) === app.ontologyId && app.workspace" class="ws-current">当前</a-tag></template>
        <template v-else-if="column.key === 'role'">{{ roleLabel[record.role as keyof typeof roleLabel] }}</template>
        <template v-else-if="column.key === 'action'"><a-button type="link" size="small" @click="enter(record)">进入</a-button><a-button v-if="record.role === 'owner' && canCreate" type="link" size="small" @click="managedWorkspace = record">成员管理</a-button></template>
        <template v-else-if="column.dataIndex === 'domain' || column.dataIndex === 'description'">{{ record[column.dataIndex] || '—' }}</template>
      </template>
    </a-table></div>
    <AppPagination v-model:page="pq.page.value" v-model:size="pq.size.value" :total="total" :empty="false" />
    <WorkspaceMembers v-if="managedWorkspace" :key="managedWorkspace.id" :workspace="managedWorkspace" @close="managedWorkspace = null" />
    <a-modal v-model:open="open" title="新建本体工作空间" :confirm-loading="creating" ok-text="创建并进入" cancel-text="取消" @ok="create">
      <a-form layout="vertical" class="ws-form">
        <a-form-item label="本体名称" required><a-input v-model:value="form.name" :maxlength="128" placeholder="输入本体名称" /></a-form-item>
        <a-form-item label="标识" required><a-input v-model:value="form.code" :maxlength="63" placeholder="例如 supply-chain" /></a-form-item>
        <a-form-item label="领域"><a-input v-model:value="form.domain" :maxlength="64" placeholder="输入业务领域" /></a-form-item>
        <a-form-item label="描述"><a-textarea v-model:value="form.description" :maxlength="1000" :rows="3" placeholder="描述本体用途" /></a-form-item>
      </a-form>
    </a-modal>
  </section>
</template>

<style scoped>
.ws-page { display: flex; flex-direction: column; gap: 16px; flex: 1; min-height: 0; }
.ws-toolbar { display: flex; align-items: center; justify-content: space-between; flex: none; }
.ws-table { flex: 1; min-height: 0; overflow: auto; background: var(--g1); border: var(--border); }
.ws-current { margin-left: 8px; }
.ws-form { margin-top: 24px; }
</style>
