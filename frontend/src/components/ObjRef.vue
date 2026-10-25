<script setup lang="ts">
/** 对象引用：悬停出悬停卡，点击在当前页打开详情抽屉（不跳转）。 */
import HoverCard from './HoverCard.vue';
import { useDrawer } from '@/composables/useDrawer';
import { checkObject } from './objectRegistry';

const props = defineProps<{ kind: string; id: string }>();
const { open } = useDrawer();
if (import.meta.env.DEV) checkObject(props.kind, props.id, 'ObjRef');
</script>

<template>
  <HoverCard :kind="props.kind" :id="props.id" :underline="false">
    <a class="obj-ref" @click.prevent="open(props.kind, props.id)"><slot /></a>
  </HoverCard>
</template>

<style scoped>
.obj-ref { color: var(--brand); }
</style>
