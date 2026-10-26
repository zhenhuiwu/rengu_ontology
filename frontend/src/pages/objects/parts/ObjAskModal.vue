<script setup lang="ts">
/**
 * 带范围的提问浮层：在当前页打开，不跳转。范围来自对象搜索的筛选结果，或对象详情的当前对象。
 * 回答：开关 qa 为真接口且后台可用（qaLive）时走 O4 /scoped-ask；后台没起来回退 mock（answerFor），口径名称带版本。
 * liveScope：对象搜索真接口模式下的范围（按 filter 由后台重算，不传全部 id）；count：真接口模式的对象数。
 */
import { computed, ref, watch } from 'vue';
import { SendOutlined } from '@ant-design/icons-vue';
import ObjRef from '@/components/ObjRef.vue';
import { useReadonly } from '@/composables/useReadonly';
import { usePreview } from '@/composables/usePreview';
import { answerFor, type AskAnswer, type AskScope } from '@/mock/objects-qa';
import { fmtInt } from '@/mock/format';
import { message } from 'ant-design-vue';
import { scopedAsk, type ScopedAskIn } from '@/api/qaObjects';
import { qaLive, qaFallBackIfDown } from '../../qa/qaFallback';
import { errText } from '../../qa/useQaSession';

const props = defineProps<{ scope: AskScope; suggestions: string[]; initial?: string; liveScope?: ScopedAskIn; count?: number }>();
const open = defineModel<boolean>('open', { default: false });
const { readonly: ro, reason } = useReadonly();
const pv = usePreview();
/** 提问只读：预览时照常可问，回答只含被预览用户范围内的数据 */
const readonly = computed(() => ro.value && !pv.active.value);
const q = ref('');
const asked = ref('');
const answer = ref<AskAnswer | null>(null);
const loading = ref(false);
let seq = 0;

watch(open, (v) => {
  if (!v) return;
  answer.value = null; asked.value = ''; seq++; loading.value = false;
  q.value = props.initial ?? '';
  if (props.initial && !readonly.value) send();
}, { immediate: true });

function send(text?: string) {
  const t = (text ?? q.value).trim();
  if (!t || readonly.value) return;
  asked.value = t; q.value = ''; answer.value = null;
  if (!qaLive.value) { answer.value = answerFor(props.scope, t); return; }
  const my = ++seq; loading.value = true;
  scopedAsk(props.liveScope ?? props.scope, t)
    .then((a) => { if (my === seq) answer.value = a; })
    .catch((e) => {
      if (my !== seq) return;
      if (qaFallBackIfDown(e)) answer.value = answerFor(props.scope, t);
      else { asked.value = ''; message.error(`提问失败：${errText(e)}`); }
    })
    .finally(() => { if (my === seq) loading.value = false; });
}
const countText = computed(() => (props.scope.single ? '' : `共 ${fmtInt(props.count ?? props.scope.ids.length)} 个对象`));
</script>

<template>
  <a-modal v-model:open="open" :title="scope.single ? `对「${scope.single.title}」提问` : '用这批对象提问'" :width="720" :footer="null" destroy-on-close>
    <div class="oa">
      <div class="oa-scope">
        <span class="c-3">提问范围</span>
        <span>{{ scope.label }}</span>
        <a-tag v-for="c in scope.conds" :key="c" class="oa-tag">{{ c }}</a-tag>
        <span v-if="countText" class="c-3 num">{{ countText }}</span>
        <span v-if="pv.active.value" class="t-12 c-2">只含 {{ pv.userName.value }} 数据范围内的数据</span>
      </div>

      <template v-if="asked && loading && !answer">
        <div class="oa-q"><span class="oa-q-bubble">{{ asked }}</span></div>
        <div class="oa-ans"><a-spin size="small" /></div>
      </template>
      <template v-else-if="answer">
        <div class="oa-q"><span class="oa-q-bubble">{{ asked }}</span></div>
        <div class="oa-ans">
          <p class="oa-text">{{ answer.text }}</p>
          <template v-if="answer.rows.length">
            <div class="t-12 c-3 oa-rows-title">{{ answer.rowsTitle }}</div>
            <div v-for="r in answer.rows" :key="r.id" class="oa-row">
              <ObjRef :kind="r.kind" :id="r.id">{{ r.name }}</ObjRef>
              <span class="c-3 oa-row-note">{{ r.note }}</span>
              <span class="num oa-row-v">{{ r.value }}</span>
            </div>
          </template>
          <div class="t-12 c-3 oa-basis">{{ answer.basis }}</div>
        </div>
      </template>
      <div v-else class="oa-sugs">
        <span class="t-12 c-3">可以这样问</span>
        <div class="oa-sug-list">
          <a-tooltip v-for="s in suggestions" :key="s" :title="readonly ? reason : undefined">
            <a-button size="small" :disabled="readonly" @click="send(s)">{{ s }}</a-button>
          </a-tooltip>
        </div>
      </div>

      <div class="oa-input">
        <a-textarea v-model:value="q" :rows="2" :disabled="readonly" :placeholder="readonly ? reason : '在这个范围内提问，Enter 发送'" @press-enter.prevent="send()" />
        <a-tooltip :title="readonly ? reason : undefined">
          <a-button type="primary" :disabled="readonly || loading || !q.trim()" @click="send()"><template #icon><SendOutlined /></template>发送</a-button>
        </a-tooltip>
      </div>
    </div>
  </a-modal>
</template>

<style scoped>
.oa { display: flex; flex-direction: column; gap: 12px; }
.oa-scope { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; padding: 8px 12px; background: var(--g2); border-radius: var(--radius); }
.oa-tag { margin: 0; }
.oa-q { display: flex; justify-content: flex-end; }
.oa-q-bubble { padding: 4px 12px; background: var(--g3); border-radius: var(--radius); }
.oa-ans { border: var(--border); border-radius: var(--radius); padding: 12px 16px; }
.oa-text { margin: 0 0 8px; }
.oa-rows-title { margin-bottom: 4px; }
.oa-row { display: flex; align-items: center; gap: 12px; height: 32px; border-bottom: var(--border); }
.oa-row:last-of-type { border-bottom: none; }
.oa-row-note { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.oa-row-v { width: 80px; text-align: right; }
.oa-basis { margin-top: 8px; }
.oa-sugs { display: flex; flex-direction: column; gap: 8px; }
.oa-sug-list { display: flex; flex-wrap: wrap; gap: 8px; }
.oa-input { display: flex; gap: 8px; align-items: flex-end; }
</style>
