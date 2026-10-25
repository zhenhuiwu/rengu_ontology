<script setup lang="ts">
/**
 * 公共详情抽屉。全站只挂一个（在 AppLayout 里），由 URL 驱动，最多两层：
 *   ?drawer=<kind>:<id>                      第一层
 *   ?drawer=<kind>:<id>&drawer2=<kind>:<id>   第二层叠在第一层之上（第一层被推开 180px），带返回按钮
 * 页面里打开：useDrawer().open('order', 'SO...') 或 <ObjRef kind id>；抽屉内容里的 ObjRef 自动开第二层，
 * 在第二层里再打开对象会替换第二层。每层内容见 DrawerPanel.vue。
 */
import { computed } from 'vue';
import { useDrawer } from '@/composables/useDrawer';
import { resolveObject } from './objectRegistry';
import DrawerPanel from './DrawerPanel.vue';

const { current, current2 } = useDrawer();
const parentTitle = computed(() => (current.value ? resolveObject(current.value.kind, current.value.id)?.title : undefined));
</script>

<template>
  <DrawerPanel :target="current" :level="1">
    <DrawerPanel :target="current2" :level="2" :parent-title="parentTitle" />
  </DrawerPanel>
</template>
