<script setup lang="ts">
/** 顶栏通知列表（铃铛弹出层内容）。点一条：标为已读，在当前页打开对应对象的详情抽屉，不跳转页面。 */
import { computed, ref } from 'vue';
import { useAppStore } from '@/stores/app';
import { useDrawer } from '@/composables/useDrawer';
import AppPagination from './AppPagination.vue';
import { paginate } from '@/mock/paginate';

const emit = defineEmits<{ picked: [] }>();
const app = useAppStore();
const { open } = useDrawer();
const SIZE = 5;
const page = ref(1);
const paged = computed(() => paginate(app.notices, { page: page.value, size: SIZE }));
function pick(n: (typeof app.notices)[number]) {
  n.read = true;
  open(n.kind, n.objId, { level: 1 });
  emit('picked');
}
</script>

<template>
  <div class="nl">
    <div class="nl-head">
      <span class="fw-600">通知</span>
      <a v-if="app.unread" class="t-12" @click="app.notices.forEach((n) => (n.read = true))">全部标为已读</a>
    </div>
    <ul class="nl-list">
      <li v-for="n in paged.items" :key="n.id" class="nl-item" @click="pick(n)">
        <span class="nl-dot" :class="{ on: !n.read }" />
        <div class="nl-body">
          <div class="nl-row" :title="n.desc ? `${n.title}\n${n.desc}` : n.title"><span class="nl-ell" :class="n.read ? 'c-2' : 'c-1'">{{ n.title }}</span><span class="t-12 c-2 num nl-time">{{ n.time }}</span></div>
        </div>
      </li>
    </ul>
    <AppPagination v-model:page="page" variant="simple" :total="app.notices.length" :size="SIZE" class="nl-pager" empty-text="暂无通知" />
  </div>
</template>

<style scoped>
.nl { width: 340px; }
.nl-head { display: flex; justify-content: space-between; align-items: center; padding-bottom: 8px; border-bottom: var(--border); }
.nl-list { list-style: none; margin: 0; padding: 0; }
.nl-item { display: flex; gap: 8px; padding: 8px 4px; border-bottom: var(--border); cursor: pointer; }
.nl-item:hover { background: var(--g3); }
.nl-dot { width: 6px; height: 6px; border-radius: 50%; margin-top: 8px; flex: none; }
.nl-dot.on { background: var(--brand); }
.nl-body { flex: 1; min-width: 0; }
.nl-row { display: flex; justify-content: space-between; gap: 8px; }
.nl-ell { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; min-width: 0; }
.nl-time { flex: none; }
.nl-pager { padding-top: 8px; }
</style>
