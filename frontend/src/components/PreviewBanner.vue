<script setup lang="ts">
/** “以此用户预览”横幅（AppLayout 自动显示）。高度 var(--banner-h)，与历史版本横幅同一位置和样式。 */
import { useRouter } from 'vue-router';
import { message } from 'ant-design-vue';
import { EyeOutlined } from '@ant-design/icons-vue';
import { usePreviewStore } from '@/stores/preview';
const pv = usePreviewStore();
const router = useRouter();
function exit() {
  const name = pv.session?.user.name;
  router.push(pv.exit());
  message.success(`已退出预览：${name}`);
}
</script>

<template>
  <div v-if="pv.session" class="pvb t-12" role="status">
    <EyeOutlined class="c-warning" />
    <a-tooltip :title="`角色：${pv.session.roles.join('、') || '无'} · ${pv.session.startedAt} 开始 · 已记入审计日志`">
      <span class="c-1">正在以 {{ pv.session.user.name }}（{{ pv.session.roles.join('、') || '无角色' }}）的权限预览 · 只读 ·</span>
    </a-tooltip>
    <a class="pvb-exit" @click="exit">退出预览</a>
  </div>
</template>

<style scoped>
.pvb { height: var(--banner-h); flex: none; display: flex; align-items: center; justify-content: center; gap: 6px; background: var(--warning-bg); border-bottom: var(--border); }
.pvb-exit { color: var(--brand); }
</style>
