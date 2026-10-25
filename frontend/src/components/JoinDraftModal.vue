<script setup lang="ts">
/** 加入草稿：选已有草稿或新建。全站一个（AppLayout），用 `await useAppStore().askJoinDraft()` 打开。 */
import { ref, watch } from 'vue';
import { useAppStore } from '@/stores/app';
import { DRAFTS } from '@/mock';

const app = useAppStore();
const mode = ref<'join' | 'new'>('join');
const pick = ref(DRAFTS[0].id);
const title = ref('');
watch(() => app.joinDraft.open, (o) => { if (o) { mode.value = 'join'; pick.value = app.activeDraft ?? DRAFTS[0].id; title.value = ''; } });

function finish(id: string | null) {
  if (id) app.setDraft(id);
  app.joinDraft.resolve?.(id);
  app.joinDraft = { open: false, resolve: null };
}
const newId = () => `CR-${String(Math.max(...DRAFTS.map((d) => +d.id.slice(3))) + 1).padStart(4, '0')}`;
function ok() {
  if (mode.value === 'join') return finish(pick.value);
  if (!title.value.trim()) return;
  DRAFTS.unshift({ id: newId(), title: title.value.trim(), owner: app.user.name, updatedAt: '刚刚', changes: 0 });
  finish(DRAFTS[0].id);
}
</script>

<template>
  <a-modal :open="app.joinDraft.open" title="加入草稿" :width="520" ok-text="确定" cancel-text="取消"
    :ok-button-props="{ disabled: mode === 'new' && !title.trim() }" @ok="ok" @cancel="finish(null)">
    <p class="c-2 jd-tip">已发布版本不能直接修改。加入一个草稿，修改会记在草稿里，审批后随草稿发布。</p>
    <a-radio-group v-model:value="mode" class="jd-mode">
      <a-radio-button value="join">加入已有草稿</a-radio-button>
      <a-radio-button value="new">新建草稿</a-radio-button>
    </a-radio-group>
    <a-radio-group v-if="mode === 'join'" v-model:value="pick" class="jd-list">
      <a-radio v-for="d in DRAFTS" :key="d.id" :value="d.id" class="jd-item">
        <span class="num fw-600">{{ d.id }}</span> {{ d.title }}
        <div class="t-12 c-3">{{ d.owner }} · {{ d.changes }} 处修改 · {{ d.updatedAt }}</div>
      </a-radio>
    </a-radio-group>
    <a-form v-else layout="vertical">
      <a-form-item label="草稿标题" required>
        <a-input v-model:value="title" placeholder="例如：门店新增“业态”属性" :maxlength="40" />
      </a-form-item>
      <div class="t-12 c-3">草稿号自动生成：{{ newId() }}</div>
    </a-form>
  </a-modal>
</template>

<style scoped>
.jd-tip { margin: 0 0 12px; }
.jd-mode { margin-bottom: 12px; }
.jd-list { display: flex; flex-direction: column; gap: 8px; width: 100%; }
.jd-item { align-items: flex-start; }
</style>
