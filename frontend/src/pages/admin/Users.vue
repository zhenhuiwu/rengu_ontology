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
    <div class="panel-head">
      <span class="t-16 fw-600">账号信息</span>
      <a-button size="small" :loading="loading" @click="reload">刷新</a-button>
    </div>
    <div class="account-body">
      <a-alert v-if="error" type="error" show-icon :message="error" />
      <a-spin :spinning="loading">
        <template v-if="account">
          <div class="account-heading">
            <div><h2>{{ account.name }}</h2><p>{{ account.dept || '—' }}</p></div>
            <a-tag color="green">{{ account.status }}</a-tag>
          </div>
          <a-descriptions bordered :column="{ xs: 1, sm: 1, md: 2 }" size="middle">
            <a-descriptions-item label="用户名">{{ account.account }}</a-descriptions-item>
            <a-descriptions-item label="用户编号"><span class="num">{{ account.id }}</span></a-descriptions-item>
            <a-descriptions-item label="所属组织">{{ account.dept || '—' }}</a-descriptions-item>
            <a-descriptions-item label="最近登录"><span class="num">{{ account.lastLogin || '—' }}</span></a-descriptions-item>
            <a-descriptions-item label="创建日期"><span class="num">{{ account.createdAt || '—' }}</span></a-descriptions-item>
          </a-descriptions>
        </template>
        <a-empty v-else-if="!loading && !error" description="暂无账号信息" />
      </a-spin>
    </div>
  </section>
</template>

<style scoped>
.account-panel { flex: 1; min-width: 0; min-height: 0; overflow: auto; }
.account-body { padding: 24px; }
.account-heading { display: flex; align-items: center; justify-content: space-between; gap: 16px; margin-bottom: 24px; }
.account-heading h2 { margin: 0 0 4px; font-size: 22px; font-weight: 600; }
.account-heading p { margin: 0; color: var(--text-secondary, #666); }
.account-body :deep(.ant-descriptions-item-content) { overflow-wrap: anywhere; }
@media (max-width: 640px) { .account-body { padding: 16px; } }
</style>
