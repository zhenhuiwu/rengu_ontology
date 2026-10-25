<script setup lang="ts">
/** 分组页签：组标题 + 页签平铺在一行（不折叠，常用页签一次点击可达）。v-model 绑定当前页签 key。 */
defineProps<{ groups: { key: string; title: string; tabs: { key: string; title: string }[] }[] }>();
const model = defineModel<string>({ required: true });
</script>

<template>
  <div class="gt">
    <div v-for="g in groups" :key="g.key" class="gt-group">
      <span class="t-12 c-3 gt-label">{{ g.title }}</span>
      <button
        v-for="t in g.tabs" :key="t.key" type="button"
        class="t-14 gt-tab" :class="{ active: model === t.key }"
        @click="model = t.key"
      >{{ t.title }}</button>
    </div>
  </div>
</template>

<style scoped>
.gt { display: flex; align-items: stretch; height: 44px; border-bottom: var(--border); padding: 0 16px; flex: none; }
.gt-group { display: flex; align-items: stretch; }
.gt-group + .gt-group { margin-left: 16px; padding-left: 16px; border-left: var(--border); }
.gt-label { align-self: center; margin-right: 8px; }
.gt-tab { background: none; border: 0; border-bottom: 2px solid transparent; margin: 0 8px; padding: 0 4px; color: var(--g9); cursor: pointer; font-family: inherit; }
.gt-tab:hover { color: var(--brand); }
.gt-tab.active { color: var(--brand); border-bottom-color: var(--brand); }
</style>
