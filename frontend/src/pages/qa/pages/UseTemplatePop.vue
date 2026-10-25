<script setup lang="ts">
/** “用此模板新建”气泡：页面名称、范围、口径、刷新计划；确定后在“我的页面”新增一页并打开它 */
import { ref, watch } from 'vue';
import { PAGE_SCOPES, type QaTemplate, type TplCreate } from '@/mock/qa-pages';

const props = defineProps<{ tpl: QaTemplate; disabled?: boolean; reason?: string; size?: 'small' | 'middle'; primary?: boolean }>();
const emit = defineEmits<{ create: [TplCreate] }>();
const open = ref(false);
const name = ref(''); const scope = ref('华东大区'); const caliber = ref('不含税'); const refresh = ref('每天 08:00');
watch(open, (o) => { if (o) name.value = `${props.tpl.name}（华东）`; });
function ok() { emit('create', { tpl: props.tpl, name: name.value.trim(), scope: scope.value, caliber: caliber.value, refresh: refresh.value }); open.value = false; }
</script>

<template>
  <a-popover v-model:open="open" trigger="click" placement="leftTop" :title="`用此模板新建 · ${tpl.name}`">
    <template #content>
      <div class="ut">
        <div class="ut-f"><span class="c-3 ut-l">页面名称</span><a-input v-model:value="name" :maxlength="30" /></div>
        <div class="ut-f"><span class="c-3 ut-l">范围</span><a-select v-model:value="scope" class="ut-c" :options="PAGE_SCOPES.map((s) => ({ value: s, label: s }))" /></div>
        <div class="ut-f"><span class="c-3 ut-l">口径</span>
          <a-radio-group v-model:value="caliber"><a-radio value="不含税">不含税</a-radio><a-radio value="含税">含税</a-radio></a-radio-group>
        </div>
        <div class="ut-f"><span class="c-3 ut-l">刷新</span><a-select v-model:value="refresh" class="ut-c" :options="['每小时整点', '每天 08:00', '每周一 08:00', '手动刷新'].map((s) => ({ value: s, label: s }))" /></div>
        <div class="t-12 c-3">将生成 {{ tpl.cards.length }} 张卡片，口径取当前发布版本。</div>
        <div class="ut-btns"><a-button size="small" @click="open = false">取消</a-button><a-button size="small" type="primary" :disabled="!name.trim()" @click="ok">新建</a-button></div>
      </div>
    </template>
    <a-tooltip :title="reason"><a-button :size="size ?? 'small'" :type="primary ? 'primary' : 'link'" :disabled="disabled">用此模板新建</a-button></a-tooltip>
  </a-popover>
</template>

<style scoped>
.ut { width: 320px; display: flex; flex-direction: column; gap: 10px; }
.ut-f { display: flex; align-items: center; gap: 8px; }
.ut-l { width: 56px; flex: none; }
.ut-c { flex: 1; }
.ut-btns { display: flex; justify-content: flex-end; gap: 8px; }
</style>
