<script setup lang="ts">
import { computed, watch } from 'vue';
import { useRoute } from 'vue-router';
import { theme } from 'ant-design-vue';
import zhCN from 'ant-design-vue/es/locale/zh_CN';

const route = useRoute();
const navy = computed(() => route.query.theme === 'navy' || route.query.theme === 'dark');
watch(navy, (value) => {
  if (value) document.documentElement.dataset.chrome = 'navy';
  else delete document.documentElement.dataset.chrome;
}, { immediate: true });
const activeTheme = computed(() => ({
  algorithm: navy.value ? theme.darkAlgorithm : theme.defaultAlgorithm,
  token: { colorPrimary: navy.value ? '#3B82F6' : '#1f5fd1', borderRadius: 4, fontSize: 14,
    colorBgContainer: navy.value ? '#212D4B' : '#ffffff', colorBgElevated: navy.value ? '#2C3B5C' : '#ffffff',
    fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif' },
}));
</script>

<template>
  <a-config-provider :theme="activeTheme" :locale="zhCN" :auto-insert-space-in-button="false">
    <a-app class="agent-app"><router-view /></a-app>
  </a-config-provider>
</template>

<style>
.agent-app { height: 100%; min-width: 0; }
body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif; }
button, textarea { font: inherit; }
</style>
