<script setup lang="ts">
/** 指标 + 版本；公共 METRICS 里有的指标点开指标口径抽屉，其余只显示文字（复购客户销售额、退款率尚未进公共 mock） */
import { computed } from 'vue';
import { useDrawer } from '@/composables/useDrawer';
import { T_METRICS } from '@/mock/qa-tuning';

const props = defineProps<{ k: string }>();
const m = computed(() => T_METRICS[props.k]);
const drawer = useDrawer();
</script>

<template>
  <a v-if="m.drawerId" class="c-1" @click.stop="drawer.open('metric', m.drawerId!)">{{ m.name }} <span class="num">{{ m.version }}</span></a>
  <span v-else class="c-1">{{ m.name }} <span class="num">{{ m.version }}</span></span>
</template>
