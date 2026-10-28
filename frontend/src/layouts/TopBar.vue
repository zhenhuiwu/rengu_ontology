<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import { useRouter } from 'vue-router';
import { message } from 'ant-design-vue';
import { useAppStore } from '@/stores/app';
import { loadTenants, tenantOptions } from '@/api/tenants';
import { loadMyProfile } from '@/api/profile';
import { workspaceCatalog, type OntologyWorkspace } from '@/api/workspaces';
import { anyReal } from '@/api/config';
import { currentApiContext } from '@/api/http';
import { adminApiOn } from '@/api/admin';
import { PAGES } from '@/nav';
import VersionPicker from '@/components/VersionPicker.vue';
import NoticeList from '@/components/NoticeList.vue';
import { isNotificationsReal, notificationContextKey, unreadNotifications } from '@/api/notifications';

const app = useAppStore();
const router = useRouter();
const workspaces = ref<OntologyWorkspace[]>([]), workspaceListError = ref('');
let workspaceListSeq = 0;
const workspaceOptions = computed(() => workspaces.value.map(workspace => ({ value: String(workspace.id), label: workspace.name })));
async function reloadWorkspaces() {
  if (!anyReal()) return;
  const seq = ++workspaceListSeq;
  try { const result = await workspaceCatalog(); if (seq === workspaceListSeq) { workspaces.value = result.items; workspaceListError.value = ''; } }
  catch { if (seq === workspaceListSeq) { workspaces.value = []; workspaceListError.value = '本体列表加载失败'; } }
}
watch([() => app.persona, () => app.workspaceEpoch], () => void reloadWorkspaces(), { immediate: true });
function switchWorkspace(id: string) {
  if (id === app.ontologyId && !app.workspaceError) return;
  void router.push({ name: 'ontology-overview', query: { ontology: id } });
}
const profileLoading = ref(false), profileError = ref('');
let profileSeq = 0;
async function reloadProfile() {
  if (!adminApiOn()) return;
  const my = ++profileSeq, key = currentApiContext().demoAs;
  app.user = { name: '', role: '' }; profileLoading.value = true; profileError.value = '';
  try { const profile = await loadMyProfile(); if (my === profileSeq && key === currentApiContext().demoAs && profile) app.user = { name: profile.name, role: profile.roleNames.join('、') || '未分配角色' }; }
  catch { if (my === profileSeq && key === currentApiContext().demoAs) profileError.value = '当前身份加载失败'; }
  finally { if (my === profileSeq && key === currentApiContext().demoAs) profileLoading.value = false; }
}
watch(() => currentApiContext().demoAs, () => { void reloadProfile(); }, { immediate: true });
const q = ref('');
const noticeOpen = ref(false);
let unreadSequence = 0;
function invalidateUnread() { unreadSequence++; }
async function reloadUnread() {
  const seq = ++unreadSequence, key = notificationContextKey();
  if (!isNotificationsReal() || !app.workspace || app.workspaceError) { app.notificationUnread = 0; return; }
  try { const result = await unreadNotifications(); if (seq === unreadSequence && key === notificationContextKey()) app.setNotificationUnread(key, result.unread); }
  catch { /* The opened inbox shows its own loading error; never substitute sample notices. */ }
}
watch([notificationContextKey, () => app.workspaceEpoch, () => app.workspaceError], () => { noticeOpen.value = false; void reloadUnread(); }, { immediate: true });
onMounted(() => window.addEventListener('focus', reloadUnread));
onBeforeUnmount(() => { unreadSequence++; window.removeEventListener('focus', reloadUnread); });
const inputRef = ref<{ focus: () => void } | null>(null);

// Ctrl K / ⌘K 聚焦全局搜索兼提问框
function onKey(e: KeyboardEvent) {
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); inputRef.value?.focus(); }
}
onMounted(() => window.addEventListener('keydown', onKey));
// 租户 / 组织：后台 sys_tenant + sys_org（GET /tenants），mock 模式回退 TENANTS
const tenants = tenantOptions;
const tenantError = ref('');
watch(tenants, (options) => {
  if (options.length) tenantError.value = '';
  if (!options.some((t) => t.value === app.tenant)) app.tenant = (options.find((t) => t.default) ?? options[0])?.value ?? app.tenant;
}, { immediate: true });
async function reloadTenants() {
  const identity = currentApiContext().demoAs;
  tenantError.value = '';
  try {
    await loadTenants();
  } catch { if (identity === currentApiContext().demoAs) tenantError.value = '租户与组织加载失败'; }
}
watch(() => currentApiContext().demoAs, () => void reloadTenants(), { immediate: true });
watch(() => router.currentRoute.value.name, (name) => { if (name === 'workspaces') { void reloadProfile(); void reloadTenants(); } });
// 顶栏 ⓘ 使用帮助菜单（原型：快速入门 / 反馈只给提示）
function onHelp({ key }: { key: string | number }) {
  if (key === 'kbd') setTimeout(() => inputRef.value?.focus(), 0);
  else if (key === 'start') message.info('快速入门：在顶部搜索框输入对象、指标或直接提问');
  else if (key === 'feedback') message.info(adminApiOn() ? '请把问题和截图发给平台管理员' : '请把问题和截图发给平台管理员王敏');
}
onBeforeUnmount(() => window.removeEventListener('keydown', onKey));

const options = () => {
  const kw = q.value.trim();
  const pages = kw ? PAGES.filter((p) => p.title.includes(kw)).slice(0, 6) : [];
  return [
    ...pages.map((p) => ({ value: `page:${p.key}`, label: `${p.module.title} / ${p.title}` })),
    ...(kw ? [{ value: `ask:${kw}`, label: `提问：${kw}` }] : []),
  ];
};
function onSelect(v: string) {
  if (v.startsWith('page:')) router.push({ name: v.slice(5) });
  else router.push({ name: 'qa-workbench', query: { q: v.slice(4) } });
  q.value = '';
}
</script>

<template>
  <header class="tb">
    <router-link to="/" class="tb-brand">
      <svg width="20" height="20" viewBox="0 0 20 20" aria-hidden="true">
        <rect width="20" height="20" rx="3" fill="var(--brand)" />
        <circle cx="6" cy="6.5" r="2" fill="#fff" /><circle cx="14" cy="6.5" r="2" fill="#fff" /><circle cx="10" cy="14" r="2" fill="#fff" />
        <path d="M6 6.5H14L10 14Z" stroke="#fff" stroke-width="1.1" fill="none" />
      </svg>
      <span class="t-16 fw-600 c-1">本体智能平台</span>
    </router-link>

    <div v-if="anyReal()" class="tb-workspace">
      <span class="t-12 c-3">本体</span>
      <a-select :value="app.workspace ? app.ontologyId : undefined" :options="workspaceOptions" :loading="app.workspaceLoading" :placeholder="workspaceListError || '选择本体工作空间'" :bordered="false" class="tb-workspace-select" aria-label="本体工作空间" @change="switchWorkspace" />
      <a-button type="text" size="small" @click="router.push({ name: 'workspaces' })">管理</a-button>
    </div>
    <span class="tb-org-label t-12 c-3">组织</span>
    <a-select v-model:value="app.tenant" :options="tenants" :placeholder="tenantError || '暂无组织'" :status="tenantError ? 'error' : undefined" :bordered="false" class="tb-tenant" popup-class-name="tb-tenant-pop" />
    <span v-if="tenantError" class="t-12 c-3">{{ tenantError }} <a-button type="link" size="small" @click="reloadTenants">重试</a-button></span>

    <div class="tb-search">
      <a-auto-complete
        v-model:value="q" :options="options()" class="tb-ac" :dropdown-match-select-width="true"
        @select="onSelect"
      >
        <a-input ref="inputRef" placeholder="搜索对象、指标、页面，或直接输入问题" allow-clear>
          <template #prefix><SearchOutlined class="c-3" /></template>
          <template #suffix><span class="tb-kbd t-12 c-3">Ctrl K</span></template>
        </a-input>
      </a-auto-complete>
    </div>

    <div class="tb-right">
      <VersionPicker v-if="!app.workspaceError && router.currentRoute.value.name !== 'workspaces'" />
      <a-divider type="vertical" />
      <a-popover v-model:open="noticeOpen" trigger="click" placement="bottomRight">
        <template #content><NoticeList :active="noticeOpen" @updated="invalidateUnread" @picked="noticeOpen = false" /></template>
        <a-badge :count="app.unread" size="small" :number-style="{ backgroundColor: 'var(--g8)', color: '#fff', boxShadow: 'none' }">
          <a-button type="text" size="small" class="tb-icon" aria-label="通知"><BellOutlined /></a-button>
        </a-badge>
      </a-popover>
      <a-dropdown trigger="click" placement="bottomRight">
        <a-button type="text" size="small" class="tb-icon" aria-label="使用帮助" title="使用帮助"><QuestionCircleOutlined /></a-button>
        <template #overlay>
          <a-menu class="tb-help" @click="onHelp">
            <a-menu-item-group title="使用帮助">
              <a-menu-item key="start">快速入门</a-menu-item>
              <a-menu-item key="kbd"><span class="tb-help-row">快捷键：搜索<span class="tb-kbd t-12 c-2">Ctrl K</span></span></a-menu-item>
              <a-menu-item key="feedback">反馈问题</a-menu-item>
              <a-menu-item key="ver" disabled><span class="tb-help-row c-2">版本<span class="num">{{ app.ontologyVersion }}</span></span></a-menu-item>
            </a-menu-item-group>
          </a-menu>
        </template>
      </a-dropdown>
      <a-dropdown placement="bottomRight">
        <span class="tb-user">
          <a-avatar :size="24" class="tb-avatar"><span class="t-12">{{ app.user.name.slice(0, 1) || '—' }}</span></a-avatar>
          <span class="t-14 c-1 tb-user-name" :title="app.user.name">{{ app.user.name || (profileLoading ? '身份加载中' : '用户未提供') }}</span><a-button v-if="profileError" type="link" size="small" :title="profileError" @click.stop="reloadProfile">重试身份</a-button>
        </span>
        <template #overlay>
          <a-menu :items="[{ key: 'profile', label: '个人设置' }, { key: 'logout', label: '退出登录' }]" />
        </template>
      </a-dropdown>
    </div>
  </header>
</template>

<style scoped>
.tb { height: var(--topbar-h); flex: none; display: flex; align-items: center; gap: 16px; padding: 0 16px; background: var(--g1); border-bottom: var(--border); }
.tb-brand { flex: none; display: flex; align-items: center; gap: 8px; width: calc(var(--sider-w) - 16px); text-decoration: none; }
.tb-tenant { width: 210px; flex: none; }
.tb-org-label { margin-right: -12px; }
.tb-workspace { display: flex; align-items: center; gap: 4px; flex: none; }
.tb-workspace-select { width: 184px; }
.tb-search { min-width: 120px; flex: 1; display: flex; justify-content: center; }
.tb-ac { width: min(100%, 560px); }
.tb-kbd { border: var(--border); border-radius: 2px; padding: 0 4px; line-height: 18px; }
.tb-right { display: flex; align-items: center; gap: 8px; }
.tb-icon { color: var(--g9); }
.tb-user { display: flex; align-items: center; gap: 8px; cursor: pointer; padding-left: 8px; }
.tb-user-name { max-width: 112px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.tb-avatar { background: var(--brand); }
@media (max-width: 1400px) { .tb-tenant { width: 170px; } .tb-org-label { display: none; } .tb-workspace-select { width: 152px; } .tb { gap: 8px; } }
.tb-help { min-width: 200px; }
.tb-help-row { display: flex; justify-content: space-between; gap: 16px; white-space: nowrap; }
</style>
