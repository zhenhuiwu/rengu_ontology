<script setup lang="ts">
/** “固定到页面”小弹窗：选已有页面（下拉可搜索）或新建页面。状态 ?pin=1 */
import { ref, computed } from 'vue';
import { QA_PAGE_LIST } from '@/mock';

defineProps<{ open: boolean; card: string }>();
const emit = defineEmits<{ (e: 'close'): void; (e: 'done', page: string): void }>();
const mode = ref<'exist' | 'new'>('exist');
const pageId = ref<string>(QA_PAGE_LIST[0].id);
const newName = ref('华东复购分析');
const options = QA_PAGE_LIST.map((p) => ({ value: p.id, label: p.name, cards: p.cards }));
const ok = computed(() => (mode.value === 'exist' ? !!pageId.value : !!newName.value.trim()));
function submit() {
  const name = mode.value === 'exist' ? QA_PAGE_LIST.find((p) => p.id === pageId.value)?.name ?? '' : newName.value.trim();
  emit('done', name);
}
</script>

<template>
  <a-modal :open="open" title="固定到页面" :width="440" ok-text="固定" cancel-text="取消"
    :ok-button-props="{ disabled: !ok }" @ok="submit" @cancel="emit('close')">
    <div class="qa-pin-row"><span class="c-3 qa-pin-label">卡片</span><span>{{ card }}</span></div>
    <div class="qa-pin-row">
      <span class="c-3 qa-pin-label">固定到</span>
      <a-radio-group v-model:value="mode">
        <a-radio value="exist">已有页面</a-radio>
        <a-radio value="new">新建页面</a-radio>
      </a-radio-group>
    </div>
    <div class="qa-pin-row">
      <span class="qa-pin-label" />
      <a-select v-if="mode === 'exist'" v-model:value="pageId" show-search option-filter-prop="label" placeholder="搜索页面"
        :options="options" class="qa-pin-ctl">
        <template #option="{ label, cards }"><div class="qa-pin-opt"><span>{{ label }}</span><span class="t-12 c-3">{{ cards }} 张卡片</span></div></template>
      </a-select>
      <a-input v-else v-model:value="newName" placeholder="页面名称" class="qa-pin-ctl" />
    </div>
    <div class="qa-pin-row"><span class="qa-pin-label" /><span class="t-12 c-3">固定后按当前依据定时刷新，口径和范围不随会话变化。</span></div>
  </a-modal>
</template>

<style scoped>
.qa-pin-row { display: flex; align-items: center; gap: 12px; margin-bottom: 12px; }
.qa-pin-row:last-child { margin-bottom: 0; }
.qa-pin-label { width: 48px; flex: none; }
.qa-pin-ctl { flex: 1; }
.qa-pin-opt { display: flex; justify-content: space-between; }
</style>
