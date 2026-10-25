<script setup lang="ts">
import zhCN from 'ant-design-vue/es/locale/zh_CN';
import { theme } from '@/theme';
</script>

<template>
  <a-config-provider :theme="theme" :locale="zhCN" :auto-insert-space-in-button="false">
    <a-app class="app-root"><router-view /></a-app>
  </a-config-provider>
</template>

<style>
.app-root { height: 100%; }
</style>
