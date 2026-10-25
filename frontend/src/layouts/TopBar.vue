<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue';
import { useRouter } from 'vue-router';
import { useAppStore, TENANTS } from '@/stores/app';
import { PAGES } from '@/nav';
import VersionPicker from '@/components/VersionPicker.vue';
import NoticeList from '@/components/NoticeList.vue';

const app = useAppStore();
const router = useRouter();
const q = ref('');
const noticeOpen = ref(false);
const inputRef = ref<{ focus: () => void } | null>(null);

// Ctrl K / ⌘K 聚焦全局搜索兼提问框
function onKey(e: KeyboardEvent) {
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); inputRef.value?.focus(); }
}
onMounted(() => window.addEventListener('keydown', onKey));
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

    <a-select v-model:value="app.tenant" :options="TENANTS" :bordered="false" class="tb-tenant" popup-class-name="tb-tenant-pop" />

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
      <VersionPicker />
      <a-divider type="vertical" />
      <a-popover v-model:open="noticeOpen" trigger="click" placement="bottomRight">
        <template #content><NoticeList @picked="noticeOpen = false" /></template>
        <a-badge :count="app.unread" size="small">
          <a-button type="text" size="small" class="tb-icon" aria-label="通知"><BellOutlined /></a-button>
        </a-badge>
      </a-popover>
      <a-button type="text" size="small" class="tb-icon"><QuestionCircleOutlined /></a-button>
      <a-dropdown placement="bottomRight">
        <span class="tb-user">
          <a-avatar :size="24" class="tb-avatar"><span class="t-12">{{ app.user.name.slice(0, 1) }}</span></a-avatar>
          <span class="t-14 c-1">{{ app.user.name }}</span>
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
.tb-brand { display: flex; align-items: center; gap: 8px; width: calc(var(--sider-w) - 16px); text-decoration: none; }
.tb-tenant { width: 236px; }
.tb-search { flex: 1; display: flex; justify-content: center; }
.tb-ac { width: min(560px, 36vw); }
.tb-kbd { border: var(--border); border-radius: 2px; padding: 0 4px; line-height: 18px; }
.tb-right { display: flex; align-items: center; gap: 8px; }
.tb-icon { color: var(--g9); }
.tb-user { display: flex; align-items: center; gap: 8px; cursor: pointer; padding-left: 8px; }
.tb-avatar { background: var(--brand); }
</style>
