import { onBeforeUnmount, onMounted } from 'vue';
import { message } from 'ant-design-vue';
import { currentApiContext } from '@/api/http';
import { consumeAssistantPrefill, type AssistantTarget } from '@/api/assistant';

export function useAssistantDraft(targets: AssistantTarget[], blocked: () => boolean, apply: (draft: Record<string, unknown>, target: AssistantTarget) => void) {
  function receive(notify = false) {
    if (blocked()) { if (notify) message.info('请先完成或关闭当前编辑，再采用助手建议'); return; }
    for (const target of targets) {
      const value = consumeAssistantPrefill(target, currentApiContext().ontologyId ?? '');
      if (value) { apply(value.draft, target); break; }
    }
  }
  function onPrefill(event: Event) {
    if (targets.includes((event as CustomEvent).detail?.target)) receive(true);
  }
  onMounted(() => { window.addEventListener('assistant-prefill', onPrefill); receive(); });
  onBeforeUnmount(() => window.removeEventListener('assistant-prefill', onPrefill));
}
