<script setup lang="ts">
/** 悬停卡：只读预览一个对象。<HoverCard kind="store" id="SH0031">上海浦东张江高科店</HoverCard> */
import { computed, ref, watch } from 'vue';
import { resolveObject } from './objectRegistry';
import { usesRealObject } from './realObjectResolver';

const props = withDefaults(defineProps<{ kind: string; id: string; underline?: boolean }>(), { underline: true });
const open = ref(false);
const real = computed(() => usesRealObject(props.kind));
const view = computed(() => !real.value || open.value ? resolveObject(props.kind, props.id) : null);
watch([() => props.kind, () => props.id], () => { open.value = false; });
</script>

<template>
  <a-popover v-if="real || view" v-model:open="open" placement="bottomLeft" :mouse-enter-delay="0.3" :overlay-inner-style="{ padding: 0 }">
    <template #content>
      <div v-if="view" class="hc">
        <div class="hc-head">
          <span class="t-14 fw-600 c-1 hc-title" :title="`${view.title} · ${view.kindLabel} · ${view.code}`">{{ view.title }}<span class="fw-400 c-2 num"> · {{ view.code }}</span></span>
          <a-tag v-if="view.status" :color="view.status.color" class="hc-tag">{{ view.status.text }}</a-tag>
        </div>
        <dl class="hc-list">
          <template v-for="f in view.brief" :key="f.label">
            <dt class="t-12 c-3">{{ f.label }}</dt>
            <dd class="t-12 c-1 num">{{ f.value }}</dd>
          </template>
        </dl>
      </div>
    </template>
    <span :class="{ 'hc-trigger': props.underline }"><slot>{{ view?.title ?? props.id }}</slot></span>
  </a-popover>
  <span v-else><slot /></span>
</template>

<style scoped>
.hc { width: 300px; padding: 12px 16px; }
.hc-head { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.hc-title { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.hc-tag { margin-inline-end: 0; }
.hc-list { display: grid; grid-template-columns: 64px 1fr; gap: 4px 8px; margin: 8px 0 0; padding-top: 8px; border-top: var(--border); }
.hc-list dd { margin: 0; word-break: break-all; }
.hc-trigger { border-bottom: 1px dashed var(--g7); cursor: default; }
</style>
