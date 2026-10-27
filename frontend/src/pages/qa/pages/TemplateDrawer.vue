<script setup lang="ts">
/** 模板预览抽屉（?tpl=T001）：说明、口径、卡片预览（simple 分页），底部“用此模板新建” */
import { computed, ref, watch } from 'vue';
import { qaReal } from '@/api/qa';
import AppPagination from '@/components/AppPagination.vue';
import CardPreview from './CardPreview.vue';
import InfoTip from '../InfoTip.vue';
import UseTemplatePop from './UseTemplatePop.vue';
import type { QaTemplate, TplCreate } from '@/mock/qa-pages';

const props = defineProps<{ tpl?: QaTemplate; readonly: boolean; reason: string }>();
const emit = defineEmits<{ close: []; create: [TplCreate]; created: [string] }>();
const real = qaReal();
const SIZE = 6; const page = ref(1);
watch(() => props.tpl?.id, () => { page.value = 1; });
const cards = computed(() => (props.tpl?.cards ?? []).slice((page.value - 1) * SIZE, page.value * SIZE));
const calibers = computed(() => [...new Set((props.tpl?.cards ?? []).map((c) => `${c.metric} ${c.version}`))]);
</script>

<template>
  <a-drawer :open="!!tpl" :width="760" @close="emit('close')">
    <template #title>
      <span v-if="tpl" class="td-title">模板预览 · {{ tpl.name }} <InfoTip :title="`${tpl.desc}。模板只含卡片结构和口径，不含数据；新建后按当前身份的数据权限取数。`" /></span>
    </template>
    <template #extra>
      <UseTemplatePop v-if="tpl" :tpl="tpl" size="middle" primary :disabled="readonly" :reason="reason" @create="(e) => emit('create', e)" @created="(id) => emit('created', id)" />
    </template>
    <div v-if="tpl" class="td">
      <div class="td-meta">
        <span><span class="c-3">场景</span> {{ tpl.scene }}</span>
        <span><span class="c-3">提供方</span> {{ tpl.provider }}</span>
        <span><span class="c-3">使用</span> <span class="num">{{ tpl.uses }}</span> 次</span>
        <span><span class="c-3">更新</span> <span class="num">{{ tpl.updatedAt }}</span></span>
      </div>
      <div class="td-row"><span class="c-3 t-12">口径版本</span><a-tag v-for="c in calibers" :key="c">{{ c }}</a-tag></div>
      <div class="td-grid"><CardPreview v-for="(c, i) in cards" :key="c.key" :card="c" :structure-only="real" :index="(page - 1) * SIZE + i + 1" /></div>
      <AppPagination v-model:page="page" variant="simple" :total="tpl.cards.length" :size="SIZE" unit="张卡片" />
    </div>
  </a-drawer>
</template>

<style scoped>
.td { display: flex; flex-direction: column; gap: 10px; }
.td-meta { display: flex; gap: 24px; }
.td-title { display: inline-flex; align-items: center; gap: 8px; }
.td-row { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
.td-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
</style>
