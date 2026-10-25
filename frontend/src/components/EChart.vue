<script setup lang="ts">
/** ECharts 封装：统一字体、轴线灰阶和 12px 轴文字。高度由父容器决定（height:100%）。 */
import { computed } from 'vue';
import { use } from 'echarts/core';
import { SVGRenderer } from 'echarts/renderers';
import { LineChart, BarChart, PieChart } from 'echarts/charts';
import { GridComponent, TooltipComponent, LegendComponent, MarkLineComponent } from 'echarts/components';
import VChart from 'vue-echarts';
import { FONT_FAMILY, GRAY, BRAND } from '@/theme';

use([SVGRenderer, LineChart, BarChart, PieChart, GridComponent, TooltipComponent, LegendComponent, MarkLineComponent]);

const props = defineProps<{ option: Record<string, unknown> }>();
const merged = computed(() => ({
  color: [BRAND, '#7aa3e5', GRAY.g7],
  textStyle: { fontFamily: FONT_FAMILY, fontSize: 12, color: GRAY.g9 },
  animationDuration: 300,
  ...props.option,
}));
</script>

<template>
  <VChart class="echart" :option="merged" autoresize />
</template>

<style scoped>
.echart { width: 100%; height: 100%; }
</style>
