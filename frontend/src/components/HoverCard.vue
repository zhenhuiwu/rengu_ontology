<script setup lang="ts">
/** 悬停卡：只读预览一个对象。<HoverCard kind="store" id="SH0031">上海浦东张江高科店</HoverCard> */
import { computed } from 'vue';
import { resolveObject } from './objectRegistry';

const props = withDefaults(defineProps<{ kind: string; id: string; underline?: boolean }>(), { underline: true });
const view = computed(() => resolveObject(props.kind, props.id));
</script>

<template>
  <a-popover v-if="view" placement="bottomLeft" :mouse-enter-delay="0.3" :overlay-inner-style="{ padding: 0 }">
    <template #content>
      <div class="hc">
        <div class="hc-head">
          <span class="t-14 fw-600 c-1 hc-title">{{ view.title }}</span>
          <a-tag v-if="view.status" :color="view.status.color" class="hc-tag">{{ view.status.text }}</a-tag>
        </div>
        <div class="t-12 c-3">{{ view.kindLabel }} · {{ view.code }}</div>
        <dl class="hc-list">
          <template v-for="f in view.brief" :key="f.label">
            <dt class="t-12 c-3">{{ f.label }}</dt>
            <dd class="t-12 c-1 num">{{ f.value }}</dd>
          </template>
        </dl>
      </div>
    </template>
    <span :class="{ 'hc-trigger': props.underline }"><slot>{{ view.title }}</slot></span>
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
