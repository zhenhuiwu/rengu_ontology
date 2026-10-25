<script setup lang="ts">
/** 左下“常用问题 · 管理”“我的页面 · 全部”的本页抽屉（不跳转）。每页 10 条 simple 分页；状态 ?panel=favorites|pages */
import { ref, computed, watch } from 'vue';
import { message } from 'ant-design-vue';
import { SearchOutlined } from '@ant-design/icons-vue';
import AppPagination from '@/components/AppPagination.vue';
import { paginate } from '@/mock/paginate';
import { QA_FAVORITE_LIST, QA_PAGE_LIST } from '@/mock';

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
    ? favs.value.filter((f) => !k || f.question.includes(k)).map((f) => ({ id: f.id, name: f.question, meta: `用过 ${f.uses} 次 · 最近 ${f.lastUsed}` }))
    : QA_PAGE_LIST.filter((p) => !k || p.name.includes(k)).map((p) => ({ id: p.id, name: p.name, meta: `${p.cards} 张卡片 · ${p.updatedAt} 更新${p.shared ? ' · 已共享' : ''}` }));
});
const paged = computed(() => paginate(rows.value, { page: page.value, size: SIZE }));
watch(() => props.panel, () => { page.value = 1; kw.value = ''; });
watch(kw, () => { page.value = 1; });
function remove(id: string) { favs.value = favs.value.filter((f) => f.id !== id); message.success('已移出常用问题'); }
</script>

<template>
  <a-drawer :open="panel === 'favorites' || panel === 'pages'" :title="title" :width="480" @close="emit('close')">
    <div class="qa-ld">
      <a-input v-model:value="kw" :placeholder="isFav ? '搜索常用问题' : '搜索页面'" allow-clear>
        <template #prefix><SearchOutlined class="c-3" /></template>
      </a-input>
      <div class="qa-ld-list">
        <div v-for="r in paged.items" :key="r.id" class="qa-ld-item">
          <div class="qa-ld-main">
            <div class="qa-ld-name">{{ r.name }}</div>
            <div class="t-12 c-3">{{ r.meta }}</div>
          </div>
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
.qa-ld-main { flex: 1; min-width: 0; }
.qa-ld-name { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
</style>
