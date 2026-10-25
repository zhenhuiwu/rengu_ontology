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
import { QA_SESSION_LIST, QA_TODAY, QA_FAVORITES, QA_MY_PAGES } from '@/mock';
import { useQueryParam, useQueryPage } from '../useQueryParam';

const emit = defineEmits<{ (e: 'panel', v: 'favorites' | 'pages'): void }>();
const PAGE_SIZE = 12;
const list = ref([...QA_SESSION_LIST]);
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
function saveRename() {
  const s = list.value.find((x) => x.id === editing.value);
  if (s && draft.value.trim()) s.title = draft.value.trim();
  editing.value = '';
}
function doDelete(id: string) {
  list.value = list.value.filter((x) => x.id !== id);
  delId.value = '';
  message.success('会话已删除');
}
const active = QA_SESSION_LIST[0].id;
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
const pageCount = computed(() => Math.max(1, Math.ceil(filtered.value.length / PAGE_SIZE)));
const current = computed(() => Math.min(page.value, pageCount.value));
const rows = computed(() => filtered.value.slice((current.value - 1) * PAGE_SIZE, current.value * PAGE_SIZE));
const when = (date: string) => (date === QA_TODAY ? '今天' : date.slice(5));
// 改关键词或时间范围后回到第 1 页
watch([kw, range], () => { if (page.value !== 1) page.value = 1; });
</script>

<template>
  <section class="panel qa-sessions">
    <a-button type="primary" block><template #icon><PlusOutlined /></template>新建会话</a-button>
    <div class="qa-ss-filter">
      <a-input :value="kw" placeholder="搜索会话" allow-clear @change="(e: Event) => (kw = (e.target as HTMLInputElement).value)">
        <template #prefix><SearchOutlined class="c-3" /></template>
      </a-input>
      <a-select v-model:value="range" :options="RANGES" class="qa-ss-range" />
    </div>
    <div class="qa-ss-head c-3"><span>历史会话</span><span class="t-12">共 {{ filtered.length }} 条</span></div>
    <div class="qa-ss-list">
      <div v-for="s in rows" :key="s.id" class="qa-ss-item"
        :class="{ on: s.id === active, hold: menuId === s.id || delId === s.id, edit: editing === s.id }" :title="s.title">
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
    <a-pagination v-if="filtered.length > PAGE_SIZE" simple size="small" class="qa-ss-pager"
      :current="current" :page-size="PAGE_SIZE" :total="filtered.length" @change="(p: number) => (page = p)" />
    <div class="qa-ss-bottom">
      <div class="qa-ss-group c-3">常用问题<a @click="emit('panel', 'favorites')">管理</a></div>
      <div v-for="s in QA_FAVORITES" :key="s" class="qa-ss-item"><span class="qa-ss-title">{{ s }}</span></div>
      <div class="qa-ss-group c-3">我的页面<a @click="emit('panel', 'pages')">全部</a></div>
      <div v-for="s in QA_MY_PAGES" :key="s" class="qa-ss-item"><span class="qa-ss-title">{{ s }}</span></div>
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
.qa-ss-pager { display: flex; justify-content: center; padding: 8px 0 4px; }
.qa-ss-group { display: flex; justify-content: space-between; padding: 8px 8px 4px; }
.qa-ss-bottom { margin-top: auto; border-top: var(--border); padding-top: 4px; }
</style>
