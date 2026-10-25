<script setup lang="ts">
/**
 * 公共布局：顶栏 + 左侧两级导航 + 面包屑/页面标题 + 页面主体。
 * 整页 100vh，不出现页面滚动条；页面主体 .page-body 本身不滚动，页面内最多一个 .scroll-area。
 * 注意：布局里不用 overflow:hidden 兜底，超出一屏会真实撑出滚动条，验收脚本才能发现。
 * DetailDrawer / ConfirmActionModal 全站各挂一个。
 */
import { computed } from 'vue';
import { useRoute } from 'vue-router';
import TopBar from './TopBar.vue';
import SideNav from './SideNav.vue';
import PageHeader from '@/components/PageHeader.vue';
import DetailDrawer from '@/components/DetailDrawer.vue';
import ConfirmActionModal from '@/components/ConfirmActionModal.vue';
import { PAGES } from '@/nav';
import VersionBanner from '@/components/VersionBanner.vue';
import JoinDraftModal from '@/components/JoinDraftModal.vue';

const route = useRoute();
const page = computed(() => PAGES.find((p) => p.key === route.meta.navKey));
const title = computed(() => (route.meta.title as string) ?? page.value?.title ?? '');
const crumbs = computed(() => {
  if (route.meta.crumbs) return route.meta.crumbs as { title: string; to?: string }[];
  if (!page.value) return [{ title: '首页' }];
  return [{ title: page.value.area.title }, { title: page.value.module.title }, { title: page.value.title }];
});
</script>

<template>
  <div class="app">
    <TopBar />
    <VersionBanner />
    <div class="app-main">
      <SideNav />
      <main class="app-content">
        <PageHeader :crumbs="crumbs" :title="title">
          <template #extra><div id="page-extra" class="page-extra" /></template>
        </PageHeader>
        <div class="page-body">
          <router-view />
        </div>
      </main>
    </div>
    <DetailDrawer />
    <ConfirmActionModal />
    <JoinDraftModal />
  </div>
</template>

<style scoped>
.app { height: 100vh; display: flex; flex-direction: column; }
.app-main { flex: 1; min-height: 0; display: flex; }
.app-content { flex: 1; min-width: 0; display: flex; flex-direction: column; }
.page-extra { display: flex; gap: 8px; }
.page-body { flex: 1; min-height: 0; padding: var(--gap); display: flex; flex-direction: column; }
</style>
