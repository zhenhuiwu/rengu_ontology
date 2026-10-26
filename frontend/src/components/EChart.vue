<script setup lang="ts">
/** ECharts 封装：统一字体、轴线灰阶和 12px 轴文字。高度由父容器决定（height:100%）。 */
import { computed, onMounted, onBeforeUnmount, ref } from 'vue';
import { use } from 'echarts/core';
import { SVGRenderer } from 'echarts/renderers';
import { LineChart, BarChart, PieChart } from 'echarts/charts';
import { GridComponent, TooltipComponent, LegendComponent, MarkLineComponent } from 'echarts/components';
import VChart from 'vue-echarts';
import { FONT_FAMILY, GRAY, BRAND } from '@/theme';

use([SVGRenderer, LineChart, BarChart, PieChart, GridComponent, TooltipComponent, LegendComponent, MarkLineComponent]);

const props = defineProps<{ option: Record<string, unknown> }>();
const root = ref<HTMLDivElement | null>(null);
const chart = ref<InstanceType<typeof VChart> | null>(null);
const ready = ref(false);
let observer: ResizeObserver | undefined;
onMounted(() => {
  observer = new ResizeObserver(([entry]) => {
    const { width, height } = entry.contentRect;
    if (width <= 0 || height <= 0) return;
    ready.value = true;
    chart.value?.resize({ width, height });
  });
  if (root.value) observer.observe(root.value);
});
onBeforeUnmount(() => observer?.disconnect());
const merged = computed(() => ({
  color: [BRAND, '#7aa3e5', GRAY.g7],
  textStyle: { fontFamily: FONT_FAMILY, fontSize: 12, color: GRAY.g9 },
  animationDuration: 300,
  ...props.option,
}));
</script>

<template>
  <div ref="root" class="echart">
    <VChart v-if="ready" ref="chart" class="echart-canvas" :option="merged" />
  </div>
</template>

<style scoped>
.echart, .echart-canvas { width: 100%; height: 100%; }
</style>
