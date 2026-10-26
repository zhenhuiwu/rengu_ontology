<script setup lang="ts">
/**
 * “固定到页面”小弹窗：选已有页面（下拉可搜索）或新建页面。状态 ?pin=1
 * 真接口（qaLive 且带 turnId）：下拉走 P1 brief，固定走 P7；新建页面先 P2 再 P7。没有 turnId 或后台未启动时照旧演示。
 */
import { ref, computed, watch } from 'vue';
import { message } from 'ant-design-vue';
import { QA_PAGE_LIST } from '@/mock';
import { listPagesBrief } from '@/api/qa';
import * as pagesApi from '@/api/qaPages';
import { qaLive, qaFallBackIfDown } from '../qaFallback';
import { errText } from '../useQaSession';

const props = defineProps<{ open: boolean; card: string; turnId?: string }>();
const emit = defineEmits<{ (e: 'close'): void; (e: 'done', page: string): void }>();
const mode = ref<'exist' | 'new'>('exist');
const pageId = ref<string>(QA_PAGE_LIST[0].id);
const newName = ref('华东复购分析');
const live = computed(() => qaLive.value && !!props.turnId);
const livePages = ref<{ id: string; name: string; cards: number }[] | null>(null);
const list = computed(() => (live.value && livePages.value ? livePages.value : QA_PAGE_LIST));
const options = computed(() => list.value.map((p) => ({ value: p.id, label: p.name, cards: p.cards })));
const ok = computed(() => (mode.value === 'exist' ? !!pageId.value : !!newName.value.trim()));
const busy = ref(false);
watch(() => [props.open, live.value] as const, async ([o, l]) => {
  if (!o || !l) return;
  try {
    const r = await listPagesBrief({ page: 1, size: 100 });
    livePages.value = r.items;
    if (!r.items.some((p) => p.id === pageId.value)) pageId.value = r.items[0]?.id ?? '';
  } catch (e) { if (!qaFallBackIfDown(e)) message.error(`页面列表加载失败：${errText(e)}`); }
}, { immediate: true });
async function submit() {
  const name = mode.value === 'exist' ? list.value.find((p) => p.id === pageId.value)?.name ?? '' : newName.value.trim();
  if (live.value && props.turnId) {
    busy.value = true;
    try {
      const pid = mode.value === 'exist' ? pageId.value : (await pagesApi.createPage({ name, scope: '华东大区' })).id;
      await pagesApi.pinToPage(pid, { turnId: props.turnId, title: props.card });
      livePages.value = null;
    } catch (e) {
      if (!qaFallBackIfDown(e)) { message.error(`固定失败：${errText(e)}`); return; }
    } finally { busy.value = false; }
  }
  emit('done', name);
}
</script>

<template>
  <a-modal :open="open" title="固定到页面" :width="440" ok-text="固定" cancel-text="取消"
    :ok-button-props="{ disabled: !ok, loading: busy }" @ok="submit" @cancel="emit('close')">
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
