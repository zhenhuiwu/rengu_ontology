<script setup lang="ts">
/**
 * “固定到页面”小弹窗：选已有页面（下拉可搜索）或新建页面。状态 ?pin=1
 * 真接口（qaLive 且带 turnId）：下拉走 P1 brief，固定走 P7；新建页面先 P2 再 P7。
 * 固定是写操作：开关 qa 为真时一律调真接口，没有 turnId（演示卡）或失败（含后台未启动）只报错，不假装固定成功；开关为假才演示。
 */
import { ref, computed, watch } from 'vue';
import { message } from 'ant-design-vue';
import { QA_PAGE_LIST } from '@/mock';
import { listPagesBrief } from '@/api/qa';
import * as pagesApi from '@/api/qaPages';
import { qaWriteReal, qaWriteFail } from '../qaFallback';
import { currentApiContext } from '@/api/http';
import { errText } from '../useQaSession';

const props = defineProps<{ open: boolean; card: string; turnId?: string }>();
const emit = defineEmits<{ (e: 'close'): void; (e: 'done', page: string): void }>();
const mode = ref<'exist' | 'new'>('exist');
const pageId = ref<string>(qaWriteReal.value ? '' : QA_PAGE_LIST[0]?.id ?? '');
const newName = ref(qaWriteReal.value ? '' : '华东复购分析');
const live = computed(() => qaWriteReal.value && !!props.turnId);
const livePages = ref<{ id: string; name: string; cards: number }[]>([]);
const pagesKey = ref('');
const list = computed(() => qaWriteReal.value ? pagesKey.value === readKey() ? livePages.value : [] : QA_PAGE_LIST);
const options = computed(() => list.value.map((p) => ({ value: p.id, label: p.name, cards: p.cards })));
const loading = ref(false); const loadError = ref('');
const ok = computed(() => (!qaWriteReal.value || (!!props.turnId && pagesKey.value === readKey())) && !loading.value
  && (mode.value === 'exist' ? list.value.some((p) => p.id === pageId.value) : !!newName.value.trim()));
const busy = ref(false);
const contextKey = () => JSON.stringify(currentApiContext());
const readKey = () => JSON.stringify([contextKey(), props.open, props.turnId, qaWriteReal.value]);
let seq = 0; let writeSeq = 0;
async function loadPages() {
  const n = ++seq; const key = readKey();
  ++writeSeq; busy.value = false;
  pagesKey.value = key;
  livePages.value = []; pageId.value = qaWriteReal.value ? '' : QA_PAGE_LIST[0]?.id ?? '';
  loadError.value = ''; loading.value = false; newName.value = qaWriteReal.value ? '' : '华东复购分析';
  if (!props.open || !live.value) return;
  loading.value = true;
  try {
    const items: { id: string; name: string; cards: number }[] = [];
    for (let page = 1; ; page++) {
      const r = await listPagesBrief({ page, size: 200 });
      if (n !== seq || key !== readKey()) return;
      items.push(...r.items);
      if (items.length >= r.total || !r.items.length) break;
    }
    livePages.value = items; pageId.value = items[0]?.id ?? '';
  } catch (e) { if (n === seq && key === readKey()) loadError.value = `页面列表加载失败：${errText(e)}`; }
  finally { if (n === seq && key === readKey()) loading.value = false; }
}
watch(readKey, loadPages, { immediate: true });
async function submit() {
  if (busy.value || !ok.value) return;
  const context = contextKey(); const turnId = props.turnId; const title = pagesApi.pinTitle(props.card);
  const name = mode.value === 'exist' ? list.value.find((p) => p.id === pageId.value)?.name ?? '' : newName.value.trim();
  if (qaWriteReal.value) {
    if (!turnId) { message.error('缺少已保存的问答轮次，无法固定'); return; }
    const n = ++writeSeq; busy.value = true;
    try {
      const created = mode.value === 'new' ? (await pagesApi.createPage({ name })).id : undefined;
      if (n !== writeSeq || context !== contextKey()) return;
      const pid = created ?? pageId.value;
      try { await pagesApi.pinToPage(pid, { turnId, title }); }
      catch (e) {
        if (created && context === contextKey()) await pagesApi.deletePage(created).catch(() => undefined);
        throw e;
      }
      if (n !== writeSeq || context !== contextKey()) return;
      livePages.value = [];
    } catch (e) { if (n === writeSeq && context === contextKey()) qaWriteFail('固定', e); return; }
    finally { if (n === writeSeq) busy.value = false; }
  }
  if (context === contextKey()) emit('done', name);
}
</script>

<template>
  <a-modal :open="open" title="固定到页面" :width="440" ok-text="固定" cancel-text="取消"
    :ok-button-props="{ disabled: !ok, loading: busy }" @ok="submit" @cancel="emit('close')">
    <div v-if="loadError" role="alert">{{ loadError }} <a @click="loadPages">重试</a></div>
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
        :options="options" :loading="loading" :disabled="qaWriteReal && !turnId" class="qa-pin-ctl">
        <template #option="{ label, cards }"><div class="qa-pin-opt"><span>{{ label }}</span><span class="t-12 c-3">{{ cards }} 张卡片</span></div></template>
      </a-select>
      <a-input v-else v-model:value="newName" placeholder="页面名称" class="qa-pin-ctl" />
    </div>
    <div class="qa-pin-row"><span class="qa-pin-label" /><span class="t-12 c-3">固定后保存当前问答结果及依据；自动重算尚未接入。</span></div>
  </a-modal>
</template>

<style scoped>
.qa-pin-row { display: flex; align-items: center; gap: 12px; margin-bottom: 12px; }
.qa-pin-row:last-child { margin-bottom: 0; }
.qa-pin-label { width: 48px; flex: none; }
.qa-pin-ctl { flex: 1; }
.qa-pin-opt { display: flex; justify-content: space-between; }
</style>
