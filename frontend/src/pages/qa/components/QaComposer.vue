<script setup lang="ts">
/** 输入框。下方“范围”“口径”是整个会话共用的（v-model 由工作台持有），中途修改会在对话里插入系统提示 */
import { ref, computed } from 'vue';
import { useQaSession, clarifyFollowUp } from '../useQaSession';
import { SendOutlined, PaperClipOutlined } from '@ant-design/icons-vue';
import { useScopeCaliber } from '../qaScope';
import { useReadonly } from '@/composables/useReadonly';
import { usePreview } from '@/composables/usePreview';
/** 历史版本只读：全部置灰。历史版本里“不含税”口径尚未发布（v2026.10.3 才改），口径框显示“按该版本默认”，不显示当前版本的口径 */
const { readonly, reason } = useReadonly();
/** 预览期间也只读，但口径框照常显示当前口径（不是历史版本） */
const { active: previewing } = usePreview();
const HIST_CALIBER = [{ value: '__ver', label: '口径：按该版本默认' }];
const scope = defineModel<string>('scope', { required: true });
const caliber = defineModel<string>('caliber', { required: true });
const { draft: text, send, real, live, sending, activeId, detail } = useQaSession();
/* 范围 = 组织层级维度取值，口径 = 指标 variants（没有就隐藏口径框），见 ../qaScope.ts；会话里存的值不在选项里时补上，免得显示成空 */
const sc = useScopeCaliber();
const withCur = (opts: { value: string; label: string }[], v: string, pre: string) =>
  (!v || opts.some((o) => o.value === v) ? opts : [...opts, { value: v, label: `${pre}：${v}` }]);
const scopeOpts = computed(() => withCur(sc.scopeOpts.value, scope.value, '范围'));
const caliberOpts = computed(() => withCur(sc.caliberOpts.value, caliber.value, '口径'));
const input = ref<{ focus: () => void }>();
const canSend = computed(() => !readonly.value && !!text.value.trim() && !(real && (sending.value || !activeId.value)));
const sendTip = computed(() => reason.value || (real && sending.value ? '正在查询上一问' : text.value.trim() ? '' : '先输入问题'));
function onSend() { if (canSend.value) send(); }
/** Enter 发送，Shift + Enter 换行；输入法组字中不发送 */
function onKey(e: KeyboardEvent) {
  if (e.key !== 'Enter' || e.shiftKey || e.isComposing) return;
  e.preventDefault(); onSend();
}
/** 追问词：直接发送 */
const MOCK_FOLLOW_UPS = ['按门店等级再拆一下', '和去年同期比', '为什么安徽降得最多'];
/** 真接口：取本会话最后一轮回答的 answer.suggest.followUps（链路目前只在反问轮给，= 反问选项）；没有就不显示追问条 [推断] */
const lastAnswer = computed(() => {
  const ts = detail.value?.id === activeId.value ? detail.value.turns : [];
  const t = ts[ts.length - 1];
  return t && t.kind === 'answer' ? t : null;
});
const followUps = computed<string[]>(() => {
  if (!live.value) return MOCK_FOLLOW_UPS;
  const a = lastAnswer.value?.answer as { suggest?: { followUps?: string[] } } | undefined;
  return a?.suggest?.followUps ?? [];
});
/** 反问轮的追问词跟卡片选项一样拼成自包含问题 */
function onChip(f: string) {
  if (readonly.value || (real && sending.value)) return;
  const t = lastAnswer.value;
  if (real && t && 'card' in t.answer && t.answer.card === 'clarify') send(clarifyFollowUp(t.question, t.answer.clarify.term, f));
  else send(f);
}
</script>

<template>
  <div class="qa-composer">
    <div class="qa-cp-input">
      <a-textarea ref="input" v-model:value="text" @keydown="onKey" :disabled="readonly" :title="reason || undefined" :bordered="false" :auto-size="{ minRows: 1, maxRows: 1 }" placeholder="输入问题，或描述要执行的动作和目标；Enter 发送" />
      <div class="qa-cp-bar">
        <a-space :size="8">
            <a-select v-model:value="scope" :title="reason || '本会话共用，修改后从下一问开始生效'" :disabled="readonly" size="small" class="qa-cp-sel" :options="scopeOpts" />
            <a-select v-if="readonly && !previewing" value="__ver" :title="reason" disabled size="small" class="qa-cp-sel qa-cp-sel-wide" :options="HIST_CALIBER" />
            <a-select v-else-if="sc.showCaliber.value" v-model:value="caliber" :title="reason || '本会话共用，修改后从下一问开始生效'" :disabled="readonly" size="small" class="qa-cp-sel" :options="caliberOpts" />
          <a-tooltip :title="reason || '暂未开放：引用对象将在后续版本提供'"><a-button size="small" type="text" disabled><template #icon><PaperClipOutlined /></template>引用对象</a-button></a-tooltip>
          <a-divider v-if="followUps.length" type="vertical" />
          <a-tooltip v-for="f in followUps" :key="f" :title="reason || '点击直接追问'"><a-tag class="qa-cp-chip" :class="{ off: readonly }" @click="onChip(f)">{{ f }}</a-tag></a-tooltip>
        </a-space>
        <a-tooltip :title="sendTip || undefined"><a-button type="primary" class="qa-cp-send" :disabled="!canSend" @click="onSend"><template #icon><SendOutlined /></template>发送</a-button></a-tooltip>
      </div>
    </div>
  </div>
</template>

<style scoped>
.qa-composer { flex: none; padding: 10px 20px 12px; background: var(--g1); border-top: var(--border); }
.qa-cp-input { border: 1px solid var(--g6); border-radius: var(--radius); padding: 8px 12px; }
.qa-cp-input :deep(textarea) { padding: 0; }
.qa-cp-bar { display: flex; justify-content: space-between; align-items: center; margin-top: 8px; }
.qa-cp-sel { width: 150px; }
.qa-cp-sel-wide { width: 180px; }
.qa-cp-chip.off { cursor: not-allowed; color: var(--g6); }
.qa-cp-chip { cursor: pointer; background: var(--g1); margin-inline-end: 0; }
</style>
