<script setup lang="ts">
/** 发起变更：新建变更单，或把改动加入已有变更单（整版发布，一张变更单可以改多个对象）。 */
import { computed, ref, watch } from 'vue';
import { allDrafts } from './useDraftView';

const props = defineProps<{ targetName: string; titlePlaceholder?: string }>();
const open = defineModel<boolean>('open', { default: false });
const emit = defineEmits<{ ok: [opts: { join?: string; title?: string }] }>();

const mode = ref<'new' | 'join'>('new');
const title = ref('');
const desc = ref('');
const join = ref<string>();
const options = computed(() => allDrafts().map((d) => ({ value: d.id, label: `${d.id}　${d.title}（${d.owner}）` })));
watch(open, (v) => { if (v) { mode.value = 'new'; title.value = props.titlePlaceholder ? '' : `${props.targetName}：`; desc.value = ''; join.value = undefined; } });
const canOk = computed(() => (mode.value === 'new' ? title.value.trim().length > 0 : !!join.value));
function ok() { emit('ok', mode.value === 'join' ? { join: join.value } : { title: title.value.trim() }); open.value = false; }
</script>

<template>
  <a-modal v-model:open="open" title="发起变更" :width="520" ok-text="进入草稿" :ok-button-props="{ disabled: !canOk }" @ok="ok">
    <p class="t-14 c-2 sc-p">改动先保存在变更草稿里，审核通过并整版发布后才生效。发布前问数不受影响。</p>
    <a-form layout="vertical">
      <a-form-item label="变更对象"><span class="t-14 c-1">{{ targetName }}</span></a-form-item>
      <a-form-item>
        <a-radio-group v-model:value="mode">
          <a-radio value="new">新建变更单</a-radio>
          <a-radio value="join">加入已有变更单</a-radio>
        </a-radio-group>
      </a-form-item>
      <template v-if="mode === 'new'">
        <a-form-item label="变更标题" required><a-input v-model:value="title" :maxlength="40" :placeholder="titlePlaceholder" /></a-form-item>
        <a-form-item label="变更说明"><a-textarea v-model:value="desc" :rows="3" placeholder="为什么改、影响哪些指标和问数" /></a-form-item>
      </template>
      <a-form-item v-else label="变更单" required>
        <a-select v-model:value="join" :options="options" placeholder="选择未发布的变更单" />
      </a-form-item>
    </a-form>
  </a-modal>
</template>

<style scoped>
.sc-p { margin: 0 0 16px; }
</style>
