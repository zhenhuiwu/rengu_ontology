<script setup lang="ts">
/** 页面头：面包屑 + 页面标题（20/600）+ 右侧操作区（slot #extra）。由 AppLayout 统一渲染，页面一般不需要直接用。 */
defineProps<{ crumbs: { title: string; to?: string }[]; title: string }>();
</script>

<template>
  <div class="ph">
    <div class="ph-left">
      <a-breadcrumb v-if="crumbs.length" class="ph-crumb">
        <a-breadcrumb-item v-for="c in crumbs" :key="c.title">
          <router-link v-if="c.to" :to="c.to">{{ c.title }}</router-link>
          <span v-else>{{ c.title }}</span>
        </a-breadcrumb-item>
      </a-breadcrumb>
      <h1 class="t-20 fw-600 c-1 ph-title" :class="{ 'ph-title-solo': !crumbs.length }">{{ title }}</h1>
    </div>
    <div class="ph-extra"><slot name="extra" /></div>
  </div>
</template>

<style scoped>
.ph { flex: none; display: flex; align-items: flex-end; justify-content: space-between; padding: 12px 24px; background: var(--g1); border-bottom: var(--border); }
.ph-title { margin: 4px 0 0; }
.ph-title-solo { margin: 0; }
.ph-extra { display: flex; gap: 8px; align-items: center; }
</style>
