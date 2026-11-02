<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import { useRouter } from 'vue-router';
import { message } from 'ant-design-vue';
import { useAppStore } from '@/stores/app';
import { useAuthStore } from '@/stores/auth';
import { loadMyProfile } from '@/api/profile';
import { workspaceCatalog, type OntologyWorkspace } from '@/api/workspaces';
import { anyReal } from '@/api/config';
import { currentApiContext } from '@/api/http';
import { adminApiOn } from '@/api/admin';
import { PAGES } from '@/nav';
import VersionPicker from '@/components/VersionPicker.vue';
import NoticeList from '@/components/NoticeList.vue';
import ChangePasswordModal from '@/components/ChangePasswordModal.vue';
import { isNotificationsReal, notificationContextKey, unreadNotifications } from '@/api/notifications';
import { applyChrome, chrome } from '@/theme';

const app = useAppStore();
const auth = useAuthStore();
const signingOut = ref(false);
const passwordOpen = ref(false);
async function onUserMenu({ key }: { key: string | number }) {
  if (key === 'password') { passwordOpen.value = true; return; }
  if (key !== 'logout' || signingOut.value) return;
  signingOut.value = true;
  try { await auth.signOut(); }
  catch { message.error('退出失败，请重试'); signingOut.value = false; }
}
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
  profileLoading.value = true; profileError.value = '';
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
watch(() => router.currentRoute.value.name, (name) => { if (name === 'workspaces') void reloadProfile(); });
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
    <router-link to="/" class="tb-brand" aria-label="仁谷科技">
      <span class="tb-mark" aria-hidden="true" />
      <span class="tb-word">
        <span class="tb-cn">仁谷科技</span>
        <span class="tb-en">RenGu Technology</span>
      </span>
    </router-link>

    <div v-if="anyReal()" class="tb-workspace">
      <span class="t-12 c-3">本体</span>
      <a-select :value="app.workspace ? app.ontologyId : undefined" :options="workspaceOptions" :loading="app.workspaceLoading" :placeholder="workspaceListError || '选择本体工作空间'" :bordered="false" class="tb-workspace-select" aria-label="本体工作空间" @change="switchWorkspace" />
      <a-button type="text" size="small" @click="router.push({ name: 'workspaces' })">管理</a-button>
    </div>
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
      <a-segmented
        class="tb-chrome" size="small" :value="chrome" aria-label="界面配色"
        :options="[{ label: '白色', value: 'light' }, { label: '海军蓝', value: 'navy' }]"
        @change="(value: string | number) => applyChrome(value === 'navy' ? 'navy' : 'light')"
      />
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
      <a-dropdown placement="bottomRight" :trigger="['click']">
        <button type="button" class="tb-user" :disabled="signingOut" aria-label="账户菜单">
          <a-avatar :size="24" class="tb-avatar"><span class="t-12">{{ app.user.name.slice(0, 1) || '—' }}</span></a-avatar>
          <span class="t-14 c-1 tb-user-name" :title="profileError || app.user.name">{{ app.user.name || (profileLoading ? '身份加载中' : '用户未提供') }}</span>
        </button>
        <template #overlay>
          <a-menu :items="[{ key: 'password', label: '修改密码', disabled: signingOut }, { key: 'logout', label: signingOut ? '正在退出…' : '退出登录', disabled: signingOut }]" @click="onUserMenu" />
        </template>
      </a-dropdown>
    </div>
  </header>
  <ChangePasswordModal v-model:open="passwordOpen" />
</template>

<style scoped>
.tb { height: var(--topbar-h); flex: none; min-width: 0; display: flex; align-items: center; gap: 16px; padding: 0 16px; background: var(--g1); border-bottom: var(--border); }
.tb-brand { flex: none; display: flex; align-items: center; gap: 8px; min-width: calc(var(--sider-w) - 16px); text-decoration: none; color: inherit; }
.tb-mark { width: 28px; height: 32px; flex: none; background: var(--mark); -webkit-mask: url('../assets/rengu-mark.png') center / contain no-repeat; mask: url('../assets/rengu-mark.png') center / contain no-repeat; }
.tb-word { display: flex; flex-direction: column; min-width: 0; }
.tb-cn { color: var(--g10); font-size: var(--fs-16); font-weight: var(--fw-strong); line-height: 20px; }
.tb-en { color: var(--g8); font-size: var(--fs-12); line-height: 14px; letter-spacing: 0.01em; }
.tb-chrome { flex: none; }
.tb-workspace { display: flex; align-items: center; gap: 4px; flex: none; }
.tb-workspace-select { width: 184px; }
.tb-search { min-width: 0; flex: 1; display: flex; justify-content: center; }
.tb-ac { width: min(100%, 560px); }
.tb-kbd { border: var(--border); border-radius: 2px; padding: 0 4px; line-height: 18px; }
.tb-right { display: flex; align-items: center; gap: 8px; flex: none; }
.tb-icon { color: var(--g9); }
.tb-user { display: flex; align-items: center; gap: 8px; cursor: pointer; padding: 4px 0 4px 8px; border: 0; border-radius: 4px; color: inherit; background: transparent; font: inherit; }
.tb-user:focus-visible { outline: 2px solid var(--brand); outline-offset: 3px; }
.tb-user:disabled { cursor: wait; opacity: .65; }
.tb-user-name { max-width: 112px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.tb-avatar { background: var(--brand); }
@media (max-width: 1400px) { .tb-workspace-select { width: 152px; } .tb { gap: 8px; } }
@media (max-width: 1000px) {
  .tb { overflow-x: auto; }
  .tb-search { min-width: 180px; }
}
.tb-help { min-width: 200px; }
.tb-help-row { display: flex; justify-content: space-between; gap: 16px; white-space: nowrap; }
</style>
