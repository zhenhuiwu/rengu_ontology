<script setup lang="ts">
import { computed } from 'vue';
import zhCN from 'ant-design-vue/es/locale/zh_CN';
import { chrome, lightTheme, navyTheme } from '@/theme';
import { useAuthStore } from '@/stores/auth';
import { useRoute } from 'vue-router';

const auth = useAuthStore();
const route = useRoute();
const activeTheme = computed(() => (chrome.value === 'navy' ? navyTheme : lightTheme));
</script>

<template>
  <a-config-provider :theme="activeTheme" :locale="zhCN" :auto-insert-space-in-button="false">
    <a-app class="app-root"><router-view v-if="route.name === 'login' || auth.authenticated" /></a-app>
  </a-config-provider>
</template>

<style>
.app-root { height: 100%; }
</style>
