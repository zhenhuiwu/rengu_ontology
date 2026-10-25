<script setup lang="ts">
/** 左下“常用问题 · 管理”“我的页面 · 全部”的本页抽屉（不跳转）。每页 10 条 simple 分页；状态 ?panel=favorites|pages
 * 真接口（开关 qa）：常用问题 W13（q）、我的页面 P1 view=brief（kw）按页取，移出走 W15 */
import { ref, computed, watch } from 'vue';
import { message } from 'ant-design-vue';
import { SearchOutlined } from '@ant-design/icons-vue';
import AppPagination from '@/components/AppPagination.vue';
import { paginate } from '@/mock/paginate';
import { QA_FAVORITE_LIST, QA_PAGE_LIST } from '@/mock';
import * as qaApi from '@/api/qa';
import { qaLive as live, qaFallBackIfDown } from '@/pages/qa/qaFallback';
import { errText, useQaSession } from '../useQaSession';

type Row = { id: string; name: string; c1: string; c2: string; tag?: string };
const real = qaApi.qaReal();
const srv = ref<{ items: Row[]; total: number }>({ items: [], total: 0 });

const props = defineProps<{ panel: string }>();
const emit = defineEmits<{ (e: 'close'): void; (e: 'ask', q: string): void }>();
const SIZE = 10;
const page = ref(1);
const kw = ref('');
const favs = ref([...QA_FAVORITE_LIST]);
const isFav = computed(() => props.panel === 'favorites');
const title = computed(() => (isFav.value ? '常用问题' : '我的页面'));
const rows = computed(() => {
  const k = kw.value.trim();
  return isFav.value
    ? favs.value.filter((f) => !k || f.question.includes(k)).map((f) => ({ id: f.id, name: f.question, c1: `用过 ${f.uses} 次`, c2: `最近 ${f.lastUsed}` }))
    : QA_PAGE_LIST.filter((p) => !k || p.name.includes(k)).map((p) => ({ id: p.id, name: p.name, c1: `${p.cards} 张卡片`, c2: `${p.updatedAt} 更新`, tag: p.shared ? '已共享' : '' }));
});
const paged = computed<{ items: Row[]; total: number }>(() => (live.value ? srv.value : paginate<Row>(rows.value, { page: page.value, size: SIZE })));
watch(() => props.panel, () => { page.value = 1; kw.value = ''; });
watch(kw, () => { page.value = 1; });
let reqSeq = 0;
async function load() {
  if (!live.value || (props.panel !== 'favorites' && props.panel !== 'pages')) return;
  const my = ++reqSeq;
  const k = kw.value.trim() || undefined;
  try {
    const items: Row[] = [];
    let total = 0;
    if (isFav.value) {
      const r = await qaApi.listFavorites({ q: k, page: page.value, size: SIZE });
      r.items.forEach((f) => items.push({ id: f.id, name: f.question, c1: `用过 ${f.uses} 次`, c2: `最近 ${f.lastUsed}` }));
      total = r.total;
    } else {
      const r = await qaApi.listPagesBrief({ kw: k, page: page.value, size: SIZE });
      r.items.forEach((p) => items.push({ id: p.id, name: p.name, c1: `${p.cards} 张卡片`, c2: `${p.updatedAt} 更新`, tag: p.shared ? '已共享' : '' }));
      total = r.total;
    }
    if (my !== reqSeq) return;
    srv.value = { items, total };
    if (!items.length && total && page.value > 1) page.value = Math.ceil(total / SIZE);
  } catch (e) { if (my === reqSeq && !qaFallBackIfDown(e)) message.error(`${title.value}加载失败：${errText(e)}`); }
}
if (real) watch([() => props.panel, kw, page], load, { immediate: true });
const { favTick } = useQaSession();
async function remove(id: string) {
  if (real) {
    try { await qaApi.deleteFavorite(id); } catch (e) { message.error(`移出失败：${errText(e)}`); return; }
    message.success('已移出常用问题');
    favTick.value++;
    await load();
    return;
  }
  favs.value = favs.value.filter((f) => f.id !== id); message.success('已移出常用问题');
}
</script>

<template>
  <a-drawer :open="panel === 'favorites' || panel === 'pages'" :title="title" :width="480" @close="emit('close')">
    <div class="qa-ld">
      <a-input v-model:value="kw" :placeholder="isFav ? '搜索常用问题' : '搜索页面'" allow-clear>
        <template #prefix><SearchOutlined class="c-3" /></template>
      </a-input>
      <div class="qa-ld-list">
        <div v-for="r in paged.items" :key="r.id" class="qa-ld-item">
          <span class="qa-ld-name" :title="r.name">{{ r.name }}</span>
          <a-tag v-if="'tag' in r && r.tag" class="qa-ld-tag">{{ r.tag }}</a-tag>
          <span class="qa-ld-c1 num">{{ r.c1 }}</span>
          <span class="qa-ld-c2 num">{{ r.c2 }}</span>
          <a-space v-if="isFav" :size="12">
            <a @click="emit('ask', r.name)">提问</a>
            <a-popconfirm title="移出常用问题？" ok-text="移出" cancel-text="取消" @confirm="remove(r.id)"><a class="c-3">移出</a></a-popconfirm>
          </a-space>
          <a v-else>打开</a>
        </div>
      </div>
      <AppPagination v-model:page="page" :size="SIZE" variant="simple" :unit="isFav ? '个问题' : '个页面'" :total="paged.total" :empty-text="isFav ? '没有匹配的常用问题' : '没有匹配的页面'" />
    </div>
  </a-drawer>
</template>

<style scoped>
.qa-ld { display: flex; flex-direction: column; gap: 8px; height: 100%; }
.qa-ld-list { flex: 1; min-height: 0; }
.qa-ld-item { display: flex; align-items: center; gap: 12px; padding: 8px 0; border-bottom: var(--border); }
.qa-ld-name { flex: 1; min-width: 0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.qa-ld-tag { margin-inline-end: 0; flex: none; }
.qa-ld-c1 { width: 72px; flex: none; white-space: nowrap; }
.qa-ld-c2 { width: 84px; flex: none; white-space: nowrap; }
</style>
