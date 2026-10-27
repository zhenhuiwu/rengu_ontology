<script setup lang="ts">
/**
 * 问数工作台左栏：新建会话、关键词搜索 + 时间筛选、历史会话（每页 10 条，simple 分页贴栏底）；常用问题、我的页面贴底。
 * 翻页、搜索、筛选写在 URL：?sessPage=2&sessQ=库存&sessRange=7d
 * 悬停会话显示“…”菜单：重命名（行内编辑）、删除（Popconfirm 二次确认）。
 * “常用问题 · 管理”“我的页面 · 全部”不跳转，打开本页抽屉（?panel=favorites / ?panel=pages）。
 */
import { computed, ref, watch, nextTick, onBeforeUnmount } from 'vue';
import { message } from 'ant-design-vue';
import { PlusOutlined, SearchOutlined, EllipsisOutlined } from '@ant-design/icons-vue';
import { QA_TODAY, QA_FAVORITES, QA_MY_PAGES } from '@/mock';
import type { QaSession } from '@/mock/qa';
import { useQaSession, errText } from '../useQaSession';
import { qaFallBackIfDown } from '../qaFallback';
import * as qaApi from '@/api/qa';
import { currentApiContext } from '@/api/http';
import AppPagination from '@/components/AppPagination.vue';
import { useQueryParam, useQueryPage } from '../useQueryParam';
import { useReadonly } from '@/composables/useReadonly';
import { usePreview } from '@/composables/usePreview';
import { useScopeCaliber } from '../qaScope';

const { readonly, reason } = useReadonly();

const emit = defineEmits<{ (e: 'panel', v: 'favorites' | 'pages'): void }>();
const pageSize = ref(10);
const listEl = ref<HTMLElement>();
const { sessions: mockList, activeId: active, newSession, real, live, detail, listTick, favTick, newSessionReal, creating } = useQaSession();
/* 真接口 W1 与本地分页固定每页 10 条。 */
const contextKey = () => { const c = currentApiContext(); return JSON.stringify([c.ontologyId, c.demoAs, c.ver, c.previewAs]); };
const filterKey = () => JSON.stringify([contextKey(), kw.value.trim(), range.value]);
const listKey = () => JSON.stringify([filterKey(), page.value, pageSize.value]);
const srv = ref({ items: [] as QaSession[], total: 0, filter: '', key: '' });
const list = computed<QaSession[]>({ get: () => (live.value ? (srv.value.key === listKey() ? srv.value.items : []) : mockList.value), set: (v) => { mockList.value = v; } });
function requestContext() {
  const c = currentApiContext();
  return { headers: { 'X-Demo-As': c.demoAs ?? '', 'X-Ontology-Id': c.ontologyId ?? '' }, readonlyParams: false, query: { ver: c.ver, previewAs: c.previewAs } };
}
const favTop = ref<string[]>(live.value ? [] : QA_FAVORITES);
const pageTop = ref<string[]>(live.value ? [] : QA_MY_PAGES);
// 仅显式 mock 模式使用原演示快捷入口。
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
  const key = contextKey();
  try {
    const d = await qaApi.patchSession(id, { title: draft.value.trim() }, requestContext());
    if (key !== contextKey() || disposed) return;
    s.title = d.title;
    if (detail.value?.id === id) detail.value.title = d.title;
  } catch (e) { if (key === contextKey() && !disposed) message.error(`重命名失败：${errText(e)}`); }
}
async function doDelete(id: string) {
  delId.value = '';
  if (real) {
    const key = contextKey();
    try { await qaApi.deleteSession(id, requestContext()); } catch (e) { if (key === contextKey() && !disposed) message.error(`删除失败：${errText(e)}`); return; }
    if (key !== contextKey() || disposed) return;
    message.success('会话已删除');
    await load();
    if (key !== contextKey() || disposed) return;
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
const sc = useScopeCaliber();
const pvScope = usePreview().scopeOf;
async function onNew() {
  if (readonly.value) return;
  kw.value = ''; range.value = 'all'; page.value = 1;
  // 新会话沿用当前会话的范围口径；没有就用默认范围 / 第一个口径（后台口径目前只收 不含税/含税，见 handoff）
  if (real) await newSessionReal(detail.value?.scope || sc.defaultScope(pvScope), detail.value?.caliber || (sc.caliberOpts.value[0]?.value ?? ''));
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
const total = computed(() => (live.value ? (srv.value.filter === filterKey() ? srv.value.total : 0) : filtered.value.length));
const pageCount = computed(() => Math.max(1, Math.ceil(total.value / pageSize.value)));
const current = computed(() => Math.min(page.value, pageCount.value));
const rows = computed(() => (live.value ? list.value : filtered.value.slice((current.value - 1) * pageSize.value, current.value * pageSize.value)));
let reqSeq = 0;
let shortcutSeq = 0;
let disposed = false;
async function load() {
  const my = ++reqSeq;
  if (!live.value) return;
  const key = listKey(), filter = filterKey(), selected = active.value;
  const query = { q: kw.value.trim() || undefined, range: range.value, page: page.value, size: pageSize.value };
  const valid = () => !disposed && live.value && my === reqSeq && key === listKey();
  try {
    const r = await qaApi.listSessions(query, requestContext());
    if (!valid()) return;
    srv.value = { items: r.items, total: r.total, filter, key };
    // 删除末页或总数改变时收敛；不以旧页响应覆盖用户随后选中的会话。
    const last = Math.max(1, Math.ceil(r.total / query.size));
    if (query.page > last) { page.value = last; return; }
    if (!selected && active.value === selected && r.items.length) active.value = r.items[0].id;
  } catch (e) { if (valid()) { srv.value = { ...srv.value, items: [], key }; readErr('会话列表')(e); } }
}
async function loadShortcuts() {
  const my = ++shortcutSeq, key = contextKey();
  favTop.value = []; pageTop.value = [];
  if (!live.value) return;
  const options = requestContext();
  const results = await Promise.allSettled([
    qaApi.listFavorites({ page: 1, size: 3 }, options),
    qaApi.listPagesBrief({ page: 1, size: 2 }, options),
  ]);
  if (disposed || my !== shortcutSeq || key !== contextKey() || !live.value) return;
  if (results[0].status === 'fulfilled') favTop.value = results[0].value.items.map((x) => x.question);
  else readErr('常用问题')(results[0].reason);
  if (results[1].status === 'fulfilled') pageTop.value = results[1].value.items.map((x) => x.name);
  else readErr('我的页面')(results[1].reason);
}
if (real) {
  watch([contextKey, live, kw, range, page, pageSize, listTick], load, { immediate: true });
  watch([contextKey, live, favTick], loadShortcuts, { immediate: true });
}
onBeforeUnmount(() => { disposed = true; ++reqSeq; ++shortcutSeq; });
const when = (date: string) => {
  const d = new Date();
  const today = real ? `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}` : QA_TODAY;
  return date === today ? '今天' : date.slice(5);
};
// 改关键词或时间范围后回到第 1 页
watch([kw, range], () => { if (page.value !== 1) page.value = 1; });
</script>

<template>
  <section class="panel qa-sessions">
    <a-tooltip :title="reason"><a-button block :disabled="readonly" :loading="creating" @click="onNew"><template #icon><PlusOutlined /></template>新建会话</a-button></a-tooltip>
    <div class="qa-ss-filter">
      <a-input :value="kw" placeholder="搜索会话" allow-clear @change="(e: Event) => (kw = (e.target as HTMLInputElement).value)">
        <template #prefix><SearchOutlined class="c-3" /></template>
      </a-input>
      <a-select v-model:value="range" :options="RANGES" class="qa-ss-range" />
    </div>
    <div class="qa-ss-head c-3"><span>历史会话</span><span v-if="total <= pageSize" class="t-12">共 {{ total }} 条</span></div>
    <div ref="listEl" class="qa-ss-list">
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
    <div class="qa-ss-pager"><AppPagination v-if="total > pageSize" v-model:page="pageModel" variant="simple" :total="total" :size="pageSize" :empty="false" /></div>
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
.qa-ss-list { flex: 1; min-height: 0; overflow: hidden; }
.qa-sessions > :not(.qa-ss-list) { flex-shrink: 0; }
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
.qa-ss-pager { padding: 0 8px; height: 40px; }
.qa-ss-group { display: flex; justify-content: space-between; padding: 8px 8px 4px; }
.qa-ss-bottom { flex: none; border-top: var(--border); padding-top: 4px; }
</style>
