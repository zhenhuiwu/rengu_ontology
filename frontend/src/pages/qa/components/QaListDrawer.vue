<script setup lang="ts">
/** 常用问题本页抽屉（?panel=favorites）。每页 10 条 simple 分页，移出走 W15。 */
import { ref, computed, watch, onBeforeUnmount } from 'vue';
import { message } from 'ant-design-vue';
import { SearchOutlined } from '@ant-design/icons-vue';
import AppPagination from '@/components/AppPagination.vue';
import { paginate } from '@/mock/paginate';
import { QA_FAVORITE_LIST } from '@/mock';
import * as qaApi from '@/api/qa';
import { currentApiContext } from '@/api/http';
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
const title = '常用问题';
const rows = computed(() => {
  const k = kw.value.trim();
  return favs.value.filter((f) => !k || f.question.includes(k)).map((f) => ({ id: f.id, name: f.question, c1: `用过 ${f.uses} 次`, c2: `最近 ${f.lastUsed}` }));
});
const paged = computed<{ items: Row[]; total: number }>(() => (live.value ? srv.value : paginate<Row>(rows.value, { page: page.value, size: SIZE })));
watch(() => props.panel, () => { page.value = 1; kw.value = ''; });
watch(kw, () => { page.value = 1; });
let reqSeq = 0;
const contextKey = () => JSON.stringify(currentApiContext());
onBeforeUnmount(() => { ++reqSeq; });
async function load() {
  const my = ++reqSeq, key = contextKey(), panel = props.panel;
  if (!live.value || panel !== 'favorites') return;
  const valid = () => my === reqSeq && key === contextKey() && panel === props.panel;
  const k = kw.value.trim() || undefined;
  try {
    const items: Row[] = [];
    let total = 0, actualPage = page.value;
    const options = qaApi.qaRequestOptions();
    const r = await qaApi.listFavorites({ q: k, page: page.value, size: SIZE }, options);
    r.items.forEach((f) => items.push({ id: f.id, name: f.question, c1: `用过 ${f.uses} 次`, c2: `最近 ${f.lastUsed}` }));
    total = r.total; actualPage = r.page;
    if (!valid()) return;
    srv.value = { items, total };
    if (actualPage !== page.value) page.value = actualPage;
  } catch (e) { if (valid() && !qaFallBackIfDown(e)) message.error(`${title}加载失败：${errText(e)}`); }
}
if (real) {
  watch(contextKey, () => { ++reqSeq; srv.value = { items: [], total: 0 }; }, { flush: 'sync' });
  watch([() => props.panel, kw, page, contextKey], load, { immediate: true });
}
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
  <a-drawer :open="panel === 'favorites'" :title="title" :width="480" @close="emit('close')">
    <div class="qa-ld">
      <a-input v-model:value="kw" placeholder="搜索常用问题" allow-clear>
        <template #prefix><SearchOutlined class="c-3" /></template>
      </a-input>
      <div class="qa-ld-list">
        <div v-for="r in paged.items" :key="r.id" class="qa-ld-item">
          <span class="qa-ld-name" :title="r.name">{{ r.name }}</span>
          <a-tag v-if="'tag' in r && r.tag" class="qa-ld-tag">{{ r.tag }}</a-tag>
          <span class="qa-ld-c1 num">{{ r.c1 }}</span>
          <span class="qa-ld-c2 num">{{ r.c2 }}</span>
          <a-space :size="12">
            <a @click="emit('ask', r.name)">提问</a>
            <a-popconfirm title="移出常用问题？" ok-text="移出" cancel-text="取消" @confirm="remove(r.id)"><a class="c-3">移出</a></a-popconfirm>
          </a-space>
        </div>
      </div>
      <AppPagination v-model:page="page" :size="SIZE" variant="simple" unit="个问题" :total="paged.total" empty-text="没有匹配的常用问题" />
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
