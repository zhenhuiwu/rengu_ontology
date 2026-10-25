<script setup lang="ts">
/** 左侧两级导航：顶部切换使用/构建/管理三区，下面是该区的一级模块（展开）和二级页面。数据来自 src/nav.ts */
import { computed, h } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import * as Icons from '@ant-design/icons-vue';
import { NAV, PAGES, type NavArea } from '@/nav';
import { useAppStore } from '@/stores/app';

const route = useRoute();
const router = useRouter();
const app = useAppStore();

const currentPage = computed(() => PAGES.find((p) => p.key === route.meta.navKey));
const areaKey = computed<NavArea['key']>(() => currentPage.value?.area.key ?? (route.meta.area as NavArea['key']) ?? 'use');
const area = computed(() => NAV.find((a) => a.key === areaKey.value)!);

const items = computed(() =>
  area.value.modules.map((m) => {
    const groups = [...new Set(m.pages.map((p) => p.group).filter(Boolean))] as string[];
    const toItem = (p: (typeof m.pages)[number]) => ({ key: p.key, label: p.title });
    return {
      key: m.key,
      label: m.title,
      icon: () => h((Icons as unknown as Record<string, object>)[m.icon] as never),
      children: groups.length
        ? groups.map((g) => ({ type: 'group', key: `${m.key}-${g}`, label: g, children: m.pages.filter((p) => p.group === g).map(toItem) }))
        : m.pages.map(toItem),
    };
  }),
);
const openKeys = computed(() => area.value.modules.map((m) => m.key));
const selectedKeys = computed(() => (currentPage.value ? [currentPage.value.key] : []));

/** 只显示当前用户有权限的区；只剩一个区时整个切换条隐藏 */
const areaOptions = computed(() => NAV.filter((a) => app.can(a.key)).map((a) => ({ label: a.title, value: a.key })));
/** 切换区：回到该区上次访问的路由（Pinia + localStorage），没有则进该区第一页 */
function switchArea(k: string | number) {
  const a = NAV.find((x) => x.key === k)!;
  router.push(app.lastRoute[a.key] ?? a.modules[0].pages[0].path);
}
</script>

<template>
  <aside class="sn">
    <div v-if="areaOptions.length > 1" class="sn-area">
      <a-segmented :value="areaKey" :options="areaOptions" block @change="switchArea" />
    </div>
    <a-menu
      class="sn-menu" mode="inline" :items="items"
      :open-keys="openKeys" :selected-keys="selectedKeys" :inline-indent="16"
      @click="({ key }: { key: string }) => router.push({ name: key })"
    />
  </aside>
</template>

<style scoped>
.sn { width: var(--sider-w); flex: none; display: flex; flex-direction: column; background: var(--g1); border-right: var(--border); }
.sn-area { padding: 12px 12px 8px; flex: none; }
.sn-menu { border-inline-end: 0 !important; flex: 1; min-height: 0; }
.sn-menu:first-child { padding-top: 8px; }
</style>
