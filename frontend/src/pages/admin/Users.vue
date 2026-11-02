<script setup lang="ts">
import { ref, watch } from 'vue';
import { adminApiOn, queryUsers, type UserRow } from '@/api/admin';
import { currentApiContext, isApiError } from '@/api/http';

const account = ref<UserRow | null>(null);
const loading = ref(false);
const error = ref('');
let request = 0;

async function reload() {
  const sequence = ++request;
  const context = JSON.stringify(currentApiContext());
  account.value = null;
  error.value = '';
  if (!adminApiOn()) {
    error.value = '账号信息需要连接平台服务';
    return;
  }
  loading.value = true;
  try {
    const result = await queryUsers({ dept: '全部', status: '启用', kw: 'U010001', page: 1, size: 10 });
    if (sequence !== request || context !== JSON.stringify(currentApiContext())) return;
    account.value = result?.items.find((user) => user.id === 'U010001' && user.account === 'admin') ?? null;
    if (!account.value) error.value = '未找到启用的 admin 账号';
  } catch (cause) {
    if (sequence === request && context === JSON.stringify(currentApiContext())) {
      error.value = isApiError(cause) ? cause.message : '账号信息加载失败';
    }
  } finally {
    if (sequence === request) loading.value = false;
  }
}

watch(() => JSON.stringify(currentApiContext()), reload, { immediate: true });
</script>

<template>
  <section class="panel account-panel">
    <div class="panel-head account-toolbar">
      <span class="t-16 fw-600">账号信息</span>
      <a-button :loading="loading" @click="reload">刷新</a-button>
    </div>
    <div class="account-body">
      <a-alert v-if="error" type="error" show-icon :message="error" />
      <a-spin :spinning="loading">
        <template v-if="account">
          <div class="account-heading">
            <h2 class="t-20 fw-600 c-1">{{ account.name }}</h2>
            <a-tag color="green">{{ account.status }}</a-tag>
          </div>
          <dl class="account-fields">
            <div><dt class="t-12 c-3">用户名</dt><dd class="t-14 c-1">{{ account.account }}</dd></div>
            <div><dt class="t-12 c-3">用户编号</dt><dd class="t-14 c-1 num">{{ account.id }}</dd></div>
            <div><dt class="t-12 c-3">所属组织</dt><dd class="t-14 c-1">{{ account.dept || '—' }}</dd></div>
            <div><dt class="t-12 c-3">最近登录</dt><dd class="t-14 c-1 num">{{ account.lastLogin || '—' }}</dd></div>
            <div><dt class="t-12 c-3">创建日期</dt><dd class="t-14 c-1 num">{{ account.createdAt || '—' }}</dd></div>
          </dl>
        </template>
        <a-empty v-else-if="!loading && !error" description="暂无账号信息" />
      </a-spin>
    </div>
  </section>
</template>

<style scoped>
.account-panel { flex: none; width: 100%; max-width: 1120px; min-width: 0; min-height: 0; overflow: auto; }
.account-toolbar { height: auto; min-height: 64px; padding: 12px 24px; gap: 16px; }
.account-body { padding: 24px; }
.account-heading { display: flex; align-items: center; gap: 12px; margin-bottom: 24px; }
.account-heading h2 { margin: 0; }
.account-heading :deep(.ant-tag) { margin: 0; }
.account-fields { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); column-gap: 48px; margin: 0; }
.account-fields > div { min-width: 0; padding: 16px 0; border-top: var(--border); }
.account-fields dt { margin-bottom: 8px; }
.account-fields dd { margin: 0; overflow-wrap: anywhere; }
.account-body :deep(.ant-alert) { margin-bottom: 16px; }
@media (max-width: 640px) {
  .account-toolbar { padding: 12px 16px; }
  .account-body { padding: 16px; }
  .account-fields { grid-template-columns: minmax(0, 1fr); }
}
</style>
