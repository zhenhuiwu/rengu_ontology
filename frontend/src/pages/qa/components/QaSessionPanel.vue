<script setup lang="ts">
/**
 * 问数工作台左栏：新建会话、关键词搜索 + 时间筛选、历史会话（每页 12 条，simple 分页贴栏底）；常用问题、我的页面贴底。
 * 翻页、搜索、筛选写在 URL：?sessPage=2&sessQ=库存&sessRange=7d
 * 悬停会话显示“…”菜单：重命名（行内编辑）、删除（Popconfirm 二次确认）。
 * “常用问题 · 管理”“我的页面 · 全部”不跳转，打开本页抽屉（?panel=favorites / ?panel=pages）。
 */
import { computed, ref, watch, nextTick } from 'vue';
import { message } from 'ant-design-vue';
import { PlusOutlined, SearchOutlined, EllipsisOutlined } from '@ant-design/icons-vue';
import { QA_TODAY, QA_FAVORITES, QA_MY_PAGES } from '@/mock';
import type { QaSession } from '@/mock/qa';
import { useQaSession, errText } from '../useQaSession';
import { qaFallBackIfDown } from '../qaFallback';
import * as qaApi from '@/api/qa';
import AppPagination from '@/components/AppPagination.vue';
import { useQueryParam, useQueryPage } from '../useQueryParam';
import { useReadonly } from '@/composables/useReadonly';

const { readonly, reason } = useReadonly();

const emit = defineEmits<{ (e: 'panel', v: 'favorites' | 'pages'): void }>();
const PAGE_SIZE = 12;
const { sessions: mockList, activeId: active, newSession, real, live, detail, listTick, favTick, newSessionReal } = useQaSession();
/* 真接口：当前页由 W1 取（q / range / page / size=12）；mock 照旧本地过滤 */
const srv = ref<{ items: QaSession[]; total: number }>({ items: [], total: 0 });
const list = computed<QaSession[]>({ get: () => (live.value ? srv.value.items : mockList.value), set: (v) => { mockList.value = v; } });
const favTop = ref<string[]>(live.value ? [] : QA_FAVORITES);
const pageTop = ref<string[]>(live.value ? [] : QA_MY_PAGES);
// 后台没起来 → 回退 mock（qaFallback.ts）
watch(live, (on) => { if (!on) { favTop.value = QA_FAVORITES; pageTop.value = QA_MY_PAGES; } });
const readErr = (what: string) => (e: unknown) => { if (!qaFallBackIfDown(e)) message.error(`${what}加载失败：${errText(e)}`); };
const editing = ref('');
const draft = ref('');
const menuId = ref('');
const delId = ref('');
const editInput = ref<{ focus: () => void }[]>([]);
async function onMenu(id: string, key: string) {
  menuId.value = '';
  const s = list.value.find((x) => x.id === id); if (!s) return;
  if (key === 'rename') { editing.value = id; draft.value = s.title; await nextTick(); editInput.value[0]?.focus(); }
  else delId.value = id;
}
async function saveRename() {
  const id = editing.value;
  const s = list.value.find((x) => x.id === id);
  editing.value = '';
  if (!s || !draft.value.trim() || draft.value.trim() === s.title) return;
  if (!real) { s.title = draft.value.trim(); return; }
  try {
    const d = await qaApi.patchSession(id, { title: draft.value.trim() });
    s.title = d.title;
    if (detail.value?.id === id) detail.value.title = d.title;
  } catch (e) { message.error(`重命名失败：${errText(e)}`); }
}
async function doDelete(id: string) {
  delId.value = '';
  if (real) {
    try { await qaApi.deleteSession(id); } catch (e) { message.error(`删除失败：${errText(e)}`); return; }
    message.success('会话已删除');
    await load();
    if (active.value === id) {
      active.value = srv.value.items[0]?.id ?? '';
      // 删的是最后一个会话：清掉屏上的旧对话，回到空状态
      if (!active.value) detail.value = null;
    }
    return;
  }
  list.value = list.value.filter((x) => x.id !== id);
  if (active.value === id) active.value = list.value[0]?.id ?? '';
  message.success('会话已删除');
}
async function onNew() {
  if (readonly.value) return;
  kw.value = ''; range.value = 'all'; page.value = 1;
  if (real) await newSessionReal(detail.value?.scope ?? '华东', detail.value?.caliber ?? '不含税');
  else newSession();
}
function select(id: string) { if (editing.value !== id) active.value = id; }
const pageModel = computed({ get: () => current.value, set: (p: number) => { page.value = p; } });
const kw = useQueryParam('sessQ');
const range = useQueryParam('sessRange', 'all');
const page = useQueryPage('sessPage');
const RANGES = [
  { value: 'all', label: '全部时间' },
  { value: 'today', label: '今天' },
  { value: '7d', label: '近 7 天' },
  { value: '30d', label: '近 30 天' },
];
const daysAgo = (date: string) => Math.round((Date.parse(QA_TODAY) - Date.parse(date)) / 86400000);
const filtered = computed(() => {
  const k = kw.value.trim();
  const limit = range.value === 'today' ? 0 : range.value === '7d' ? 6 : range.value === '30d' ? 29 : Infinity;
  return list.value.filter((s) => (!k || s.title.includes(k)) && daysAgo(s.date) <= limit);
});
const total = computed(() => (live.value ? srv.value.total : filtered.value.length));
const pageCount = computed(() => Math.max(1, Math.ceil(total.value / PAGE_SIZE)));
const current = computed(() => Math.min(page.value, pageCount.value));
const rows = computed(() => (live.value ? srv.value.items : filtered.value.slice((current.value - 1) * PAGE_SIZE, current.value * PAGE_SIZE)));
let reqSeq = 0;
async function load() {
  if (!live.value) return;
  const my = ++reqSeq;
  try {
    const r = await qaApi.listSessions({ q: kw.value.trim() || undefined, range: range.value, page: page.value, size: PAGE_SIZE });
    if (my !== reqSeq) return;
    srv.value = { items: r.items, total: r.total };
    // 页码超出（比如删掉最后一页唯一一条）回落到最后一页
    if (!r.items.length && r.total && page.value > 1) { page.value = Math.ceil(r.total / PAGE_SIZE); return; }
    if (!active.value && r.items.length) active.value = r.items[0].id;
  } catch (e) { if (my === reqSeq) readErr('会话列表')(e); }
}
if (real) {
  watch([kw, range, page, listTick], load, { immediate: true });
  watch(favTick, () => { if (live.value) qaApi.listFavorites({ page: 1, size: 3 }).then((r) => { favTop.value = r.items.map((x) => x.question); }).catch(readErr('常用问题')); }, { immediate: true });
  qaApi.listPagesBrief({ page: 1, size: 2 }).then((r) => { pageTop.value = r.items.map((x) => x.name); }).catch(readErr('我的页面'));
}
const when = (date: string) => (date === QA_TODAY ? '今天' : date.slice(5));
// 改关键词或时间范围后回到第 1 页
watch([kw, range], () => { if (page.value !== 1) page.value = 1; });
</script>

<template>
  <section class="panel qa-sessions">
    <a-tooltip :title="reason"><a-button block :disabled="readonly" @click="onNew"><template #icon><PlusOutlined /></template>新建会话</a-button></a-tooltip>
    <div class="qa-ss-filter">
      <a-input :value="kw" placeholder="搜索会话" allow-clear @change="(e: Event) => (kw = (e.target as HTMLInputElement).value)">
        <template #prefix><SearchOutlined class="c-3" /></template>
      </a-input>
      <a-select v-model:value="range" :options="RANGES" class="qa-ss-range" />
    </div>
    <div class="qa-ss-head c-3"><span>历史会话</span><span v-if="total <= PAGE_SIZE" class="t-12">共 {{ total }} 条</span></div>
    <div class="qa-ss-list">
      <div v-for="s in rows" :key="s.id" class="qa-ss-item"
        :class="{ on: s.id === active, hold: menuId === s.id || delId === s.id, edit: editing === s.id }" :title="s.title" @click="select(s.id)">
        <a-input v-if="editing === s.id" ref="editInput" v-model:value="draft" size="small" class="qa-ss-edit"
          @press-enter="saveRename" @blur="saveRename" @keydown.esc="editing = ''" />
        <template v-else>
          <span class="qa-ss-title">{{ s.title }}</span>
          <span class="t-12 c-3 num qa-ss-date">{{ when(s.date) }}</span>
          <a-popconfirm :open="delId === s.id" title="删除这个会话？" description="删除后不可恢复，已固定到页面的卡片不受影响。"
            ok-text="删除" cancel-text="取消" :ok-button-props="{ danger: true }" placement="right"
            @confirm="doDelete(s.id)" @cancel="delId = ''" @open-change="(v: boolean) => { if (!v) delId = ''; }">
            <a-dropdown :trigger="['click']" :open="menuId === s.id" @open-change="(v: boolean) => (menuId = v ? s.id : '')">
              <a-button type="text" size="small" class="qa-ss-more" title="更多" @click.stop><EllipsisOutlined /></a-button>
              <template #overlay>
                <a-menu @click="({ key }: { key: string }) => onMenu(s.id, key)">
                  <a-menu-item key="rename">重命名</a-menu-item>
                  <a-menu-item key="delete" danger>删除</a-menu-item>
                </a-menu>
              </template>
            </a-dropdown>
          </a-popconfirm>
        </template>
      </div>
      <div v-if="!rows.length" class="qa-ss-empty c-3">没有匹配的会话</div>
    </div>
    <AppPagination v-if="total > PAGE_SIZE" v-model:page="pageModel" variant="simple" :total="total" :size="PAGE_SIZE" :empty="false" class="qa-ss-pager" />
    <div class="qa-ss-bottom">
      <div class="qa-ss-group c-3">常用问题<a @click="emit('panel', 'favorites')">管理</a></div>
      <div v-for="s in favTop" :key="s" class="qa-ss-item"><span class="qa-ss-title">{{ s }}</span></div>
      <div class="qa-ss-group c-3">我的页面<a @click="emit('panel', 'pages')">全部</a></div>
      <div v-for="s in pageTop" :key="s" class="qa-ss-item"><span class="qa-ss-title">{{ s }}</span></div>
    </div>
  </section>
</template>

<style scoped>
.qa-sessions { width: 232px; flex: none; padding: 12px 8px; }
.qa-ss-filter { display: flex; gap: 4px; margin: 8px 0 4px; }
.qa-ss-filter :deep(.ant-input-affix-wrapper) { flex: 1; min-width: 0; }
.qa-ss-range { width: 96px; flex: none; }
.qa-ss-head { display: flex; justify-content: space-between; align-items: center; padding: 8px 8px 4px; }
.qa-ss-list { flex: none; }
.qa-ss-item { display: flex; align-items: center; gap: 8px; padding: 4px 8px; border-radius: var(--radius); cursor: pointer; }
.qa-ss-title { flex: 1; min-width: 0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.qa-ss-item:hover { background: var(--g3); }
.qa-ss-item.on { background: var(--brand-bg); color: var(--brand); }
.qa-ss-item.edit { padding: 2px 4px; }
.qa-ss-edit { flex: 1; }
/* “…”只在悬停（或菜单/确认打开）时出现，占用日期的位置，不改变行高 */
.qa-ss-more { display: none; width: 24px; height: 22px; padding: 0; flex: none; }
.qa-ss-item:hover .qa-ss-more, .qa-ss-item.hold .qa-ss-more { display: inline-flex; align-items: center; justify-content: center; }
.qa-ss-item:hover .qa-ss-date, .qa-ss-item.hold .qa-ss-date { display: none; }
.qa-ss-empty { padding: 16px 8px; text-align: center; }
.qa-ss-pager { padding: 0 8px; min-height: 32px; }
.qa-ss-group { display: flex; justify-content: space-between; padding: 8px 8px 4px; }
.qa-ss-bottom { margin-top: auto; border-top: var(--border); padding-top: 4px; }
</style>
