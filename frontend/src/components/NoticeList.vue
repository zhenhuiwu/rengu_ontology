<script setup lang="ts">
/** Real notifications belong to the current person/workspace; preview never impersonates recipients. */
import { computed, ref, watch } from 'vue';
import { useRouter } from 'vue-router';
import { useAppStore } from '@/stores/app';
import { useDrawer } from '@/composables/useDrawer';
import AppPagination from './AppPagination.vue';
import { paginate } from '@/mock/paginate';
import type { Notice } from '@/mock/releases';
import { anyReal } from '@/api/config';
import { isNotificationsReal, listNotifications, markNotificationRead, markAllNotificationsRead, notificationContextKey, type Notification } from '@/api/notifications';

const props = withDefaults(defineProps<{ active?: boolean }>(), { active: true });
const emit = defineEmits<{ picked: []; updated: [] }>();
const app = useAppStore();
const router = useRouter();
const { open } = useDrawer();
const real = isNotificationsReal(), mock = !anyReal();
const SIZE = real ? 10 : 5;
const page = ref(1);
const filter = ref<'全部' | '未读'>('全部');
const items = ref<Notification[]>([]), total = ref(0), loadedContext = ref('');
const loading = ref(false), busy = ref(false), error = ref('');
let listSequence = 0, mutationSequence = 0;
const paged = computed(() => real ? { items: loadedContext.value === notificationContextKey() ? items.value : [], total: loadedContext.value === notificationContextKey() ? total.value : 0 } : mock ? paginate(app.notices.filter(n => filter.value === '全部' || !n.read), { page: page.value, size: SIZE }) : { items: [], total: 0 });
const errText = (e: unknown) => e instanceof Error ? e.message : '请求失败';
watch(notificationContextKey, () => { listSequence++; mutationSequence++; page.value = 1; items.value = []; total.value = 0; loadedContext.value = ''; error.value = ''; loading.value = false; busy.value = false; });
async function load() {
  const seq = ++listSequence, key = notificationContextKey();
  if (!real || !props.active || app.workspaceError) return;
  loading.value = true; error.value = '';
  try {
    const data = await listNotifications(page.value, filter.value === '未读');
    if (seq !== listSequence || key !== notificationContextKey()) return;
    loadedContext.value = key; items.value = data.items; total.value = data.total;
    app.setNotificationUnread(key, data.unread); emit('updated');
    if (page.value !== data.page) page.value = data.page;
  } catch (e) { if (seq === listSequence && key === notificationContextKey()) error.value = errText(e); }
  finally { if (seq === listSequence) loading.value = false; }
}
watch(filter, () => { page.value = 1; items.value = []; total.value = 0; });
watch([notificationContextKey, page, filter, () => props.active], load, { immediate: true });
async function pick(n: Notice) {
  if (mock) { n.read = true; open(n.kind, n.objId, { level: 1 }); emit('picked'); return; }
  if (!real || busy.value) return;
  const key = notificationContextKey(), seq = ++mutationSequence;
  listSequence++; loading.value = false; busy.value = true; error.value = '';
  try {
    const result = n.read ? null : await markNotificationRead(n.id);
    if (key !== notificationContextKey() || seq !== mutationSequence) return;
    if (result) { items.value = items.value.map(item => item.id === n.id ? { ...item, read: true } : item); app.setNotificationUnread(key, result.unread); emit('updated'); }
    await router.push({ path: '/build/ontology/releases', query: { ontology: app.ontologyId, cr: n.objId } });
    emit('picked');
  } catch (e) { if (key === notificationContextKey() && seq === mutationSequence) error.value = errText(e); }
  finally { if (seq === mutationSequence) busy.value = false; }
}
async function readAll() {
  if (mock) { app.notices.forEach(n => { n.read = true; }); return; }
  if (!real || busy.value) return;
  const key = notificationContextKey(), seq = ++mutationSequence;
  listSequence++; loading.value = false; busy.value = true; error.value = '';
  try {
    const result = await markAllNotificationsRead();
    if (key !== notificationContextKey() || seq !== mutationSequence) return;
    items.value = items.value.map(item => ({ ...item, read: true }));
    app.setNotificationUnread(key, result.unread); emit('updated');
    await load();
  } catch (e) { if (key === notificationContextKey() && seq === mutationSequence) error.value = errText(e); }
  finally { if (seq === mutationSequence) busy.value = false; }
}
</script>

<template>
  <div class="nl">
    <div class="nl-head">
      <span class="fw-600">通知</span>
      <a-space size="small"><a-button v-if="real" type="link" size="small" :disabled="loading || busy" @click="load">刷新</a-button><a-button v-if="app.unread" type="link" size="small" :disabled="loading || busy" @click="readAll">全部标为已读</a-button></a-space>
    </div>
    <a-alert v-if="error" type="error" :message="error" show-icon />
    <a-segmented v-if="real || mock" v-model:value="filter" :options="['全部', '未读']" size="small" class="nl-filter" />
    <div v-if="real && loading" class="t-12 c-3 nl-state">正在读取当前本体的通知…</div>
    <div v-if="!real && !mock" class="t-12 c-3 nl-state">通知接口未启用</div>
    <a-empty v-else-if="!loading && !error && !paged.items.length" description="暂无通知" />
    <ul class="nl-list">
      <li v-for="n in paged.items" :key="n.id">
        <button type="button" class="nl-item" :disabled="busy" @click="pick(n)">
        <span class="nl-dot" :class="{ on: !n.read }" />
        <div class="nl-body">
          <div class="nl-row" :title="n.desc ? `${n.title}\n${n.desc}` : n.title"><span class="nl-ell" :class="n.read ? 'c-2' : 'c-1'">{{ n.title }}</span><span class="t-12 c-2 num nl-time">{{ n.time }}</span></div>
        </div>
        </button>
      </li>
    </ul>
    <AppPagination v-if="paged.total" v-model:page="page" variant="simple" :total="paged.total" :size="SIZE" :empty="false" class="nl-pager" />
  </div>
</template>

<style scoped>
.nl { width: 340px; }
.nl-head { display: flex; justify-content: space-between; align-items: center; padding-bottom: 8px; border-bottom: var(--border); }
.nl-list { list-style: none; margin: 0; padding: 0; }
.nl-item { display: flex; gap: 8px; width: 100%; padding: 8px 4px; border: 0; border-bottom: var(--border); background: transparent; text-align: left; font: inherit; cursor: pointer; }
.nl-item:hover { background: var(--g3); }
.nl-item:focus-visible { outline: 2px solid var(--brand); outline-offset: -2px; }
.nl-item:disabled { cursor: wait; }
.nl-state { padding: 16px 0; }
.nl-filter { margin: 12px 0; }
.nl-dot { width: 6px; height: 6px; border-radius: 50%; margin-top: 8px; flex: none; }
.nl-dot.on { background: var(--brand); }
.nl-body { flex: 1; min-width: 0; }
.nl-row { display: flex; justify-content: space-between; gap: 8px; }
.nl-ell { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; min-width: 0; }
.nl-time { flex: none; }
.nl-pager { padding-top: 8px; }
</style>
