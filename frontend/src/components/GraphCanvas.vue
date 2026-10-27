<script setup lang="ts">
/**
 * AntV X6 画布示例（空画布）。网格背景、拖动平移、Ctrl+滚轮缩放；尺寸跟随父容器，不出现滚动条。
 * 关系图谱 / 状态机页面在此基础上加节点：graph.addNode(...)。通过 @ready 拿到 graph 实例。
 */
import { onBeforeUnmount, onMounted, ref } from 'vue';
import { Graph } from '@antv/x6';
import { GRAY } from '@/theme';
import { positionGraphLabels } from './graphEdges';

const emit = defineEmits<{ ready: [graph: Graph] }>();
const el = ref<HTMLDivElement>();
let graph: Graph | null = null;

onMounted(() => {
  if (!el.value) return;
  graph = new Graph({
    container: el.value,
    autoResize: true,
    background: { color: GRAY.g2 },
    grid: { visible: true, size: 16, type: 'dot', args: { color: GRAY.g6, thickness: 1 } },
    panning: true,
    mousewheel: { enabled: true, modifiers: ['ctrl', 'meta'], minScale: 0.3, maxScale: 3 },
  });
  const current = graph;
  current.on('render:done', () => positionGraphLabels(current));
  emit('ready', current);
});
onBeforeUnmount(() => { graph?.dispose(); graph = null; });

const zoom = (d: number) => graph?.zoom(d);
const fit = () => { graph?.zoomToFit({ padding: 24, maxScale: 1 }); graph?.centerContent(); };
</script>

<template>
  <div class="gc">
    <div ref="el" class="gc-canvas" />
    <div class="gc-tools">
      <a-button size="small" @click="zoom(0.1)"><PlusOutlined /></a-button>
      <a-button size="small" @click="zoom(-0.1)"><MinusOutlined /></a-button>
      <a-button size="small" @click="fit"><ExpandOutlined /></a-button>
    </div>
    <div class="gc-empty t-14 c-3"><slot name="empty" /></div>
  </div>
</template>

<style scoped>
.gc { position: relative; width: 100%; height: 100%; overflow: hidden; }
.gc-canvas { position: absolute; inset: 0; }
.gc-tools { position: absolute; right: 12px; bottom: 12px; display: flex; gap: 4px; }
.gc-empty { position: absolute; left: 50%; top: 50%; transform: translate(-50%, -50%); pointer-events: none; }
</style>
