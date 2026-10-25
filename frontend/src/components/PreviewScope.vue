<script setup lang="ts">
/**
 * 预览期间在页头右侧显示被预览用户的行级数据范围（AppLayout 自动挂，只在“使用”区显示）。
 * 对象搜索 / 对象详情按当前对象类型（?type= / ?kind=）显示该类型的范围；其他页面（问数等）显示全部有限制的对象。
 * 页面要在自己的结果区域里显示范围，用 usePreview().scopeLabel('订单')。
 */
import { computed } from 'vue';
import { useRoute } from 'vue-router';
import { PAGES } from '@/nav';
import { usePreview } from '@/composables/usePreview';

const route = useRoute();
const { active, limited, scopeLabel } = usePreview();
const KIND_LABEL: Record<string, string> = { store: '门店', order: '订单', member: '会员', product: '商品', region: '大区', inventory: '库存快照', alert: '告警', employee: '员工' };
const inUse = computed(() => (PAGES.find((p) => p.key === route.meta.navKey)?.area.key ?? route.meta.area) === 'use');
const objType = computed(() => {
  const k = route.meta.navKey === 'object-search' ? route.query.type || 'store' : route.meta.navKey === 'object-detail' ? route.query.kind || 'store' : null;
  return k ? KIND_LABEL[String(k)] ?? null : null;
});
const text = computed(() => {
  if (objType.value) return scopeLabel(objType.value);
  const l = limited.value;
  if (!l.length) return '数据范围：全部';
  return `数据范围：${l[0].name} ${l[0].scope}${l.length > 1 ? ` 等 ${l.length} 类对象` : ''}`;
});
const tip = computed(() => (limited.value.length ? limited.value.map((o) => `${o.name}：${o.scope}`).join('\n') : '所有对象不限行级范围'));
</script>

<template>
  <a-tooltip v-if="active && inUse" :title="tip" overlay-class-name="pvs-tip">
    <span class="pvs t-12 c-1">{{ text }}</span>
  </a-tooltip>
</template>

<style scoped>
.pvs { display: inline-flex; align-items: center; height: 24px; padding: 0 8px; border: var(--border); background: var(--g2); border-radius: var(--radius); white-space: nowrap; max-width: 420px; overflow: hidden; text-overflow: ellipsis; }
</style>
<style>
.pvs-tip .ant-tooltip-inner { white-space: pre-line; }
</style>
